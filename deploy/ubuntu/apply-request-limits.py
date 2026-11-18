"""Guarded two-file application update; no schema or dependency changes."""
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

APP = Path('/srv/oxxom-oblik/app')
STATE = Path('/srv/oxxom-oblik/update-backups')
PYTHON = '/srv/oxxom-oblik/venv/bin/python'
GUARD = 'deploy/ubuntu/apply-security-update.py'
GUARD_SHA = '5f68b67de8db6b78598926e557755baa15636c3faabddbf23a470a41b7330dfc'
SUPPORT = {
    'deploy/ubuntu/manage.py': '5e88a1d73e00c5e63125e690fe54f98ba76fb3abdc78d5f3bb51a8fac9707e00',
    'deploy/ubuntu/environment_file.py': '71a6dbac8f0dfd442f51a83905f924c6874a829bbd6c2edcab49939390983964',
}
FILES = {
    'backend/config/settings.py': {
        'before': '2656ef0097a392c974f2659d47909df6857d61e1fd248c98d0a12f7d51a95677',
        'after': '2797f3f6baf82dc6262bd63b672e17635aafe42150a943720552f9ec6b2592e4',
    },
    'backend/core/request_limits.py': {'before': None, 'after': 'e075525c48253c62737b700b0f4ae8a81e45b200a5f33457e131ea632918e507'},
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def package(archive):
    wanted = {*FILES, GUARD}
    with tarfile.open(archive, 'r:gz') as bundle:
        members = {}
        for item in bundle.getmembers():
            parts = Path(item.name).parts
            if Path(item.name).is_absolute() or '..' in parts:
                raise ValueError('Unsafe archive path')
            name = '/'.join(parts[1:])
            if name in wanted:
                if not item.isfile() or item.size > 1024 * 1024 or name in members:
                    raise ValueError('Invalid update member')
                members[name] = item
        if set(members) != wanted:
            raise ValueError('Missing update member')
        payload = {name: bundle.extractfile(item).read() for name, item in members.items()}
    helper = payload.pop(GUARD)
    if sha(helper) != GUARD_SHA or any(sha(payload[name]) != policy['after'] for name, policy in FILES.items()):
        raise ValueError('Update checksum mismatch')
    for name, data in payload.items():
        compile(data, name, 'exec')
    return helper, payload


def check_support(guard, root):
    for name, digest in SUPPORT.items():
        path = guard.safe_target(root, name)
        if not path.is_file() or sha(path.read_bytes()) != digest:
            raise guard.PreflightError('Unreviewed maintenance helper: ' + name)


def service_check(log):
    # Check with the service account, including read/traverse permissions.
    command = ['runuser', '-u', 'oxxom', '--', PYTHON, '-B',
               str(APP / 'deploy/ubuntu/manage.py')]
    subprocess.run(command + ['check'], check=True, timeout=60, stdout=log, stderr=log)
    code = (
        "from django.conf import settings; "
        "from core.request_limits import RequestBodyLimitMiddleware; "
        "m=settings.MIDDLEWARE; "
        "assert m.index('core.request_limits.RequestBodyLimitMiddleware') < "
        "m.index('django.middleware.csrf.CsrfViewMiddleware'); "
        "assert settings.DATA_UPLOAD_MAX_NUMBER_FILES == 1; "
        "assert settings.SESSION_COOKIE_AGE == 604800"
    )
    subprocess.run(command + ['shell', '-c', code], check=True, timeout=60, stdout=log, stderr=log)


def main():
    if os.geteuid() != 0 or len(sys.argv) != 2:
        raise SystemExit('Run with sudo python3 apply-request-limits.py release.tar.gz')
    os.umask(0o077)
    if APP.is_symlink() or STATE.is_symlink():
        raise ValueError('Unexpected installation path')
    helper, payload = package(sys.argv[1])
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / 'seo-update.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with tempfile.TemporaryDirectory(prefix='oblik-request-guard-') as temporary:
            helper_path = Path(temporary) / 'guard.py'
            helper_path.write_bytes(helper)
            spec = importlib.util.spec_from_file_location('request_update_guard', helper_path)
            guard = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(guard)
            check_support(guard, APP)
            originals = guard.verify_baseline(APP, {'files': FILES}, payload)
            env_path = Path('/etc/oxxom-oblik/app.env')
            env_before = env_path.read_bytes()
            guard.smoke()
            if all(originals[name] == value for name, value in payload.items()):
                print('SECURITY-01B-REQUEST OK: already installed')
                return
            backup = Path(tempfile.mkdtemp(prefix='request-limits-', dir=STATE))
            defaults = (APP / 'backend/config/settings.py').stat()
            info = {name: (APP / name).stat() if (APP / name).exists() else defaults for name in FILES}
            for name, data in originals.items():
                if data is not None:
                    target = backup / 'originals' / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(APP / name, target)
            (backup / 'receipt.json').write_text(json.dumps({
                'release': 'SECURITY-01B-REQUEST', 'files': FILES,
            }, indent=2))
            print('Recovery files: ' + str(backup), flush=True)
            with (backup / 'checks.log').open('w') as log:
                def validate():
                    service_check(log)
                    if env_path.read_bytes() != env_before:
                        raise RuntimeError('Environment changed concurrently')
                    guard.restart(log)
                    guard.smoke()
                try:
                    guard.apply_payload(APP, payload, originals, info, validate)
                except BaseException:
                    guard.restart(log)
                    guard.smoke()
                    print('UPDATE FAILED: original application files restored', flush=True)
                    raise
    print('SECURITY-01B-REQUEST OK: request parsing bounded before CSRF; service checks passed')
    print('No migrations. Environment, MFA keys, session duration, NPM and backup automation unchanged.')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        message = str(error) if type(error).__name__ == 'PreflightError' else type(error).__name__
        print('STOP: ' + message + '; values omitted; review the recovery log if printed', file=sys.stderr)
        sys.exit(1)
