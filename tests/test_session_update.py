import importlib.util
import io
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


update = load('session_update', ROOT / 'deploy/ubuntu/apply-session-security.py')
guard = load('session_guard', ROOT / update.GUARD)


class SessionUpdateTests(unittest.TestCase):
    def archive(self, folder, corrupt=False, symlink=False, duplicate=False, traversal=False):
        path = Path(folder) / 'release.tar.gz'
        with tarfile.open(path, 'w:gz') as bundle:
            for name in [*update.FILES, update.GUARD]:
                data = (ROOT / name).read_bytes()
                if corrupt and name == 'backend/accounts/session_security.py':
                    data += b'# unreviewed change'
                item = tarfile.TarInfo('reviewed/' + name)
                item.size = len(data)
                if symlink and name == 'backend/accounts/session_security.py':
                    item.type = tarfile.SYMTYPE
                    item.linkname = '/etc/passwd'
                bundle.addfile(item, io.BytesIO(data))
                if duplicate and name == update.GUARD:
                    bundle.addfile(item, io.BytesIO(data))
            if traversal:
                item = tarfile.TarInfo('reviewed/../escape.py')
                bundle.addfile(item, io.BytesIO(b''))
        return path

    def test_pinned_package_rejects_tampering_links_duplicates_and_traversal(self):
        with tempfile.TemporaryDirectory() as folder:
            helper, payload = update.package(self.archive(folder))
            self.assertEqual(len(payload), 9)
            self.assertEqual(set(payload), set(update.FILES))
            self.assertEqual(update.sha(helper), update.GUARD_SHA)
            self.assertFalse(any('/migrations/' in name or '.env' in name for name in payload))
            for options in ({'corrupt': True}, {'symlink': True},
                            {'duplicate': True}, {'traversal': True}):
                with self.subTest(options=options), self.assertRaises(ValueError):
                    update.package(self.archive(folder, **options))

    def test_installed_package_is_idempotent_and_unknown_edit_fails_before_write(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            payload = {name: (ROOT / name).read_bytes() for name in update.FILES}
            for name, data in payload.items():
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_bytes(data)
            self.assertEqual(guard.verify_baseline(root, {'files': update.FILES}, payload), payload)
            name = 'backend/config/settings.py'
            (root / name).write_bytes(b'unknown server customization')
            before = {key: (root / key).read_bytes() for key in payload}
            with self.assertRaises(guard.PreflightError):
                guard.verify_baseline(root, {'files': update.FILES}, payload)
            self.assertEqual(before, {key: (root / key).read_bytes() for key in payload})

    def test_failed_health_check_restores_every_existing_file_and_removes_new_module(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            payload = {name: (ROOT / name).read_bytes() for name in update.FILES}
            originals, info = {}, {}
            defaults = root / 'mode.py'
            defaults.write_bytes(b'permissions fixture')
            defaults.chmod(0o640)
            for name in payload:
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                originals[name] = None if update.FILES[name]['before'] is None else b'old:' + name.encode()
                if originals[name] is not None:
                    target.write_bytes(originals[name])
                    target.chmod(0o640)
                info[name] = target.stat() if target.exists() else defaults.stat()

            def fail():
                self.assertTrue(all((root / name).read_bytes() == value for name, value in payload.items()))
                raise RuntimeError('synthetic failed health check')

            with self.assertRaises(RuntimeError):
                guard.apply_payload(root, payload, originals, info, fail)
            for name, original in originals.items():
                with self.subTest(name=name):
                    if original is None:
                        self.assertFalse((root / name).exists())
                    else:
                        self.assertEqual((root / name).read_bytes(), original)
                        self.assertEqual((root / name).stat().st_mode & 0o777, 0o640)

    def test_changed_maintenance_helper_is_not_executed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            for name in update.SUPPORT:
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_bytes((ROOT / name).read_bytes())
            update.check_support(guard, root)
            (root / next(iter(update.SUPPORT))).write_bytes(b'unreviewed helper')
            with self.assertRaises(guard.PreflightError):
                update.check_support(guard, root)

    def test_service_checks_use_unprivileged_identity(self):
        with patch.object(update.subprocess, 'run') as run:
            update.service_check(io.StringIO())
        self.assertEqual(run.call_count, 2)
        for call in run.call_args_list:
            self.assertEqual(call.args[0][:4], ['runuser', '-u', 'oxxom', '--'])

    def test_launcher_rejects_unpinned_revisions_before_any_download(self):
        path = ROOT / 'deploy/ubuntu/update-session-security.sh'
        for revision in ('main', '16a844e', '../main', 'g' * 40):
            with self.subTest(revision=revision):
                result = subprocess.run(['sh', str(path), revision], capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('commit SHA', result.stderr)
