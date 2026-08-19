from odoo import fields, models


class IrModuleModule(models.Model):
    _inherit = 'ir.module.module'

    api_endpoint_ids = fields.One2many('core.api.endpoint', 'module_id')
    api_endpoint_count = fields.Integer(compute='_compute_api_endpoint_count')
    api_enabled = fields.Boolean(
        'API Enabled', default=True, readonly=True,
        help="Off: every @log_api route of this module answers 503, whatever "
             "the individual endpoint switches say.")

    def _compute_api_endpoint_count(self):
        for module in self:
            module.api_endpoint_count = len(module.api_endpoint_ids)

    def action_api_enable(self):
        self.write({'api_enabled': True})

    def action_api_disable(self):
        self.write({'api_enabled': False})

    def action_view_api_endpoints(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.name,
            'res_model': 'core.api.endpoint',
            'view_mode': 'list,form',
            'domain': [('module_id', '=', self.id)],
        }
