"""Guarded session-security update; no schema or dependency changes."""
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
FILES = {'backend/accounts/auth_views.py': {'before': '3c0161dd9968adb9811ff93b1b7026e09787e2c2e5d77682b3b849b67f5fba09',
                                    'after': 'f0f868ed9084a25a8b11c102379df82f11f6c66fcc2179973b41ba1bc0af7ffe'},
 'backend/accounts/session_security.py': {'before': None,
                                          'after': 'b676bbab14aadf17a6de3c20961bb3d5565febd38cdf030e46ac761cf7abde58'},
 'backend/accounts/static/accounts/login.js': {'before': '5e70dfd0cd9fb9b07a92497180cda0a0544a00598ce54fe97384ed03edaa6e08',
                                               'after': '7c2328d84335f031fdc92922830ff7d7ae87da737d0c796361dbaeff1d8d80ab'},
 'backend/accounts/templates/accounts/login.html': {'before': '187f4ccc1864c3ffb1571a67d506bd918b8478ca953dd5aacb1638820dc6c363',
                                                    'after': '651fd8dd4bcebab0d647e7f65307dbd7dbbe439c1eb0f766e43d3c966281acb6'},
 'backend/config/settings.py': {'before': '2797f3f6baf82dc6262bd63b672e17635aafe42150a943720552f9ec6b2592e4',
                                'after': 'b49e2d69b8545c2831d0d9b92f6031e8f46f96d6173aa13c9cc1637f448e80c5'},
 'backend/config/urls.py': {'before': 'd40c9c4c2bcd81895cb7cc0659d0a63181550e92ab204fa2db0ff1e8d6cfa07a',
                            'after': '3a1206c2fc7c9b680e94b690c0ab4353a0aa0091053de0d9a9d10bbfac8b3041'},
 'backend/core/locale/ui.json': {'before': '4c99a884dcf1bf2113c7f218e35ee594d934bdc97eeec0e22a0705b999ad9842',
                                 'after': '305f556b8d7ee8d7933ae76672d02c73428194ab9e2c9184a1472e153a0dccb6'},
 'backend/core/static/core/ui/translations.js': {'before': 'bfd44a3ba2b372e6cb79d0ef25af02c51e124dcf34b03175dc110b54cf1f28ed',
                                                 'after': 'f20dca09787e21ac0de03902c28c9d9cbaa53abd0f8be11a40d31e714a6a368e'},
 'backend/core/templates/core/ui-head.html': {'before': '9fffee154e4452e7406a66e37a0f5299c4b9282f58d5ad5c8eed6bc517cc9f3d',
                                              'after': 'a8845430e839e4bcc0c427024d637c361f941a8818ccd72b03218d2caa33c94f'}}


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
        if name.endswith('.py'):
            compile(data, name, 'exec')
        elif name.endswith('.json'):
            json.loads(data)
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
        "from django.urls import resolve; "
        "from django.template.loader import get_template; "
        "from accounts.auth_views import terminate_other_sessions; "
        "assert resolve('/api/v1/auth/sessions/revoke-others').func == terminate_other_sessions; "
        "get_template('accounts/login.html'); "
        "assert settings.SESSION_COOKIE_AGE == 604800; "
        "assert not settings.SESSION_EXPIRE_AT_BROWSER_CLOSE; "
        "assert settings.DATA_UPLOAD_MAX_NUMBER_FILES == 1; "
        "m=settings.MIDDLEWARE; "
        "assert m.index('core.request_limits.RequestBodyLimitMiddleware') < "
        "m.index('django.middleware.csrf.CsrfViewMiddleware'); "
        "assert settings.LOGGING['loggers']['oxxom.security']['level'] == 'INFO'"
    )
    subprocess.run(command + ['shell', '-c', code], check=True, timeout=60, stdout=log, stderr=log)


def main():
    if os.geteuid() != 0 or len(sys.argv) != 2:
        raise SystemExit('Run with sudo python3 apply-session-security.py release.tar.gz')
    os.umask(0o077)
    if APP.is_symlink() or STATE.is_symlink():
        raise ValueError('Unexpected installation path')
    helper, payload = package(sys.argv[1])
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / 'seo-update.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with tempfile.TemporaryDirectory(prefix='oblik-session-guard-') as temporary:
            helper_path = Path(temporary) / 'guard.py'
            helper_path.write_bytes(helper)
            spec = importlib.util.spec_from_file_location('session_update_guard', helper_path)
            guard = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(guard)
            check_support(guard, APP)
            originals = guard.verify_baseline(APP, {'files': FILES}, payload)
            env_path = Path('/etc/oxxom-oblik/app.env')
            env_before = env_path.read_bytes()
            guard.smoke()
            if all(originals[name] == value for name, value in payload.items()):
                with tempfile.TemporaryFile(mode='w+') as log:
                    service_check(log)
                if guard.request('/api/v1/auth/sessions/revoke-others')[0] != 405:
                    raise RuntimeError('Session-control route check failed')
                print('SECURITY-01B-SESSIONS OK: already installed')
                return
            backup = Path(tempfile.mkdtemp(prefix='session-security-', dir=STATE))
            defaults = (APP / 'backend/config/settings.py').stat()
            info = {name: (APP / name).stat() if (APP / name).exists() else defaults for name in FILES}
            for name, data in originals.items():
                if data is not None:
                    target = backup / 'originals' / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(APP / name, target)
            (backup / 'receipt.json').write_text(json.dumps({
                'release': 'SECURITY-01B-SESSIONS', 'files': FILES,
            }, indent=2))
            print('Recovery files: ' + str(backup), flush=True)
            with (backup / 'checks.log').open('w') as log:
                def validate():
                    service_check(log)
                    if env_path.read_bytes() != env_before:
                        raise RuntimeError('Environment changed concurrently')
                    guard.restart(log)
                    guard.smoke()
                    if guard.request('/api/v1/auth/sessions/revoke-others')[0] != 405:
                        raise RuntimeError('Session-control route check failed')
                try:
                    guard.apply_payload(APP, payload, originals, info, validate)
                except BaseException:
                    guard.restart(log)
                    guard.smoke()
                    print('UPDATE FAILED: original application files restored', flush=True)
                    raise
    print('SECURITY-01B-SESSIONS OK: fresh MFA session revocation installed; service checks passed')
    print('No migrations. Environment, MFA keys, session duration, NPM and backup automation unchanged.')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        message = str(error) if type(error).__name__ == 'PreflightError' else type(error).__name__
        print('STOP: ' + message + '; values omitted; review the recovery log if printed', file=sys.stderr)
        sys.exit(1)
