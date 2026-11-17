"""Apply the reviewed SEO file set to the archive-based Ubuntu installation."""
import argparse
import fcntl
import hashlib
import http.client
import json
import os
from pathlib import Path, PurePosixPath
import secrets
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
SERVICE = 'oxxom-oblik'
MANIFEST = 'deploy/ubuntu/seo-release.json'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_release(archive):
    """Never extract arbitrary archive paths, links or private files."""
    with tarfile.open(archive, 'r:gz') as bundle:
        members = bundle.getmembers()
        manifests = [item for item in members if item.name.endswith('/' + MANIFEST)]
        if len(manifests) != 1 or not manifests[0].isfile() or manifests[0].size > 100_000:
            raise ValueError('Expected one regular release manifest')
        prefix = manifests[0].name[:-len(MANIFEST)]
        manifest = json.load(bundle.extractfile(manifests[0]))
        if manifest.get('release') != 'SEO-01' or not 20 <= len(manifest['files']) <= 60:
            raise ValueError('Unexpected release manifest')
        payload = {}
        for name, entry in manifest['files'].items():
            path = PurePosixPath(name)
            if (path.is_absolute() or '..' in path.parts or str(path) != name
                    or not name.startswith('backend/') or any(part.startswith('.') for part in path.parts)):
                raise ValueError('Unexpected release path')
            matches = [item for item in members if item.name == prefix + name]
            if len(matches) != 1 or not matches[0].isfile() or matches[0].size > 2_000_000:
                raise ValueError('Missing or unsafe release member: ' + name)
            data = bundle.extractfile(matches[0]).read()
            if digest(data) != entry['sha256']:
                raise ValueError('Release checksum mismatch: ' + name)
            payload[name] = data
        return manifest, payload


def previous_files(app, manifest):
    previous = {}
    if app.is_symlink() or not app.is_dir():
        raise ValueError('Unexpected application directory')
    for name, entry in manifest['files'].items():
        path = app / name
        for parent in path.parents:
            if parent == app:
                break
            if parent.is_symlink():
                raise ValueError('Symlink in application path')
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ValueError('Unexpected application file: ' + name)
        data = path.read_bytes() if path.exists() else None
        checksum = digest(data) if data is not None else None
        if checksum not in entry['before'] + [entry['sha256']]:
            raise ValueError('Unreviewed server change; nothing overwritten: ' + name)
        info = path.stat() if data is not None else None
        previous[name] = {'sha256': checksum, 'mode': stat.S_IMODE(info.st_mode) if info else 0o644,
                          'uid': info.st_uid if info else 0, 'gid': info.st_gid if info else 0}
    return previous


def replace_file(path, data, info):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.seo-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), info['mode'])
            if os.geteuid() == 0:
                os.fchown(handle.fileno(), info['uid'], info['gid'])
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def restore_files(app, backup, previous):
    for name, info in previous.items():
        target = app / name
        if info['sha256'] is None:
            target.unlink(missing_ok=True)
        else:
            data = (backup / name).read_bytes()
            if digest(data) != info['sha256']:
                raise ValueError('Rollback checksum mismatch')
            replace_file(target, data, info)


def run(arguments, log, **options):
    return subprocess.run(arguments, stdout=log, stderr=log, check=True, timeout=180, **options)


def check_candidate(app, payload, log):
    with tempfile.TemporaryDirectory(prefix='seo-check-') as directory:
        candidate = Path(directory)
        shutil.copytree(app / 'backend', candidate / 'backend',
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.sqlite3'))
        for name, data in payload.items():
            target = candidate / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        check = '''import sys, os
sys.path.insert(0, sys.argv[1])
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings'
from django.conf import settings
settings.DATABASES = {'default': {'ENGINE': 'django.db.backends.dummy'}}
settings.SECURE_SSL_REDIRECT = False
settings.OXXOM_INDEX_PUBLIC = True
import django
django.setup()
from django.core.management import call_command
call_command('test', 'core.test_seo', 'core.test_landing', 'core.test_i18n.InterfaceCatalogTests', verbosity=1)
'''
        environment = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'APP_ENV': 'test',
                       'DJANGO_SECRET_KEY': secrets.token_urlsafe(48), 'POSTGRES_DB': 'unused',
                       'POSTGRES_USER': 'unused', 'POSTGRES_PASSWORD': secrets.token_urlsafe(32)}
        run([PYTHON, '-c', check, str(candidate / 'backend')], log, env=environment, cwd=directory)


