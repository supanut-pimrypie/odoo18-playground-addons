"""Base building blocks for every API this project exposes.

    class MyApi(http.Controller):
        @http.route('/api/thing', type='http', auth='user', methods=['POST'], csrf=False)
        @log_api
        def thing(self, **kw):
            return json_response({'ok': True})
"""
import functools
import json
import time
import traceback

from odoo.http import request, root

from ..models.schema import validate


def json_response(data, status=200):
    """Version-safe JSON response (works on 16/17/18)."""
    return request.make_response(
        json.dumps(data, ensure_ascii=False, default=str),
        status=status,
        headers=[('Content-Type', 'application/json')],
    )


def _as_json(value):
    """Parsed json, or the value itself when it is not json text."""
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value or '{}')
    except (TypeError, ValueError):
        return value


def json_body():
    """Parsed JSON body, {} when absent or malformed."""
    try:
        return json.loads(request.httprequest.get_data(as_text=True) or '{}')
    except ValueError:
        return {}


def _route_of(http_request):
    """Declared route pattern for the request, e.g. /api/thing/<int:id>.

    Odoo 18 keeps no endpoint reference on the request, so re-match against
    the cached routing map. Returns None if anything is off.
    """
    try:
        adapter = root.get_db_router(request.db).bind('')
        return adapter.match(http_request.path,
                             method=http_request.method,
                             return_rule=True)[0].rule
    except Exception:
        return None


def log_api(_func=None, request_schema=None, response_schema=None):
    """Log one inbound call: who called, payload, response, duration.

    Usable bare or with JSON Schemas:

        @log_api
        @log_api(request_schema={'type': 'object', 'required': ['name']})

    A request that does not match `request_schema` is answered 400 and never
    reaches the endpoint. A response that does not match `response_schema` is
    still sent -- breaking a working reply over our own bug helps nobody -- but
    its log row is marked as an error carrying the details.
    """
    def decorate(func):

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            http_request = request.httprequest
            started = time.time()
            # the toggle keys on the declared route pattern, not the concrete
            # path: /api/thing/<int:id> must stay one row, not one per id.
            rule = _route_of(http_request) or '%s.%s' % (func.__module__,
                                                         func.__qualname__)
            # odoo.addons.<addon>.controllers.<file> -> <addon>
            parts = func.__module__.split('.')
            addon = (parts[2] if parts[:2] == ['odoo', 'addons'] and len(parts) > 2
                     else None)
            body = (http_request.get_data(as_text=True)
                    or dict(request.params or {})
                    or dict(http_request.args))
            vals = {
                'direction': 'inbound',
                'name': http_request.path,
                'method': http_request.method,
                'user_id': request.env.uid,
                'remote_addr': http_request.remote_addr,
                'forwarded_for': http_request.headers.get('X-Forwarded-For'),
                'request_headers': dict(http_request.headers),
                'request_body': body,
            }
            log = request.env['core.api.log']

            def refuse(payload, status):
                result = json_response(payload, status=status)
                vals.update(state='error', status_code=status, duration_ms=0,
                            response_body=result.get_data(as_text=True))
                log.log_call(vals)
                return result

            # master switch first: it is ormcached, the endpoint lookup is not
            if not log._switch_on('core_api.inbound_enabled'):
                return refuse({'error': 'This endpoint is disabled.',
                               'endpoint': rule}, 503)
            endpoint_id, enabled = request.env['core.api.endpoint']._get_or_register(
                rule, func.__name__, addon, http_request.method)
            vals['endpoint_id'] = endpoint_id
            if not enabled:
                return refuse({'error': 'This endpoint is disabled.',
                               'endpoint': rule}, 503)

            if request_schema:
                problems = validate(json_body(), request_schema)
                if problems:
                    return refuse({'error': 'Invalid request payload',
                                   'details': problems}, 400)

            try:
                result = func(*args, **kwargs)
            except Exception as e:
                vals.update(
                    state='error',
                    status_code=getattr(e, 'code', 500),
                    error=traceback.format_exc(),
                    duration_ms=int((time.time() - started) * 1000),
                )
                # request cursor is doomed by the rollback: log on a fresh one
                log.log_call(vals, new_cursor=True)
                raise

            sent = (result.get_data(as_text=True)
                    if hasattr(result, 'get_data') else result)
            vals.update(
                state='success',
                status_code=getattr(result, 'status_code', 200),
                response_body=sent,
                duration_ms=int((time.time() - started) * 1000),
            )
            if response_schema:
                problems = validate(_as_json(sent), response_schema)
                if problems:
                    detail = ('Response does not match its schema: '
                              + '; '.join(problems))
                    vals.update(state='error', error=detail)
            log.log_call(vals)
            return result

        wrapper._core_api_logged = True
        wrapper._core_api_schemas = {'request': request_schema,
                                     'response': response_schema}
        return wrapper

    return decorate(_func) if callable(_func) else decorate
