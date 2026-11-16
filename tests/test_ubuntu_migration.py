"""Transfer safety checks; use synthetic secrets and mocked external commands."""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'deploy/ubuntu'))
from environment_file import domain_name, read_env
from cryptography.fernet import Fernet


def module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


transfer = module('ubuntu_transfer', 'scripts/prepare-ubuntu-transfer.py')
restore = module('ubuntu_restore', 'deploy/ubuntu/restore-first-install.py')
git_check = module('ubuntu_git_check', 'scripts/check-git-content.py')


class MigrationConfigurationTests(unittest.TestCase):
    def test_domain_rejects_nginx_injection_ip_and_url(self):
        for value in ('https://oblik.example', '127.0.0.1', 'bad;include.example',
                      'oblik.example/path', '*.example', 'a..example', '-a.example'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                domain_name(value)
        self.assertEqual(domain_name('Oblik.Example.'), 'oblik.example')

    def test_environment_parser_never_evaluates_shell(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'sample.env'
            path.write_text('# comment\nKEY=$(id);echo\nVALUE=hello=world\n')
            self.assertEqual(read_env(path), {'KEY': '$(id);echo', 'VALUE': 'hello=world'})
            path.write_text('KEY=synthetic-secret\nKEY=another\n')
            with self.assertRaises(ValueError) as error:
                read_env(path)
            self.assertNotIn('synthetic-secret', str(error.exception))

    def test_preserves_django_mfa_keys_and_separates_database_role(self):
        key = Fernet.generate_key().decode()
        old = {'DJANGO_SECRET_KEY': 'synthetic-test-secret-' * 4, 'POSTGRES_PASSWORD': 'old-password'}
        env = transfer.production_values(old, key, 'oblik.example')
        self.assertEqual(env['OXXOM_MFA_KEY'], key)
        self.assertEqual(env['DJANGO_SECRET_KEY'], old['DJANGO_SECRET_KEY'])
        self.assertNotEqual(env['POSTGRES_PASSWORD'], old['POSTGRES_PASSWORD'])
        self.assertEqual(env['POSTGRES_USER'], 'oxxom_oblik_app')
        self.assertEqual(env['POSTGRES_PORT'], '5432')
        self.assertEqual(env['DJANGO_CSRF_TRUSTED_ORIGINS'], 'https://oblik.example')
        self.assertEqual(env['APP_ENV'], 'production')
        self.assertEqual(env['TRUST_PROXY'], 'true')

    def test_invalid_mfa_key_rejected(self):
        with self.assertRaises(ValueError):
            transfer.production_values({'DJANGO_SECRET_KEY': 'synthetic' * 10}, 'wrong', 'oblik.example')


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / 'repo'
        self.root.mkdir()
        (self.root / '.env').write_text('APP_ENV=development\nPOSTGRES_DB=test\nPOSTGRES_USER=test\nDJANGO_SECRET_KEY=' + 'synthetic-test-secret-' * 4 + '\n')
        (self.root / '.secrets').mkdir()
        self.key = Fernet.generate_key().decode()
        (self.root / '.secrets/mfa.key').write_text(self.key)
        self.output = self.root.parent / 'transfer'
        self.patch = patch.object(transfer, 'ROOT', self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def external(self, argv, **kwargs):
        if 'pg_dump' in argv[-1]:
            kwargs['stdout'].write(b'PGDMP-synthetic-fixture')
        return subprocess.CompletedProcess(argv, 0, stdout='test\ntest\n')

    def test_export_is_private_validated_and_does_not_print_keys(self):
        printed = io.StringIO()
        with patch.object(transfer.subprocess, 'run', side_effect=self.external) as run, contextlib.redirect_stdout(printed):
            transfer.export('oblik.example', self.output)
        self.assertEqual(len(run.call_args_list), 3)
        self.assertIn('pg_restore', run.call_args_list[2].args[0])
        self.assertEqual(os.stat(self.output).st_mode & 0o777, 0o700)
        for name in ('app.env', 'database.dump'):
            self.assertEqual(os.stat(self.output / name).st_mode & 0o777, 0o600)
        self.assertEqual(read_env(self.output / 'app.env')['OXXOM_MFA_KEY'], self.key)
        self.assertNotIn(self.key, printed.getvalue())
        self.assertNotIn('synthetic-test-secret-', printed.getvalue())

    def test_failed_export_removes_partial_package(self):
        with patch.object(transfer.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'pg_dump')):
            with self.assertRaises(subprocess.CalledProcessError):
                transfer.export('oblik.example', self.output)
        self.assertFalse(self.output.exists())

    def test_existing_directory_and_repository_destination_rejected(self):
        with self.assertRaises(ValueError):
            transfer.export('oblik.example', self.root / 'transfer')
        self.output.mkdir()
        (self.output / 'keep').write_text('keep')
        with self.assertRaises(FileExistsError):
            transfer.export('oblik.example', self.output)
        self.assertEqual((self.output / 'keep').read_text(), 'keep')

    def test_invalid_archive_removes_package(self):
        def invalid(argv, **kwargs):
            result = self.external(argv, **kwargs)
            if 'pg_restore' in argv:
                raise subprocess.CalledProcessError(1, argv)
            return result
        with patch.object(transfer.subprocess, 'run', side_effect=invalid):
            with self.assertRaises(subprocess.CalledProcessError):
                transfer.export('oblik.example', self.output)
        self.assertFalse(self.output.exists())

    def test_database_identity_mismatch_never_exports(self):
        with patch.object(transfer.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, stdout='another\nuser\n')) as run:
            with self.assertRaises(ValueError):
                transfer.export('oblik.example', self.output)
        self.assertEqual(run.call_count, 1)
        self.assertFalse(self.output.exists())


class FirstRestoreTests(unittest.TestCase):
    def env(self):
        return {'APP_ENV': 'production', 'POSTGRES_HOST': '127.0.0.1', 'POSTGRES_PORT': '5432',
                'POSTGRES_DB': 'oxxom_test', 'POSTGRES_USER': 'oxxom_test_app',
                'POSTGRES_PASSWORD': 'synthetic-test-password'}

    def test_existing_database_or_role_refuses_mutation(self):
        with patch.object(restore.os, 'geteuid', return_value=0), patch.object(restore, 'read_env', return_value=self.env()), patch.object(restore.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, stdout='t\n')) as run:
            with self.assertRaises(ValueError):
                restore.restore(Path('unused.env'), Path('unused.dump'))
        self.assertEqual(run.call_count, 1)
        self.assertTrue(run.call_args.kwargs['input'].startswith('SELECT EXISTS'))

    def test_new_database_restore_is_single_transaction_and_no_superuser(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / 'synthetic.dump'
            archive.write_bytes(b'PGDMP-synthetic')
            with patch.object(restore.os, 'geteuid', return_value=0), patch.object(restore, 'read_env', return_value=self.env()), patch.object(restore.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, stdout='f\n')) as run, contextlib.redirect_stdout(io.StringIO()):
                restore.restore(Path('unused.env'), archive)
        self.assertEqual(run.call_count, 4)
        create_sql = run.call_args_list[2].kwargs['input']
        self.assertIn('NOSUPERUSER NOCREATEDB NOCREATEROLE', create_sql)
        self.assertNotIn('DROP ', create_sql)
        command = run.call_args_list[3].args[0]
        self.assertIn('--single-transaction', command)
        self.assertIn('--exit-on-error', command)
        self.assertNotIn('synthetic-test-password', ' '.join(command))


