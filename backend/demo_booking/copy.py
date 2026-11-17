import json
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def catalog():
    return json.loads((Path(__file__).parent / 'copy.json').read_text(encoding='utf-8'))


def strings(language):
    return catalog()[language if language in ('uk', 'en', 'ru', 'cs') else 'uk']
