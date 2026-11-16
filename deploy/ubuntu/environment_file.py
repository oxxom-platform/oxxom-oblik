"""Small, non-shell environment reader for production maintenance tools."""
import re
from pathlib import Path


def read_env(path):
    values = {}
    for raw in Path(path).read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        key, separator, value = line.partition('=')
        if not separator or not re.fullmatch(r'[A-Z][A-Z0-9_]*', key) or key in values:
            raise ValueError('Invalid or duplicate environment entry (values omitted)')
        values[key] = value
    return values


def domain_name(value):
    value = value.lower().rstrip('.')
    labels = value.split('.')
    if len(value) > 253 or len(labels) < 2 or all(part.isdigit() for part in labels):
        raise ValueError('Use a DNS domain name, without https://, port or path')
    if not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', part) for part in labels):
        raise ValueError('Invalid DNS domain name; use ASCII/punycode')
    return value
