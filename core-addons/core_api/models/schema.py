"""Payload validation, thin wrapper over jsonschema.

Schemas are plain JSON Schema (draft 2020-12):

    {
        'type': 'object',
        'required': ['name'],
        'properties': {
            'name': {'type': 'string', 'minLength': 1},
            'qty': {'type': 'integer', 'minimum': 1},
            'tags': {'type': 'array', 'items': {'type': 'string'}},
        },
    }

No odoo import here on purpose: the module is testable without a database.
"""
import json
from functools import lru_cache

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError


@lru_cache(maxsize=256)
def _validator(schema_json):
    schema = json.loads(schema_json)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def validate(payload, schema):
    """Return a list of human-readable problems. Empty list means valid.

    A broken schema is reported the same way rather than raised: a typo in a
    developer's schema must not turn into a 500 for the caller.
    """
    if not schema:
        return []
    try:
        validator = _validator(json.dumps(schema, sort_keys=True, default=str))
    except (SchemaError, ValueError) as e:
        return ['invalid schema: %s' % e]
    return ['%s: %s' % (_where(error), error.message)
            for error in sorted(validator.iter_errors(payload),
                                key=lambda e: list(e.absolute_path))]


def _where(error):
    path = list(error.absolute_path)
    if not path:
        return 'payload'
    out = str(path[0])
    for part in path[1:]:
        out += '[%s]' % part if isinstance(part, int) else '.%s' % part
    return out
