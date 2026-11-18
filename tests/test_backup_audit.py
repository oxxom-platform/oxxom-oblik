from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('backup_audit', ROOT / 'deploy/ubuntu/audit-backups.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
CANARY = 'SYNTHETIC-PRIVATE-CONTENT-DO-NOT-PRINT'


class BackupAuditTests(unittest.TestCase):
    def test_special_or_oversized_metadata_is_rejected_before_a_blocking_read(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            pipe = root / 'pipe'
            os.mkfifo(pipe)
            with self.assertRaises(ValueError):
                audit.local_text(pipe)
            path = root / 'large'
            path.write_text('x' * 100)
            with self.assertRaises(ValueError):
                audit.local_text(path, limit=10)

    def test_mount_projection_omits_source_and_credentials_and_never_accesses_share(self):
        raw = {'filesystems': [{'fstype': 'cifs', 'source': '//192.0.2.51/' + CANARY,
                               'options': 'rw,vers=3.1.1,seal,username=' + CANARY}]}
        with patch.object(audit, 'command', return_value=(0, json.dumps(raw))) as run:
            result = audit.mount_metadata()
        self.assertTrue(result['cifs'] and result['smb_3_1_1'] and result['encryption_seal'])
        self.assertFalse(result['remote_repository_reachability_verified'])
        self.assertNotIn(CANARY, json.dumps(result))
        self.assertIn('--nocanonicalize', run.call_args.args[0])
        self.assertEqual(run.call_args.args[0][-1], 'FSTYPE,OPTIONS')
        for code, expected in [(1, 'NOT_MOUNTED'), (None, 'UNAVAILABLE')]:
            with patch.object(audit, 'command', return_value=(code, CANARY)):
                self.assertEqual(audit.mount_metadata()['state'], expected)

    def test_fstab_redacts_inline_credentials_and_reports_persistence_flags(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'fstab'
            path.write_text('//192.0.2.51/' + CANARY + ' ' + audit.MOUNT
                            + ' cifs credentials=/private/' + CANARY
                            + ',password=' + CANARY + ',vers=3.1.1,seal,x-systemd.automount 0 0\n')
            result = audit.fstab_metadata(path)
        self.assertEqual(len(result['matching_entries']), 1)
        row = result['matching_entries'][0]
        self.assertTrue(row['automount_option'] and row['credentials_file_option'] and row['inline_credentials_present'])
        self.assertNotIn(CANARY, json.dumps(result))
        self.assertNotIn('192.0.2.51', json.dumps(result))

    def test_secret_files_are_only_stat_ed_and_symlink_is_not_followed(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'restic.password'
            path.write_text(CANARY)
            path.chmod(0o600)
            link = Path(folder) / 'link'
            link.symlink_to(path)
            with patch.object(Path, 'read_text', side_effect=AssertionError('Secret file read')):
                result = audit.secret_metadata(path)
                linked = audit.secret_metadata(link)
            self.assertTrue(result['regular_file'] and result['mode_0600'] and result['nonempty'])
            self.assertFalse(linked['regular_file'])
            self.assertNotIn(CANARY, json.dumps(result))

    def test_receipts_match_exact_recorded_pair_without_printing_private_fields(self):
        now = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
        first = {'job': CANARY, 'database': CANARY, 'created_utc': (now - timedelta(hours=25)).isoformat(),
                 'data_snapshot': 'a' * 64, 'keys_snapshot': 'b' * 64, 'file_restore_verified': True,
                 'password': CANARY}
        restored = {**first, 'verified_utc': (now - timedelta(hours=24)).isoformat(),
                    'database_restore_verified': True, 'isolated_resources_removed': True}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'first-success.json').write_text(json.dumps(first))
            (root / 'restore-success.json').write_text(json.dumps(restored))
            result = audit.receipts_metadata(root, now)
            self.assertTrue(result['restore_matches_first_recorded_pair'])
            self.assertEqual(result['first_backup']['receipt_age_hours'], 25)
            self.assertEqual(result['isolated_restore']['receipt_age_hours'], 24)
            self.assertNotIn(CANARY, json.dumps(result))
            self.assertNotIn('a' * 64, json.dumps(result))
            restored['keys_snapshot'] = 'c' * 64
            (root / 'restore-success.json').write_text(json.dumps(restored))
            self.assertFalse(audit.receipts_metadata(root, now)['restore_matches_first_recorded_pair'])
            self.assertFalse(result['latest_remote_backup_freshness_verified'])

    def test_bad_or_linked_receipts_never_become_verified(self):
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / 'receipt.json'
            target = root / 'private.env'
            target.write_text(CANARY)
            path.symlink_to(target)
            with patch.object(Path, 'read_text', side_effect=AssertionError('Followed a link')):
                result, raw = audit.receipt(path, 'created_utc', now)
            self.assertEqual(result['state'], 'UNAVAILABLE')
            self.assertIsNone(raw)
            path.unlink()
            path.write_text(json.dumps({'created_utc': CANARY, 'data_snapshot': 'latest'}))
            result, raw = audit.receipt(path, 'created_utc', now)
            self.assertIsNone(result['timestamp_utc'])
            self.assertFalse(result['snapshot_pair_fields_valid'])
            self.assertNotIn(CANARY, json.dumps(result))

    def test_cron_is_counted_without_disclosing_file_names_commands_or_env_assignments(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            crontab = root / 'crontab'
            crontab.write_text('# restic ' + CANARY + '\nBACKUP_LABEL=' + CANARY
                               + '\n0 3 * * * root /srv/oxxom-oblik/backup --token=' + CANARY + '\n')
            cron = root / 'cron.d'
            cron.mkdir()
            (cron / CANARY).write_text('0 4 * * * root restic --password=' + CANARY + '\n')
            (cron / 'linked').symlink_to(crontab)
            result = audit.cron_metadata(crontab, (cron,))
        self.assertEqual(result['files_read'], 2)
        self.assertEqual(result['project_candidate_files'], 1)
        self.assertEqual(result['backup_candidate_files'], 2)
        self.assertEqual(result['files_skipped_or_unavailable'], 1)
        self.assertNotIn(CANARY, json.dumps(result))
        self.assertFalse(result['indirect_jobs_and_external_schedulers_verified'])

    def test_unknown_units_and_job_contents_are_redacted_and_only_show_commands_are_used(self):
        name = 'backup@192.0.2.51-' + CANARY + '.timer'
        calls = []
        def command(args):
            calls.append(args)
            if args[0] == 'systemd-escape':
                return 0, r'mnt-oxxom\x2doblik\x2dbackup.mount'
            if args[1] in {'list-timers', 'list-unit-files'}:
                return 0, name + ' enabled\n'
            return 0, ('LoadState=loaded\nActiveState=active\nUnitFileState=enabled\n'
                       'Triggers=oxxom-oblik-backup.service\nOnFailure=' + CANARY
                       + '\nExecStart=' + CANARY + '\nEnvironment=' + CANARY
                       + '\nNextElapseUSecRealtime=some future time\nResult=success\nExecMainStatus=0\n')
        with patch.object(audit, 'command', side_effect=command):
            result = audit.systemd_metadata()
        row = result['candidate_timers'][0]
        self.assertTrue(row['next_trigger_present'] and row['failure_hook_configured'])
        self.assertIsNone(row['public_unit_name'])
        self.assertNotIn(CANARY, json.dumps(result))
        self.assertNotIn('192.0.2.51', json.dumps(result))
        self.assertTrue(all(args[0] == 'systemd-escape' or args[1] in {'list-timers', 'list-unit-files', 'show'} for args in calls))
        self.assertFalse(any('--property=ExecStart' in args or '--property=Environment' in args for args in calls))

    def test_unavailable_commands_do_not_print_stderr_or_exception_details(self):
        with patch.object(audit.subprocess, 'run', side_effect=OSError(CANARY)):
            self.assertEqual(audit.command(['findmnt']), (None, ''))
        with patch.object(audit.os, 'geteuid', return_value=0), patch.object(audit, 'audit', side_effect=ValueError(CANARY)), patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertEqual(audit.main(), 2)
        self.assertNotIn(CANARY, output.getvalue())

    def test_non_root_cli_does_not_attempt_inventory(self):
        with patch.object(audit.os, 'geteuid', return_value=1234), patch.object(audit, 'audit') as run, patch('sys.stdout', new_callable=io.StringIO):
            self.assertEqual(audit.main(), 2)
        run.assert_not_called()

    def test_launcher_pins_payload_and_rejects_symbolic_revisions(self):
        path = ROOT / 'deploy/ubuntu/audit-backups.sh'
        digest = hashlib.sha256((ROOT / 'deploy/ubuntu/audit-backups.py').read_bytes()).hexdigest()
        self.assertIn("collector_sha='" + digest + "'", path.read_text())
        for revision in ('main', '123abcd', '../main', 'g' * 40):
            result = subprocess.run(['sh', str(path), revision], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('commit SHA', result.stderr)
