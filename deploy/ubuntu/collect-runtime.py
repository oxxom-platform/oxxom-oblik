"""Read-only runtime comparison. No Django, env files, database or package changes."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys


PYTHON = '/srv/oxxom-oblik/venv/bin/python'
BOOTSTRAP = frozenset({'pip', 'setuptools', 'wheel'})
NAME = re.compile(r'[a-z0-9](?:[a-z0-9._-]{0,198}[a-z0-9])?\Z', re.ASCII)
VERSION = re.compile(r'[0-9][a-zA-Z0-9.!+_-]{0,99}\Z', re.ASCII)
ENTRY = re.compile(
    r'([a-zA-Z0-9][a-zA-Z0-9._-]*)(?:\[[a-zA-Z0-9,_-]+\])?'
    r'==([0-9][a-zA-Z0-9.!+_-]*)'
    r'((?:\s+--hash=sha256:[a-f0-9]{64})+)\Z', re.ASCII)
OPTIONS = frozenset({'--require-hashes', '--only-binary=:all:'})

# Read package metadata only. Never import applications, print metadata bodies,
# direct_url.json contents, environment values, hostnames or filesystem paths.
CHILD = r'''
import importlib.metadata, json, platform, sys
rows=[]
for distribution in importlib.metadata.distributions():
    rows.append({'name':distribution.metadata.get('Name', ''),
                 'version':distribution.version,
                 'direct_url_present':distribution.read_text('direct_url.json') is not None})
print(json.dumps({'python':list(sys.version_info[:3]), 'os':platform.system(),
                  'machine':platform.machine(), 'virtualenv':sys.prefix != sys.base_prefix,
                  'packages':rows}))
'''


def canonical(name):
    return re.sub(r'[-_.]+', '-', name.lower())


def parse_lock(text):
    if len(text) > 1024 * 1024:
        raise ValueError('Lock too large')
    result, parts = {}, []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.split('#', 1)[0].strip()
        if not line:
            continue
        if not parts and line in OPTIONS:
            continue
        continued = line.endswith('\\')
        parts.append(line[:-1].rstrip() if continued else line)
        if continued:
            continue
        entry = ENTRY.fullmatch(' '.join(parts))
        parts = []
        if entry is None:
            raise ValueError('Invalid pinned requirement at line ' + str(number))
        name, version = canonical(entry[1]), entry[2]
        if not NAME.fullmatch(name) or not VERSION.fullmatch(version) or name in result:
            raise ValueError('Invalid or duplicate package')
        result[name] = version
    if parts or not result:
        raise ValueError('Incomplete lock')
    return result


def compare(lock, raw):
    """Expose only known package names and validated version strings."""
    version = raw.get('python')
    if (not isinstance(version, list) or len(version) != 3
            or any(type(item) is not int or not 0 <= item < 1000 for item in version)
            or type(raw.get('virtualenv')) is not bool
            or not isinstance(raw.get('packages'), list) or len(raw['packages']) > 10000):
        raise ValueError('Invalid runtime inventory')
    target = (version[:2] == [3, 12] and raw.get('os') == 'Linux'
              and raw.get('machine') in {'x86_64', 'AMD64'} and raw['virtualenv'])
    installed, duplicates, unexpected, invalid, direct = {}, set(), 0, 0, 0
    for row in raw['packages']:
        if not isinstance(row, dict):
            raise ValueError('Invalid package metadata')
        name, value = row.get('name'), row.get('version')
        if not isinstance(name, str) or not NAME.fullmatch(name.lower()):
            invalid += 1
            continue
        name = canonical(name)
        if name in BOOTSTRAP:
            continue
        if name not in lock:
            unexpected += 1
            continue
        if not isinstance(value, str) or not VERSION.fullmatch(value):
            invalid += 1
            continue
        if type(row.get('direct_url_present')) is not bool:
            raise ValueError('Invalid source metadata')
        direct += row['direct_url_present']
        if name in installed:
            duplicates.add(name)
        installed[name] = value
    missing = sorted(set(lock) - set(installed))
    mismatches = {name: {'expected': lock[name], 'installed': value}
                  for name, value in installed.items() if lock[name] != value}
    matched = not (missing or mismatches or unexpected or invalid or duplicates or direct)
    return {
        'status': 'MATCH' if target and matched else 'DRIFT',
        'target_supported': target, 'python': '.'.join(map(str, version)),
        'expected_packages': len(lock), 'installed_expected_packages': dict(sorted(installed.items())),
        'missing_packages': missing, 'version_mismatches': mismatches,
        'unexpected_package_count': unexpected, 'invalid_metadata_count': invalid,
        'duplicate_expected_packages': sorted(duplicates), 'direct_url_expected_count': direct,
        'installed_artifact_hashes_verified': False,
        'scope': 'Package metadata comparison only; not a VM advisory scan or file integrity proof.',
    }


def collect(python):
    result = subprocess.run([python, '-I', '-B', '-c', CHILD], capture_output=True,
                            text=True, timeout=30)
    if result.returncode or len(result.stdout) > 1024 * 1024:
        raise ValueError('Runtime inventory unavailable')
    data = json.loads(result.stdout)
    if not isinstance(data, dict):
        raise ValueError('Invalid runtime inventory')
    return data


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lock', required=True, type=Path)
    parser.add_argument('--python', default=PYTHON)
    args = parser.parse_args(argv)
    try:
        data = args.lock.read_bytes()
        lock = parse_lock(data.decode('utf-8'))
        result = compare(lock, collect(args.python))
        result['lock_sha256'] = hashlib.sha256(data).hexdigest()
    except (OSError, ValueError, subprocess.SubprocessError):
        print(json.dumps({'status': 'BLOCKED', 'reason': 'Inventory or lock unavailable; values omitted'}))
        return 2
    print(json.dumps(result, indent=2))
    return 0 if result['status'] == 'MATCH' else 1


if __name__ == '__main__':
    sys.exit(main())
