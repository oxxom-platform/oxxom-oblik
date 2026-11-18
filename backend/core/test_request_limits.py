"""Synthetic requests only; no production accounts or network targets."""
from unittest.mock import patch
from uuid import uuid4

from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import HttpResponse
from django.test import Client, RequestFactory, SimpleTestCase, TestCase
from django.core.handlers.wsgi import WSGIRequest
import pyotp

from accounts.models import AuthThrottle, Membership, Organization, TOTPDevice, User
from accounts.mfa import encrypt
from .request_limits import (
    API_LIMIT, AUTH_LIMIT, IMPORT_LIMIT, RequestBodyLimitMiddleware,
)


class LengthPolicyTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.middleware = RequestBodyLimitMiddleware(lambda request: HttpResponse('next'))
        self.import_path = f'/api/v1/organizations/{uuid4()}/imports/products/preview'

    def request(self, path, length, method='POST', **meta):
        request = self.factory.generic(method, path, **meta)
        request.META['CONTENT_LENGTH'] = length
        return self.middleware(request)

    def test_boundaries_and_only_exact_import_preview_has_larger_budget(self):
        for path, limit in [('/api/v1/auth/login', AUTH_LIMIT),
                            ('/api/v1/organizations', API_LIMIT),
                            (self.import_path, IMPORT_LIMIT)]:
            with self.subTest(path=path):
                self.assertEqual(self.request(path, str(limit)).status_code, 200)
                response = self.request(path, str(limit + 1))
                self.assertEqual(response.status_code, 413)
                self.assertIn('no-store', response['Cache-Control'])
                self.assertEqual(response['X-Robots-Tag'], 'noindex, nofollow')
        for path in [self.import_path + '/extra', self.import_path.replace('products', 'unknown'),
                     self.import_path.replace('preview', 'confirm')]:
            self.assertEqual(self.request(path, str(API_LIMIT + 1)).status_code, 413)

    def test_malformed_lengths_and_unknown_length_bodies_fail_closed(self):
        for length in ['-1', '1.0', ' 1', '١', '9' * 10000]:
            self.assertEqual(self.request('/api/v1/auth/login', length).status_code, 400)
        self.assertEqual(self.request('/api/v1/auth/login', '',
                                     CONTENT_TYPE='application/json').status_code, 411)
        self.assertEqual(self.request('/api/v1/auth/login', '',
                                     HTTP_TRANSFER_ENCODING='chunked').status_code, 411)
        self.assertEqual(self.request('/api/v1/auth/logout', '').status_code, 200)

    def test_all_mutating_methods_are_bounded(self):
        for method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            self.assertEqual(self.request('/unknown', str(API_LIMIT + 1), method).status_code, 413)


class ParsingOrderTests(TestCase):
    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.client.get('/login/')
        self.csrf = self.client.cookies['csrftoken'].value

    def test_oversized_anonymous_multipart_never_parses_or_writes_throttle(self):
        # Baseline allocated a TemporaryUploadedFile before invalid-login 400.
        with patch.object(WSGIRequest, '_load_post_and_files',
                          side_effect=AssertionError('body parsed before limit')):
            response = self.client.post('/api/v1/auth/login', {
                'email': 'synthetic@example.test', 'password': 'wrong',
                'unwanted': SimpleUploadedFile('synthetic.bin', b'x' * (3 * 1024 * 1024)),
            }, HTTP_X_CSRFTOKEN=self.csrf)
        self.assertEqual(response.status_code, 413)
        self.assertFalse(AuthThrottle.objects.exists())
        self.assertNotIn('sessionid', self.client.cookies)

    def test_oversized_import_is_rejected_before_csrf_or_authorization(self):
        path = f'/api/v1/organizations/{uuid4()}/imports/products/preview'
        with patch.object(WSGIRequest, '_load_post_and_files',
                          side_effect=AssertionError('upload parsed before limit')):
            response = self.client.post(path, {
                'file': SimpleUploadedFile('catalog.csv', b'x' * IMPORT_LIMIT),
            }, HTTP_X_CSRFTOKEN=self.csrf)
        self.assertEqual(response.status_code, 413)

    def test_small_multipart_still_requires_csrf_and_mfa(self):
        path = f'/api/v1/organizations/{uuid4()}/imports/products/preview'
        response = self.client.post(path, {'file': SimpleUploadedFile('catalog.csv', b'name\nTest\n')})
        self.assertEqual(response.status_code, 403)
        response = self.client.post(path, {'file': SimpleUploadedFile('catalog.csv', b'name\nTest\n')},
                                    HTTP_X_CSRFTOKEN=self.csrf)
        self.assertEqual(response.status_code, 401)

    def test_legitimate_multipart_login_mfa_and_csv_with_real_csrf_checks(self):
        password = 'Synthetic-Parsing-Only-648!'
        user = User.objects.create_user('parsing@example.test', password)
        org = Organization.objects.create(name='Synthetic import tenant')
        Membership.objects.create(user=user, organization=org, role='owner')
        secret = pyotp.random_base32()
        TOTPDevice.objects.create(user=user, secret=encrypt(secret), confirmed=True)
        response = self.client.post('/api/v1/auth/login', {
            'email': user.email, 'password': password,
        }, HTTP_X_CSRFTOKEN=self.csrf)
        self.assertEqual(response.status_code, 200)
        response = self.client.post('/api/v1/auth/verify', {'code': pyotp.TOTP(secret).now()},
                                    HTTP_X_CSRFTOKEN=self.csrf)
        self.assertEqual(response.status_code, 200)
        csrf = self.client.cookies['csrftoken'].value  # Login rotates the CSRF secret.
        path = f'/api/v1/organizations/{org.pk}/imports/units/preview'
        response = self.client.post(path, {
            'file': SimpleUploadedFile('catalog.csv', b'name;symbol\nSynthetic;sy\n'),
        }, HTTP_X_CSRFTOKEN=csrf)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['batch']['counts'], {'new': 1, 'duplicate': 0, 'error': 0})

    def test_multiple_small_files_are_rejected(self):
        response = self.client.post('/api/v1/auth/login', {
            'a': SimpleUploadedFile('one.csv', b'name\nA\n'),
            'b': SimpleUploadedFile('two.csv', b'name\nB\n'),
        }, HTTP_X_CSRFTOKEN=self.csrf)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(AuthThrottle.objects.exists())
