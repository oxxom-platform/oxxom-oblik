"""Export local Docker PostgreSQL and preserve authentication keys for Ubuntu.

Run with the project's Python environment. No key or password is printed.
The destination is private, must not exist, and must be outside the repository.
"""
import argparse
import os
from pathlib import Path
import secrets
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'deploy/ubuntu'))
from environment_file import domain_name, read_env


def production_values(local, mfa, domain):
    from cryptography.fernet import Fernet
    Fernet(mfa.encode())
    secret = local.get('DJANGO_SECRET_KEY', '')
    if len(secret) < 50 or secret.startswith('CHANGE_ME'):
        raise ValueError('Valid existing DJANGO_SECRET_KEY is required')
    return dict(APP_ENV='production', DJANGO_DEBUG='false', DJANGO_SECRET_KEY=secret,
                DJANGO_ALLOWED_HOSTS=domain, DJANGO_CSRF_TRUSTED_ORIGINS='https://' + domain,
                POSTGRES_DB='oxxom_oblik', POSTGRES_USER='oxxom_oblik_app',
                POSTGRES_PASSWORD=secrets.token_urlsafe(32), POSTGRES_HOST='127.0.0.1',
                POSTGRES_PORT='5432', OXXOM_MFA_KEY=mfa, TRUST_PROXY='true')


def export(domain, output):
    output = output.expanduser().resolve()
    root = ROOT.resolve()
    if output == root or root in output.parents:
        raise ValueError('Transfer directory must be outside the repository')
    local = read_env(ROOT / '.env')
    if local.get('APP_ENV', 'development') != 'development':
        raise ValueError('This command only exports the local development database')
    if local.get('POSTGRES_HOST', '127.0.0.1') not in {'127.0.0.1', 'localhost', '::1'}:
        raise ValueError('Local Django is configured for a remote database; review export source')
    mfa = local.get('OXXOM_MFA_KEY', '')
    if not mfa:
        mfa = (ROOT / '.secrets/mfa.key').read_text().strip()
    values = production_values(local, mfa, domain)
    # systemd EnvironmentFile must interpret exactly the values written here.
    if any(not value or any(char.isspace() or char in "\\\"'" for char in value) for value in values.values()):
        raise ValueError('Unexpected whitespace or quoting in configuration; migrate with manual review')
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    os.chmod(output, 0o700)
    dump = output / 'database.dump'
    docker = ['docker', 'compose', 'exec', '-T', 'db']
    try:
        identity = subprocess.run(docker + ['sh', '-c', 'printf "%s\\n%s\\n" "$POSTGRES_DB" "$POSTGRES_USER"'],
                                  cwd=ROOT, capture_output=True, text=True, check=True)
        if identity.stdout.splitlines() != [local.get('POSTGRES_DB'), local.get('POSTGRES_USER')]:
            raise ValueError('Docker database identity does not match local Django configuration')
        with dump.open('xb') as target:
            os.chmod(dump, 0o600)
            subprocess.run(docker + ['sh', '-c',
                'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --no-owner --no-acl'],
                cwd=ROOT, stdout=target, check=True)
        with dump.open('rb') as source:
            subprocess.run(docker + ['pg_restore', '--list'], cwd=ROOT,
                           stdin=source, stdout=subprocess.DEVNULL, check=True)
        if dump.stat().st_size == 0:
            raise ValueError('Database dump is empty')
        with (output / 'app.env').open('x') as target:
            os.chmod(target.name, 0o600)
            target.write(''.join(f'{key}={value}\n' for key, value in values.items()))
    except Exception:
        # A failed export must never look like a complete migration package.
        for name in ('database.dump', 'app.env'):
            (output / name).unlink(missing_ok=True)
        output.rmdir()
        raise
    print(f'Transfer prepared: {output}')
    print('Database archive checked. Existing Django and MFA keys preserved; database password regenerated.')
    print('Contains private data and keys. Transfer over SSH; never upload to GitHub or chat.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--domain', required=True, type=domain_name)
    parser.add_argument('--output', required=True, type=Path)
    arguments = parser.parse_args()
    try:
        export(arguments.domain, arguments.output)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f'Transfer failed ({type(error).__name__}); no credentials printed.', file=sys.stderr)
        sys.exit(1)
