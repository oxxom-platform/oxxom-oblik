"""Backup metadata discovery; no mounts, jobs or credential/password-file reads."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys


MOUNT = '/mnt/oxxom-oblik-backup'
CONFIG = Path('/etc/oxxom-oblik/backup')
STATE = Path('/var/lib/oxxom-oblik-backup')
FSTAB = Path('/etc/fstab')
CRONTAB = Path('/etc/crontab')
CRON_DIRECTORIES = tuple(Path(name) for name in (
    '/etc/cron.d', '/etc/cron.hourly', '/etc/cron.daily', '/etc/cron.weekly',
    '/etc/cron.monthly', '/var/spool/cron/crontabs'))
UNIT = re.compile(r'[A-Za-z0-9_.@:\\-]{1,180}\.(?:timer|service|mount|automount)\Z')
PROJECT = re.compile(r'oxxom[-_]oblik', re.I)
BACKUP = re.compile(r'backup|restic|rsnapshot|borg', re.I)
PUBLIC_UNITS = {'oxxom-oblik-backup.timer', 'oxxom-oblik-backup-verify.timer',
                'oxxom-oblik-ufw-rollback.timer', 'restic-backup.timer', 'backup.timer'}
SAFE_STATES = {'active', 'inactive', 'failed', 'activating', 'deactivating',
               'enabled', 'enabled-runtime', 'disabled', 'static', 'generated',
               'transient', 'masked', 'indirect', 'not-found', 'alias', 'linked',
               'linked-runtime', 'success', 'exit-code', 'signal', 'timeout',
               'core-dump', 'resources', 'start-limit-hit', 'protocol'}


def command(args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=8)
        if len(result.stdout) > 1024 * 1024:
            return None, ''
        return result.returncode, result.stdout
    except (OSError, subprocess.SubprocessError):
        return None, ''


def local_text(path, limit=128 * 1024):
    """Reject links and special files before reading bounded local metadata."""
    def opener(name, flags):
        return os.open(name, flags | os.O_NOFOLLOW | os.O_NONBLOCK)
    with open(path, encoding='utf-8', errors='replace', opener=opener) as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError('Unsafe metadata file')
        text = handle.read(limit + 1)
        if len(text) > limit:
            raise ValueError('Metadata grew during read')
        return text


def secret_metadata(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {'state': 'MISSING'}
    except OSError:
        return {'state': 'UNAVAILABLE'}
    regular = stat.S_ISREG(info.st_mode)
    return {'state': 'PRESENT', 'regular_file': regular, 'root_owned': info.st_uid == 0,
            'mode_0600': stat.S_IMODE(info.st_mode) == 0o600,
            'nonempty': regular and info.st_size > 0}


def mount_metadata():
    rc, raw = command(['findmnt', '--json', '--kernel', '--nocanonicalize',
                       '--mountpoint', MOUNT, '--output', 'FSTYPE,OPTIONS'])
    if rc == 1:
        return {'state': 'NOT_MOUNTED'}
    if rc != 0:
        return {'state': 'UNAVAILABLE'}
    try:
        rows = json.loads(raw)['filesystems']
        if len(rows) != 1:
            raise ValueError('Ambiguous mount')
        row = rows[0]
        options = set(row['options'].split(','))
        return {'state': 'MOUNTED', 'cifs': row['fstype'] == 'cifs',
                'smb_3_1_1': 'vers=3.1.1' in options, 'encryption_seal': 'seal' in options,
                'read_write': 'rw' in options,
                'source_binding_verified': False, 'remote_repository_reachability_verified': False}
    except (ValueError, KeyError, TypeError, AttributeError):
        return {'state': 'UNAVAILABLE'}


def fstab_metadata(path=FSTAB):
    try:
        text = local_text(path)
    except (OSError, ValueError):
        return {'state': 'UNAVAILABLE'}
    entries = []
    for raw in text.splitlines():
        fields = raw.split('#', 1)[0].split()
        if len(fields) < 4 or fields[1] != MOUNT:
            continue
        options = set(fields[3].split(','))
        entries.append({'cifs': fields[2] == 'cifs', 'smb_3_1_1': 'vers=3.1.1' in options,
                        'encryption_seal': 'seal' in options,
                        'automount_option': 'x-systemd.automount' in options,
                        'noauto_option': 'noauto' in options,
                        'credentials_file_option': any(x.startswith('credentials=') for x in options),
                        'inline_credentials_present': any(x.startswith(('password=', 'pass=', 'username=', 'user=')) for x in options)})
    return {'state': 'READ', 'matching_entries': entries}


def unit_metadata(name):
    if not UNIT.fullmatch(name):
        return {'state': 'UNAVAILABLE'}
    properties = ('LoadState', 'ActiveState', 'UnitFileState', 'Triggers', 'OnFailure',
                  'LastTriggerUSec', 'NextElapseUSecRealtime', 'NextElapseUSecMonotonic',
                  'Result', 'ExecMainStatus')
    rc, raw = command(['systemctl', 'show', name, '--no-pager',
                       *['--property=' + key for key in properties]])
    if rc != 0:
        return {'state': 'UNAVAILABLE'}
    values = dict(line.split('=', 1) for line in raw.splitlines() if '=' in line)
    result = {'state': 'READ',
              'loaded': values.get('LoadState') == 'loaded',
              'active_state': values.get('ActiveState') if values.get('ActiveState') in SAFE_STATES else 'UNKNOWN',
              'unit_file_state': values.get('UnitFileState') if values.get('UnitFileState') in SAFE_STATES else 'UNKNOWN',
              'failure_hook_configured': bool(values.get('OnFailure')),
              'last_trigger_present': values.get('LastTriggerUSec', '') not in {'', 'n/a', '0'},
              'next_trigger_present': any(values.get(key, '') not in {'', 'n/a', '0'} for key in ('NextElapseUSecRealtime', 'NextElapseUSecMonotonic'))}
    if name.endswith('.service'):
        result['last_result'] = values.get('Result') if values.get('Result') in SAFE_STATES else 'UNKNOWN'
        exit_status = values.get('ExecMainStatus', '')
        result['last_exit_status'] = int(exit_status) if re.fullmatch(r'\d{1,3}', exit_status) else None
    else:
        triggered = [value for value in values.get('Triggers', '').split()
                     if UNIT.fullmatch(value) and value.endswith('.service')]
        result['triggered_service_count'] = len(triggered)
        result['triggered_services'] = [unit_metadata(service) for service in triggered[:8]]
    return result


def systemd_metadata():
    names, unavailable = set(), 0
    for args in (['systemctl', 'list-unit-files', '--type=timer', '--no-legend', '--no-pager'],
                 ['systemctl', 'list-timers', '--all', '--no-legend', '--no-pager']):
        rc, raw = command(args)
        if rc != 0:
            unavailable += 1
            continue
        names.update(token for token in raw.split() if token.endswith('.timer')
                     and UNIT.fullmatch(token) and (PROJECT.search(token) or BACKUP.search(token)))
    timers = []
    for index, name in enumerate(sorted(names)[:64], 1):
        timers.append({'candidate': index, 'public_unit_name': name if name in PUBLIC_UNITS else None,
                       'unit_name_fingerprint': hashlib.sha256(name.encode()).hexdigest()[:16],
                       'project_name_marker': bool(PROJECT.search(name)),
                       'backup_name_marker': bool(BACKUP.search(name)), **unit_metadata(name)})
    rc, raw = command(['systemd-escape', '--path', '--suffix=mount', MOUNT])
    mount = raw.strip()
    persistent_units = {'state': 'UNAVAILABLE'}
    if rc == 0 and UNIT.fullmatch(mount) and mount.endswith('.mount'):
        persistent_units = {'mount': unit_metadata(mount),
                            'automount': unit_metadata(mount[:-6] + '.automount')}
    return {'candidate_timers': timers, 'unavailable_listing_commands': unavailable,
            'candidates_truncated': len(names) > 64, 'mount_units': persistent_units,
            'keyword_discovery_is_not_complete_schedule_verification': True}


def cron_metadata(crontab=CRONTAB, directories=CRON_DIRECTORIES):
    files = [(crontab, False)]
    listing_errors, truncated = 0, False
    for folder in directories:
        try:
            children = sorted(folder.iterdir())
            truncated |= len(children) > 256
            files.extend((path, folder.name not in {'cron.d', 'crontabs'}) for path in children[:256]
                         if not path.name.startswith('.'))
        except FileNotFoundError:
            continue
        except OSError:
            listing_errors += 1
    result = {'files_read': 0, 'files_skipped_or_unavailable': listing_errors,
              'project_candidate_files': 0, 'backup_candidate_files': 0,
              'candidate_scan_truncated': truncated,
              'commands_and_file_names_omitted': True,
              'indirect_jobs_and_external_schedulers_verified': False}
    for path, executable in files:
        try:
            info = path.lstat()
            if executable and not (info.st_mode & 0o111):
                continue
            text = local_text(path)
        except FileNotFoundError:
            continue
        except (OSError, ValueError):
            result['files_skipped_or_unavailable'] += 1
            continue
        result['files_read'] += 1
        lines = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith('#')]
        if not executable:
            lines = [line for line in lines if not re.match(r'^\s*[A-Za-z_]\w*\s*=', line)]
        content = '\n'.join(lines)
        result['project_candidate_files'] += bool(PROJECT.search(content))
        result['backup_candidate_files'] += bool(BACKUP.search(content))
    return result


def receipt(path, timestamp, now):
    try:
        info = path.lstat()
        raw = json.loads(local_text(path, 65536))
        if not isinstance(raw, dict):
            raise ValueError('Invalid receipt')
    except FileNotFoundError:
        return {'state': 'MISSING'}, None
    except (OSError, ValueError):
        return {'state': 'UNAVAILABLE'}, None
    created, age = None, None
    try:
        value = datetime.fromisoformat(raw[timestamp])
        if value.tzinfo is None:
            raise ValueError('Timezone missing')
        created = value.astimezone(timezone.utc).isoformat()
        age = round((now - value).total_seconds() / 3600, 2)
    except (KeyError, TypeError, ValueError, OverflowError):
        pass
    pair = all(isinstance(raw.get(kind + '_snapshot'), str)
               and re.fullmatch(r'[a-f0-9]{64}', raw[kind + '_snapshot'])
               for kind in ('data', 'keys'))
    return {'state': 'READ', 'timestamp_utc': created, 'receipt_age_hours': age,
            'root_owned': info.st_uid == 0, 'private_mode': not bool(info.st_mode & 0o077),
            'snapshot_pair_fields_valid': bool(pair),
            'file_restore_verified': raw.get('file_restore_verified') is True,
            'database_restore_verified': raw.get('database_restore_verified') is True,
            'isolated_resources_removed': raw.get('isolated_resources_removed') is True}, raw


def receipts_metadata(state=STATE, now=None):
    now = now or datetime.now(timezone.utc)
    first, first_raw = receipt(state / 'first-success.json', 'created_utc', now)
    restore, restore_raw = receipt(state / 'restore-success.json', 'verified_utc', now)
    match = None
    if first_raw is not None and restore_raw is not None:
        match = (first.get('snapshot_pair_fields_valid') and restore.get('snapshot_pair_fields_valid')
                 and isinstance(first_raw.get('job'), str) and bool(first_raw['job'])
                 and isinstance(first_raw.get('database'), str) and bool(first_raw['database'])
                 and all(first_raw.get(key) == restore_raw.get(key)
                         for key in ('job', 'database', 'data_snapshot', 'keys_snapshot')))
    return {'first_backup': first, 'isolated_restore': restore,
            'restore_matches_first_recorded_pair': match,
            'latest_remote_backup_freshness_verified': False}


def audit():
    return {'status': 'OBSERVED', 'meaning': 'Metadata discovery completed; not backup acceptance.',
            'live_mount': mount_metadata(), 'persistent_mount_fstab': fstab_metadata(),
            'systemd': systemd_metadata(), 'cron': cron_metadata(),
            'secret_file_metadata': {name: secret_metadata(CONFIG / name) for name in
                                     ('windows.credentials', 'restic-data.password', 'restic-keys.password')},
            'local_receipts': receipts_metadata(),
            'unverified': ['remote repository reachability and correct target',
                           'latest complete data/keys pair and freshness',
                           'actual automated job execution', 'retention and paired deletion safety',
                           'failure alert delivery', 'mount/backup after reboot',
                           'independent immutable or offline copy'],
            'scope': 'No secret-file contents, cron commands, database names, share addresses, snapshot IDs or job commands are printed. No backup, mount, restore or deletion is executed.'}


def main():
    if os.geteuid() != 0:
        print(json.dumps({'status': 'BLOCKED', 'reason': 'Run with sudo on the Ubuntu application VM'}))
        return 2
    try:
        result = audit()
    except Exception:
        print(json.dumps({'status': 'BLOCKED', 'reason': 'Metadata collection incomplete; values omitted'}))
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
