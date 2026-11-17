"""Update only two existing demo assets; no service restart or database writes."""
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile

APP = Path('/srv/oxxom-oblik/app')
STATE = Path('/srv/oxxom-oblik/update-backups')
GUARD = 'deploy/ubuntu/apply-security-update.py'
GUARD_SHA = '5f68b67de8db6b78598926e557755baa15636c3faabddbf23a470a41b7330dfc'
FILES = {
  "backend/core/static/core/demo-booking.css": {
    "before": "06111f95477b5fc26cddceee952323e3eaec5eb5ab13c45716469580a3a86937",
    "after": "a44ef639949692d4bb123336db10c9dbd6180af8956c309f47a7ccf23eda92d4"
  },
  "backend/core/static/core/demo-booking.js": {
    "before": "add34c3e11cfba428bdd3add6e86e3f71ff46fb79b5bf936263cca29cda35ba1",
    "after": "4e49de7593ba68e94266bf67b53f93a3b1bfb3c556d4dc85c016e2cc78b89924"
  }
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def package(archive):
    with tarfile.open(archive, 'r:gz') as bundle:
        members = {}
        for item in bundle.getmembers():
            path = Path(item.name)
            if path.is_absolute() or '..' in path.parts:
                raise ValueError('Unsafe archive path')
            name = '/'.join(path.parts[1:])
            if item.isfile() and name in {*FILES, GUARD}:
                if name in members or item.size > 1024 * 1024:
                    raise ValueError('Duplicate or oversized update file')
                members[name] = item
        if set(members) != {*FILES, GUARD}:
            raise ValueError('Missing update file')
        data = {name: bundle.extractfile(item).read() for name, item in members.items()}
        helper = data.pop(GUARD)
        if sha(helper) != GUARD_SHA or any(sha(data[name]) != info['after'] for name, info in FILES.items()):
            raise ValueError('Update checksum mismatch')
        return helper, data


def verify(guard):
    for name, info in FILES.items():
        asset = Path(name).name
        route = '/assets/landing/' + asset + '?v=' + info['after'][:16]
        status, _, body = guard.request(route)
        if status != 200 or sha(body) != info['after']:
            raise RuntimeError('Updated demo asset response failed')
    for prefix in ('', 'en/', 'ru/', 'cs/'):
        status, _, body = guard.request('/' + prefix + 'demo/')
        if status != 200 or any(info['after'][:16].encode() not in body for info in FILES.values()):
            raise RuntimeError('Fresh demo asset references failed')
    guard.smoke()


def main():
    if os.geteuid() != 0 or len(sys.argv) != 2:
        raise SystemExit('Run with sudo python3 apply-demo-arrow.py release.tar.gz')
    os.umask(0o077)
    if APP.is_symlink() or STATE.is_symlink():
        raise ValueError('Unexpected installation path')
    helper, payload = package(sys.argv[1])
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / 'seo-update.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with tempfile.TemporaryDirectory(prefix='oblik-arrow-guard-') as temporary:
            helper_path = Path(temporary) / 'guard.py'
            helper_path.write_bytes(helper)
            spec = importlib.util.spec_from_file_location('reviewed_arrow_guard', helper_path)
            guard = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(guard)
            originals = guard.verify_baseline(APP, {'files': FILES}, payload)
            guard.smoke()
            backup = Path(tempfile.mkdtemp(prefix='demo-arrow-', dir=STATE))
            info = {name: (APP / name).stat() for name in FILES}
            for name in FILES:
                target = backup / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(APP / name, target)
            (backup / 'receipt.json').write_text(json.dumps({'release': 'DEMO-ARROW-01', 'files': FILES}, indent=2))
            print('Recovery files: ' + str(backup), flush=True)
            try:
                guard.apply_payload(APP, payload, originals, info, lambda: verify(guard))
            except BaseException:
                print('UPDATE FAILED: original demo assets restored; service was not restarted', flush=True)
                raise
    print('DEMO-ARROW-01 OK: centred animated arrow to the form; four languages checked')
    print('Reduced motion supported. Service, NPM, database and MFA were not changed.')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        message = str(error) if type(error).__name__ == 'PreflightError' else type(error).__name__
        print('STOP: ' + message + '; values omitted', file=sys.stderr)
        sys.exit(1)
