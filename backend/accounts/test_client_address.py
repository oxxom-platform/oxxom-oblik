import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.db import close_old_connections
from django.test import Client, RequestFactory, SimpleTestCase, TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature

from .client_address import client_address
from .models import AuthThrottle, User

PROXY = '192.0.2.10'
PASSWORD = 'Synthetic-Security-Test-Password-762!'


@override_settings(OXXOM_AUTH_PROXY_IPS=(PROXY,))
class ClientAddressTests(SimpleTestCase):
    def request(self, peer=PROXY, forwarded='198.51.100.11', real='198.51.100.11'):
        return RequestFactory().post('/', REMOTE_ADDR=peer, HTTP_X_FORWARDED_FOR=forwarded, HTTP_X_REAL_IP=real)

    def test_trusted_proxy_uses_last_appended_address(self):
        self.assertEqual(client_address(self.request(forwarded='203.0.113.99, 198.51.100.11')), '198.51.100.11')

    def test_untrusted_peer_ignores_all_forwarded_values(self):
        self.assertEqual(client_address(self.request(peer='203.0.113.2', forwarded='spoof', real='spoof')), '203.0.113.2')

    def test_trusted_proxy_missing_malformed_and_conflicting_headers_fail_closed(self):
        for forwarded, real in [('', ''), ('not-an-ip', 'not-an-ip'), ('198.51.100.11', ''),
                                ('198.51.100.11', '198.51.100.12'), ('a' * 2049, '198.51.100.11'),
                                ('fe80::1%ens0', 'fe80::1%ens0')]:
            with self.subTest(forwarded=forwarded[:20]):
                with self.assertRaises(ValueError):
                    client_address(self.request(forwarded=forwarded, real=real))

    def test_ipv6_and_ipv4_mapped_addresses_are_canonical(self):
        self.assertEqual(client_address(self.request(forwarded='2001:db8:0::1', real='2001:db8::1')), '2001:db8::1')
        self.assertEqual(client_address(self.request(peer='::ffff:192.0.2.10', forwarded='::ffff:198.51.100.11')), '198.51.100.11')


@override_settings(OXXOM_AUTH_PROXY_IPS=(PROXY,), OXXOM_MFA_KEY=Fernet.generate_key().decode())
class ProxyThrottleTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('security-b@example.test', PASSWORD)

    def client_for(self, source='198.51.100.11'):
        return Client(REMOTE_ADDR=PROXY, HTTP_X_FORWARDED_FOR=source, HTTP_X_REAL_IP=source.rsplit(',', 1)[-1].strip())

    def test_failures_from_one_client_do_not_lock_out_another_behind_same_proxy(self):
        first = self.client_for()
        for _ in range(5):
            self.assertEqual(first.post('/api/v1/auth/login', {'email': 'absent@example.test', 'password': 'wrong'}).status_code, 400)
        other = self.client_for('198.51.100.22')
        self.assertEqual(other.post('/api/v1/auth/login', {'email': self.user.email, 'password': PASSWORD}).status_code, 200)

    def test_spoofed_prefix_cannot_evade_source_limit_or_allocate_account_rows(self):
        for i in range(5):
            self.client_for(f'203.0.113.{i}, 198.51.100.11').post('/api/v1/auth/login', {'email': f'absent{i}@example.test', 'password': 'wrong'})
        count = AuthThrottle.objects.count()
        for i in range(5, 9):
            response = self.client_for(f'203.0.113.{i}, 198.51.100.11').post('/api/v1/auth/login', {'email': f'absent{i}@example.test', 'password': 'wrong'}, HTTP_X_REAL_IP='198.51.100.11')
            self.assertEqual(response.status_code, 429)
        self.assertEqual(AuthThrottle.objects.count(), count)

    def test_account_limit_survives_client_change_and_window_expires(self):
        for i in range(5):
            self.client_for(f'198.51.100.{20+i}').post('/api/v1/auth/login', {'email': self.user.email, 'password': 'wrong'})
        other = self.client_for('198.51.100.99')
        self.assertEqual(other.post('/api/v1/auth/login', {'email': self.user.email, 'password': PASSWORD}).status_code, 429)
        with patch('accounts.auth_views.time.time', return_value=time.time() + 901):
            self.assertEqual(other.post('/api/v1/auth/login', {'email': self.user.email, 'password': PASSWORD}).status_code, 200)

    def test_missing_proxy_headers_reject_auth_without_creating_counters(self):
        response = Client(REMOTE_ADDR=PROXY).post('/api/v1/auth/login', {'email': self.user.email, 'password': PASSWORD})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error'], 'invalid_auth_source')
        self.assertEqual(AuthThrottle.objects.count(), 0)


@override_settings(OXXOM_AUTH_PROXY_IPS=(PROXY,), OXXOM_MFA_KEY=Fernet.generate_key().decode())
class ConcurrentProxyThrottleTests(TransactionTestCase):
    @skipUnlessDBFeature('has_select_for_update')
    def test_simultaneous_attempts_stop_after_five_failures(self):
        def attempt(i):
            close_old_connections()
            try:
                client = Client(REMOTE_ADDR=PROXY, HTTP_X_FORWARDED_FOR='198.51.100.11', HTTP_X_REAL_IP='198.51.100.11')
                return client.post('/api/v1/auth/login', {'email': f'absent{i}@example.test', 'password': 'wrong'}).status_code
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=6) as pool:
            statuses = list(pool.map(attempt, range(8)))
        self.assertEqual(statuses.count(400), 5)
        self.assertEqual(statuses.count(429), 3)
        self.assertEqual(AuthThrottle.objects.count(), 6)
