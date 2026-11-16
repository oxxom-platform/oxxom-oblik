"""Reject staged private files and locally known credentials before first push."""
import fnmatch
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'deploy/ubuntu'))
from environment_file import read_env

# A pattern avoids embedding a complete key header in the scanner's own source.
# Do not exempt this file: a real credential here must still block publication.
PRIVATE_KEY_HEADER = re.compile(rb'-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----')
GIT_TIMEOUT = 15
SCAN_TIMEOUT = 180


class ScanError(Exception):
    """An incomplete check must never allow a commit to proceed."""


def git_output(arguments, step, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ScanError('Total check time limit exceeded. Check did not complete.')
    try:
        return subprocess.check_output(['git', *arguments], cwd=ROOT,
                                       stderr=subprocess.DEVNULL,
                                       timeout=min(GIT_TIMEOUT, remaining))
    except subprocess.TimeoutExpired:
        raise ScanError(f'Timed out while {step}. Check did not complete.') from None
    except subprocess.CalledProcessError:
        raise ScanError(f'Git failed while {step}. Check did not complete; no file was skipped.') from None


def scan():
    deadline = time.monotonic() + SCAN_TIMEOUT
    print('Reading staged file list...', flush=True)
    # Deleted files have no staged content. Read failures for other files are errors.
    paths = [path for path in git_output(['diff', '--cached', '--name-only', '--diff-filter=ACMRT', '-z'],
                                        'reading the staged file list', deadline).decode().split('\0') if path]
    print(f'Checking {len(paths)} staged files; progress every 10 files. Maximum wait per Git command: {GIT_TIMEOUT}s.', flush=True)
    known = []
    if (ROOT / '.env').exists():
        env = read_env(ROOT / '.env')
        known.extend(value.encode() for key, value in env.items()
                     if ('PASSWORD' in key or 'SECRET' in key or key == 'OXXOM_MFA_KEY') and len(value) >= 8)
    if (ROOT / '.secrets/mfa.key').exists():
        known.append((ROOT / '.secrets/mfa.key').read_bytes().strip())
    unsafe = []
    for number, path in enumerate(paths, 1):
        if number == 1 or number % 10 == 0 or number == len(paths):
            print(f'[{number}/{len(paths)}] {path}', flush=True)
        name = Path(path).name
        private = (name == '.env' or (name.startswith('.env.') and name != '.env.example')
                   or any(part in {'.secrets', '.migration', 'backups', 'data', '.openai'} for part in Path(path).parts)
                   or any(fnmatch.fnmatch(name, pattern) for pattern in ('*.key', '*.pem', '*.dump', '*.sqlite3', '*.sql')))
        content = git_output(['show', ':' + path], f'reading staged file {path}', deadline)
        leaked = any(value and value in content for value in known) or PRIVATE_KEY_HEADER.search(content) is not None
        if private or leaked:
            unsafe.append(path)
    if unsafe:
        print('STOP: private staged files or local credentials detected. Values omitted.', file=sys.stderr)
        for path in unsafe:
            print(path, file=sys.stderr)
        return 1
    print(f'Checked {len(paths)} staged files: no private paths or locally known credentials detected.', flush=True)
    print('This check does not replace review or scan unknown historical credentials.')
    return 0


def main():
    try:
        return scan()
    except ScanError as error:
        print(f'STOP: {error}', file=sys.stderr, flush=True)
        return 2
    except (OSError, ValueError) as error:
        print(f'STOP: local file/configuration read failed ({type(error).__name__}); values omitted.', file=sys.stderr, flush=True)
        return 2


if __name__ == '__main__':
    sys.exit(main())
