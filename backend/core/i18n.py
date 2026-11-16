"""Interface copy only: never translate names, notes, IDs or business records."""
import json
import re
from functools import lru_cache
from pathlib import Path
from django.utils.translation import get_language

LANGUAGES = ('uk', 'en', 'ru', 'cs')
COOKIE = 'oxxom_language'


def language(value):
    return value if value in LANGUAGES else 'uk'


@lru_cache(maxsize=1)
def catalog():
    return json.loads((Path(__file__).parent / 'locale' / 'ui.json').read_text(encoding='utf-8'))


def translate(source, code=None):
    if not isinstance(source, str):
        return source
    code = language(code or (get_language() or 'uk').split('-')[0])
    row = catalog().get(source)
    if code == 'uk':
        return source
    index = LANGUAGES.index(code) - 1
    if row:
        return row[index]
    for pattern, key in patterns():
        match = pattern.fullmatch(source)
        suffix = ''
        if match is None and key.endswith(': '):
            match = pattern.match(source)
            if match:
                suffix = translate(source[match.end():], code)
        if match:
            translated = catalog()[key][index]
            return re.sub(r'\{(\d+)\}', lambda m: match.group(int(m[1]) + 1), translated) + suffix
    for prefix in prefixes():
        if source.startswith(prefix):
            remainder = source[len(prefix):]
            if prefix.startswith('Реквізити організації неповні:'):
                remainder = ', '.join(translate(field, code) for field in remainder.split(', '))
            return catalog()[prefix][index] + remainder
    return source


@lru_cache(maxsize=1)
def prefixes():
    return sorted((key for key in catalog() if len(key) >= 10 and key.rstrip().endswith((':', '«'))), key=len, reverse=True)


@lru_cache(maxsize=1)
def patterns():
    result = []
    for key in catalog():
        if re.search(r'\{\d+\}', key):
            expression = re.escape(key)
            expression = re.sub(r'\\\{\d+\\\}', '(.*?)', expression)
            result.append((re.compile(expression, re.S), key))
    return result


def context(request):
    code = language(request.COOKIES.get(COOKIE))
    return {'ui_language': code, 'ui_workspace_name': translate('Робочий простір', code)}
