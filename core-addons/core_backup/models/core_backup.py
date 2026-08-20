import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
from datetime import timedelta

import odoo
from odoo import api, fields, models
from odoo.tools.misc import exec_pg_environ, find_pg_tool, human_size, str2bool
from odoo.tools.osutil import zip_dir

_logger = logging.getLogger(__name__)

DEFAULT_DIR = '/var/lib/odoo/backups'
DEFAULT_RETENTION_DAYS = 14
FILENAME_FORMAT = '%Y%m%d_%H%M%S'


class CoreBackup(models.Model):
    _name = 'core.backup'
    _description = 'Database Backup'
    _order = 'id desc'

    name = fields.Char('File', required=True, index=True)
    path = fields.Char('Full Path', required=True)
    db_name = fields.Char('Database', index=True)
    state = fields.Selection(
        [('running', 'Running'), ('done', 'Done'), ('error', 'Error')],
        default='running', required=True, index=True)
    size = fields.Integer('Size (bytes)')
    size_display = fields.Char('Size', compute='_compute_size_display')
    duration_ms = fields.Integer('Duration (ms)')
    with_filestore = fields.Boolean('With Filestore')
    error = fields.Text()

    @api.depends('size')
    def _compute_size_display(self):
        for backup in self:
            backup.size_display = human_size(backup.size) or '0 bytes'

    # ------------------------------------------------------------------
    # dumping
    # ------------------------------------------------------------------

    @api.model
    def _dump_manifest(self):
        """The dict odoo.service.db.dump_db_manifest() builds, on our cursor.

        Duplicated rather than imported on purpose: dump_db_manifest is
        decorated with @check_db_management_enabled, and this deployment runs
        list_db = False, so importing it would only buy an AccessDenied.
        Keep it byte-identical to odoo/service/db.py so a native restore
        recognises the archive.
        """
        cr = self.env.cr
        pg_version = "%d.%d" % divmod(cr._obj.connection.server_version / 100, 100)
        cr.execute("SELECT name, latest_version FROM ir_module_module "
                   "WHERE state = 'installed'")
        return {
            'odoo_dump': '1',
            'db_name': cr.dbname,
            'version': odoo.release.version,
            'version_info': odoo.release.version_info,
            'major_version': odoo.release.major_version,
            'pg_version': pg_version,
            'modules': dict(cr.fetchall()),
        }

    @api.model
    def _dump_to(self, target, with_filestore=True):
        """Write a zip at `target`, laid out like a native odoo backup:
        dump.sql first, then filestore/ and manifest.json.

        Same steps as dump_db(..., backup_format='zip'), minus the
        @check_db_management_enabled gate that makes the original unusable
        here. The fnct_sort key is what puts dump.sql first in the archive.
        """
        db_name = self.env.cr.dbname
        # TemporaryDirectory is the only thing in this module that may be
        # rmtree'd: it is ours, private, and holds no user data.
        with tempfile.TemporaryDirectory() as dump_dir:
            if with_filestore:
                filestore = odoo.tools.config.filestore(db_name)
                if os.path.exists(filestore):
                    shutil.copytree(filestore, os.path.join(dump_dir, 'filestore'))
            with open(os.path.join(dump_dir, 'manifest.json'), 'w') as fh:
                json.dump(self._dump_manifest(), fh, indent=4)
            cmd = [find_pg_tool('pg_dump'), '--no-owner',
                   '--file=' + os.path.join(dump_dir, 'dump.sql'), db_name]
            # exec_pg_environ() puts PGPASSWORD in here. Never log env or cmd.
            subprocess.run(cmd, env=exec_pg_environ(), stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                           check=True)
            with open(target, 'wb') as stream:
                zip_dir(dump_dir, stream, include_dir=False,
                        fnct_sort=lambda file_name: file_name != 'dump.sql')

    @api.model
    def _backup_dir(self):
        return self.env['ir.config_parameter'].sudo().get_param(
            'core_backup.dir', DEFAULT_DIR)

    @api.model
    def _run_backup(self):
        """Cron target: dump the database into the configured directory.

        Never raises: a cron that raises is retried and mails nobody, so a
        failure is recorded on the row instead.
        """
        params = self.env['ir.config_parameter'].sudo()
        if not str2bool(params.get_param('core_backup.enabled', 'True')):
            _logger.info("core.backup: disabled, skipping run")
            return self.browse()

        directory = self._backup_dir()
        with_filestore = str2bool(
            params.get_param('core_backup.with_filestore', 'True'))
        db_name = self.env.cr.dbname
        name = '%s_%s.zip' % (db_name, fields.Datetime.now().strftime(FILENAME_FORMAT))
        backup = self.sudo().create({
            'name': name,
            'path': os.path.join(directory, name),
            'db_name': db_name,
            'with_filestore': with_filestore,
        })
        started = time.monotonic()
        try:
            os.makedirs(directory, exist_ok=True)
            self._dump_to(backup.path, with_filestore=with_filestore)
            backup.write({
                'state': 'done',
                # ponytail: size is int4, so an archive over 2 GiB overflows
                # and lands the row in 'error'. Widen to Float if that day comes.
                'size': os.path.getsize(backup.path),
                'duration_ms': int((time.monotonic() - started) * 1000),
            })
        except Exception as exc:
            stderr = getattr(exc, 'stderr', None)
            if isinstance(stderr, bytes):
                stderr = stderr.decode('utf-8', 'replace')
            backup.write({
                'state': 'error',
                'error': stderr or str(exc) or exc.__class__.__name__,
                'duration_ms': int((time.monotonic() - started) * 1000),
            })
            _logger.exception("core.backup: %s failed", name)
            return backup

        _logger.info("core.backup: wrote %s (%s)", backup.path, backup.size_display)
        try:
            self._gc_backups()
        except Exception:  # a broken sweep must not mark a good backup failed
            _logger.exception("core.backup: retention sweep failed")
        return backup

    def action_backup_now(self):
        """Header button: same run as the cron."""
        self._run_backup()
        return True

    # ------------------------------------------------------------------
    # retention
    # ------------------------------------------------------------------

    @api.model
    def _gc_backups(self):
        """Drop backups older than core_backup.retention_days, row and file.

        Deliberately called only at the end of a *successful* run, never
        after a failure: otherwise a stretch of failing backups would keep
        ageing out the last archives that still work, and the retention
        window would quietly empty itself.
        """
        days = int(self.env['ir.config_parameter'].sudo().get_param(
            'core_backup.retention_days', DEFAULT_RETENTION_DAYS))
        old = self.sudo().search([
            ('create_date', '<', fields.Datetime.now() - timedelta(days=days))])
        count = len(old)
        old.unlink()  # unlink() is what removes the files
        _logger.info("core.backup: removed %s backups older than %s days",
                     count, days)
        return count

    def _remove_file(self, path):
        """Delete one recorded archive, and only if it really sits directly
        inside the configured backup directory.

        `path` is an ordinary writable column, so a tampered value must not
        be able to steer a cleanup at something else on disk. Anything that
        does not resolve into the backup directory is refused and logged.
        """
        if not path:
            return
        root = os.path.realpath(self._backup_dir())
        real = os.path.realpath(path)
        if os.path.dirname(real) != root:
            _logger.warning("core.backup: refusing to delete %s, outside %s",
                            real, root)
            return
        try:
            os.remove(real)
        except FileNotFoundError:
            pass  # already gone: nothing to clean up, not an error
        except OSError:
            _logger.exception("core.backup: could not delete %s", real)

    def unlink(self):
        """The row is the only handle on the file, so drop both.

        Row first, file second, for the reason ir_attachment.unlink gives:
        let the database refuse the delete before touching the disk, so two
        transactions racing on the same row cannot both remove the archive.

        The window it leaves is the reverse one -- a rollback after this
        point restores a row whose file is already gone. That is visible
        rather than silent (the archive simply is not there), and
        _remove_file treats an absent file as nothing to do.
        """
        paths = [backup.path for backup in self]
        result = super().unlink()
        for path in paths:
            self._remove_file(path)
        return result