def request(path):
    connection = http.client.HTTPConnection('127.0.0.1', 4181, timeout=5)
    try:
        connection.request('GET', path, headers={'Host': 'oblik.oxxom.com.ua', 'X-Forwarded-Proto': 'https'})
        response = connection.getresponse()
        return response.status, dict((key.lower(), value) for key, value in response.getheaders()), response.read()
    finally:
        connection.close()


def smoke():
    for attempt in range(15):
        try:
            if request('/api/v1/health')[0] == 200:
                break
        except OSError:
            pass
        time.sleep(1)
    else:
        raise RuntimeError('Application health did not recover')
    for path, language in [('/', 'uk'), ('/en/', 'en'), ('/ru/', 'ru'), ('/cs/', 'cs')]:
        status, headers, body = request(path)
        if (status != 200 or headers.get('content-language') != language or 'x-robots-tag' in headers
                or b'rel="canonical"' not in body or b'favicon-oblik.png' not in body):
            raise RuntimeError('Public page check failed: ' + path)
    for path in ('/robots.txt', '/sitemap.xml', '/favicon.ico', '/assets/brand/favicon-oblik.png'):
        if request(path)[0] != 200:
            raise RuntimeError('Public resource check failed: ' + path)
    for path, expected in [('/login/', 200), ('/api/v1/organizations', 401)]:
        status, headers, _ = request(path)
        if status != expected or headers.get('x-robots-tag') != 'noindex, nofollow':
            raise RuntimeError('Private route protection check failed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--commit', required=True)
    arguments = parser.parse_args()
    if os.geteuid() != 0 or len(arguments.commit) != 40 or any(c not in '0123456789abcdef' for c in arguments.commit):
        raise SystemExit('Run with sudo and the reviewed full commit ID')
    os.umask(0o077)
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / 'seo-update.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        manifest, payload = read_release(arguments.archive)
        previous = previous_files(APP, manifest)
        receipt = Path(tempfile.mkdtemp(prefix='seo-', dir=STATE))
        (receipt / 'receipt.json').write_text(json.dumps({'commit': arguments.commit, 'previous': previous}, indent=2))
        with (receipt / 'checks.log').open('w') as log:
            print('[1/4] Checking the candidate without a database connection...', flush=True)
            check_candidate(APP, payload, log)
            print('[2/4] Creating a fresh recovery backup using the existing Windows target...', flush=True)
            config = Path('/etc/oxxom-oblik/backup')
            if not all((config / name).is_file() for name in ('restic-data.password', 'restic-keys.password')):
                raise RuntimeError('Existing backup configuration is required')
            run([PYTHON, str(APP / 'deploy/ubuntu/first-backup.py')], log, stdin=subprocess.DEVNULL)
            for name, info in previous.items():
                if info['sha256'] is not None:
                    target = receipt / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(APP / name, target)
            # Recheck after the backup: never silently overwrite an intervening server edit.
            if previous_files(APP, manifest) != previous:
                raise RuntimeError('Application changed during preparation')
            print('[3/4] Applying the reviewed files and restarting the application...', flush=True)
            try:
                run(['systemctl', 'stop', SERVICE], log)
                for name, data in payload.items():
                    replace_file(APP / name, data, previous[name])
                run(['systemctl', 'start', SERVICE], log)
                print('[4/4] Checking languages, indexing, favicon and private routes...', flush=True)
                smoke()
            except BaseException:
                run(['systemctl', 'stop', SERVICE], log)
                restore_files(APP, receipt, previous)
                run(['systemctl', 'start', SERVICE], log)
                raise RuntimeError('SEO update failed; previous application files restored') from None
        (receipt / 'success.json').write_text(json.dumps({'commit': arguments.commit, 'local_smoke_passed': True}))
        print('SEO-01 OK: four languages, metadata, sitemap, noindex and OBLIK favicon')
        print('Recovery files: ' + str(receipt))
        print('External HTTPS, Search Console and field performance still require verification')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, tarfile.TarError) as error:
        raise SystemExit('SEO UPDATE STOPPED: ' + str(error)) from None
