"""The dump, the retention sweep and the delete guards.

Every case points core_backup.dir at a throwaway directory, so nothing here
can touch a real archive.
"""
import json
import os
import shutil
import tempfile
import zipfile

import odoo
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestBackup(TransactionCase):

    def setUp(self):
        super().setUp()
        self.Backup = self.env['core.backup']
        self.dir = tempfile.mkdtemp(prefix='core_backup_test_')
        self._set('core_backup.dir', self.dir)
        self._set('core_backup.enabled', 'True')
        self._set('core_backup.with_filestore', 'True')

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)
        super().tearDown()

    def _set(self, key, value):
        self.env['ir.config_parameter'].sudo().set_param(key, value)

    def _age(self, backup, days):
        """Push a row past the retention window. create_date is readonly."""
        self.env.cr.execute(
            "UPDATE core_backup SET create_date = now() - interval %s WHERE id = %s",
            ('%s days' % days, backup.id))
        backup.invalidate_recordset()

    def _seed_filestore(self):
        """zip_dir only writes files, never directory entries, so an empty
        filestore would leave no filestore/ member to assert on."""
        filestore = odoo.tools.config.filestore(self.env.cr.dbname)
        os.makedirs(os.path.join(filestore, 'aa'), exist_ok=True)
        path = os.path.join(filestore, 'aa', 'core_backup_test_blob')
        with open(path, 'w') as fh:
            fh.write('blob')
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))

    # ------------------------------------------------------------------

    def test_run_writes_a_readable_archive(self):
        self._seed_filestore()
        backup = self.Backup._run_backup()

        self.assertEqual(backup.state, 'done', backup.error)
        self.assertTrue(backup.size)
        self.assertTrue(os.path.isfile(backup.path))
        with zipfile.ZipFile(backup.path) as archive:
            names = archive.namelist()
            self.assertEqual(names[0], 'dump.sql')  # the fnct_sort key
            self.assertIn('manifest.json', names)
            self.assertTrue(any(n.startswith('filestore/') for n in names), names)
            manifest = json.loads(archive.read('manifest.json'))

        self.assertEqual(manifest['odoo_dump'], '1')
        self.assertEqual(manifest['db_name'], self.env.cr.dbname)
        self.assertTrue(manifest['version'])
        self.assertTrue(manifest['modules'])
        self.assertIn('core_backup', manifest['modules'])

    def test_master_switch_off_creates_nothing(self):
        self._set('core_backup.enabled', 'False')
        self.assertFalse(self.Backup._run_backup())
        self.assertFalse(self.Backup.search([]))
        self.assertFalse(os.listdir(self.dir))

    def test_bad_directory_ends_in_error_without_raising(self):
        # /dev/null is a file, so makedirs under it cannot succeed
        self._set('core_backup.dir', '/dev/null/nope')
        backup = self.Backup._run_backup()

        self.assertEqual(backup.state, 'error')
        self.assertTrue(backup.error)
        self.assertFalse(backup.size)

    def _aged_backup(self, days, name='old.zip'):
        """A done row with a real file in the backup dir, pushed out of the
        retention window. Cheaper and less flaky than a second real dump,
        whose filename is only second-resolution."""
        path = os.path.join(self.dir, name)
        with open(path, 'w') as fh:
            fh.write('archive')
        backup = self.Backup.create({'name': name, 'path': path, 'state': 'done'})
        self._age(backup, days)
        return backup

    def test_retention_drops_the_aged_row_and_its_file(self):
        self._set('core_backup.retention_days', '14')
        old = self._aged_backup(20)
        old_path = old.path
        recent = self.Backup._run_backup()  # a successful run sweeps

        self.assertFalse(old.exists())
        self.assertFalse(os.path.exists(old_path))
        self.assertEqual(recent.state, 'done', recent.error)
        self.assertTrue(os.path.exists(recent.path))

    def test_a_failed_run_does_not_sweep(self):
        self._set('core_backup.retention_days', '14')
        old = self._aged_backup(20)

        self._set('core_backup.dir', '/dev/null/nope')
        self.assertEqual(self.Backup._run_backup().state, 'error')

        self.assertTrue(old.exists())
        self.assertTrue(os.path.exists(old.path))

    def test_a_path_outside_the_backup_dir_is_refused(self):
        outside = tempfile.mkdtemp(prefix='core_backup_outside_')
        self.addCleanup(shutil.rmtree, outside, True)
        victim = os.path.join(outside, 'not_ours.zip')
        with open(victim, 'w') as fh:
            fh.write('keep me')

        self.Backup.create({'name': 'not_ours.zip', 'path': victim,
                            'state': 'done'}).unlink()

        self.assertTrue(os.path.exists(victim))

    def test_unlink_removes_the_file(self):
        backup = self.Backup._run_backup()
        path = backup.path
        backup.unlink()

        self.assertFalse(os.path.exists(path))

    def test_a_missing_file_is_not_an_error(self):
        backup = self.Backup._run_backup()
        os.remove(backup.path)
        backup.unlink()  # must not raise
