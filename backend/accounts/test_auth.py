import time
import base64
import xml.etree.ElementTree as ET
from unittest.mock import patch
import pyotp
from cryptography.fernet import Fernet
from django.test import TestCase, Client, override_settings
from django.db import connection
from .models import User, Organization, Membership, TOTPDevice, RecoveryCode
from .mfa import encrypt


@override_settings(OXXOM_MFA_KEY=Fernet.generate_key().decode())
class AuthenticationTests(TestCase):
    password = 'Only-For-Tests-Password-879!'

    def setUp(self):
        self.user = User.objects.create_user('owner@example.com', self.password)
        self.org = Organization.objects.create(name='Private A')
        Organization.objects.create(name='Private B')
        Membership.objects.create(user=self.user, organization=self.org, role=Membership.Role.OWNER)
        self.secret = pyotp.random_base32()

    def password_step(self, client=None):
        return (client or self.client).post('/api/v1/auth/login', {'email': self.user.email, 'password': self.password})

    def device(self):
        return TOTPDevice.objects.create(user=self.user, secret=encrypt(self.secret), confirmed=True)

    def full_login(self):
        self.device()
        self.password_step()
        result = self.client.post('/api/v1/auth/verify', {'code': pyotp.TOTP(self.secret).now()})
        self.assertEqual(result.status_code, 200)

    def test_password_alone_and_force_login_do_not_grant_access(self):
        self.assertEqual(self.password_step().status_code, 200)
        self.assertEqual(self.client.get('/api/v1/organizations').status_code, 401)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get('/api/v1/organizations').status_code, 401)

    def test_setup_confirmation_encrypts_secret_and_issues_ten_hashed_codes(self):
        self.assertEqual(self.password_step().json()['next'], 'setup')
        response = self.client.post('/api/v1/auth/setup')
        secret = response.json()['secret']
        data = response.json()
        self.assertTrue(data['qr_code'].startswith('data:image/svg+xml;base64,'))
        svg = ET.fromstring(base64.b64decode(data['qr_code'].split(',', 1)[1]))
        self.assertEqual(svg.tag, '{http://www.w3.org/2000/svg}svg')
        self.assertTrue(svg.findall('.//{http://www.w3.org/2000/svg}path'))
        parsed = pyotp.parse_uri(data['uri'])
        self.assertEqual(parsed.secret, secret)
        self.assertEqual(parsed.issuer, 'OXXOM OBLIK')
        self.assertEqual(parsed.name, self.user.email)
        self.assertIn('no-store', response['Cache-Control'])
        self.assertNotIn(secret, TOTPDevice.objects.get().secret)
        result = self.client.post('/api/v1/auth/verify', {'code': pyotp.TOTP(secret).now()})
        self.assertEqual(result.status_code, 200)
        codes = result.json()['recovery_codes']
        self.assertEqual(len(codes), 10)
        self.assertNotIn(codes[0], list(RecoveryCode.objects.values_list('code_hash', flat=True)))
        self.assertTrue(TOTPDevice.objects.get().confirmed)
        self.assertEqual(self.client.post('/api/v1/auth/setup').status_code, 401)
        orgs = self.client.get('/api/v1/organizations').json()['organizations']
        self.assertEqual([o['name'] for o in orgs], ['Private A'])

    def test_qr_setup_requires_password_and_never_reveals_configured_devices(self):
        response = self.client.post('/api/v1/auth/setup')
        self.assertEqual(response.status_code, 401)
        self.assertNotIn('qr_code', response.json())
        self.device()
        self.password_step()
        response = self.client.post('/api/v1/auth/setup')
        self.assertEqual(response.status_code, 409)
        self.assertNotIn('qr_code', response.json())

    def test_unconfirmed_qr_is_stable_on_retry(self):
        self.password_step()
        first = self.client.post('/api/v1/auth/setup').json()
        second = self.client.post('/api/v1/auth/setup').json()
        self.assertEqual(first['secret'], second['secret'])
        self.assertEqual(first['qr_code'], second['qr_code'])
        self.assertEqual(TOTPDevice.objects.filter(user=self.user).count(), 1)

    def test_login_shows_qr_without_manual_secret_and_security_mode_is_explicit(self):
        response = self.client.get('/login/')
        self.assertContains(response, 'id="totp-qr"')
        self.assertNotContains(response, 'id="secret"')
        self.assertContains(response, '/assets/auth/login.js')
        self.assertIs(response.context['security_mode'], False)
        response = self.client.get('/login/?security=1')
        self.assertIs(response.context['security_mode'], True)

    def test_wrong_expired_and_replayed_totp_denied(self):
        self.device()
        self.password_step()
        expired = pyotp.TOTP(self.secret).at(time.time() - 120)
        self.assertEqual(self.client.post('/api/v1/auth/verify', {'code': expired}).status_code, 400)
        self.assertEqual(self.client.post('/api/v1/auth/verify', {'code': 'wrong'}).status_code, 400)
        code = pyotp.TOTP(self.secret).now()
        self.assertEqual(self.client.post('/api/v1/auth/verify', {'code': code}).status_code, 200)
        other = Client()
        self.password_step(other)
        self.assertEqual(other.post('/api/v1/auth/verify', {'code': code}).status_code, 400)

    def test_recovery_code_is_single_use_and_regeneration_revokes_old_codes(self):
        self.password_step()
        setup = self.client.post('/api/v1/auth/setup').json()
        secret = setup['secret']
        response = self.client.post('/api/v1/auth/verify', {'code': pyotp.TOTP(secret).now()})
        old_codes = response.json()['recovery_codes']
        self.client.post('/api/v1/auth/logout')
        self.password_step()
        self.assertEqual(self.client.post('/api/v1/auth/verify', {'code': old_codes[0]}).status_code, 200)
        self.assertEqual(RecoveryCode.objects.count(), 9)
        other = Client()
        self.password_step(other)
        self.assertEqual(other.post('/api/v1/auth/verify', {'code': old_codes[0]}).status_code, 400)
        future = time.time() + 60
        with patch('accounts.mfa.time.time', return_value=future):
            response = self.client.post('/api/v1/auth/recovery/regenerate', {'code': pyotp.TOTP(secret).at(future)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()['recovery_codes']), 10)
        self.password_step(other)
        self.assertEqual(other.post('/api/v1/auth/verify', {'code': old_codes[1]}).status_code, 400)

    def test_five_password_failures_limit_account_even_from_different_source(self):
        for _ in range(5):
            self.assertEqual(self.client.post('/api/v1/auth/login', {'email': self.user.email, 'password': 'wrong'}).status_code, 400)
        self.assertEqual(self.client.post('/api/v1/auth/login', {'email': self.user.email, 'password': self.password}, REMOTE_ADDR='192.0.2.99').status_code, 429)
        with patch('accounts.auth_views.time.time', return_value=time.time() + 901):
            self.assertEqual(self.password_step().status_code, 200)

    def test_source_limit_applies_across_accounts(self):
        for i in range(5):
            self.client.post('/api/v1/auth/login', {'email': f'absent{i}@example.com', 'password': 'wrong'})
        self.assertEqual(self.password_step().status_code, 429)

    def test_mfa_failures_are_limited(self):
        self.device()
        self.password_step()
        for _ in range(5):
            self.assertEqual(self.client.post('/api/v1/auth/verify', {'code': 'bad'}).status_code, 400)
        self.assertEqual(self.client.post('/api/v1/auth/verify', {'code': pyotp.TOTP(self.secret).now()}).status_code, 429)

    def test_challenge_expires_and_password_change_invalidates_it(self):
        self.password_step()
        session = self.client.session
        session['pending_started'] = time.time() - 301
        session.save()
        self.assertEqual(self.client.post('/api/v1/auth/setup').status_code, 401)
        self.password_step()
        self.user.set_password('New-Password-For-Tests-782!')
        self.user.save()
        self.assertEqual(self.client.post('/api/v1/auth/setup').status_code, 401)

    def test_session_persists_for_seven_days_without_an_idle_timeout(self):
        self.full_login()
        session = self.client.session
        self.assertFalse(session.get_expire_at_browser_close())
        self.assertGreater(session.get_expiry_age(), 604780)
        self.assertLessEqual(session.get_expiry_age(), 604800)
        # Six inactive days do not sign the employee out.
        session['mfa_started'] = session['mfa_last'] = time.time() - 6 * 86400
        session.save()
        self.assertEqual(self.client.get('/api/v1/organizations').status_code, 200)
        remaining = self.client.session.get_expiry_age()
        self.assertGreater(remaining, 86380)
        self.assertLessEqual(remaining, 86400)
        # Activity does not extend the original seven-day deadline.
        session = self.client.session
        session['mfa_started'] = time.time() - 604801
        session['mfa_last'] = time.time()
        session.save()
        self.assertEqual(self.client.get('/api/v1/organizations').status_code, 401)

    def test_password_change_revokes_full_session(self):
        self.full_login()
        self.user.set_password('Changed-For-Tests-Password-792!')
        self.user.save()
        self.assertEqual(self.client.get('/api/v1/organizations').status_code, 401)

    def test_disable_then_reenable_does_not_restore_session(self):
        self.full_login()
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])
        self.user.is_active = True
        self.user.save(update_fields=['is_active'])
        self.assertEqual(self.client.get('/api/v1/organizations').status_code, 401)

    def test_membership_revocation_is_immediate(self):
        self.full_login()
        Membership.objects.filter(user=self.user).update(is_active=False)
        self.assertEqual(self.client.get('/api/v1/organizations').json()['organizations'], [])

    def test_csrf_is_required_on_password_setup_verify_logout(self):
        client = Client(enforce_csrf_checks=True)
        for route in ['login', 'setup', 'verify', 'logout', 'recovery/regenerate']:
            self.assertEqual(client.post('/api/v1/auth/' + route).status_code, 403)
        self.assertEqual(client.get('/login/').status_code, 200)
        token = client.cookies['csrftoken'].value
        response = client.post('/api/v1/auth/login', {'email': self.user.email, 'password': self.password}, HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 200)

    def test_missing_key_and_wrong_key_fail_closed(self):
        with override_settings(OXXOM_MFA_KEY=''):
            self.assertEqual(self.password_step().status_code, 503)
        self.device()
        self.password_step()
        with override_settings(OXXOM_MFA_KEY=Fernet.generate_key().decode()):
            self.assertEqual(self.client.post('/api/v1/auth/verify', {'code': '123456'}).status_code, 503)
        self.assertEqual(self.client.get('/api/v1/organizations').status_code, 401)


