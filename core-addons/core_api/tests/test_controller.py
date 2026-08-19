"""End-to-end check of @log_api: registration, logging, toggle, form bodies.

The controller below only exists while the test suite is loaded.
"""
import json

from odoo import http
from odoo.exceptions import UserError
from odoo.tests import HttpCase, tagged

from ..controllers.api import json_body, json_response, log_api


class _TestApi(http.Controller):

    @http.route('/core_api_test/echo', type='http', auth='public',
                methods=['POST'], csrf=False)
    @log_api
    def echo(self, **kw):
        return json_response({'got': json_body() or kw})

    @http.route('/core_api_test/boom', type='http', auth='public',
                methods=['GET'], csrf=False)
    @log_api
    def boom(self):
        raise ValueError('kaboom')

    @http.route('/core_api_test/order', type='http', auth='public',
                methods=['POST'], csrf=False)
    @log_api(
        request_schema={
            'type': 'object',
            'required': ['name', 'qty'],
            'properties': {
                'name': {'type': 'string', 'minLength': 1},
                'qty': {'type': 'integer', 'minimum': 1},
            },
        },
        response_schema={
            'type': 'object',
            'required': ['ok'],
            'properties': {'ok': {'type': 'boolean'}},
        },
    )
    def order(self, **kw):
        return json_response({'ok': True})

    @http.route('/core_api_test/liar', type='http', auth='public',
                methods=['GET'], csrf=False)
    @log_api(response_schema={'type': 'object', 'required': ['expected']})
    def liar(self):
        return json_response({'nothing': 'like the schema'})


