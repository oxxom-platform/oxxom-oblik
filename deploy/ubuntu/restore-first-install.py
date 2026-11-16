"""First install only. Refuse existing database/role, then restore atomically.

Run as root using the application venv. Does not drop or overwrite databases.
"""
import argparse
import os
from pathlib import Path
import re
import subprocess
import sys
from environment_file import read_env


def restore(env_file, archive):
    from psycopg import sql
    if os.geteuid() != 0:
        raise ValueError('Run as root')
    env = read_env(env_file)
    if env.get('APP_ENV') != 'production' or env.get('POSTGRES_HOST') != '127.0.0.1' or env.get('POSTGRES_PORT') != '5432':
        raise ValueError('Expected local production PostgreSQL on port 5432')
    db, role, password = (env[key] for key in ('POSTGRES_DB', 'POSTGRES_USER', 'POSTGRES_PASSWORD'))
    if not all(re.fullmatch(r'oxxom_[a-z0-9_]{1,50}', value) for value in (db, role)) or not password:
        raise ValueError('Expected application-specific oxxom_ database and role')
    psql = ['runuser', '-u', 'postgres', '--', 'psql', '-X', '--no-password', '-v', 'ON_ERROR_STOP=1', '-d', 'postgres']
    exists = sql.SQL('SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname={}) OR EXISTS(SELECT 1 FROM pg_roles WHERE rolname={});').format(sql.Literal(db), sql.Literal(role))
    result = subprocess.run(psql + ['-At'], input=exists.as_string(), text=True, capture_output=True, check=True)
    if result.stdout.strip() != 'f':
        raise ValueError('Database or role already exists. Refusing overwrite; inspect before proceeding.')
    with archive.open('rb') as source:
        subprocess.run(['runuser', '-u', 'postgres', '--', 'pg_restore', '--list'],
                       stdin=source, stdout=subprocess.DEVNULL, check=True)
    create = sql.SQL("SET password_encryption='scram-sha-256'; CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}; CREATE DATABASE {} OWNER {};").format(sql.Identifier(role), sql.Literal(password), sql.Identifier(db), sql.Identifier(role))
    # SQL contains the password. Do not print even a failing statement to a terminal.
    subprocess.run(psql, input=create.as_string(), text=True, capture_output=True, check=True)
    with archive.open('rb') as source:
        subprocess.run(['runuser', '-u', 'postgres', '--', 'pg_restore', '--no-password',
                        '--exit-on-error', '--single-transaction', '--no-owner', '--no-acl',
                        '--role', role, '--dbname', db], stdin=source, check=True)
    print('Database restored into a new application-owned database. No existing database replaced.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, default=Path('/etc/oxxom-oblik/app.env'))
    parser.add_argument('--archive', type=Path, required=True)
    args = parser.parse_args()
    try:
        restore(args.env_file, args.archive)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print(f'Restore stopped ({type(error).__name__}). Database/role may have been created; inspect before retrying.', file=sys.stderr)
        sys.exit(1)
