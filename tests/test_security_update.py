import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('security_update', ROOT / 'deploy/ubuntu/apply-security-update.py')
update = importlib.util.module_from_spec(spec)
spec.loader.exec_module(update)


class SecurityUpdateTests(unittest.TestCase):
    def test_unknown_server_change_stops_before_writes(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve()
            (root / 'original.py').write_bytes(b'local change')
            manifest = {'files': {'original.py': {'before': update.sha(b'old')}}}
            with self.assertRaisesRegex(ValueError, 'Unreviewed server change'):
                update.verify_baseline(root, manifest, {'original.py': b'new'})
            self.assertEqual((root / 'original.py').read_bytes(), b'local change')

    def test_failed_validation_restores_original_and_removes_new_file(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve()
            (root / 'old.py').write_bytes(b'old')
            info = (root / 'old.py').stat()
            payload = {'old.py': b'new', 'new/feature.py': b'feature'}
            def fail():
                self.assertEqual((root / 'old.py').read_bytes(), b'new')
                raise RuntimeError('simulated failed health check')
            with self.assertRaises(RuntimeError):
                update.apply_payload(root, payload, {'old.py': b'old', 'new/feature.py': None},
                                     {key: info for key in payload}, fail)
            self.assertEqual((root / 'old.py').read_bytes(), b'old')
            self.assertFalse((root / 'new/feature.py').exists())

    def test_new_directories_are_readable_under_private_umask(self):
        import os
        with tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve()
            original = root / 'original.py'
            original.write_bytes(b'old')
            old_umask = os.umask(0o077)
            try:
                update.replace(root / 'management/commands/build.py', b'new', original.stat())
            finally:
                os.umask(old_umask)
            self.assertEqual((root / 'management').stat().st_mode & 0o777, 0o750)
            self.assertEqual((root / 'management/commands').stat().st_mode & 0o777, 0o750)

    def test_symlink_and_parent_traversal_are_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve()
            (root / 'outside').mkdir()
            (root / 'linked').symlink_to(root / 'outside', target_is_directory=True)
            for path in ('../outside.py', '/etc/passwd', 'linked/file.py'):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    update.safe_target(root, path)

    def package(self, root, corrupt=False, extra=False):
        files = {name: b'code' for name in update.PAYLOAD_FILES}
        manifest = {'release': 'SECURITY-01A', 'files': {name: {'before': None, 'after': update.sha(data)} for name, data in files.items()}}
        if extra:
            manifest['files']['backend/unreviewed.py'] = {'before': None, 'after': update.sha(b'code')}
        if corrupt:
            files[next(iter(files))] = b'tampered'
        files[update.MANIFEST] = json.dumps(manifest).encode()
        archive = root / 'release.tar.gz'
        with tarfile.open(archive, 'w:gz') as bundle:
            for name, data in files.items():
                member = tarfile.TarInfo('oxxom-release/' + name)
                member.size = len(data)
                bundle.addfile(member, io.BytesIO(data))
        return archive

    def test_archive_checks_exact_allowlist_and_every_payload_hash(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve()
            with patch.object(update, 'APP', root / 'app'):
                manifest, files = update.read_package(self.package(root))
                self.assertEqual(set(files), update.PAYLOAD_FILES)
                with self.assertRaisesRegex(ValueError, 'checksum'):
                    update.read_package(self.package(root, corrupt=True))
                with self.assertRaisesRegex(ValueError, 'allowlist'):
                    update.read_package(self.package(root, extra=True))

    def test_manifest_matches_local_payload_and_contains_no_db_or_secret_file(self):
        manifest = json.loads((ROOT / update.MANIFEST).read_text())
        self.assertEqual(set(manifest['files']), update.PAYLOAD_FILES)
        for name, hashes in manifest['files'].items():
            self.assertEqual(update.sha((ROOT / name).read_bytes()), hashes['after'], name)
            self.assertNotIn('/migrations/', name)
            self.assertNotIn('.env', name)


if __name__ == '__main__':
    unittest.main()
