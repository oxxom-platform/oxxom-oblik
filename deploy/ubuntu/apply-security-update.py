"""SECURITY-01A: guarded file-only update, no DB/environment/key changes."""
import fcntl
import hashlib
import http.client
from ipaddress import ip_address
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time

APP = Path('/srv/oxxom-oblik/app')
STATE = Path('/srv/oxxom-oblik/update-backups')
PYTHON = '/srv/oxxom-oblik/venv/bin/python'
MANIFEST = 'deploy/ubuntu/security-release.json'
PAYLOAD_FILES = {
    'backend/accounts/auth_views.py', 'backend/accounts/client_address.py',
    'backend/config/settings.py', 'backend/config/urls.py',
    'backend/core/errors.py', 'backend/core/locale_middleware.py',
    'backend/core/locale/errors.json', 'backend/core/static/core/errors.css',
    'backend/core/templates/core/error.html',
    'backend/core/management/commands/build_error_pages.py',
    'deploy/ubuntu/collect-security.py',
}


class PreflightError(ValueError):
    """Controlled diagnostic containing only public file paths or policy text."""


def sha(data):
    return hashlib.sha256(data).hexdigest()


def safe_target(root, name):
    parts = Path(name).parts
    if not parts or Path(name).is_absolute() or '..' in parts:
        raise ValueError('Unsafe release path')
    target = root / name
    if any(p.is_symlink() for p in (target, *target.parents)):
        raise ValueError('Symlink in application path')
    if target.exists() and not target.is_file():
        raise ValueError('Unexpected application file type')
    return target


def read_package(archive):
    with tarfile.open(archive, 'r:gz') as bundle:
        files = {}
        for member in bundle.getmembers():
            parts = Path(member.name).parts
            if not parts or '..' in parts or Path(member.name).is_absolute():
                raise ValueError('Unsafe archive path')
            name = '/'.join(parts[1:])
            if member.isfile():
                if name in files:
                    raise ValueError('Duplicate archive member')
                files[name] = member
        member = files.get(MANIFEST)
        if member is None or member.size > 65536:
            raise ValueError('Missing release manifest')
        manifest = json.loads(bundle.extractfile(member).read())
        if manifest.get('release') != 'SECURITY-01A':
            raise ValueError('Wrong release manifest')
        if set(manifest['files']) != PAYLOAD_FILES:
            raise ValueError('Unexpected release file allowlist')
        payload = {}
        for name, hashes in manifest['files'].items():
            safe_target(APP, name)
            member = files.get(name)
            if member is None or member.size > 1024 * 1024:
                raise ValueError('Missing or oversized release file')
            data = bundle.extractfile(member).read()
            if sha(data) != hashes['after']:
                raise ValueError('Release checksum mismatch')
            payload[name] = data
        return manifest, payload


def verify_baseline(root, manifest, payload):
    originals = {}
    for name, data in payload.items():
        path = safe_target(root, name)
        original = path.read_bytes() if path.exists() else None
        digest = sha(original) if original is not None else None
        if digest not in (manifest['files'][name]['before'], sha(data)):
            raise PreflightError('Unreviewed server change; application unchanged: ' + name)
        originals[name] = original
    return originals


