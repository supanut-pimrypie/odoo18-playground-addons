"""Aggregates behind the API Dashboard client action."""
import json

from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged

from ..models.api_log import DASHBOARD_BREAKDOWN_LIMIT, DASHBOARD_WINDOWS

WINDOW_KEYS = {'days', 'since', 'total', 'success', 'failed',
               'success_pct', 'failed_pct'}


@tagged('post_install', '-at_install')
class TestDashboardData(TransactionCase):

    def setUp(self):
        super().setUp()
        self.Log = self.env['core.api.log']
        # log_call(new_cursor=True) commits rows outside the test transaction,
        # so the table is not empty at the start. Wipe it: TransactionCase
        # rolls the delete back.
        self.Log.sudo().search([]).unlink()

    def _log(self, state, **vals):
        return self.Log.create(dict(
            {'direction': 'inbound', 'name': '/x', 'state': state}, **vals))

    def _age(self, log, days):
        """Push a row out of a window. create_date is readonly to the ORM."""
        self.env.cr.execute(
            "UPDATE core_api_log SET create_date = now() - interval %s WHERE id = %s",
            ('%s days' % days, log.id))
        log.invalidate_recordset()

    def _endpoint(self, module):
        return self.env['core.api.endpoint'].sudo().create({
            'route': '/e/%s' % module.name, 'name': module.name,
            'module_id': module.id})

    def _module(self, name='core_api'):
        return self.env['ir.module.module'].search([('name', '=', name)])

    def test_shape(self):
        data = self.Log._dashboard_data()
        self.assertEqual([w['days'] for w in data['windows']],
                         list(DASHBOARD_WINDOWS))
        for win in data['windows']:
            self.assertEqual(set(win), WINDOW_KEYS)
        self.assertIsInstance(data['since'], str)
        self.assertEqual(data['breakdown'], [])
        self.assertEqual(data['breakdown_by'], 'module')
        self.assertEqual(data['module_options'], [])
        self.assertIsNone(data['module_id'])
        json.dumps(data)  # the JS gets this over RPC: no lazy strings, no dates

    def test_percentages(self):
        for _ in range(5):
            self._log('success')
        self._log('error')
        day = self.Log._dashboard_data()['windows'][0]
        self.assertEqual(day['days'], 1)
        self.assertEqual((day['total'], day['success'], day['failed']), (6, 5, 1))
        self.assertEqual(day['success_pct'], 83.3)
        self.assertEqual(day['failed_pct'], 16.7)

    def test_empty_window_does_not_divide_by_zero(self):
        self._age(self._log('success'), 3)  # inside 7d, outside 1d
        windows = {w['days']: w for w in self.Log._dashboard_data()['windows']}
        self.assertEqual(windows[1]['total'], 0)
        self.assertEqual(windows[1]['success_pct'], 0.0)
        self.assertEqual(windows[1]['failed_pct'], 0.0)
        self.assertEqual(windows[7]['total'], 1)

    def test_rows_without_endpoint_land_in_no_module(self):
        self._log('success')
        self._log('error')
        self.assertEqual(self.Log._dashboard_data()['breakdown'], [
            {'id': False, 'label': 'No module', 'count': 2,
             'success': 1, 'failed': 1, 'pct': 100.0},
        ])

    def test_module_bucket_uses_the_module_name(self):
        module = self._module()
        self._log('success', endpoint_id=self._endpoint(module).id)
        self._log('error')
        data = self.Log._dashboard_data()
        self.assertEqual(data['breakdown_by'], 'module')
        # both buckets have one call, so the tie-break on label decides order
        self.assertEqual(sorted((r['id'], r['label']) for r in data['breakdown']),
                         sorted([(module.id, module.shortdesc), (False, 'No module')]))

    def test_rows_beyond_the_cap_fold_into_other(self):
        modules = self.env['ir.module.module'].search(
            [], limit=DASHBOARD_BREAKDOWN_LIMIT + 1)
        self.assertEqual(len(modules), DASHBOARD_BREAKDOWN_LIMIT + 1)
        for module in modules:
            self._log('success', endpoint_id=self._endpoint(module).id)
        rows = self.Log._dashboard_data()['breakdown']
        self.assertEqual(len(rows), DASHBOARD_BREAKDOWN_LIMIT + 1)
        self.assertEqual(rows[-1], {'id': False, 'label': 'Other', 'count': 1,
                                    'success': 1, 'failed': 0, 'pct': 11.1})

        # exactly at the cap: no Other row at all
        self.Log.search([('module_id', '=', rows[0]['id'])]).unlink()
        rows = self.Log._dashboard_data()['breakdown']
        self.assertEqual(len(rows), DASHBOARD_BREAKDOWN_LIMIT)
        self.assertNotIn('Other', [r['label'] for r in rows])

    def test_old_rows_are_out_of_every_window(self):
        self._age(self._log('success'), 40)
        data = self.Log._dashboard_data()
        self.assertEqual([w['total'] for w in data['windows']], [0, 0, 0, 0])
        self.assertEqual(data['breakdown'], [])

    def test_filtering_by_module_drills_into_endpoints(self):
        module = self._module()
        one = self._endpoint(module)
        two = self.env['core.api.endpoint'].sudo().create({
            'route': '/e/second', 'name': 'second', 'module_id': module.id})
        self._log('success', endpoint_id=one.id)
        self._log('error', endpoint_id=one.id)
        self._log('success', endpoint_id=two.id)
        self._log('success')  # no module: must be filtered out

        data = self.Log._dashboard_data(module_id=module.id)
        self.assertEqual(data['module_id'], module.id)
        self.assertEqual(data['breakdown_by'], 'endpoint')
        self.assertEqual([w['total'] for w in data['windows']], [3, 3, 3, 3])
        self.assertEqual(data['breakdown'], [
            {'id': one.id, 'label': one.display_name, 'count': 2,
             'success': 1, 'failed': 1, 'pct': 66.7},
            {'id': two.id, 'label': two.display_name, 'count': 1,
             'success': 1, 'failed': 0, 'pct': 33.3},
        ])

    def test_filtering_by_false_groups_the_no_module_rows_by_path(self):
        self._log('success', name='/a')
        self._log('error', name='/a')
        self._log('success', name='https://out.example/hook')
        self._log('success', endpoint_id=self._endpoint(self._module()).id)

        data = self.Log._dashboard_data(module_id=False)
        self.assertIs(data['module_id'], False)  # not 0, not ''
        self.assertEqual(data['breakdown_by'], 'path')
        self.assertEqual([w['total'] for w in data['windows']], [3, 3, 3, 3])
        self.assertEqual(data['breakdown'], [
            {'id': '/a', 'label': '/a', 'count': 2,
             'success': 1, 'failed': 1, 'pct': 66.7},
            {'id': 'https://out.example/hook', 'label': 'https://out.example/hook',
             'count': 1, 'success': 1, 'failed': 0, 'pct': 33.3},
        ])

    def test_module_options_do_not_shrink_when_filtered(self):
        module = self._module()
        self._log('success', endpoint_id=self._endpoint(module).id)
        self._log('success')
        expected = [{'id': False, 'label': 'No module'},
                    {'id': module.id, 'label': module.shortdesc}]
        expected.sort(key=lambda o: o['label'])
        for module_id in (None, module.id, False):
            self.assertEqual(
                self.Log._dashboard_data(module_id=module_id)['module_options'],
                expected, 'module_id=%r shrank the dropdown' % (module_id,))

    def test_rpc_entry_point_checks_read_access(self):
        """dashboard_data aggregates with sudo, so it guards the read right
        itself: any internal user can reach it over call_kw."""
        plain = self.env['res.users'].create({
            'name': 'No API rights', 'login': 'core_api_no_rights',
            'groups_id': [(6, 0, [self.env.ref('base.group_user').id])],
        })
        with self.assertRaises(AccessError):
            self.Log.with_user(plain).dashboard_data()
        self.assertTrue(self.Log.dashboard_data())
