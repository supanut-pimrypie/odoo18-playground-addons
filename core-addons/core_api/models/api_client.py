import time

import requests

from odoo import _, api, models
from odoo.exceptions import UserError

from .schema import validate

DEFAULT_TIMEOUT = 30


def _json_or_text(response):
    try:
        return response.json()
    except ValueError:
        return response.text


class BaseApiClient(models.AbstractModel):
    """Outbound HTTP with logging. Inherit or call directly:

        self.env['core.api.client']._call('post', url, json=payload)
    """
    _name = 'core.api.client'
    _description = 'Outbound API Client'

    @api.model
    def _call(self, method, url, timeout=DEFAULT_TIMEOUT,
              request_schema=None, response_schema=None, **kwargs):
        """Send one logged HTTP call.

        `request_schema` is checked before anything leaves the process and
        raises: sending a payload we know is wrong is a bug worth stopping.
        `response_schema` only marks the log row, since by then the remote has
        already answered and the caller may still want what came back.
        """
        started = time.time()
        log = self.env['core.api.log']
        vals = {
            'direction': 'outbound',
            'name': url,
            'method': method.upper(),
            'user_id': self.env.uid,
            'request_headers': kwargs.get('headers'),
            'request_body': kwargs.get('json') or kwargs.get('data') or kwargs.get('params'),
        }
        payload = kwargs.get('json')
        if request_schema and payload is not None:
            problems = validate(payload, request_schema)
            if problems:
                vals.update(state='error', status_code=0, duration_ms=0,
                            error='Outbound payload does not match its schema: '
                                  + '; '.join(problems))
                log.log_call(vals, new_cursor=True)
                raise UserError(_("Outbound payload does not match its schema: %s", "; ".join(problems)))

        if not log._switch_on('core_api.outbound_enabled'):
            vals.update(state='error', status_code=503, duration_ms=0,
                        error="Outbound API calls are disabled in Settings.")
            log.log_call(vals, new_cursor=True)
            raise UserError(_("Outbound API calls are disabled in Settings."))
        try:
            response = requests.request(method, url, timeout=timeout, **kwargs)
        except Exception as e:
            vals.update(state='error', error=str(e),
                        duration_ms=int((time.time() - started) * 1000))
            log.log_call(vals, new_cursor=True)
            raise
        vals.update(
            state='success' if response.ok else 'error',
            status_code=response.status_code,
            response_body=response.text,
            duration_ms=int((time.time() - started) * 1000),
        )
        if response_schema and response.ok:
            problems = validate(_json_or_text(response), response_schema)
            if problems:
                vals.update(state='error',
                            error='Response does not match its schema: '
                                  + '; '.join(problems))
        log.log_call(vals)
        return response
