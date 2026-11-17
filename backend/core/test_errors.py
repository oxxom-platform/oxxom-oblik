import base64
import hashlib
import re
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, SuspiciousOperation
from django.http import HttpResponse
from django.test import Client, RequestFactory, SimpleTestCase, override_settings
from django.urls import path

from . import errors
from django.core.management import call_command
from pathlib import Path
import tempfile


def forbidden(request):
    raise PermissionDenied('PRIVATE exception detail')


def invalid(request):
    raise SuspiciousOperation('PRIVATE exception detail')


def broken(request):
    raise RuntimeError('PRIVATE exception detail')


def form(request):
    return HttpResponse('form')


urlpatterns = [path('denied/', forbidden), path('invalid/', invalid), path('broken/', broken),
               path('form/', form), path('api/broken', broken), path('api/form', form)]
handler400 = 'core.errors.bad_request'
handler403 = 'core.errors.permission_denied'
handler404 = 'core.errors.not_found'
handler500 = 'core.errors.server_error'


@override_settings(DEBUG=False, ROOT_URLCONF=__name__)
class ErrorPagesTests(SimpleTestCase):
    def test_proxy_bundle_is_self_contained_and_limited_to_own_host(self):
        with tempfile.TemporaryDirectory() as name:
            destination = Path(name) / 'new'
            call_command('build_error_pages', destination, verbosity=0)
            self.assertEqual(len(list(destination.glob('*.html'))), 32)
            config = (destination / 'oblik-errors.conf').read_text()
            self.assertIn('internal;', config)
            self.assertIn('return 503', config)
            self.assertNotIn('proxy_intercept_errors on', config)
            self.assertNotIn('proxy_pass', config)
            self.assertNotIn('server_name', config)
            self.assertNotIn('error_page 404', config)
            self.assertIn("default_type application/json", config)
            self.assertIn('types { text/html html; }', config)

    def test_real_handlers_preserve_status_and_hide_exception_data(self):
        client = Client(raise_request_exception=False)
        for path, status in [('/missing/?private=PRIVATE', 404), ('/denied/', 403),
                             ('/invalid/', 400), ('/broken/', 500)]:
            with self.subTest(status=status):
                response = client.get(path)
                self.assertEqual(response.status_code, status)
                self.assertContains(response, f'HTTP {status}', status_code=status)
                self.assertNotContains(response, 'PRIVATE', status_code=status)
                self.assertEqual(response['X-Robots-Tag'], 'noindex, nofollow')
                self.assertIn('no-store', response['Cache-Control'])
                self.assertEqual(response['X-Frame-Options'], 'DENY')

    def test_all_pages_all_languages_work_without_db_sessions_or_context_processors(self):
        request = RequestFactory().get('/unknown')
        with patch('core.workspace.context', side_effect=AssertionError('DB context called')):
            for language in ('uk', 'en', 'ru', 'cs'):
                request.COOKIES['oxxom_language'] = language
                for status in errors.STATUSES:
                    with self.subTest(language=language, status=status):
                        response = errors.error_response(request, status)
                        self.assertEqual(response.status_code, status)
                        self.assertContains(response, f'lang="{language}"', status_code=status)
                        self.assertEqual(response['Content-Language'], language)
                        self.assertNotIn(b'<script', response.content)
                        self.assertNotIn(b'http://', response.content)
                        self.assertNotIn(b'https://', response.content)

    def test_url_language_wins_cookie_and_arbitrary_cookie_is_not_reflected(self):
        self.client.cookies['oxxom_language'] = 'ru'
        response = self.client.get('/en/missing/')
        self.assertContains(response, 'lang="en"', status_code=404)
        self.assertEqual(response['Content-Language'], 'en')
        self.client.cookies['oxxom_language'] = 'PRIVATE<script>'
        response = self.client.get('/missing/')
        self.assertContains(response, 'lang="uk"', status_code=404)
        self.assertNotContains(response, 'PRIVATE', status_code=404)

    def test_style_hash_matches_exact_browser_style_and_no_inline_script(self):
        response = errors.error_response(RequestFactory().get('/missing'), 404)
        style = re.search(rb'<style>(.*?)</style>', response.content, re.S).group(1)
        digest = base64.b64encode(hashlib.sha256(style).digest()).decode()
        self.assertIn("'sha256-" + digest + "'", response['Content-Security-Policy'])
        self.assertIn("default-src 'none'", response['Content-Security-Policy'])

    def test_api_errors_and_csrf_keep_json_contract(self):
        client = Client(raise_request_exception=False, enforce_csrf_checks=True)
        for path, status, code in [('/api/missing', 404, 'not_found'),
                                   ('/api/broken', 500, 'internal_error')]:
            response = client.get(path)
            self.assertEqual(response.status_code, status)
            self.assertEqual(response.json(), {'error': code})
        response = client.post('/api/form', {})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {'error': 'csrf_failed'})
        response = client.post('/form/', {})
        self.assertEqual(response.status_code, 403)
        self.assertNotIn(b'CSRF verification failed', response.content)

    def test_non_get_errors_have_no_automatic_retry(self):
        request = RequestFactory().post('/documents/', {})
        response = errors.error_response(request, 504)
        self.assertNotIn(b'http-equiv="refresh"', response.content)
        self.assertNotIn(b'<form', response.content)
        self.assertNotIn(b'location.reload', response.content)

    def test_middleware_preserves_json_and_retry_header(self):
        request = RequestFactory().get('/documents/')
        upstream = HttpResponse('Maintenance', status=503, headers={'Retry-After': '120'})
        response = errors.ErrorPageMiddleware(lambda request: upstream)(request)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response['Retry-After'], '120')
        self.assertContains(response, 'HTTP 503', status_code=503)
        request = RequestFactory().get('/api/v1/health')
        upstream = errors.JsonResponse({'error': 'unavailable'}, status=503)
        self.assertIs(errors.ErrorPageMiddleware(lambda request: upstream)(request), upstream)
