import importlib.util
import io
from pathlib import Path
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


update = load('request_update', ROOT / 'deploy/ubuntu/apply-request-limits.py')
guard = load('request_guard', ROOT / update.GUARD)


class RequestUpdateTests(unittest.TestCase):
    def archive(self, directory, corrupt=False, symlink=False):
        path = Path(directory) / 'release.tar.gz'
        with tarfile.open(path, 'w:gz') as bundle:
            for name in {*update.FILES, update.GUARD}:
                data = (ROOT / name).read_bytes()
                if corrupt and name in update.FILES:
                    data += b'# unreviewed'
                item = tarfile.TarInfo('reviewed/' + name)
                item.size = len(data)
                if symlink and name in update.FILES:
                    item.type = tarfile.SYMTYPE
                    item.linkname = '/etc/passwd'
                bundle.addfile(item, io.BytesIO(data))
        return path

    def test_package_scope_and_checksums_and_symlink_rejection(self):
        with tempfile.TemporaryDirectory() as folder:
            helper, payload = update.package(self.archive(folder))
            self.assertEqual(set(payload), {
                'backend/config/settings.py', 'backend/core/request_limits.py'})
            self.assertEqual(update.sha(helper), update.GUARD_SHA)
            for options in ({'corrupt': True}, {'symlink': True}):
                with self.subTest(options=options), self.assertRaises(ValueError):
                    update.package(self.archive(folder, **options))

    def test_unknown_baseline_fails_before_write(self):
        with tempfile.TemporaryDirectory() as folder:
            app = Path(folder).resolve()
            for name in update.FILES:
                (app / name).parent.mkdir(parents=True, exist_ok=True)
            settings = app / 'backend/config/settings.py'
            settings.write_bytes(b'custom server configuration')
            payload = {name: (ROOT / name).read_bytes() for name in update.FILES}
            with self.assertRaises(guard.PreflightError):
                guard.verify_baseline(app, {'files': update.FILES}, payload)
            self.assertEqual(settings.read_bytes(), b'custom server configuration')
            self.assertFalse((app / 'backend/core/request_limits.py').exists())

    def test_rollback_removes_new_middleware_and_restores_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            app = Path(folder).resolve()
            for name in update.FILES:
                (app / name).parent.mkdir(parents=True, exist_ok=True)
            settings = app / 'backend/config/settings.py'
            settings.write_bytes(b'previous settings')
            settings.chmod(0o640)
            old = {name: settings.read_bytes() if name.endswith('settings.py') else None
                   for name in update.FILES}
            info = {name: settings.stat() for name in update.FILES}
            payload = {name: (ROOT / name).read_bytes() for name in update.FILES}
            def fail():
                self.assertTrue((app / 'backend/core/request_limits.py').is_file())
                raise RuntimeError('synthetic failed health check')
            with self.assertRaises(RuntimeError):
                guard.apply_payload(app, payload, old, info, fail)
            self.assertEqual(settings.read_bytes(), b'previous settings')
            self.assertFalse((app / 'backend/core/request_limits.py').exists())
            self.assertEqual(settings.stat().st_mode & 0o777, 0o640)

    def test_maintenance_helpers_are_verified_before_execution(self):
        with tempfile.TemporaryDirectory() as folder:
            app = Path(folder).resolve()
            for name in update.SUPPORT:
                (app / name).parent.mkdir(parents=True, exist_ok=True)
                (app / name).write_bytes((ROOT / name).read_bytes())
            update.check_support(guard, app)
            (app / next(iter(update.SUPPORT))).write_bytes(b'unreviewed helper')
            with self.assertRaises(guard.PreflightError):
                update.check_support(guard, app)
