"""Metadata-only JSON logs. Never log bodies, state, certificates or release tokens."""
import json
import logging
import re
import uuid

LOGGER=logging.getLogger('genesis.audit')
PATTERN=re.compile(r'^[A-Za-z0-9._-]{1,64}$')

def correlation(value=None):
    if value is None:return str(uuid.uuid4())
    if not PATTERN.fullmatch(value):raise ValueError('Invalid correlation ID')
    return value

def event(name,**fields):
    LOGGER.info(json.dumps(dict(event=name,**fields),sort_keys=True))

def configure():
    handler=logging.StreamHandler()
    handler.setFormatter(logging.Formatter('%(message)s'))
    LOGGER.handlers=[handler]
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate=False
