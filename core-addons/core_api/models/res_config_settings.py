from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    core_api_inbound_enabled = fields.Boolean(
        'Accept incoming API calls', default=True,
        config_parameter='core_api.inbound_enabled',
        help="Master switch. Off: every logged route answers 503.")
    core_api_outbound_enabled = fields.Boolean(
        'Allow outgoing API calls', default=True,
        config_parameter='core_api.outbound_enabled',
        help="Master switch. Off: core.api.client._call() refuses to fire.")
    core_api_log_retention_days = fields.Integer(
        'Keep logs for (days)', default=30,
        config_parameter='core_api.log_retention_days')
