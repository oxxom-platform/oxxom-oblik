"""Exercise the Ubuntu root-to-service permission boundary with synthetic files."""
import importlib.util
import os
from pathlib import Path
import pwd
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('demo_permissions_update', ROOT / 'deploy/ubuntu/apply-demo-update.py')
update = importlib.util.module_from_spec(spec)
spec.loader.exec_module(update)


@unittest.skipUnless(sys.platform.startswith('linux') and os.geteuid() == 0,
                     'Requires isolated Linux CI root, never production data')
class DemoPermissionTests(unittest.TestCase):
    def test_root_owned_baseline_failed_import_then_service_can_read_repaired_package(self):
        account = pwd.getpwnam('nobody')
        with tempfile.TemporaryDirectory(prefix='oblik-permissions-test-') as name:
            root = Path(name).resolve()
            root.chmod(0o755)
            (root / 'backend/config').mkdir(parents=True, mode=0o755)
            (root / 'backend').chmod(0o755)
            (root / 'backend/config').chmod(0o755)
            settings = root / 'backend/config/settings.py'
            settings.write_bytes(b'# synthetic root-owned baseline\n')
            settings.chmod(0o644)
            guard = update.load_guard((ROOT / update.BASE_HELPER).read_bytes(), root)
            package = 'backend/demo_booking/__init__.py'
            module = 'backend/demo_booking/services.py'
            payload = {package: b'', module: b'sentinel = 42\n'}
            # Reproduce the old installer: root:root 0750 denies service import.
            guard.replace(root / package, b'', settings.stat())
            guard.replace(root / module, payload[module], settings.stat())
            script = ('import sys, os; sys.path.insert(0, ' + repr(str(root / 'backend'))
                      + '); import demo_booking.services as service; assert service.sentinel == 42; '
                      + 'assert not os.access(service.__file__, os.W_OK)')
            def service_import():
                return subprocess.run(['runuser', '-u', account.pw_name, '--', sys.executable,
                                       '-I', '-B', '-c', script], capture_output=True, text=True, timeout=10)
            failed = service_import()
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn('ModuleNotFoundError', failed.stderr)
            # A previous rollback leaves the inaccessible empty directory.
            (root / package).unlink()
            (root / module).unlink()
            update.prepare_demo_directories(root, guard, account.pw_gid)
            info = update.payload_info(root, payload, account.pw_gid)
            guard.apply_payload(root, payload, {key: None for key in payload}, info, lambda: None)
            result = service_import()
            self.assertEqual(result.returncode, 0, result.stderr)
            for path in update.DEMO_DIRECTORIES:
                metadata = (root / path).stat()
                self.assertEqual((metadata.st_uid, metadata.st_gid), (0, account.pw_gid))
                self.assertEqual(stat.S_IMODE(metadata.st_mode), 0o750)
            for path in payload:
                self.assertEqual(stat.S_IMODE((root / path).stat().st_mode), 0o640)
            self.assertEqual(stat.S_IMODE(settings.stat().st_mode), 0o644)
            self.assertEqual(settings.stat().st_gid, 0)

    def test_directory_policy_rejects_symlinks_without_changing_the_target(self):
        with tempfile.TemporaryDirectory(prefix='oblik-permissions-link-test-') as name:
            root = Path(name).resolve()
            (root / 'backend').mkdir()
            target = root / 'unrelated'
            target.mkdir(mode=0o700)
            before = target.stat()
            (root / 'backend/demo_booking').symlink_to(target, target_is_directory=True)
            guard = update.load_guard((ROOT / update.BASE_HELPER).read_bytes(), root)
            with self.assertRaises(ValueError):
                update.prepare_demo_directories(root, guard, pwd.getpwnam('nobody').pw_gid)
            after = target.stat()
            self.assertEqual((after.st_uid, after.st_gid, after.st_mode),
                             (before.st_uid, before.st_gid, before.st_mode))


if __name__ == '__main__':
    unittest.main()
