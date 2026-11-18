"""Public marketing URLs and copy. Never include account or business context."""
import hashlib
import json
from functools import lru_cache
from html import escape
from html.parser import HTMLParser
from pathlib import Path

ORIGIN = 'https://oblik.oxxom.com.ua'
CONTENT_UPDATED = '2026-11-17'
ROOT = Path(__file__).parent
STATIC = ROOT / 'static/core'
LANGUAGES = {
    'uk': {'path': '/', 'label': 'UA', 'name': 'Українська', 'locale': 'uk-UA', 'og': 'uk_UA',
           'title': 'OXXOM OBLIK — облік товарів, складу та оплат',
           'description': 'Облік товарів і послуг, складські залишки, документи та оплати в одному сервісі. OXXOM OBLIK допомагає впорядкувати щоденні операції бізнесу.'},
    'en': {'path': '/en/', 'label': 'EN', 'name': 'English', 'locale': 'en-GB', 'og': 'en_GB',
           'title': 'OXXOM OBLIK — inventory, documents and payments',
           'description': 'Manage goods, services, inventory, sales documents and payment records in one workspace. OXXOM OBLIK keeps everyday business operations organised.'},
    'ru': {'path': '/ru/', 'label': 'RU', 'name': 'Русский', 'locale': 'ru-UA', 'og': 'ru_UA',
           'title': 'OXXOM OBLIK — учёт товаров, склада и оплат',
           'description': 'Учёт товаров и услуг, складские остатки, документы и оплаты в одном сервисе. OXXOM OBLIK помогает упорядочить ежедневные операции бизнеса.'},
    'cs': {'path': '/cs/', 'label': 'CZ', 'name': 'Čeština', 'locale': 'cs-CZ', 'og': 'cs_CZ',
           'title': 'OXXOM OBLIK — evidence zboží, skladu a plateb',
           'description': 'Evidence zboží, služeb, skladových zásob, prodejních dokladů a plateb na jednom místě. OXXOM OBLIK pomáhá uspořádat každodenní chod firmy.'},
}
PUBLIC_PATHS = {item['path']: code for code, item in LANGUAGES.items()}
PUBLIC_PATHS.update({item['path'] + 'demo/': code for code, item in LANGUAGES.items()})


@lru_cache(maxsize=64)
def _fingerprint(name, modified, size):
    return hashlib.sha256((STATIC / name).read_bytes()).hexdigest()[:16]


def fingerprint(name):
    info = (STATIC / name).stat()
    return _fingerprint(name, info.st_mtime_ns, info.st_size)


def asset_url(name, route):
    return route + '?v=' + fingerprint(name)


def public_assets(code):
    return {
        'logo': asset_url('oxxom-oblik-web.png', '/assets/brand/oxxom-oblik-web.png'),
        'og': asset_url('og-oblik-' + code + '.png', '/assets/brand/og-oblik-' + code + '.png'),
        'css': asset_url('landing.css', '/assets/landing/landing.css'),
        'js': asset_url('landing.js', '/assets/landing/landing.js'),
        'preferences': asset_url('public-preferences.js', '/assets/landing/public-preferences.js'),
        'ui_css': asset_url('ui/preferences.css', '/assets/ui/preferences.css'),
    }


def text(source, code):
    from .i18n import catalog
    row = catalog().get(source)
    return source if code == 'uk' or row is None else row[('en', 'ru', 'cs').index(code)]


def translated_tree(value, code):
    if isinstance(value, str):
        return text(value, code)
    if isinstance(value, list):
        return [translated_tree(item, code) for item in value]
    if isinstance(value, dict):
        return {key: translated_tree(item, code) for key, item in value.items()}
    return value


@lru_cache(maxsize=1)
def demo_states():
    return json.loads((ROOT / 'locale/landing-demo.json').read_text())


