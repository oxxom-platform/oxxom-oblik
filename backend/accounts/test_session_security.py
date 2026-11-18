import json
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from cryptography.fernet import Fernet
import pyotp
from django.contrib.auth.hashers import make_password
from django.db import close_old_connections, transaction
from django.test import Client, RequestFactory, TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature

from .mfa import encrypt
from .models import AuthThrottle, RecoveryCode, TOTPDevice, User
from .session_security import locked_session_user


URL = '/api/v1/auth/sessions/revoke-others'


def session_client(user, started=None, csrf=False):
    client = Client(enforce_csrf_checks=csrf)
    client.force_login(user)
    session = client.session
    started = time.time() - 86400 if started is None else started
    session['mfa_started'] = session['mfa_last'] = started
    session.set_expiry(datetime.fromtimestamp(started, timezone.utc) + timedelta(days=7))
    session.save()
    return client


@override_settings(OXXOM_MFA_KEY=Fernet.generate_key().decode())
class SessionSecurityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.password = 'Synthetic-Session-Only-749!'
        cls.user = User.objects.create_user('sessions@example.test', cls.password)
        cls.other = User.objects.create_user('unrelated@example.test', cls.password)
        cls.secret = pyotp.random_base32()
        TOTPDevice.objects.create(user=cls.user, secret=encrypt(cls.secret), confirmed=True)

    def setUp(self):
        self.current = session_client(self.user)
        self.second = session_client(self.user)
        self.unrelated = session_client(self.other)

    def code(self):
        return pyotp.TOTP(self.secret).now()

    def test_revocation_keeps_current_expiry_and_other_users_but_denies_old_cookies(self):
        old_key = self.current.cookies['sessionid'].value
        started = self.current.session['mfa_started']
        expiry = self.current.session.get_expiry_date()
        before_stamp = self.user.security_stamp
        response = self.current.post(URL, {'code': self.code()})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'other_sessions_revoked'})
        self.user.refresh_from_db()
        self.assertNotEqual(before_stamp, self.user.security_stamp)
        self.assertNotEqual(old_key, self.current.cookies['sessionid'].value)
        self.assertEqual(self.current.session['mfa_started'], started)
        self.assertEqual(self.current.session.get_expiry_date(), expiry)
        self.assertEqual(self.current.get('/api/v1/organizations').status_code, 200)
        self.assertEqual(self.second.get('/api/v1/organizations').status_code, 401)
        self.assertEqual(self.unrelated.get('/api/v1/organizations').status_code, 200)
        replay = Client(); replay.cookies['sessionid'] = old_key
        self.assertEqual(replay.get('/api/v1/organizations').status_code, 401)
        self.assertIn('no-store', response['Cache-Control'])

    def test_pending_password_challenge_cannot_be_completed_after_revocation(self):
        pending = Client()
        self.assertEqual(pending.post('/api/v1/auth/login', {
            'email': self.user.email, 'password': self.password,
        }).status_code, 200)
        self.assertEqual(self.current.post(URL, {'code': self.code()}).status_code, 200)
        response = pending.post('/api/v1/auth/verify', {'code': self.code()})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()['error'], 'password_required')

    def test_recovery_code_is_not_step_up_and_is_not_consumed(self):
        stamp = User.objects.get(pk=self.user.pk).security_stamp
        code = secrets.token_hex(8)
        RecoveryCode.objects.create(user=self.user, code_hash=make_password(code))
        response = self.current.post(URL, {'code': code})
        self.assertEqual(response.status_code, 400)
        self.assertTrue(RecoveryCode.objects.filter(user=self.user).exists())
        self.user.refresh_from_db()
        self.assertEqual(self.user.security_stamp, stamp)
        self.assertEqual(self.second.get('/api/v1/organizations').status_code, 200)

    def test_totp_cannot_be_replayed_and_failed_attempts_are_limited(self):
        code = self.code()
        self.assertEqual(self.current.post(URL, {'code': code}).status_code, 200)
        for _ in range(5):
            self.assertEqual(self.current.post(URL, {'code': code}).status_code, 400)
        self.assertEqual(self.current.post(URL, {'code': code}).status_code, 429)
        self.assertEqual(self.current.get('/api/v1/organizations').status_code, 200)

    def test_mfa_and_csrf_are_required(self):
        self.assertEqual(Client().post(URL, {'code': self.code()}).status_code, 401)
        password_only = Client(); password_only.force_login(self.user)
        self.assertEqual(password_only.post(URL, {'code': self.code()}).status_code, 401)
        strict = session_client(self.user, csrf=True)
        strict.get('/login/?security=1')
        self.assertEqual(strict.post(URL, {'code': self.code()}).status_code, 403)
        self.assertEqual(strict.post(URL, {'code': self.code()},
                                    HTTP_X_CSRFTOKEN=strict.cookies['csrftoken'].value).status_code, 200)
        self.assertEqual(strict.get('/api/v1/organizations').status_code, 200)
        self.assertEqual(strict.get(URL).status_code, 405)

    def test_wrong_mfa_key_fails_closed(self):
        stamp = User.objects.get(pk=self.user.pk).security_stamp
        with override_settings(OXXOM_MFA_KEY=Fernet.generate_key().decode()):
            self.assertEqual(self.current.post(URL, {'code': self.code()}).status_code, 503)
        self.assertEqual(User.objects.get(pk=self.user.pk).security_stamp, stamp)
        self.assertEqual(self.second.get('/api/v1/organizations').status_code, 200)

    def test_recovery_rotation_also_revokes_other_sessions(self):
        old = secrets.token_hex(8)
        RecoveryCode.objects.create(user=self.user, code_hash=make_password(old))
        response = self.current.post('/api/v1/auth/recovery/regenerate', {'code': self.code()})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()['recovery_codes']), 10)
        self.assertEqual(self.second.get('/api/v1/organizations').status_code, 401)
        self.assertEqual(self.current.get('/api/v1/organizations').status_code, 200)
        self.assertEqual(self.unrelated.get('/api/v1/organizations').status_code, 200)

    def test_stale_in_flight_session_is_rechecked_under_user_lock(self):
        request = RequestFactory().post(URL)
        request.user = self.user
        request.session = self.second.session
        User.objects.filter(pk=self.user.pk).update(security_stamp=uuid4())
        with transaction.atomic():
            self.assertIsNone(locked_session_user(request))

    def test_expired_session_cannot_revoke_other_sessions(self):
        stamp = User.objects.get(pk=self.user.pk).security_stamp
        with patch('accounts.auth_views.time.time', return_value=self.current.session['mfa_started'] + 7 * 86400):
            self.assertEqual(self.current.post(URL, {'code': self.code()}).status_code, 401)
        self.assertEqual(User.objects.get(pk=self.user.pk).security_stamp, stamp)
        self.assertEqual(TOTPDevice.objects.get(user=self.user).last_step, -1)
        self.assertEqual(self.second.get('/api/v1/organizations').status_code, 200)

    def test_recovery_regeneration_rejects_session_overtaken_by_revocation(self):
        from .session_security import locked_session_user as recheck

        def revoke_before_lock(request):
            User.objects.filter(pk=self.user.pk).update(security_stamp=uuid4())
            return recheck(request)

        code = secrets.token_hex(8)
        RecoveryCode.objects.create(user=self.user, code_hash=make_password(code))
        with patch('accounts.auth_views.locked_session_user', side_effect=revoke_before_lock):
            response = self.current.post('/api/v1/auth/recovery/regenerate', {'code': self.code()})
        self.assertEqual(response.status_code, 401)
        self.assertTrue(RecoveryCode.objects.filter(user=self.user).exists())
        self.assertEqual(TOTPDevice.objects.get(user=self.user).last_step, -1)

    def test_failure_rolls_back_stamp_totp_and_current_session_rotation(self):
        stamp = self.user.security_stamp
        key = self.current.cookies['sessionid'].value
        with patch('accounts.auth_views.security_event', side_effect=RuntimeError('synthetic audit failure')):
            with self.assertRaises(RuntimeError):
                self.current.post(URL, {'code': self.code()})
        self.assertEqual(User.objects.get(pk=self.user.pk).security_stamp, stamp)
        self.assertEqual(TOTPDevice.objects.get(user=self.user).last_step, -1)
        replay = Client(); replay.cookies['sessionid'] = key
        self.assertEqual(replay.get('/api/v1/organizations').status_code, 200)
        self.assertEqual(self.second.get('/api/v1/organizations').status_code, 200)

    def test_event_contains_only_approved_metadata(self):
        code = self.code()
        with self.assertLogs('oxxom.security', level='INFO') as log, self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.current.post(URL, {'code': code}).status_code, 200)
        message = json.loads(log.records[0].getMessage())
        self.assertEqual(message, {'event': 'sessions.revoke_others',
                                  'user_id': str(self.user.pk), 'result': 'success'})
        for private in (self.secret, code, self.password, self.user.email):
            self.assertNotIn(private, log.records[0].getMessage())

    def test_security_page_resumes_current_session_and_contains_explicit_action(self):
        before = self.current.cookies['sessionid'].value
        response = self.current.get('/login/?security=1')
        self.assertEqual(response.status_code, 200)
        self.assertIs(response.context['session_verified'], True)
        self.assertContains(response, 'id="revoke-sessions"')
        self.assertContains(response, 'id="session-code"')
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(self.current.cookies['sessionid'].value, before)
        self.assertIs(Client().get('/login/?security=1').context['session_verified'], False)


@override_settings(OXXOM_MFA_KEY=Fernet.generate_key().decode())
class SessionConcurrencyTests(TransactionTestCase):
    @skipUnlessDBFeature('has_select_for_update')
    def test_simultaneous_step_up_does_not_resurrect_revoked_session(self):
        user = User.objects.create_user('concurrent-sessions@example.test', 'Synthetic-Concurrent-753!')
        secret = pyotp.random_base32()
        TOTPDevice.objects.create(user=user, secret=encrypt(secret), confirmed=True)
        clients = [session_client(user), session_client(user)]
        barrier = Barrier(2)
        code = pyotp.TOTP(secret).now()
        def run(client):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return client.post(URL, {'code': code}).status_code
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(run, clients))
        self.assertEqual(statuses.count(200), 1)
        self.assertTrue(all(status in (200, 400, 401) for status in statuses))
        self.assertEqual(sum(client.get('/api/v1/organizations').status_code == 200 for client in clients), 1)
