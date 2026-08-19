"""Payload sanitizing. Pure functions: no odoo import, no DB, no env."""
import json
import re

MAX_BODY_LEN = 20000
REDACTED = '***'
SECRET_KEYS = (
    'password', 'passwd', 'pwd', 'token', 'access_token', 'refresh_token',
    'api_key', 'apikey', 'secret', 'client_secret', 'authorization',
    'x-api-key', 'cookie', 'set-cookie',
)
_SECRET_RE = re.compile(
    r'(?i)(' + '|'.join(SECRET_KEYS) + r')(\s*[=:]\s*)("[^"]*"|[^&\s,;}]*)'
)


def sanitize(value, max_len=MAX_BODY_LEN):
    """Redact secret-looking keys then truncate. Returns text, or False when empty."""
    if value is None or value == '' or value == {} or value == []:
        return False
    if isinstance(value, (bytes, bytearray)):
        value = value.decode('utf-8', 'replace')
    data = value
    if isinstance(value, str):
        try:
            data = json.loads(value)
        except ValueError:
            return _truncate(_SECRET_RE.sub(r'\1\g<2>' + REDACTED, value), max_len)
    try:
        text = json.dumps(_redact(data), ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = _SECRET_RE.sub(r'\1\g<2>' + REDACTED, str(data))
    return _truncate(text, max_len)


def _redact(data):
    if isinstance(data, dict):
        return {
            k: (REDACTED if str(k).lower() in SECRET_KEYS else _redact(v))
            for k, v in data.items()
        }
    if isinstance(data, (list, tuple)):
        return [_redact(v) for v in data]
    return data


def _truncate(text, max_len):
    return text if len(text) <= max_len else text[:max_len] + '...[truncated]'


def as_bool(value, default=True):
    """res.config.settings stores booleans as the strings 'True'/'False'.

    bool('False') is True, so never coerce a raw config param with bool().
    """
    if value is None or value is False or value == '':
        return default
    if isinstance(value, str):
        return value.strip().lower() not in ('false', '0', 'none', 'off')
    return bool(value)
