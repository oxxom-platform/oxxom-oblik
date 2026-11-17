"""Error responses independent of accounts, sessions and the database."""
import base64
import hashlib
import json
from functools import lru_cache
from pathlib import Path

from django.http import HttpResponse, JsonResponse
from django.template.loader import render_to_string
from django.utils.cache import patch_vary_headers

ROOT = Path(__file__).parent
STATUSES = (400, 403, 404, 429, 500, 502, 503, 504)
ERROR_CODES = {400: 'bad_request', 403: 'permission_denied', 404: 'not_found',
               429: 'too_many_requests', 500: 'internal_error', 502: 'bad_gateway',
               503: 'service_unavailable', 504: 'gateway_timeout'}


@lru_cache(maxsize=1)
def resources():
    copy = json.loads((ROOT / 'locale' / 'errors.json').read_text(encoding='utf-8'))
    css = (ROOT / 'static' / 'core' / 'errors.css').read_text(encoding='utf-8')
    logo = base64.b64encode((ROOT / 'static' / 'core' / 'oxxom-oblik-web.png').read_bytes()).decode()
    icon = base64.b64encode((ROOT / 'static' / 'core' / 'favicon-oblik.png').read_bytes()).decode()
    digest = base64.b64encode(hashlib.sha256(css.encode()).digest()).decode()
    return copy, css, logo, icon, digest


def language_for(request):
    # Does not access user, session, Accept-Language, or exception data.
    from .i18n import COOKIE, LANGUAGES, language
    prefix = request.path.split('/', 2)[1]
    return prefix if prefix in LANGUAGES else language(request.COOKIES.get(COOKIE))


def error_markup(status, language='uk', csrf=False):
    if status not in STATUSES or language not in ('uk', 'en', 'ru', 'cs'):
        raise ValueError('Unsupported error page')
    copy, css, logo, icon, _ = resources()
    common = copy[language]['common']
    title, description = copy[language]['csrf' if csrf else str(status)]
    return render_to_string('core/error.html', {
        'language': language, 'status': status, 'title': title, 'description': description,
        'css': css, 'logo': logo, 'icon': icon, 'common': common,
        'home': '/' if language == 'uk' else '/' + language + '/',
    })  # No request: context processors must not query a broken database.


def error_response(request, status, csrf=False):
    code = language_for(request)
    if request.path.startswith('/api/'):
        response = JsonResponse({'error': 'csrf_failed' if csrf else ERROR_CODES[status]}, status=status)
    else:
        response = HttpResponse(error_markup(status, code, csrf), status=status)
        response['Content-Security-Policy'] = (
            "default-src 'none'; img-src data:; style-src 'sha256-" + resources()[4]
            + "'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
    response['Content-Language'] = code
    response['Cache-Control'] = 'no-store, private'
    response['X-Robots-Tag'] = 'noindex, nofollow'
    response['X-Content-Type-Options'] = 'nosniff'
    response['X-Frame-Options'] = 'DENY'
    response['Referrer-Policy'] = 'no-referrer'
    if status in (429, 503):
        response['Retry-After'] = '900' if status == 429 else '60'
    patch_vary_headers(response, ('Cookie',))
    return response


def bad_request(request, exception=None):
    return error_response(request, 400)


def permission_denied(request, exception=None):
    return error_response(request, 403)


def not_found(request, exception=None):
    return error_response(request, 404)


def server_error(request):
    return error_response(request, 500)


def csrf_failure(request, reason=''):
    return error_response(request, 403, csrf=True)


class ErrorPageMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if (response.status_code in STATUSES and not request.path.startswith('/api/')
                and response.get('Content-Type', '').startswith('text/html')
                and not response.has_header('Content-Security-Policy')):
            branded = error_response(request, response.status_code)
            # Preserve meaningful protocol headers, including an upstream retry.
            for name in ('Retry-After', 'Allow', 'WWW-Authenticate'):
                if response.has_header(name):
                    branded[name] = response[name]
            branded.cookies = response.cookies
            return branded
        return response