from concurrent.futures import ThreadPoolExecutor
from django.db import close_old_connections
from django.test import TransactionTestCase, skipUnlessDBFeature


@override_settings(OXXOM_MFA_KEY=Fernet.generate_key().decode())
class ConcurrentMFAAcceptanceTests(TransactionTestCase):
    @skipUnlessDBFeature('has_select_for_update')
    def test_two_simultaneous_requests_cannot_accept_same_totp(self):
        user = User.objects.create_user('race@example.com', 'Only-For-Tests-Password-879!')
        secret = pyotp.random_base32()
        TOTPDevice.objects.create(user=user, secret=encrypt(secret), confirmed=True)
        clients = [Client(), Client()]
        for client in clients:
            result = client.post('/api/v1/auth/login', {'email': user.email, 'password': 'Only-For-Tests-Password-879!'})
            self.assertEqual(result.status_code, 200)
        instant = time.time()
        code = pyotp.TOTP(secret).at(instant)

        def verify(client):
            close_old_connections()
            try:
                return client.post('/api/v1/auth/verify', {'code': code}).status_code
            finally:
                close_old_connections()

        with patch('accounts.mfa.time.time', return_value=instant), ThreadPoolExecutor(max_workers=2) as executor:
            statuses = list(executor.map(verify, clients))
        self.assertEqual(sorted(statuses), [200, 400])
