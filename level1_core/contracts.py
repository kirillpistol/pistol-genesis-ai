"""Versioned JSON Schema subset; reject unknown keywords in project schemas."""
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'contracts' / 'v1'
API_VERSION = '1.0'

def loads(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate JSON key')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Non-finite JSON')))

def dumps(value):
    return json.dumps(value, allow_nan=False, separators=(',', ':'), sort_keys=True).encode()

def validate(value, schema, depth=0):
    if depth > 16:
        raise ValueError('Nesting limit')
    allowed = {'$schema', '$id', 'title', 'type', 'properties', 'required', 'additionalProperties',
               'items', 'minimum', 'maximum', 'minLength', 'maxLength', 'maxItems', 'const', 'enum'}
    if set(schema) - allowed:
        raise ValueError('Unsupported schema keyword')
    kind = schema.get('type')
    checks = {'object': lambda: type(value) is dict, 'array': lambda: type(value) is list,
              'string': lambda: type(value) is str, 'integer': lambda: type(value) is int,
              'number': lambda: type(value) in (int, float) and math.isfinite(value),
              'boolean': lambda: type(value) is bool, 'null': lambda: value is None}
    if kind and (kind not in checks or not checks[kind]()):
        raise ValueError('Schema type mismatch')
    if 'const' in schema and (type(value) is not type(schema['const']) or value != schema['const']):
        raise ValueError('Contract version/value mismatch')
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError('Invalid enum')
    if type(value) is dict:
        props = schema.get('properties', {})
        if set(schema.get('required', [])) - set(value):
            raise ValueError('Missing fields')
        extra = schema.get('additionalProperties', True)
        if extra is False and set(value)-set(props):
            raise ValueError('Unknown fields')
        for key, item in value.items():
            if len(key) > 128:
                raise ValueError('Key too long')
            validate(item, props.get(key, extra if isinstance(extra, dict) else {}), depth+1)
    elif type(value) is list:
        if len(value) > schema.get('maxItems', 4096):
            raise ValueError('Too many items')
        for item in value:
            validate(item, schema.get('items', {}), depth+1)
    elif type(value) is str:
        if not schema.get('minLength', 0) <= len(value) <= schema.get('maxLength', 65536):
            raise ValueError('String size')
    elif type(value) in (int, float):
        if not math.isfinite(value) or not schema.get('minimum', -1e300) <= value <= schema.get('maximum', 1e300):
            raise ValueError('Number range')
    elif value is not None and type(value) is not bool:
        raise ValueError('Non JSON value')

def check(name, value):
    schema = loads((ROOT / (name + '.json')).read_bytes())
    validate(value, schema)
    return value
