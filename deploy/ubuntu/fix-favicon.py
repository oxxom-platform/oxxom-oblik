"""FAVICON-02: repack the approved PNG; update only the icon and shared links."""
import fcntl
import hashlib
import http.client
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import tempfile
import time

APP = Path('/srv/oxxom-oblik/app')
STATE = Path('/srv/oxxom-oblik/update-backups')
ICON = 'backend/core/static/core/favicon.ico'
TEMPLATE = 'backend/core/templates/core/favicon.html'
SOURCE = 'backend/core/static/core/favicon-oblik.png'
SOURCE_SHA256 = '1fd603c22e0c964c2e2fe69353d25d584d34c2d14e948a85491dc5de67e73084'
BEFORE = {
    ICON: '37bc8ff0c5a4b57afcd09f81ff82adac23411d23504815f730802449a0a4f890',
    TEMPLATE: 'f8d3c1bf0058bac6deae8c5955c26e238815e81953214c8b4e07cb49e1a3e87f',
}
VERSION = '20261117-oblik2'
NEW_TEMPLATE = (
    '<link rel="icon" href="/assets/brand/favicon-oblik.png?v=' + VERSION + '" type="image/png" sizes="256x256">\n'
    '<link rel="apple-touch-icon" href="/assets/brand/apple-touch-oblik.png?v=' + VERSION + '" sizes="180x180">\n'
).encode()


def checksum(data):
    return hashlib.sha256(data).hexdigest()


def icon_bytes(png):
    """A single intact high-resolution frame; browsers handle downsampling."""
    if (png[:8] != b'\x89PNG\r\n\x1a\n' or len(png) < 33
            or struct.unpack('>II', png[16:24]) != (256, 256)):
        raise ValueError('Expected the approved 256x256 PNG')
    return (struct.pack('<HHH', 0, 1, 1)
            + struct.pack('<BBBBHHII', 0, 0, 0, 0, 1, 32, len(png), 22) + png)


def request(path):
    connection = http.client.HTTPConnection('127.0.0.1', 4181, timeout=4)
    try:
        connection.request('GET', path, headers={'Host': 'oblik.oxxom.com.ua', 'X-Forwarded-Proto': 'https'})
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def replace(path, data, info):
    fd, temporary = tempfile.mkstemp(prefix='.favicon-', dir=path.parent)
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


def restart(log):
    subprocess.run(['systemctl', 'restart', 'oxxom-oblik'], check=True, timeout=60,
                   stdout=log, stderr=log)


def verify(png, ico):
    for _ in range(15):
        try:
            if request('/api/v1/health')[0] == 200:
                break
        except OSError:
            pass
        time.sleep(1)
    else:
        raise RuntimeError('Application health did not recover')
    for path in ('/', '/login/'):
        status, body = request(path)
        if status != 200 or VERSION.encode() not in body or b'rel="shortcut icon"' in body:
            raise RuntimeError('Updated favicon links were not served')
    for path, expected in [('/favicon.ico', ico), ('/assets/brand/favicon-oblik.png?v=' + VERSION, png)]:
        status, body = request(path)
        if status != 200 or body != expected:
            raise RuntimeError('Favicon response did not match the approved asset')


def main():
    if os.geteuid() != 0:
        raise SystemExit('Run with sudo on the Ubuntu application VM')
    os.umask(0o077)
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / 'seo-update.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if APP.is_symlink():
            raise RuntimeError('Unexpected application path')
        for name in (SOURCE, ICON, TEMPLATE):
            target = APP / name
            if not target.is_file() or target.is_symlink() or any(p.is_symlink() for p in target.parents):
                raise RuntimeError('Unexpected application file: ' + name)
        png = (APP / SOURCE).read_bytes()
        if checksum(png) != SOURCE_SHA256:
            raise RuntimeError('Source PNG differs; application unchanged')
        payload = {ICON: icon_bytes(png), TEMPLATE: NEW_TEMPLATE}
        originals = {name: (APP / name).read_bytes() for name in payload}
        for name, data in originals.items():
            if checksum(data) not in (BEFORE[name], checksum(payload[name])):
                raise RuntimeError('Unreviewed server change; application unchanged: ' + name)
        if all(originals[name] == data for name, data in payload.items()):
            verify(png, payload[ICON])
            print('FAVICON-02 OK: already installed and checked')
            return
        if request('/api/v1/health')[0] != 200:
            raise RuntimeError('Application health failed before update')
        backup = Path(tempfile.mkdtemp(prefix='favicon-', dir=STATE))
        info = {name: (APP / name).stat() for name in payload}
        for name in payload:
            target = backup / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(APP / name, target)
        (backup / 'receipt.json').write_text(json.dumps({
            'release': 'FAVICON-02', 'before': {n: checksum(v) for n, v in originals.items()},
            'after': {n: checksum(v) for n, v in payload.items()},
        }, indent=2))
        print('Recovery files: ' + str(backup), flush=True)
        with (backup / 'checks.log').open('w') as log:
            try:
                print('[1/2] Replacing the ICO container and shared favicon links...', flush=True)
                for name, data in payload.items():
                    replace(APP / name, data, info[name])
                print('[2/2] Restarting the application and checking responses...', flush=True)
                restart(log)
                verify(png, payload[ICON])
            except BaseException:
                for name, data in originals.items():
                    replace(APP / name, data, info[name])
                restart(log)
                raise RuntimeError('Favicon check failed; previous files restored') from None
        (backup / 'success.json').write_text(json.dumps({'release': 'FAVICON-02', 'responses_verified': True}))
        print('FAVICON-02 OK: approved OBLIK badge, valid ICO and fresh PNG links')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        raise SystemExit('FAVICON UPDATE STOPPED: ' + str(error)) from None
