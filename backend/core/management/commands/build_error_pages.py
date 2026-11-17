"""Build self-contained pages for the OBLIK proxy host, not the HUB host."""
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from core.errors import STATUSES, ERROR_CODES, error_markup, resources


def proxy_config():
    csp = ("default-src 'none'; img-src data:; style-src 'sha256-" + resources()[4]
           + "'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
    lines = [
        '# OBLIK virtual host only. Install pages at /data/oxxom-oblik-errors.',
        '# Do not enable proxy_intercept_errors: preserve application API responses.',
        'set $oblik_error_language uk;',
        'set $oblik_error_format html;',
        'if ($request_uri ~ "^/api/") { set $oblik_error_format json; }',
        'if ($cookie_oxxom_language ~ "^(en|ru|cs)$") { set $oblik_error_language $cookie_oxxom_language; }',
    ]
    for code in ('uk', 'en', 'ru', 'cs'):
        lines.append(f'if ($request_uri ~ "^/{code}/") {{ set $oblik_error_language {code}; }}')
    for status in (429, 502, 503, 504):
        headers = [
            '    add_header Cache-Control "no-store, private" always;',
            '    add_header X-Robots-Tag "noindex, nofollow" always;',
            '    add_header X-Content-Type-Options nosniff always;',
            '    add_header X-Frame-Options DENY always;',
            '    add_header Referrer-Policy no-referrer always;',
            '    add_header Content-Language $oblik_error_language always;',
            '    add_header Strict-Transport-Security "max-age=3600" always;',
            f'    add_header Content-Security-Policy "{csp}" always;',
        ]
        if status in (429, 503):
            headers.append(f'    add_header Retry-After {900 if status == 429 else 60} always;')
        lines += [
            f'error_page {status} /__oblik_errors/{status}.$oblik_error_format;',
            f'location = /__oblik_errors/{status}.html {{',
            '    internal;',
            '    default_type text/html;',
            '    types { text/html html; }',
            f'    alias /data/oxxom-oblik-errors/{status}-$oblik_error_language.html;',
            *headers,
            '}',
            f'location = /__oblik_errors/{status}.json {{',
            '    internal;',
            '    types { application/json json; }',
            '    default_type application/json;',
            *headers,
            f'    return {status} \'{{"error":"{ERROR_CODES[status]}"}}\';',
            '}',
        ]
    return '\n'.join(lines) + '\n'


class Command(BaseCommand):
    help = 'Build 32 error pages and a server-scoped external Nginx include; never reloads Nginx'

    def add_arguments(self, parser):
        parser.add_argument('destination', type=Path)

    def handle(self, *args, **options):
        destination = options['destination']
        if destination.exists():
            raise CommandError('Use a new empty destination to avoid overwriting files')
        destination.mkdir(parents=True, mode=0o755)
        for language in ('uk', 'en', 'ru', 'cs'):
            for status in STATUSES:
                (destination / f'{status}-{language}.html').write_text(error_markup(status, language), encoding='utf-8')
        (destination / 'oblik-errors.conf').write_text(proxy_config(), encoding='utf-8')
        self.stdout.write('ERROR PAGES BUILT: 32 HTML files and OBLIK-only Nginx include; not installed')
