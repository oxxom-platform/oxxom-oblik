"""DEMO-01: fixture-only booking. Exact files, DB backup, additive migration, rollback."""
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
MANIFEST = 'deploy/ubuntu/demo-release.json'
BASE_HELPER = 'deploy/ubuntu/apply-security-update.py'
BASE_HELPER_SHA = '5f68b67de8db6b78598926e557755baa15636c3faabddbf23a470a41b7330dfc'
EXISTING = {
    'backend/config/settings.py', 'backend/config/urls.py', 'backend/core/views.py',
    'backend/core/seo.py', 'backend/core/templates/core/landing.html',
    'backend/core/static/core/landing.css', 'backend/core/locale/ui.json',
    'backend/core/static/core/ui/translations.js',
}
NEW = {
    'backend/demo_booking/__init__.py', 'backend/demo_booking/models.py',
    'backend/demo_booking/services.py', 'backend/demo_booking/copy.py',
    'backend/demo_booking/copy.json', 'backend/demo_booking/views.py',
    'backend/demo_booking/mail.py', 'backend/demo_booking/middleware.py',
    'backend/demo_booking/migrations/__init__.py',
    'backend/demo_booking/migrations/0001_initial.py',
    'backend/demo_booking/management/commands/deliver_demo_mail.py',
    'backend/demo_booking/templates/demo_booking/page.html',
    'backend/core/static/core/demo-booking.css', 'backend/core/static/core/demo-booking.js',
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def package(archive):
    with tarfile.open(archive, 'r:gz') as bundle:
        files = {}
        for item in bundle.getmembers():
            path = Path(item.name)
            if path.is_absolute() or '..' in path.parts:
                raise ValueError('Unsafe archive path')
            name = '/'.join(path.parts[1:])
            if item.isfile():
                if name in files:
                    raise ValueError('Duplicate archive member')
                files[name] = item
        def read(name, limit=2 * 1024 * 1024):
            item = files.get(name)
            if item is None or item.size > limit:
                raise ValueError('Missing or oversized release file')
            return bundle.extractfile(item).read()
        manifest = json.loads(read(MANIFEST, 65536))
        if manifest.get('release') != 'DEMO-01' or set(manifest['files']) != EXISTING | NEW:
            raise ValueError('Wrong release scope')
        helper = read(BASE_HELPER)
        if sha(helper) != BASE_HELPER_SHA:
            raise ValueError('Guard helper checksum mismatch')
        payload = {}
        for name, hashes in manifest['files'].items():
            before = hashes['before']
            if name in EXISTING and (not isinstance(before, str) or len(before) != 64
                                     or set(before) - set('0123456789abcdef')):
                raise ValueError('Existing files require an exact reviewed baseline')
            data = read(name)
            if sha(data) != hashes['after'] or (name in NEW and hashes['before'] is not None):
                raise ValueError('Payload checksum or baseline mismatch')
            payload[name] = data
        return manifest, helper, payload


def load_guard(helper, directory):
    if sha(helper) != BASE_HELPER_SHA:
        raise ValueError('Untrusted guard helper')
    path = directory / 'guard.py'
    path.write_bytes(helper)
    spec = importlib.util.spec_from_file_location('oblik_guard', path)
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)  # A checksum-pinned helper with guarded main.
    return guard


def backup_database(values, destination, log):
    env = dict(os.environ)
    env.update(values)
    env['PGPASSWORD'] = values['POSTGRES_PASSWORD']
    subprocess.run([
        'pg_dump', '--format=custom', '--no-password', '--file', str(destination),
        '--host', values.get('POSTGRES_HOST', '127.0.0.1'),
        # Match config.environment.configuration's fallback, not a guessed port.
        '--port', values.get('POSTGRES_PORT', '55432'), '--username', values['POSTGRES_USER'],
        '--dbname', values['POSTGRES_DB'],
    ], env=env, check=True, timeout=180, stdout=log, stderr=log)
    if destination.stat().st_size < 100:
        raise RuntimeError('Database backup is unexpectedly empty')
    subprocess.run(['pg_restore', '--list', str(destination)], check=True, timeout=30, stdout=log, stderr=log)


