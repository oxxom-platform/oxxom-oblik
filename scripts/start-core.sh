#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
STEP='initialization'
trap 'result=$?; if [ "$result" -ne 0 ]; then printf "\nOXXOM: stopped at step: %s (exit %s). See the error above.\n" "$STEP" "$result" >&2; fi' EXIT
STEP='1/8 local configuration'
printf '\n[1/8] Preparing local configuration\n'
python3 scripts/init-local.py
STEP='2/8 Python environment'
printf '\n[2/8] Checking Python environment\n'
OXXOM_VENV="${OXXOM_VENV:-$HOME/OXXOM-Dev/venv}"
printf 'Environment: %s\n' "$OXXOM_VENV"
if [ ! -x "$OXXOM_VENV/bin/python" ]; then python3 -m venv "$OXXOM_VENV"; fi
"$OXXOM_VENV/bin/python" -u -c 'import sys; print("Python", sys.version.split()[0], flush=True); sys.exit(0 if sys.version_info >= (3, 11) else "Python 3.11+ required")'
STEP='3/8 installing dependencies from PyPI'
printf '\n[3/8] Installing dependencies from PyPI; internet access required\n'
"$OXXOM_VENV/bin/python" -u -m pip install --disable-pip-version-check --no-input --timeout 15 --retries 2 --progress-bar off -r requirements.txt
STEP='4/8 PostgreSQL in Docker'
printf '\n[4/8] Starting PostgreSQL; Docker Desktop must be running\n'
docker compose up -d --wait --wait-timeout 120 db
STEP='4/8 PostgreSQL connection from Mac'
printf '\n[4/8] Checking actual Django connection to PostgreSQL\n'
"$OXXOM_VENV/bin/python" -u scripts/check-db.py
STEP='5/8 Django configuration and migration consistency'
printf '\n[5/8] Checking Django configuration and migrations\n'
"$OXXOM_VENV/bin/python" backend/manage.py check
"$OXXOM_VENV/bin/python" backend/manage.py makemigrations --check --dry-run
STEP='6/8 database migrations'
printf '\n[6/8] Applying database migrations\n'
"$OXXOM_VENV/bin/python" backend/manage.py migrate --noinput
STEP='7/8 integration tests'
printf '\n[7/8] Running integration tests on a separate test database\n'
"$OXXOM_VENV/bin/python" backend/manage.py test accounts core catalog documents inventory posting operations payments reports printing catalog_imports --noinput
STEP='8/8 local web server'
printf '\n[8/8] Starting OXXOM core at http://127.0.0.1:4180/\nKeep this terminal open. Stop with Ctrl+C.\n'
exec "$OXXOM_VENV/bin/python" backend/manage.py runserver 127.0.0.1:4180 --noreload
