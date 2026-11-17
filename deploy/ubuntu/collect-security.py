"""Read-only, allowlisted SECURITY-01 inventory. Never prints environment values."""
import json
import os
from pathlib import Path
import subprocess
import sys

APP = Path('/srv/oxxom-oblik/app')
PYTHON = '/srv/oxxom-oblik/venv/bin/python'

CHILD = r'''
import importlib.metadata, json, os, sys
from pathlib import Path
sys.path.insert(0, '/srv/oxxom-oblik/app/deploy/ubuntu')
from environment_file import read_env
values=read_env('/etc/oxxom-oblik/app.env')
if values.get('APP_ENV') != 'production':
    raise RuntimeError('Expected production configuration')
os.environ.update(values)
os.environ['DJANGO_SETTINGS_MODULE']='config.settings'
sys.path.insert(0, '/srv/oxxom-oblik/app/backend')
import django
django.setup()
from django.conf import settings
from django.core.checks import run_checks
result={'application': {
    'debug':settings.DEBUG, 'explicit_hosts':bool(settings.ALLOWED_HOSTS) and '*' not in settings.ALLOWED_HOSTS,
    'https_redirect':settings.SECURE_SSL_REDIRECT, 'session_days':settings.SESSION_COOKIE_AGE/86400,
    'session_secure':settings.SESSION_COOKIE_SECURE, 'session_httponly':settings.SESSION_COOKIE_HTTPONLY,
    'session_samesite':settings.SESSION_COOKIE_SAMESITE, 'csrf_secure':settings.CSRF_COOKIE_SECURE,
    'csrf_https_origins':bool(settings.CSRF_TRUSTED_ORIGINS) and all(x.startswith('https://') for x in settings.CSRF_TRUSTED_ORIGINS),
    'django_trusts_forwarded_proto':bool(getattr(settings,'SECURE_PROXY_SSL_HEADER',None)),
    'exact_auth_proxy_peers':list(getattr(settings,'OXXOM_AUTH_PROXY_IPS',())),
    'mfa_key_configured':bool(settings.OXXOM_MFA_KEY), 'hsts_seconds':settings.SECURE_HSTS_SECONDS,
    'branded_errors':'core.errors.ErrorPageMiddleware' in settings.MIDDLEWARE,
    'deployment_check_ids':[x.id for x in run_checks(tags=['security'],include_deployment_checks=True)],
},'packages':{name:importlib.metadata.version(name) for name in ('Django','gunicorn','psycopg','cryptography','pyotp','qrcode')}}
try:
    from django.db import connection
    with connection.cursor() as cursor:
        cursor.execute('SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls FROM pg_roles WHERE rolname=current_user')
        row=cursor.fetchone()
        result['database_role']=dict(zip(('superuser','create_database','create_role','replication','bypass_rls'),row))
        cursor.execute("SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()")
        result['database_connection_tls']=cursor.fetchone()[0]
    from django.db.migrations.executor import MigrationExecutor
    executor=MigrationExecutor(connection)
    result['migrations_pending']=len(executor.migration_plan(executor.loader.graph.leaf_nodes()))
except Exception as error:
    result['database_check']={'status':'BLOCKED','type':type(error).__name__}
print(json.dumps(result))
'''


def command(args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=30)
        return result.returncode, result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None, ''


def main():
    if os.geteuid() != 0:
        raise SystemExit('Run with sudo on the Ubuntu application VM')
    rc, raw = command([PYTHON, '-B', '-c', CHILD])
    try:
        result = json.loads(raw) if rc == 0 else {'application': {'status': 'BLOCKED'}}
    except ValueError:
        result = {'application': {'status': 'BLOCKED'}}
    result['service'] = {}
    for key in ('ActiveState', 'User', 'NoNewPrivileges', 'PrivateTmp', 'ProtectSystem',
                'ProtectHome', 'ProtectKernelTunables', 'ProtectKernelModules', 'RestrictSUIDSGID'):
        rc, value = command(['systemctl', 'show', 'oxxom-oblik', '--property=' + key, '--value'])
        result['service'][key] = value if rc == 0 else 'UNKNOWN'
    result['private_files'] = {}
    for name in ('/etc/oxxom-oblik/app.env', '/etc/oxxom-oblik/backup/windows.credentials'):
        try:
            info = Path(name).stat()
            result['private_files'][name] = {'mode': oct(info.st_mode & 0o777), 'uid': info.st_uid, 'gid': info.st_gid}
        except OSError:
            result['private_files'][name] = {'status': 'UNKNOWN'}
    result['backup_evidence_exists'] = {name: Path('/var/lib/oxxom-oblik-backup', name).is_file()
                                       for name in ('first-success.json', 'restore-success.json')}
    rc, timers = command(['systemctl', 'list-timers', '--all', '--no-pager', '--no-legend'])
    result['oblik_timers'] = [line for line in timers.splitlines() if 'oxxom-oblik' in line] if rc == 0 else 'UNKNOWN'
    result['unverified'] = ['external TLS and proxy headers', 'SSH and network ACL policy',
                            'backup schedule and alert delivery', 'independent immutable backup',
                            'dependency advisory scan', 'complete ASVS applicability matrix']
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
