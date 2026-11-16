"""Check the exact Django connection from the host without printing secrets."""
import os
from pathlib import Path
import socket
import sys
import faulthandler

print('Python started. Loading Django...', flush=True)
faulthandler.dump_traceback_later(20, repeat=False)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django
print('Django imported. Loading settings and apps...', flush=True)
django.setup()
faulthandler.cancel_dump_traceback_later()
from django.conf import settings
from django.db import connection, DatabaseError

config = settings.DATABASES['default']
host, port = config['HOST'], int(config['PORT'])
print(f'Django target: {host}:{port}', flush=True)
try:
    with socket.create_connection((host, port), timeout=5):
        pass
except OSError as error:
    print(f'TCP FAILED: {type(error).__name__}. The host cannot reach this port.', flush=True)
    print('Run: docker compose port db 5432', flush=True)
    print('Run: docker compose ps', flush=True)
    sys.exit(1)
print('TCP OK. Checking PostgreSQL authentication and query...', flush=True)
try:
    with connection.cursor() as cursor:
        cursor.execute('SELECT 1')
        if cursor.fetchone() != (1,):
            raise RuntimeError('Unexpected query result')
except DatabaseError as error:
    cause = error.__cause__
    state = getattr(cause, 'sqlstate', None)
    print(f'PostgreSQL FAILED: {type(cause or error).__name__}; SQLSTATE={state or "unavailable"}', flush=True)
    if state == '28P01':
        print('Password mismatch. Preserve the volume; do not recreate the database.', flush=True)
    elif state == '3D000':
        print('Configured database does not exist.', flush=True)
    else:
        print('TCP is reachable, but the PostgreSQL handshake/query failed.', flush=True)
        print('Check the published port and Docker database logs.', flush=True)
    sys.exit(1)
finally:
    connection.close()
print('PostgreSQL OK: authenticated SELECT 1 passed.', flush=True)
