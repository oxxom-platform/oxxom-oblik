import hashlib
import importlib.util
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('runtime_inventory', ROOT / 'deploy/ubuntu/collect-runtime.py')
inventory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inventory)


def lock_line(name='example', version='1.0'):
    return name + '==' + version + ' --hash=sha256:' + 'a' * 64 + '\n'


class RuntimeInventoryTests(unittest.TestCase):
    def raw(self, **overrides):
        data = {'python': [3, 12, 12], 'os': 'Linux', 'machine': 'x86_64', 'virtualenv': True,
                'packages': [{'name': 'example', 'version': '1.0', 'direct_url_present': False}]}
        data.update(overrides)
        return data

    def test_strict_lock_rejects_unhashed_unpinned_urls_includes_and_duplicate_aliases(self):
        self.assertEqual(inventory.parse_lock(lock_line('Example[binary]')), {'example': '1.0'})
        invalid = ['example==1.0', 'example>=1.0', '-r private.txt',
                   'example @ https://user:secret@example.invalid/file.whl',
                   '--trusted-host example.invalid', lock_line('a_b') + lock_line('a-b'),
                   lock_line() + 'incomplete\\', lock_line().replace('a' * 64, 'a' * 63)]
        for text in invalid:
            with self.subTest(text=text), self.assertRaises(ValueError):
                inventory.parse_lock(text)

    def test_match_requires_supported_target_virtualenv_and_exact_package_set(self):
        self.assertEqual(inventory.compare({'example': '1.0'}, self.raw())['status'], 'MATCH')
        for override in ({'python': [3, 11, 9]}, {'os': 'Darwin'}, {'machine': 'aarch64'},
                         {'virtualenv': False}, {'packages': []}):
            with self.subTest(override=override):
                self.assertEqual(inventory.compare({'example': '1.0'}, self.raw(**override))['status'], 'DRIFT')
        bootstrap = self.raw()
        bootstrap['packages'].append({'name': 'pip', 'version': '26.3', 'direct_url_present': False})
        self.assertEqual(inventory.compare({'example': '1.0'}, bootstrap)['status'], 'MATCH')

    def test_sources_conflicts_duplicates_and_extra_packages_fail_without_private_metadata(self):
        canary = 'SYNTHETIC-CREDENTIAL-CANARY'
        cases = [
            [{'name': 'example', 'version': '2.0', 'direct_url_present': False}],
            [{'name': 'example', 'version': '1.0', 'direct_url_present': True, 'direct_url': canary}],
            self.raw()['packages'] * 2,
            self.raw()['packages'] + [{'name': canary, 'version': '1.0', 'direct_url_present': True}],
            [{'name': 'example', 'version': 'https://user:' + canary, 'direct_url_present': False}],
        ]
        for rows in cases:
            with self.subTest(rows=rows):
                result = inventory.compare({'example': '1.0'}, self.raw(packages=rows))
                self.assertEqual(result['status'], 'DRIFT')
                self.assertNotIn(canary, json.dumps(result))

    def test_runtime_errors_are_redacted_and_only_isolated_metadata_child_is_run(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'runtime.lock'
            path.write_text(lock_line())
            result = type('Result', (), {'returncode': 1, 'stdout': 'PRIVATE-CANARY', 'stderr': 'PRIVATE-CANARY'})()
            with patch.object(inventory.subprocess, 'run', return_value=result) as run, patch('sys.stdout', new_callable=io.StringIO) as output:
                self.assertEqual(inventory.main(['--lock', str(path)]), 2)
            self.assertNotIn('PRIVATE-CANARY', output.getvalue())
            self.assertEqual(run.call_args.args[0][:4], [inventory.PYTHON, '-I', '-B', '-c'])
            self.assertNotIn('django', run.call_args.args[0][4])
            self.assertNotIn('app.env', run.call_args.args[0][4])

    def test_repository_lock_covers_direct_pins_and_matches_generation_receipt(self):
        lock = inventory.parse_lock((ROOT / 'requirements-production.lock').read_text())
        for file in ('requirements.txt', 'requirements-production.txt'):
            for raw in (ROOT / file).read_text().splitlines():
                line = raw.split('#', 1)[0].strip()
                if not line or line == '-r requirements.txt':
                    continue
                match = re.fullmatch(r'([A-Za-z0-9_-]+)(?:\[[A-Za-z0-9_,.-]+\])?==([\w.!+-]+)', line)
                self.assertIsNotNone(match)
                self.assertEqual(lock[inventory.canonical(match[1])], match[2])
        receipt = json.loads((ROOT / 'docs/security/RUNTIME-LOCK-BUILD.json').read_text())
        for name, digest in receipt['sha256'].items():
            self.assertEqual(hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), digest)

    def test_launcher_pins_collector_and_lock_and_rejects_symbolic_revisions(self):
        path = ROOT / 'deploy/ubuntu/check-runtime.sh'
        source = path.read_text()
        for variable, name in [('collector_sha', 'deploy/ubuntu/collect-runtime.py'),
                               ('lock_sha', 'requirements-production.lock')]:
            digest = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            self.assertIn(variable + "='" + digest + "'", source)
        for revision in ('main', '123abcd', '../main', 'g' * 40):
            result = subprocess.run(['sh', str(path), revision], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('commit SHA', result.stderr)

    def test_pip_rejects_tampered_wheel_offline(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            wheel = root / 'safehashfixture-1.0-py3-none-any.whl'
            with zipfile.ZipFile(wheel, 'w') as archive:
                archive.writestr('safehashfixture-1.0.dist-info/METADATA',
                                 'Metadata-Version: 2.1\nName: safehashfixture\nVersion: 1.0\n')
                archive.writestr('safehashfixture-1.0.dist-info/WHEEL',
                                 'Wheel-Version: 1.0\nGenerator: synthetic\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
                archive.writestr('safehashfixture-1.0.dist-info/RECORD', '')
            lock = root / 'fixture.lock'
            lock.write_text('safehashfixture==1.0 --hash=sha256:' + hashlib.sha256(wheel.read_bytes()).hexdigest())
            args = [sys.executable, '-m', 'pip', '--disable-pip-version-check', '--no-cache-dir',
                    'download', '--no-index', '--no-deps', '--only-binary=:all:',
                    '--require-hashes', '--find-links', str(root), '-r', str(lock)]
            valid = subprocess.run(args + ['--dest', str(root / 'valid')], capture_output=True, text=True, timeout=30)
            self.assertEqual(valid.returncode, 0, valid.stderr)
            with wheel.open('ab') as file:
                file.write(b'synthetic wheel tampering')
            invalid = subprocess.run(args + ['--dest', str(root / 'invalid')], capture_output=True, text=True, timeout=30)
            self.assertNotEqual(invalid.returncode, 0)
            self.assertIn('DO NOT MATCH THE HASHES', invalid.stderr)
