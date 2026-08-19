"""Sanitizer check. Runs under the odoo test runner and standalone:

    python core-addons/core_api/tests/test_sanitize.py
"""
import importlib.util
import json
import os
import unittest

_MODELS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       'models')


def _load(name):
    spec = importlib.util.spec_from_file_location(
        'core_api_' + name, os.path.join(_MODELS, name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sanitize_mod = _load('sanitize')
validate = _load('schema').validate

sanitize = sanitize_mod.sanitize
as_bool = sanitize_mod.as_bool
REDACTED = sanitize_mod.REDACTED
MAX_BODY_LEN = sanitize_mod.MAX_BODY_LEN


class TestSanitize(unittest.TestCase):

    def test_redacts_json_secrets(self):
        data = json.loads(sanitize(
            '{"user": "bob", "password": "hunter2", "nested": {"api_key": "k"}}'))
        self.assertEqual(data['user'], 'bob')
        self.assertEqual(data['password'], REDACTED)
        self.assertEqual(data['nested']['api_key'], REDACTED)

    def test_redacts_headers_dict(self):
        out = json.loads(sanitize({'Authorization': 'Bearer abc', 'Accept': 'json'}))
        self.assertEqual(out['Authorization'], REDACTED)
        self.assertEqual(out['Accept'], 'json')

    def test_redacts_non_json_body(self):
        out = sanitize('user=bob&password=hunter2')
        self.assertNotIn('hunter2', out)
        self.assertIn('bob', out)

    def test_truncates(self):
        out = sanitize('x' * (MAX_BODY_LEN + 500))
        self.assertLess(len(out), MAX_BODY_LEN + 100)
        self.assertTrue(out.endswith('[truncated]'))

    def test_empty_is_false(self):
        self.assertIs(sanitize(None), False)
        self.assertIs(sanitize(''), False)
        self.assertIs(sanitize({}), False)


class TestAsBool(unittest.TestCase):
    """config_parameter booleans arrive as strings: bool('False') is True."""

    def test_string_false_is_off(self):
        for value in ('False', 'false', ' FALSE ', '0', 'off', 'none'):
            self.assertFalse(as_bool(value), value)

    def test_string_true_is_on(self):
        for value in ('True', 'true', '1', 'yes'):
            self.assertTrue(as_bool(value), value)

    def test_unset_uses_default(self):
        self.assertTrue(as_bool(False))
        self.assertTrue(as_bool(None))
        self.assertTrue(as_bool(''))
        self.assertFalse(as_bool('', default=False))


class TestSchema(unittest.TestCase):
    """jsonschema wrapper: errors come back as readable strings, never raised."""

    SCHEMA = {
        'type': 'object',
        'required': ['name'],
        'properties': {
            'name': {'type': 'string', 'minLength': 1},
            'qty': {'type': 'integer', 'minimum': 1},
            'tags': {'type': 'array', 'items': {'type': 'string'}},
        },
    }

    def test_valid_payload(self):
        self.assertEqual(validate({'name': 'a', 'qty': 2}, self.SCHEMA), [])

    def test_missing_required(self):
        problems = validate({'qty': 2}, self.SCHEMA)
        self.assertEqual(len(problems), 1)
        self.assertIn('name', problems[0])

    def test_wrong_type_names_the_field(self):
        problems = validate({'name': 'a', 'qty': 'two'}, self.SCHEMA)
        self.assertTrue(any(p.startswith('qty:') for p in problems), problems)

    def test_nested_path(self):
        problems = validate({'name': 'a', 'tags': ['ok', 5]}, self.SCHEMA)
        self.assertTrue(any('tags[1]' in p for p in problems), problems)

    def test_no_schema_means_no_check(self):
        self.assertEqual(validate({'anything': True}, None), [])

    def test_broken_schema_is_reported_not_raised(self):
        problems = validate({}, {'type': 'not-a-type'})
        self.assertEqual(len(problems), 1)
        self.assertIn('invalid schema', problems[0])


if __name__ == '__main__':
    unittest.main()
