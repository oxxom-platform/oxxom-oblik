"""Incremental deployment must reject unknown files and recover exact originals."""
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('seo_update', ROOT / 'deploy/ubuntu/apply-seo-update.py')
update = importlib.util.module_from_spec(spec)
spec.loader.exec_module(update)


class SEOUpdateTests(unittest.TestCase):
    def test_unknown_server_edit_is_rejected_before_any_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'backend').mkdir()
            target = root / 'backend/source.py'
            target.write_bytes(b'server-specific change')
            manifest = {'files': {'backend/source.py': {'sha256': update.digest(b'candidate'),
                                                       'before': [update.digest(b'old')]}}}
            with self.assertRaisesRegex(ValueError, 'Unreviewed server change'):
                update.previous_files(root, manifest)
            self.assertEqual(target.read_bytes(), b'server-specific change')

    def test_rollback_restores_old_files_removes_new_files_and_preserves_unrelated_files(self):
        with tempfile.TemporaryDirectory() as directory:
            app, backup = Path(directory) / 'app', Path(directory) / 'backup'
            (app / 'backend').mkdir(parents=True)
            (backup / 'backend').mkdir(parents=True)
            (app / 'backend/old.py').write_bytes(b'original')
            (backup / 'backend/old.py').write_bytes(b'original')
            (app / 'backend/unrelated.py').write_bytes(b'leave alone')
            manifest = {'files': {'backend/old.py': {'sha256': update.digest(b'new'), 'before': [update.digest(b'original')]},
                                  'backend/new.py': {'sha256': update.digest(b'new'), 'before': [None]}}}
            previous = update.previous_files(app, manifest)
            for name in previous:
                update.replace_file(app / name, b'new', previous[name])
            update.restore_files(app, backup, previous)
            self.assertEqual((app / 'backend/old.py').read_bytes(), b'original')
            self.assertFalse((app / 'backend/new.py').exists())
            self.assertEqual((app / 'backend/unrelated.py').read_bytes(), b'leave alone')

    def bundle(self, directory, unsafe=None):
        files = {'backend/file' + str(i) + '.py': b'content' for i in range(20)}
        manifest = {'release': 'SEO-01', 'files': {name: {'sha256': update.digest(data), 'before': [None]}
                                                for name, data in files.items()}}
        if unsafe == 'path':
            manifest['files']['backend/../outside.py'] = manifest['files'].pop('backend/file0.py')
        if unsafe == 'checksum':
            manifest['files']['backend/file0.py']['sha256'] = '0' * 64
        archive = Path(directory) / 'release.tar.gz'
        with tarfile.open(archive, 'w:gz') as output:
            data = json.dumps(manifest).encode()
            info = tarfile.TarInfo('repo/' + update.MANIFEST)
            info.size = len(data)
            output.addfile(info, io.BytesIO(data))
            for name, data in files.items():
                info = tarfile.TarInfo('repo/' + name)
                info.size = len(data)
                if unsafe == 'link' and name == 'backend/file0.py':
                    info.type, info.linkname, info.size = tarfile.SYMTYPE, '/etc/passwd', 0
                    output.addfile(info)
                else:
                    output.addfile(info, io.BytesIO(data))
        return archive

    def test_archive_checks_payload_and_rejects_paths_links_and_wrong_checksums(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest, payload = update.read_release(self.bundle(directory))
            self.assertEqual(len(payload), 20)
            self.assertEqual(payload['backend/file0.py'], b'content')
            for unsafe in ('path', 'link', 'checksum'):
                with self.subTest(unsafe=unsafe), self.assertRaises(ValueError):
                    update.read_release(self.bundle(directory, unsafe))


if __name__ == '__main__':
    unittest.main()