def context(code):
    assets = public_assets(code)
    seo = dict(LANGUAGES[code], language=code, url=ORIGIN + LANGUAGES[code]['path'],
               image=ORIGIN + assets['og'])
    alternates = [dict(item, language=language, url=ORIGIN + item['path']) for language, item in LANGUAGES.items()]
    structured = {'@context': 'https://schema.org', '@graph': [
        {'@type': 'Organization', '@id': ORIGIN + '/#organization', 'name': 'OXXOM', 'url': ORIGIN + '/',
         'logo': {'@type': 'ImageObject', 'url': ORIGIN + assets['logo']}},
        {'@type': 'WebSite', '@id': ORIGIN + '/#website', 'url': ORIGIN + '/', 'name': 'OXXOM OBLIK',
         'inLanguage': list(LANGUAGES), 'publisher': {'@id': ORIGIN + '/#organization'}},
    ]}
    # JSON-LD is authored, never supplied by request parameters; escape HTML delimiters.
    encoded = json.dumps(structured, ensure_ascii=False)
    for character, escaped in (('<', '003c'), ('>', '003e'), ('&', '0026')):
        encoded = encoded.replace(character, chr(92) + 'u' + escaped)
    return {'seo': seo, 'seo_alternates': alternates, 'seo_jsonld': encoded,
            'demo_url': LANGUAGES[code]['path'] + 'demo/',
            # Footer link to the OXXOM company page about OBLIK, same language (Russian falls back to Ukrainian).
            'developer': {'url': 'https://www.oxxom.com.ua' + {'en': '/en', 'cs': '/cs'}.get(code, '') + '/produkty/oxxom-oblik/',
                          'label': text('Розробник: OXXOM', code)},
            'public_assets': assets, 'ui_language': code,
            'landing_data': {'language': code, 'locale': seo['locale'],
                             'urls': {key: item['path'] for key, item in LANGUAGES.items()},
                             'strings': {'Демонстрація:': text('Демонстрація:', code)},
                             'states': translated_tree(demo_states(), code)}}


class PublicHTML(HTMLParser):
    """Translate authored marketing copy only, never a workspace response."""
    def __init__(self, code):
        super().__init__(convert_charrefs=False)
        self.code, self.output, self.stack = code, [], []

    def handle_starttag(self, tag, attrs):
        skip = any(item[1] for item in self.stack) or tag in ('script', 'style') or any(key == 'data-ui-native' for key, _ in attrs)
        translated = []
        for name, value in attrs:
            if not any(item[1] for item in self.stack) and tag not in ('script', 'style') and name in ('alt', 'title', 'aria-label') and value:
                value = text(value, self.code)
            translated.append((name, value))
        if translated == attrs:
            self.output.append(self.get_starttag_text())
        else:
            self.output.append('<' + tag + ''.join(' ' + key + ('' if value is None else '="' + escape(value, quote=True) + '"') for key, value in translated) + '>')
        if tag not in ('area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'):
            self.stack.append((tag, skip))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if self.stack and self.stack[-1][0] == tag:
            self.stack.pop()

    def handle_endtag(self, tag):
        self.output.append('</' + tag + '>')
        if self.stack and self.stack[-1][0] == tag:
            self.stack.pop()

    def handle_data(self, value):
        if any(item[1] for item in self.stack) or not value.strip():
            self.output.append(value)
            return
        stripped = value.strip()
        translated = text(stripped, self.code)
        if any(tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6') for tag, _ in self.stack):
            translated = translated.rstrip('.')
        self.output.append(value.replace(stripped, escape(translated, quote=False)))

    def handle_entityref(self, name):
        self.output.append('&' + name + ';')

    def handle_charref(self, name):
        self.output.append('&#' + name + ';')

    def handle_decl(self, value):
        self.output.append('<!' + value + '>')

    def handle_comment(self, value):
        self.output.append('<!--' + value + '-->')


def render_public(markup, code):
    parser = PublicHTML(code)
    parser.feed(markup)
    parser.close()
    return ''.join(parser.output)


class SearchIndexPolicyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.conf import settings
        response = self.get_response(request)
        public = request.path in PUBLIC_PATHS or request.path in ('/robots.txt', '/sitemap.xml', '/favicon.ico') or request.path.startswith('/assets/')
        if not settings.OXXOM_INDEX_PUBLIC or not public or response.status_code >= 400:
            response['X-Robots-Tag'] = 'noindex, nofollow'
        return response
