import logging
from datetime import timedelta

from odoo import SUPERUSER_ID, api, fields, models

from .sanitize import as_bool, sanitize

_logger = logging.getLogger(__name__)

GC_BATCH = 1000
DEFAULT_RETENTION_DAYS = 30
DASHBOARD_WINDOWS = (1, 7, 14, 30)
DASHBOARD_BREAKDOWN_LIMIT = 8


class BaseApiLog(models.Model):
    _name = 'core.api.log'
    _description = 'API Call Log'
    _order = 'id desc'

    direction = fields.Selection(
        [('inbound', 'Inbound'), ('outbound', 'Outbound')],
        required=True, index=True,
        help="Inbound: someone called us. Outbound: we called someone.")
    name = fields.Char('Path', required=True, index=True)
    endpoint_id = fields.Many2one('core.api.endpoint', 'Endpoint',
                                  index=True, ondelete='set null')
    module_id = fields.Many2one(related='endpoint_id.module_id', store=True,
                                index=True)
    method = fields.Char()
    state = fields.Selection(
        [('success', 'Success'), ('error', 'Error')], index=True)
    status_code = fields.Integer()
    duration_ms = fields.Integer('Duration (ms)')
    user_id = fields.Many2one('res.users', 'Caller', index=True, ondelete='set null')
    remote_addr = fields.Char('Remote IP')
    forwarded_for = fields.Char('X-Forwarded-For')
    request_headers = fields.Text()
    request_body = fields.Text()
    response_body = fields.Text()
    error = fields.Text()

    @api.model
    def log_call(self, vals, new_cursor=False):
        """Store one call. Always sudo: public endpoints have no write rights.

        new_cursor=True writes on a fresh cursor so the row survives the
        rollback of a failed request.
        """
        for key in ('request_headers', 'request_body', 'response_body'):
            if key in vals:
                vals[key] = sanitize(vals[key])
        if not new_cursor:
            try:
                with self.env.cr.savepoint():
                    return self.sudo().create(vals)
            except Exception:  # a broken log write must not 500 a working endpoint
                _logger.exception("core.api.log: failed to store %s", vals.get('name'))
                return self.browse()
        try:
            with self.env.registry.cursor() as cr:
                api.Environment(cr, SUPERUSER_ID, {})['core.api.log'].create(vals)
                cr.commit()
        except Exception:  # never let logging break the caller's error path
            _logger.exception("core.api.log: failed to store %s", vals.get('name'))
        return self.browse()

    @api.model
    def _switch_on(self, key):
        """Master switch from Settings. Unset means on."""
        return as_bool(self.env['ir.config_parameter'].sudo().get_param(key))

    @api.model
    def dashboard_data(self, module_id=None):
        """RPC entry point: call_kw refuses underscore-prefixed methods.

        The aggregate below runs sudo, so the read right has to be checked
        here: this method is reachable over RPC by any internal user.
        """
        self.check_access('read')
        return self._dashboard_data(module_id=module_id)

    @api.model
    def _dashboard_data(self, module_id=None):
        """KPI counts per window plus a 30-day breakdown, both scoped to
        module_id: None = every module, an int = that one, False = the rows
        that belong to no module.

        Always sudo, like the rest of this model: the dashboard is a
        read-only aggregate and must not depend on record rules.
        """
        log = self.sudo()
        now = fields.Datetime.now()
        scope = [] if module_id is None else [('module_id', '=', module_id)]

        windows = []
        for days in DASHBOARD_WINDOWS:
            win_since = now - timedelta(days=days)
            counts = dict(log._read_group(
                scope + [('create_date', '>=', win_since)],
                groupby=['state'], aggregates=['__count']))
            success = counts.get('success', 0)
            failed = counts.get('error', 0)
            total = success + failed
            windows.append({
                'days': days,
                'since': fields.Datetime.to_string(win_since),
                'total': total,
                'success': success,
                'failed': failed,
                'success_pct': round(success * 100.0 / total, 1) if total else 0.0,
                'failed_pct': round(failed * 100.0 / total, 1) if total else 0.0,
            })

        since = now - timedelta(days=max(DASHBOARD_WINDOWS))
        if module_id is None:
            key, breakdown_by = 'module_id', 'module'
        elif module_id is False:
            # No-module rows have no endpoint_id either, so an endpoint
            # breakdown would collapse to one useless row. Group by name:
            # that gives the real paths and outbound URLs.
            key, breakdown_by = 'name', 'path'
        else:
            key, breakdown_by = 'endpoint_id', 'endpoint'

        # one grouped query, pivoted in python: no per-row count
        pivot = {}
        for value, state, count in log._read_group(
                scope + [('create_date', '>=', since)],
                groupby=[key, 'state'], aggregates=['__count']):
            if state not in ('success', 'error'):
                continue  # the window totals ignore stateless rows, so does this
            row = pivot.setdefault(value, {'count': 0, 'success': 0, 'failed': 0})
            row['count'] += count
            row['success' if state == 'success' else 'failed'] += count

        breakdown = sorted(
            ({'id': value.id if key != 'name' else value,
              'label': (value if key == 'name' else
                        value.display_name if value else 'No module'),
              **row}
             for value, row in pivot.items()),
            key=lambda r: (-r['count'], r['label']))
        if len(breakdown) > DASHBOARD_BREAKDOWN_LIMIT:
            rest = breakdown[DASHBOARD_BREAKDOWN_LIMIT:]
            breakdown = breakdown[:DASHBOARD_BREAKDOWN_LIMIT]
            breakdown.append({
                'id': False, 'label': 'Other',
                'count': sum(r['count'] for r in rest),
                'success': sum(r['success'] for r in rest),
                'failed': sum(r['failed'] for r in rest),
            })
        grand = sum(r['count'] for r in breakdown)
        for row in breakdown:
            row['pct'] = round(row['count'] * 100.0 / grand, 1) if grand else 0.0

        # unfiltered, so picking a module does not shrink the dropdown
        options = sorted(
            ({'id': module.id, 'label': module.display_name if module else 'No module'}
             for module, in log._read_group(
                 [('create_date', '>=', since)], groupby=['module_id'])),
            key=lambda o: o['label'])
        return {
            'windows': windows,
            'breakdown': breakdown,
            'breakdown_by': breakdown_by,
            'module_options': options,
            'module_id': module_id,
            'since': fields.Datetime.to_string(since),
        }

    @api.model
    def _gc_logs(self):
        """Cron target: drop logs older than core_api.log_retention_days."""
        days = int(self.env['ir.config_parameter'].sudo().get_param(
            'core_api.log_retention_days', DEFAULT_RETENTION_DAYS))
        domain = [('create_date', '<', fields.Datetime.now() - timedelta(days=days))]
        total = 0
        # ponytail: batched unlink. Unbounded unlink() on a year of logs times out.
        while True:
            batch = self.sudo().search(domain, limit=GC_BATCH)
            if not batch:
                break
            total += len(batch)
            batch.unlink()
        _logger.info("core.api.log: removed %s logs older than %s days", total, days)
        return total