def replace(path, data, info):
    missing = []
    parent = path.parent
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    for parent in reversed(missing):
        parent.mkdir(mode=0o750)
        os.chmod(parent, 0o750)
        os.chown(parent, info.st_uid, info.st_gid)
    fd, temporary = tempfile.mkstemp(prefix='.security-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), stat.S_IMODE(info.st_mode))
            os.fchown(handle.fileno(), info.st_uid, info.st_gid)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def request(path):
    connection = http.client.HTTPConnection('127.0.0.1', 4181, timeout=5)
    try:
        connection.request('GET', path, headers={'Host': 'oblik.oxxom.com.ua', 'X-Forwarded-Proto': 'https'})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def restart(log):
    subprocess.run(['systemctl', 'restart', 'oxxom-oblik'], check=True, timeout=60, stdout=log, stderr=log)


def smoke(updated=True):
    for _ in range(20):
        try:
            if request('/api/v1/health')[0] == 200:
                break
        except OSError:
            pass
        time.sleep(1)
    else:
        raise RuntimeError('Application health did not recover')
    for path in ('/', '/en/', '/ru/', '/cs/', '/login/', '/api/v1/ready'):
        if request(path)[0] != 200:
            raise RuntimeError('Application response failed: ' + path)
    if updated:
        status, headers, body = request('/__oblik_nonexistent_security_check__')
        if status != 404 or b'HTTP 404' not in body or 'no-store' not in headers.get('Cache-Control', ''):
            raise RuntimeError('Branded 404 check failed')
        status, headers, body = request('/api/v1/__oblik_nonexistent_security_check__')
        if status != 404 or json.loads(body) != {'error': 'not_found'}:
            raise RuntimeError('API error contract check failed')


def apply_payload(root, payload, originals, info, validate):
    """Rollback includes new files; validate is supplied separately for fault tests."""
    try:
        for name, data in payload.items():
            replace(safe_target(root, name), data, info[name])
        validate()
    except BaseException:
        for name, original in originals.items():
            target = safe_target(root, name)
            if original is None:
                target.unlink(missing_ok=True)
            else:
                replace(target, original, info[name])
        raise


def main():
    if os.geteuid() != 0 or len(sys.argv) != 2:
        raise SystemExit('Run with sudo on Ubuntu: python3 apply-security-update.py release.tar.gz')
    os.umask(0o077)
    if STATE.is_symlink() or APP.is_symlink():
        raise ValueError('Unexpected installation path')
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / 'seo-update.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        manifest, payload = read_package(sys.argv[1])
        originals = verify_baseline(APP, manifest, payload)
        sys.path.insert(0, str(APP / 'deploy/ubuntu'))
        from environment_file import read_env
        env_path = Path('/etc/oxxom-oblik/app.env')
        env_bytes = env_path.read_bytes()
        values = read_env(env_path)
        if values.get('APP_ENV') != 'production':
            raise ValueError('Review production configuration before this update')
        try:
            proxy = ip_address(values.get('OXXOM_PROXY_IP', ''))
        except ValueError:
            raise PreflightError('Configure one exact OXXOM_PROXY_IP before updating; values omitted') from None
        if proxy.is_unspecified or proxy.is_multicast:
            raise ValueError('An exact trusted proxy peer is required before this update')
        smoke(updated=False)
        if all(originals[name] == data for name, data in payload.items()):
            smoke()
            print('SECURITY-01A OK: already installed; Nginx pages require separate installation')
            return
        backup = Path(tempfile.mkdtemp(prefix='security-', dir=STATE))
        defaults = (APP / 'backend/config/settings.py').stat()
        info = {name: (APP / name).stat() if (APP / name).exists() else defaults for name in payload}
        for name, original in originals.items():
            if original is not None:
                destination = backup / 'originals' / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(APP / name, destination)
        (backup / 'receipt.json').write_text(json.dumps({
            'release': manifest['release'], 'before': {n: sha(v) if v is not None else None for n, v in originals.items()},
            'after': {n: sha(v) for n, v in payload.items()},
        }, indent=2))
        print('Recovery files: ' + str(backup), flush=True)
        with (backup / 'checks.log').open('w') as log:
            def validate():
                subprocess.run([PYTHON, '-B', str(APP / 'deploy/ubuntu/manage.py'), 'check'], check=True,
                               timeout=60, stdout=log, stderr=log)
                subprocess.run([PYTHON, '-B', str(APP / 'deploy/ubuntu/manage.py'), 'build_error_pages', str(backup / 'proxy-pages')],
                               check=True, timeout=60, stdout=log, stderr=log)
                if env_path.read_bytes() != env_bytes:
                    raise RuntimeError('Environment changed concurrently; stop and review')
                restart(log)
                smoke()
            try:
                apply_payload(APP, payload, originals, info, validate)
            except BaseException:
                restart(log)
                smoke(updated=False)
                print('UPDATE FAILED: application files rolled back; recovery evidence retained', flush=True)
                raise
        print('SECURITY-01A OK: proxy-aware throttling, branded errors and API responses checked')
        print('Database, environment, keys and existing session records were not modified by the updater')
        print('Nginx error pages BUILT, NOT installed: ' + str(backup / 'proxy-pages'))
        print('Read-only security inventory: sudo ' + PYTHON + ' ' + str(APP / 'deploy/ubuntu/collect-security.py'))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        if isinstance(error, PreflightError):
            print('STOP: ' + str(error), file=sys.stderr)
        else:
            print('STOP: ' + type(error).__name__ + '; secret values omitted. If a recovery directory was printed, review its checks.log. Otherwise preflight did not complete.', file=sys.stderr)
        sys.exit(1)
