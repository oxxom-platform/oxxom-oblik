import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from config.environment import configuration


class ConfigurationTests(unittest.TestCase):
    def environment(self, **changes):
        env = dict(DJANGO_SECRET_KEY='test-only-secret-' * 5,
                   POSTGRES_DB='test', POSTGRES_USER='test', POSTGRES_PASSWORD='test-only')
        env.update(changes)
        return env

    def test_postgres_only_and_debug_off(self):
        config = configuration(self.environment())
        self.assertEqual(config['database']['ENGINE'], 'django.db.backends.postgresql')
        self.assertFalse(config['debug'])

    def test_gss_override_is_limited_to_local_development(self):
        self.assertEqual(configuration(self.environment())['database']['OPTIONS']['gssencmode'], 'disable')
        remote = configuration(self.environment(POSTGRES_HOST='db.example'))
        self.assertNotIn('gssencmode', remote['database']['OPTIONS'])
        production = configuration(self.environment(APP_ENV='production',
            DJANGO_ALLOWED_HOSTS='oblik.example', DJANGO_CSRF_TRUSTED_ORIGINS='https://oblik.example'))
        self.assertNotIn('gssencmode', production['database']['OPTIONS'])
        self.assertNotIn('sslmode', production['database']['OPTIONS'])

    def test_no_insecure_secret_fallback(self):
        for secret in ('', 'CHANGE_ME', 'short'):
            with self.subTest(secret=secret), self.assertRaises(ValueError):
                configuration(self.environment(DJANGO_SECRET_KEY=secret))

    def test_required_database_password(self):
        with self.assertRaises(ValueError):
            configuration(self.environment(POSTGRES_PASSWORD=''))

    def test_wildcard_host_rejected(self):
        with self.assertRaises(ValueError):
            configuration(self.environment(DJANGO_ALLOWED_HOSTS='*'))

    def test_production_requires_https_origins(self):
        for origin in ('', 'http://oblik.example'):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                configuration(self.environment(APP_ENV='production',
                    DJANGO_ALLOWED_HOSTS='oblik.example', DJANGO_CSRF_TRUSTED_ORIGINS=origin))

    def test_production_rejects_debug(self):
        with self.assertRaises(ValueError):
            configuration(self.environment(APP_ENV='production', DJANGO_DEBUG='true',
                DJANGO_ALLOWED_HOSTS='oblik.example', DJANGO_CSRF_TRUSTED_ORIGINS='https://oblik.example'))

    def test_valid_production_configuration(self):
        self.assertEqual(configuration(self.environment(APP_ENV='production',
            DJANGO_ALLOWED_HOSTS='oblik.example',
            DJANGO_CSRF_TRUSTED_ORIGINS='https://oblik.example'))['mode'], 'production')

    def test_invalid_mode_and_port_rejected(self):
        for values in ({'APP_ENV': 'prodution'}, {'POSTGRES_PORT': '0'}, {'POSTGRES_PORT': '65536'}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                configuration(self.environment(**values))


if __name__ == '__main__':
    unittest.main()
