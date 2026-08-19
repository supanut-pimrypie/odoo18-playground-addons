"""Sample endpoints. Copy the shape, delete the module.

Every route below is decorated with @log_api, so it shows up under
API > API Endpoints after its first call and can be switched off there.
"""
from odoo import http
from odoo.addons.core_api.controllers.api import json_body, json_response, log_api
from odoo.http import request


class ApiDemo(http.Controller):

    @http.route('/api/demo/ping', type='http', auth='public',
                methods=['GET'], csrf=False)
    @log_api
    def ping(self):
        """Simplest possible route. Public: no session needed."""
        return json_response({'pong': True})

    @http.route('/api/demo/echo', type='http', auth='public',
                methods=['POST'], csrf=False)
    @log_api
    def echo(self, **kw):
        """JSON in, JSON out. Post a "password" key and check the log:
        the stored payload has it redacted.
        """
        return json_response({'received': json_body() or kw})

    @http.route('/api/demo/partner/<int:partner_id>', type='http', auth='user',
                methods=['GET'], csrf=False)
    @log_api
    def partner(self, partner_id):
        """Authenticated read. All ids share one endpoint row, because the
        toggle keys on the declared route, not the concrete path.
        """
        partner = request.env['res.partner'].browse(partner_id).exists()
        if not partner:
            return json_response({'error': 'not found'}, status=404)
        return json_response({'id': partner.id, 'name': partner.name})

    @http.route('/api/demo/order', type='http', auth='public',
                methods=['POST'], csrf=False)
    @log_api(
        request_schema={
            'type': 'object',
            'required': ['name', 'qty'],
            'properties': {
                'name': {'type': 'string', 'minLength': 1},
                'qty': {'type': 'integer', 'minimum': 1},
                'tags': {'type': 'array', 'items': {'type': 'string'}},
            },
        },
        response_schema={
            'type': 'object',
            'required': ['accepted'],
            'properties': {'accepted': {'type': 'boolean'}},
        },
    )
    def order(self, **kw):
        """Payload validation. A body missing "name" or with a non-integer
        "qty" is answered 400 with the offending fields listed, and the
        endpoint below never runs. The schemas show up on the endpoint form.
        """
        return json_response({'accepted': True, 'echo': json_body()})

    @http.route('/api/demo/whoami', type='http', auth='bearer',
                methods=['GET'], csrf=False)
    @log_api
    def whoami(self):
        """API key authentication, the way a machine client should do it:

            curl -H "Authorization: Bearer <key>" .../api/demo/whoami

        Make the key under My Profile > Account Security > New API Key. A
        missing or wrong key gets 401, not the login redirect auth='user'
        would send. The log row then carries the key owner as the caller.
        """
        return json_response({'uid': request.env.uid,
                              'login': request.env.user.login})

    @http.route('/api/demo/boom', type='http', auth='public',
                methods=['GET'], csrf=False)
    @log_api
    def boom(self):
        """Raises on purpose: the log row survives the rollback with the
        traceback and status 500.
        """
        raise ValueError('boom, as ordered')

    @http.route('/api/demo/outbound', type='http', auth='user',
                methods=['GET'], csrf=False)
    @log_api
    def outbound(self, url=None):
        """Outbound call through core.api.client, logged as direction=outbound.

        Defaults to calling our own /api/demo/ping so the demo needs no
        internet. Turn "Allow outgoing API calls" off in Settings > API and
        this answers 500 with a UserError instead.
        """
        base = request.env['ir.config_parameter'].sudo().get_param('web.base.url')
        response = request.env['core.api.client']._call(
            'get', url or (base + '/api/demo/ping'), timeout=10)
        return json_response({'status': response.status_code,
                              'body': response.text[:500]})
