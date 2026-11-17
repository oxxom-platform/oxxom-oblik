import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('demo_update', ROOT / 'deploy/ubuntu/apply-demo-update.py')
update = importlib.util.module_from_spec(spec)
spec.loader.exec_module(update)
GUARD = (ROOT / update.BASE_HELPER).read_bytes()


class DemoUpdateTests(unittest.TestCase):
    def archive(self, root, corrupt=False, extra=False, new_baseline=False):
        payload = {name: b'test-code' for name in update.EXISTING | update.NEW}
        manifest = {'release': 'DEMO-01', 'files': {name: {'before': update.sha(b'old') if name in update.EXISTING else None,
                                                           'after': update.sha(data)} for name,data in payload.items()}}
        if extra:
            manifest['files']['.env'] = {'before': None, 'after': update.sha(b'private')}
        if new_baseline:
            manifest['files'][next(iter(update.NEW))]['before'] = update.sha(b'unreviewed')
        if corrupt:
            payload[next(iter(payload))] = b'tampered'
        payload[update.MANIFEST] = json.dumps(manifest).encode()
        payload[update.BASE_HELPER] = GUARD
        name = root / 'release.tar.gz'
        with tarfile.open(name, 'w:gz') as archive:
            for path,data in payload.items():
                item = tarfile.TarInfo('reviewed-release/' + path)
                item.size = len(data)
                archive.addfile(item, io.BytesIO(data))
        return name

    def test_exact_scope_checksums_and_new_file_baselines(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve()
            manifest, helper, payload = update.package(self.archive(root))
            self.assertEqual(set(payload), update.EXISTING | update.NEW)
            self.assertEqual(update.sha(helper), update.BASE_HELPER_SHA)
            for kwargs in ({'corrupt':True}, {'extra':True}, {'new_baseline':True}):
                with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                    update.package(self.archive(root, **kwargs))

    def test_unpinned_guard_cannot_be_imported(self):
        with tempfile.TemporaryDirectory() as name:
            with self.assertRaises(ValueError):
                update.load_guard(b'print("UNTRUSTED")', Path(name))

    def test_rollback_restores_code_without_deleting_database_dump(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve()
            guard = update.load_guard(GUARD, root)
            app = root / 'app'
            app.mkdir()
            original = app / 'settings.py'
            original.write_bytes(b'old')
            dump = root / 'before.dump'
            dump.write_bytes(b'private recovery snapshot')
            info = original.stat()
            def fail():
                raise RuntimeError('simulated failed migration or health')
            with self.assertRaises(RuntimeError):
                guard.apply_payload(app, {'settings.py':b'new','demo/new.py':b'new'},
                                    {'settings.py':b'old','demo/new.py':None},
                                    {'settings.py':info,'demo/new.py':info}, fail)
            self.assertEqual(original.read_bytes(), b'old')
            self.assertFalse((app / 'demo/new.py').exists())
            self.assertTrue(dump.exists())

    def test_dump_credentials_are_not_command_arguments(self):
        with tempfile.TemporaryDirectory() as name:
            destination = Path(name) / 'before.dump'
            values = {'POSTGRES_USER':'app', 'POSTGRES_DB':'app_db', 'POSTGRES_PASSWORD':'PRIVATE_CREDENTIAL'}
            calls = []
            def run(arguments, **kwargs):
                calls.append((arguments,kwargs))
                if arguments[0] == 'pg_dump':
                    destination.write_bytes(b'x'*200)
            with patch.object(update.subprocess, 'run', side_effect=run):
                update.backup_database(values, destination, io.StringIO())
            self.assertNotIn('PRIVATE_CREDENTIAL', ' '.join(calls[0][0]))
            self.assertEqual(calls[0][1]['env']['PGPASSWORD'], 'PRIVATE_CREDENTIAL')
            self.assertEqual(calls[1][0][0], 'pg_restore')

    def test_backup_failure_happens_before_application_write(self):
        with tempfile.TemporaryDirectory() as name:
            import subprocess
            with patch.object(update.subprocess, 'run', side_effect=subprocess.CalledProcessError(1,['pg_dump'])):
                with self.assertRaises(subprocess.CalledProcessError):
                    update.backup_database({'POSTGRES_PASSWORD':'synthetic','POSTGRES_USER':'app','POSTGRES_DB':'app'},
                                           Path(name)/'before.dump', io.StringIO())

    def test_historical_manifest_scope_and_fixture_only_prototype(self):
        manifest = json.loads((ROOT / update.MANIFEST).read_text())
        self.assertEqual(set(manifest['files']), update.EXISTING | update.NEW)
        self.assertFalse(any('/accounts/' in name or '/posting/' in name or 'app.env' in name for name in manifest['files']))
        self.assertEqual(update.sha(GUARD), update.BASE_HELPER_SHA)


if __name__ == '__main__':
    unittest.main()
