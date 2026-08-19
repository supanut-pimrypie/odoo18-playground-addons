import inspect
import json
import logging

from odoo import SUPERUSER_ID, _, api, fields, models
from odoo.exceptions import UserError
from odoo.http import Controller

_logger = logging.getLogger(__name__)

# bootstrap classes for the kanban badge, kept here rather than as a ternary
# chain in the template: qweb's #{...} interpolation stops at the first "}",
# so an inline object literal silently produces broken markup
METHOD_COLORS = {
    'GET': 'text-bg-success',
    'POST': 'text-bg-primary',
    'PUT': 'text-bg-warning',
    'PATCH': 'text-bg-warning',
    'DELETE': 'text-bg-danger',
}
DEFAULT_METHOD_COLOR = 'text-bg-secondary'
# left colour bar, same idea as odoo's own notification toast
ENABLED_BAR = 'bg-success'
DISABLED_BAR = 'bg-danger'


def _logged_wrapper(method):
    """The @log_api wrapper in this route's decorator stack, or None.

    http.route wraps our wrapper, so the attributes are one or more layers
    down: walk __wrapped__ rather than reading the outermost function.
    """
    while method is not None:
        if getattr(method, '_core_api_logged', False):
            return method
        method = getattr(method, '__wrapped__', None)
    return None


def _schema_text(schema):
    return json.dumps(schema, indent=2, sort_keys=True, default=str) if schema else False


