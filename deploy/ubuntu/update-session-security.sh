#!/bin/sh
set -eu
release=${1:?Pass the full reviewed commit SHA}
case "$release" in *[!0-9a-f]*|'') echo 'Full lowercase commit SHA required' >&2; exit 1;; esac
[ "${#release}" -eq 40 ] || { echo 'Full commit SHA required' >&2; exit 1; }
gh auth status --hostname github.com >/dev/null 2>&1 || { echo 'Log in to GitHub CLI first' >&2; exit 1; }
umask 077
temporary=$(mktemp -d /tmp/oxxom-session-security.XXXXXX)
trap 'rm -rf "$temporary"' EXIT HUP INT TERM
printf '%s\n' 'Downloading the pinned session-security security release...'
gh api "repos/oxxom-platform/oxxom-oblik/tarball/$release" > "$temporary/release.tar.gz"
gh api "repos/oxxom-platform/oxxom-oblik/contents/deploy/ubuntu/apply-session-security.py?ref=$release" --jq '.content' |
  base64 --decode > "$temporary/apply-session-security.py"
test -s "$temporary/apply-session-security.py"
sudo python3 -B "$temporary/apply-session-security.py" "$temporary/release.tar.gz"