@tagged('post_install', '-at_install')
class TestLogApi(HttpCase):

    def _logs(self, path):
        return self.env['core.api.log'].search([('name', '=', path)], order='id desc')

    def test_registers_and_logs(self):
        res = self.url_open('/core_api_test/echo', data=json.dumps({'a': 1}),
                            headers={'Content-Type': 'application/json'})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['got'], {'a': 1})

        endpoint = self.env['core.api.endpoint'].search(
            [('route', '=', '/core_api_test/echo')])
        self.assertTrue(endpoint, "route must auto-register on first call")
        self.assertTrue(endpoint.enabled)
        self.assertEqual(endpoint.module_id.name, 'core_api')

        log = self._logs('/core_api_test/echo')[:1]
        self.assertTrue(log)
        self.assertEqual(log.direction, 'inbound')
        self.assertEqual(log.state, 'success')
        self.assertEqual(log.status_code, 200)

        # the first call registers but cannot link (row not in this
        # transaction's snapshot yet); the second one links
        self.url_open('/core_api_test/echo', data='{}',
                      headers={'Content-Type': 'application/json'})
        self.assertEqual(self._logs('/core_api_test/echo')[:1].endpoint_id, endpoint)

    def test_form_body_is_captured_and_redacted(self):
        self.url_open('/core_api_test/echo', data={'user': 'bob', 'password': 'hunter2'})
        log = self._logs('/core_api_test/echo')[:1]
        self.assertIn('bob', log.request_body)
        self.assertNotIn('hunter2', log.request_body)

    def test_disabled_endpoint_answers_503(self):
        self.url_open('/core_api_test/echo', data={'x': 1})  # register it
        endpoint = self.env['core.api.endpoint'].search(
            [('route', '=', '/core_api_test/echo')])
        endpoint.action_disable()  # the only writable path: fields are readonly
        self.assertFalse(endpoint.enabled)
        self.env.flush_all()

        res = self.url_open('/core_api_test/echo', data={'x': 1})
        self.assertEqual(res.status_code, 503)
        self.assertIn('disabled', res.json()['error'])
        self.assertEqual(self._logs('/core_api_test/echo')[:1].status_code, 503)

        endpoint.action_enable()
        self.assertTrue(endpoint.enabled)

    def test_error_still_returns_500(self):
        """The error log itself is written on a separate cursor, so it lands
        outside this test transaction and cannot be asserted here. Verified
        against a live server instead: the row shows state=error, 500.
        """
        self.assertEqual(self.url_open('/core_api_test/boom').status_code, 500)

    def test_module_switch_overrides_endpoint(self):
        module = self.env['ir.module.module'].search([('name', '=', 'core_api')])
        self.addCleanup(module.action_api_enable)
        module.action_api_disable()
        self.env.flush_all()

        res = self.url_open('/core_api_test/echo', data={'x': 1})
        self.assertEqual(res.status_code, 503)

        endpoint = self.env['core.api.endpoint'].search(
            [('route', '=', '/core_api_test/echo')])
        self.assertTrue(endpoint.enabled, "the route's own switch stays on")

    def test_method_badge_colour(self):
        endpoints = self.env['core.api.endpoint'].search(
            [('route', 'like', '/core_api_test/%')])
        by_route = {e.route: e for e in endpoints}
        self.assertEqual(by_route['/core_api_test/echo'].method, 'POST')
        self.assertEqual(by_route['/core_api_test/echo'].method_color,
                         'text-bg-primary')
        self.assertEqual(by_route['/core_api_test/boom'].method_color,
                         'text-bg-success')  # GET
        unknown = self.env['core.api.endpoint'].new({'route': '/x', 'name': 'x'})
        self.assertEqual(unknown.method_color, 'text-bg-secondary')

    def test_state_bar(self):
        endpoint = self.env['core.api.endpoint'].search(
            [('route', '=', '/core_api_test/echo')])
        self.assertEqual(endpoint.state_bar, 'bg-success')
        endpoint.action_disable()
        self.addCleanup(endpoint.action_enable)
        self.assertEqual(endpoint.state_bar, 'bg-danger')

    def test_endpoint_cannot_be_deleted(self):
        endpoint = self.env['core.api.endpoint'].search(
            [('route', '=', '/core_api_test/echo')])
        with self.assertRaises(UserError):
            endpoint.unlink()
        with self.assertRaises(UserError):
            endpoint.sudo().unlink()
        self.assertTrue(endpoint.exists())

    def test_request_schema_rejects_bad_payload(self):
        res = self.url_open(
            '/core_api_test/order', data=json.dumps({'qty': 0}),
            headers={'Content-Type': 'application/json'})
        self.assertEqual(res.status_code, 400)
        body = res.json()
        self.assertEqual(body['error'], 'Invalid request payload')
        self.assertTrue(any('name' in d for d in body['details']), body)
        self.assertTrue(any(d.startswith('qty:') for d in body['details']), body)

        log = self._logs('/core_api_test/order')[:1]
        self.assertEqual(log.status_code, 400)
        self.assertEqual(log.state, 'error')

    def test_request_schema_accepts_good_payload(self):
        res = self.url_open(
            '/core_api_test/order', data=json.dumps({'name': 'a', 'qty': 2}),
            headers={'Content-Type': 'application/json'})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self._logs('/core_api_test/order')[:1].state, 'success')

    def test_response_schema_flags_the_log_but_still_answers(self):
        res = self.url_open('/core_api_test/liar')
        self.assertEqual(res.status_code, 200, "a bad response is still sent")
        log = self._logs('/core_api_test/liar')[:1]
        self.assertEqual(log.state, 'error')
        self.assertIn('expected', log.error)

    def test_schema_is_stored_on_the_endpoint(self):
        endpoint = self.env['core.api.endpoint'].search(
            [('route', '=', '/core_api_test/order')])
        self.assertTrue(endpoint.request_schema)
        self.assertIn('minLength', endpoint.request_schema)
        self.assertTrue(endpoint.response_schema)

    def test_sweep_registers_routes_without_traffic(self):
        """The install-time sweep must have picked up this module's routes."""
        swept = self.env['core.api.endpoint'].search(
            [('route', '=', '/core_api_test/boom')])
        self.assertTrue(swept, "route sweep must run on registry load")
        self.assertEqual(swept.module_id.name, 'core_api')
        self.assertFalse(swept.stale)

    def test_gc_removes_old_logs(self):
        old = self.env['core.api.log'].create(
            {'direction': 'inbound', 'name': '/old', 'state': 'success'})
        fresh = self.env['core.api.log'].create(
            {'direction': 'inbound', 'name': '/fresh', 'state': 'success'})
        self.env.cr.execute(
            "UPDATE core_api_log SET create_date = now() - interval '400 days' WHERE id = %s",
            (old.id,))
        old.invalidate_recordset()
        self.env['core.api.log']._gc_logs()
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())


@tagged('post_install', '-at_install')
class TestOutbound(HttpCase):

    def _last(self):
        return self.env['core.api.log'].search(
            [('direction', '=', 'outbound')], order='id desc', limit=1)

    def test_outbound_logs(self):
        res = self.env['core.api.client']._call('get', self.base_url() + '/web/login')
        self.assertEqual(res.status_code, 200)
        log = self._last()
        self.assertEqual(log.state, 'success')
        self.assertEqual(log.status_code, 200)
        self.assertEqual(log.method, 'GET')

    def test_outbound_request_schema_raises_before_sending(self):
        """The refusal log goes on a separate cursor so it survives the caller's
        rollback, which also puts it outside this test transaction. Only the
        raise is assertable here; the log row is checked against a live server.
        """
        with self.assertRaises(UserError):
            self.env['core.api.client']._call(
                'post', self.base_url() + '/web/login', json={'qty': 'nope'},
                request_schema={'type': 'object',
                                'properties': {'qty': {'type': 'integer'}}})

    def test_outbound_response_schema_only_flags_the_log(self):
        res = self.env['core.api.client']._call(
            'get', self.base_url() + '/web/login',
            response_schema={'type': 'object', 'required': ['nope']})
        self.assertEqual(res.status_code, 200, "the answer is still returned")
        self.assertEqual(self._last().state, 'error')

    def test_outbound_kill_switch(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'core_api.outbound_enabled', 'False')
        with self.assertRaises(UserError):
            self.env['core.api.client']._call('get', self.base_url() + '/web/login')