def verify(guard):
    guard.smoke()
    for code, prefix in [('uk',''), ('en','en/'), ('ru','ru/'), ('cs','cs/')]:
        status, headers, body = guard.request('/' + prefix + 'demo/')
        if (status != 200 or b'id="booking-form"' not in body or b'demo@example.test' not in body
                or headers.get('Content-Language') != code or 'noindex' not in headers.get('X-Robots-Tag','')
                or b'name="email"' in body or b'name="phone"' in body):
            raise RuntimeError('Fixture-only demo page check failed')
        status, _, body = guard.request('/' + prefix)
        if status != 200 or ('href="/' + prefix + 'demo/"').encode() not in body or b'mailto:info@oxxom.com.ua' not in body:
            raise RuntimeError('Landing CTA or footer check failed')


def main():
    if os.geteuid() != 0 or len(sys.argv) != 2:
        raise SystemExit('Run with sudo on Ubuntu: python3 apply-demo-update.py release.tar.gz')
    os.umask(0o077)
    if APP.is_symlink() or STATE.is_symlink():
        raise ValueError('Unexpected installation path')
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / 'seo-update.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        manifest, helper, payload = package(sys.argv[1])
        with tempfile.TemporaryDirectory(prefix='oblik-demo-guard-') as temporary:
            guard = load_guard(helper, Path(temporary))
            originals = guard.verify_baseline(APP, manifest, payload)
            sys.path.insert(0, str(APP / 'deploy/ubuntu'))
            from environment_file import read_env
            env_path = Path('/etc/oxxom-oblik/app.env')
            env_bytes = env_path.read_bytes()
            values = read_env(env_path)
            if values.get('APP_ENV') != 'production':
                raise ValueError('Expected production configuration')
            guard.smoke()
            backup = Path(tempfile.mkdtemp(prefix='demo-', dir=STATE))
            defaults = (APP / 'backend/config/settings.py').stat()
            info = {name: (APP / name).stat() if (APP / name).exists() else defaults for name in payload}
            for name, original in originals.items():
                if original is not None:
                    target = backup / 'originals' / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(APP / name, target)
            shutil.copy2(env_path, backup / 'app.env')
            os.chmod(backup / 'app.env', 0o600)
            (backup / 'receipt.json').write_text(json.dumps({
                'release': manifest['release'], 'before': {n: sha(v) if v is not None else None for n,v in originals.items()},
                'after': {n: sha(v) for n,v in payload.items()}, 'rollback_schema': 'keep additive demo tables and receipts',
            }, indent=2))
            print('Private recovery files: ' + str(backup), flush=True)
            with (backup / 'checks.log').open('w') as log:
                print('[1/3] Backing up the database before application changes...', flush=True)
                backup_database(values, backup / 'before.dump', log)
                def validate():
                    print('[2/3] Checking code and applying the additive demo migration...', flush=True)
                    for arguments in (['check'], ['migrate', 'demo_booking', '--noinput']):
                        subprocess.run([PYTHON, '-B', str(APP / 'deploy/ubuntu/manage.py'), *arguments],
                                       check=True, timeout=90, stdout=log, stderr=log)
                    if env_path.read_bytes() != env_bytes:
                        raise RuntimeError('Environment changed concurrently')
                    print('[3/3] Restarting OBLIK and checking pages, API and test mode...', flush=True)
                    guard.restart(log)
                    verify(guard)
                try:
                    guard.apply_payload(APP, payload, originals, info, validate)
                except BaseException:
                    guard.restart(log)
                    guard.smoke()
                    print('UPDATE FAILED: original application files restored; additive demo tables retained; see recovery log', flush=True)
                    raise
            print('DEMO-01 OK: four-language prototype, fixture-only requests, email contact and existing health verified')
            print('No visitor contacts accepted. SMTP delivery stays disabled unless separately configured.')
            print('Nginx, MFA keys, session duration, existing business records and backup automation were not changed.')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Controlled baseline errors list public paths, never credential values.
        message = str(error) if type(error).__name__ == 'PreflightError' else type(error).__name__
        print('STOP: ' + message + '; see the printed private recovery directory if available; values omitted', file=sys.stderr)
        sys.exit(1)
