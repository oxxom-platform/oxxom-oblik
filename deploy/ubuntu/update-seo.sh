#!/bin/sh
# Run on Ubuntu: sh update-seo.sh FULL_REVIEWED_COMMIT_SHA
set -eu
commit=${1:-}
case "$commit" in ''|*[!0-9a-f]*) echo 'Expected the reviewed full commit ID' >&2; exit 1;; esac
[ "${#commit}" -eq 40 ] || exit 1
command -v gh >/dev/null 2>&1 || { sudo apt-get update; sudo apt-get install -y gh; }
gh auth status --hostname github.com >/dev/null 2>&1 || gh auth login --hostname github.com --git-protocol https --web
umask 077
package=$(mktemp -d /tmp/oxxom-seo-release.XXXXXX)
trap 'rm -rf "$package"' EXIT HUP INT TERM
gh api "repos/oxxom-platform/oxxom-oblik/tarball/$commit" > "$package/release.tar.gz"
python3 - "$package" <<'PY'
from pathlib import Path
import sys, tarfile
directory = Path(sys.argv[1])
with tarfile.open(directory / 'release.tar.gz', 'r:gz') as archive:
    helpers = [entry for entry in archive.getmembers() if entry.name.endswith('/deploy/ubuntu/apply-seo-update.py')]
    if len(helpers) != 1 or not helpers[0].isfile() or helpers[0].size > 100_000:
        raise SystemExit('Unexpected release helper; application unchanged')
    (directory / 'apply-seo-update.py').write_bytes(archive.extractfile(helpers[0]).read())
PY
sudo /srv/oxxom-oblik/venv/bin/python "$package/apply-seo-update.py" --archive "$package/release.tar.gz" --commit "$commit"
