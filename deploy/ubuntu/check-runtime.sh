#!/bin/sh
set -eu
release=${1:?Pass the full reviewed commit SHA}
case "$release" in *[!0-9a-f]*|'') echo 'Full lowercase commit SHA required' >&2; exit 1;; esac
[ "${#release}" -eq 40 ] || { echo 'Full commit SHA required' >&2; exit 1; }
runtime_python=${2:-/srv/oxxom-oblik/venv/bin/python}
gh auth status --hostname github.com >/dev/null 2>&1 || { echo 'Log in to GitHub CLI first' >&2; exit 1; }
umask 077
temporary=$(mktemp -d /tmp/oxxom-runtime-check.XXXXXX)
trap 'rm -rf "$temporary"' EXIT HUP INT TERM
collector_sha='a8ab1147a21865667e417bd70b6aa19bfda340307b96f82bfbf666140db0d1e1'
lock_sha='a2d23db421dbb06a6cab080a694360954793a802261f464471f27d63a285ea2b'
gh api -H 'Accept: application/vnd.github.raw+json' "repos/oxxom-platform/oxxom-oblik/contents/deploy/ubuntu/collect-runtime.py?ref=$release" > "$temporary/collect-runtime.py"
gh api -H 'Accept: application/vnd.github.raw+json' "repos/oxxom-platform/oxxom-oblik/contents/requirements-production.lock?ref=$release" > "$temporary/runtime.lock"
printf '%s  %s\n' "$collector_sha" "$temporary/collect-runtime.py" | sha256sum --check --status
printf '%s  %s\n' "$lock_sha" "$temporary/runtime.lock" | sha256sum --check --status
sudo python3 -I -B "$temporary/collect-runtime.py" --lock "$temporary/runtime.lock" --python "$runtime_python"