class BaseApiEndpoint(models.Model):
    _name = 'core.api.endpoint'
    _description = 'API Endpoint'
    _order = 'route'
    # the route is what identifies an endpoint to a reader; `name` is the
    # python function it was swept from
    _rec_name = 'route'

    route = fields.Char(
        required=True, index=True, readonly=True,
        help="Route pattern as declared, e.g. /api/thing/<int:id>.")
    name = fields.Char(required=True, readonly=True,
                       help="Controller method that serves the route.")
    method = fields.Char('HTTP Method', readonly=True, index=True,
                         help="Methods the route declares. ANY when it "
                              "declares none, which lets every verb through.")
    module_id = fields.Many2one('ir.module.module', 'Module', readonly=True,
                                ondelete='set null', index=True,
                                help="Addon that declares this route.")
    enabled = fields.Boolean(default=True, readonly=True,
                             help="Off: the route answers 503 without running.")
    module_api_enabled = fields.Boolean(related='module_id.api_enabled',
                                        string="Module API Enabled")
    request_schema = fields.Text(readonly=True,
                                 help="JSON Schema the request body must match. "
                                      "Declared in code on @log_api.")
    response_schema = fields.Text(readonly=True,
                                  help="JSON Schema the response is checked "
                                       "against. A mismatch is logged, not raised.")
    method_color = fields.Char(compute='_compute_method_color')
    state_bar = fields.Char(compute='_compute_state_bar')
    stale = fields.Boolean(readonly=True,
                           help="The code no longer declares this route. Kept "
                                "so its logs stay readable.")

    _sql_constraints = [
        ('route_uniq', 'unique(route)', 'This route is already registered.'),
    ]

    @api.depends('method')
    def _compute_method_color(self):
        for rec in self:
            rec.method_color = METHOD_COLORS.get(rec.method, DEFAULT_METHOD_COLOR)

    @api.depends('enabled')
    def _compute_state_bar(self):
        for rec in self:
            rec.state_bar = ENABLED_BAR if rec.enabled else DISABLED_BAR

    def _register_hook(self):
        """Sweep every @log_api route of the installed modules.

        Runs on each registry load, so installing or upgrading a module fills
        the list right away instead of waiting for the first request.
        """
        super()._register_hook()
        try:
            self._sync_endpoints()
        except Exception:
            _logger.exception("core.api.endpoint: route sweep failed")

    @api.model
    def _sync_endpoints(self):
        found = {}  # route -> (addon, controller method name, http verbs)
        for addon, classes in Controller.children_classes.items():
            for cls in classes:
                for fname, func in inspect.getmembers(cls, inspect.isfunction):
                    logged = _logged_wrapper(func)
                    if not logged:
                        continue
                    routing = getattr(func, 'original_routing', None) or {}
                    verbs = ','.join(routing.get('methods') or []) or 'ANY'
                    schemas = getattr(logged, '_core_api_schemas', None) or {}
                    for route in routing.get('routes') or []:
                        found.setdefault(route, (addon, fname, verbs, schemas))
        if not found:
            return

        modules = self.env['ir.module.module'].sudo().search(
            [('name', 'in', [a for a, _f, _v, _s in found.values()]),
             ('state', '=', 'installed')])
        module_by_name = {m.name: m.id for m in modules}
        existing = self.sudo().search([])
        known = set(existing.mapped('route'))
        vals = [
            {'route': route, 'name': fname, 'method': verbs,
             'module_id': module_by_name[addon],
             'request_schema': _schema_text(schemas.get('request')),
             'response_schema': _schema_text(schemas.get('response'))}
            for route, (addon, fname, verbs, schemas) in found.items()
            if route not in known and addon in module_by_name
        ]
        if vals:
            self.sudo().create(vals)
            _logger.info("core.api.endpoint: registered %s new route(s)", len(vals))

        # a route dropped from the code keeps its row, so its logs stay
        # readable, but stops pretending it is still served
        for row in existing:  # keep rows in step with what the code declares
            declared = found.get(row.route)
            if not declared:
                continue
            if row.method != declared[2]:
                row.method = declared[2]
            for key, field in (('request', 'request_schema'),
                               ('response', 'response_schema')):
                text = _schema_text(declared[3].get(key))
                if row[field] != text:
                    row[field] = text

        gone = existing.filtered(lambda e: e.route not in found and not e.stale)
        back = existing.filtered(lambda e: e.route in found and e.stale)
        if gone:
            gone.stale = True
        if back:
            back.stale = False

    def unlink(self):
        """Endpoints are owned by the route sweep, not by hand.

        Deleting one loses the switch state and orphans its logs, and the next
        sweep recreates it enabled anyway. A route dropped from the code is
        flagged stale instead. sudo() bypasses the ACL, so the block lives here
        rather than only in ir.model.access.csv.
        """
        raise UserError(_(
            "API endpoints cannot be deleted. Use Disable to stop a route; "
            "one whose code is gone is flagged as stale."))

    def action_enable(self):
        """Button target. The field is readonly, so this is the only way in."""
        self.write({'enabled': True})

    def action_disable(self):
        self.write({'enabled': False})

    def action_view_logs(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.route,
            'res_model': 'core.api.log',
            'view_mode': 'list,form',
            'domain': [('endpoint_id', '=', self.id)],
        }

    @api.model
    def _get_or_register(self, route, name=None, addon=None, method=None):
        """Return (endpoint_id, enabled). Auto-registers unknown routes.

        On the call that creates the row, endpoint_id comes back False: the
        row was committed on another connection and is not in the request
        transaction's snapshot yet, so linking it would break the log insert
        on a foreign key. The next call to the route links normally.

        Registration goes on a fresh cursor: a route that raises on its very
        first call must still show up in the UI so it can be switched off.

        "enabled" is the effective answer: a module switched off in
        API > API Modules overrides its endpoints.

        Fail-open by design: if registration blows up the route keeps serving.
        This toggle is an operational switch, not a security control -- put
        auth on the route itself.
        """
        module = self.env['ir.module.module'].sudo().search(
            [('name', '=', addon)], limit=1) if addon else False
        # ponytail: one query per request, ormcache if traffic warrants
        found = self.sudo().search([('route', '=', route)], limit=1)
        if found:
            if module and not found.module_id:
                found.sudo().module_id = module  # backfill rows from older runs
            if method and not found.method:
                found.sudo().method = method
            owner = found.module_id
            return found.id, found.enabled and (not owner or owner.api_enabled)
        try:
            with self.env.registry.cursor() as cr:
                env = api.Environment(cr, SUPERUSER_ID, {})
                rec = env['core.api.endpoint'].search([('route', '=', route)], limit=1)
                if not rec:
                    rec = env['core.api.endpoint'].create({
                        'route': route, 'name': name or route,
                        'method': method,
                        'module_id': module.id if module else False})
                cr.commit()
                return False, rec.enabled and (not module or module.api_enabled)
        except Exception:
            _logger.exception("core.api.endpoint: cannot register %s", route)
            return False, True  # unknown state must not block traffic