class GitContentTests(unittest.TestCase):
    def test_progress_is_printed_before_git_reads_each_file(self):
        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            def git_response(arguments, **kwargs):
                self.assertLessEqual(kwargs['timeout'], git_check.GIT_TIMEOUT)
                if arguments[1] == 'diff':
                    self.assertIn('Reading staged file list', output.getvalue())
                    return b'backend/example.py\0'
                self.assertIn('[1/1] backend/example.py', output.getvalue())
                return b'ordinary source code'
            with patch.object(git_check, 'ROOT', Path(directory)), patch.object(git_check.subprocess, 'check_output', side_effect=git_response), contextlib.redirect_stdout(output):
                self.assertEqual(git_check.main(), 0)
            self.assertIn('Checked 1 staged files', output.getvalue())

    def test_git_timeout_stops_check_without_printing_subprocess_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            failure = subprocess.TimeoutExpired(['git', 'show'], 15, output=b'sensitive-output')
            with patch.object(git_check, 'ROOT', Path(directory)), patch.object(git_check.subprocess, 'check_output', side_effect=[b'backend/example.py\0', failure]), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(output):
                self.assertEqual(git_check.main(), 2)
            self.assertIn('backend/example.py', output.getvalue())
            self.assertIn('Check did not complete', output.getvalue())
            self.assertNotIn('sensitive-output', output.getvalue())

    def test_git_failure_cannot_silently_skip_a_file(self):
        with tempfile.TemporaryDirectory() as directory:
            failure = subprocess.CalledProcessError(128, ['git', 'show'], output=b'sensitive-output')
            output = io.StringIO()
            with patch.object(git_check, 'ROOT', Path(directory)), patch.object(git_check.subprocess, 'check_output', side_effect=[b'backend/example.py\0', failure]), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(output):
                self.assertEqual(git_check.main(), 2)
            self.assertIn('no file was skipped', output.getvalue())
            self.assertNotIn('sensitive-output', output.getvalue())

    def test_detects_local_secret_in_source_without_printing_value(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.env').write_text('DJANGO_SECRET_KEY=synthetic-private-token\n')
            responses = [b'scripts/check-git-content.py\0', b'value="synthetic-private-token"']
            output = io.StringIO()
            with patch.object(git_check, 'ROOT', root), patch.object(git_check.subprocess, 'check_output', side_effect=responses), contextlib.redirect_stderr(output):
                self.assertEqual(git_check.main(), 1)
            self.assertIn('scripts/check-git-content.py', output.getvalue())
            self.assertNotIn('synthetic-private-token', output.getvalue())

    def test_rejects_force_added_environment_file(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(git_check, 'ROOT', Path(directory)), patch.object(git_check.subprocess, 'check_output', side_effect=[b'.env.production\0', b'EXAMPLE=value']), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(git_check.main(), 1)

    def test_scanner_and_test_sources_do_not_flag_themselves(self):
        for relative in ('scripts/check-git-content.py', 'tests/test_ubuntu_migration.py'):
            with self.subTest(path=relative), tempfile.TemporaryDirectory() as directory:
                source = (ROOT / relative).read_bytes()
                responses = [relative.encode() + b'\0', source]
                with patch.object(git_check, 'ROOT', Path(directory)), patch.object(git_check.subprocess, 'check_output', side_effect=responses), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(git_check.main(), 0)

    def test_real_private_key_formats_are_blocked_without_printing_values(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa
        rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cases = [
            (rsa_key, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()),
            (rsa_key, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()),
            (rsa_key, serialization.PrivateFormat.PKCS8, serialization.BestAvailableEncryption(b'synthetic-test-password')),
            (ec.generate_private_key(ec.SECP256R1()), serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()),
            (ed25519.Ed25519PrivateKey.generate(), serialization.PrivateFormat.OpenSSH, serialization.NoEncryption()),
        ]
        for key, key_format, encryption in cases:
            with self.subTest(format=key_format, encryption=type(encryption).__name__), tempfile.TemporaryDirectory() as directory:
                content = key.private_bytes(serialization.Encoding.PEM, key_format, encryption)
                output = io.StringIO()
                responses = [b'backend/example.py\0', content]
                with patch.object(git_check, 'ROOT', Path(directory)), patch.object(git_check.subprocess, 'check_output', side_effect=responses), contextlib.redirect_stderr(output):
                    self.assertEqual(git_check.main(), 1)
                self.assertIn('backend/example.py', output.getvalue())
                self.assertNotIn(content.decode(), output.getvalue())


if __name__ == '__main__':
    unittest.main()
