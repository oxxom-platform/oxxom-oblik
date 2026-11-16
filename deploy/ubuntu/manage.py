"""Run Django maintenance with the same environment as the systemd service."""
import os
from pathlib import Path
import sys
from environment_file import read_env

root = Path(__file__).resolve().parents[2]
values = read_env('/etc/oxxom-oblik/app.env')
if values.get('APP_ENV') != 'production':
    raise SystemExit('Expected APP_ENV=production')
os.environ.update(values)
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
os.execv(sys.executable, [sys.executable, str(root / 'backend/manage.py'), *sys.argv[1:]])
