#!/bin/sh
# Download an exact reviewed GitHub commit; never update the entire installation.
set -eu
release=${1:?Pass the full reviewed release commit SHA}
case "$release" in *[!0-9a-f]*|'') echo 'Full lowercase commit SHA required' >&2; exit 1;; esac
[ "${#release}" -eq 40 ] || { echo 'Full commit SHA required' >&2; exit 1; }
command -v gh >/dev/null 2>&1 || { echo 'GitHub CLI required' >&2; exit 1; }
gh auth status --hostname github.com >/dev/null 2>&1 || { echo 'Log in to GitHub CLI first' >&2; exit 1; }
umask 077
temporary=$(mktemp -d /tmp/oxxom-security.XXXXXX)
trap 'rm -rf "$temporary"' EXIT HUP INT TERM
printf '%s\n' '[1/2] Downloading pinned security release...'
gh api "repos/oxxom-platform/oxxom-oblik/tarball/$release" > "$temporary/release.tar.gz"
gh api "repos/oxxom-platform/oxxom-oblik/contents/deploy/ubuntu/apply-security-update.py?ref=$release" --jq '.content' |
    base64 --decode > "$temporary/apply-security-update.py"
test -s "$temporary/apply-security-update.py"
printf '%s\n' '[2/2] Checking the installed baseline and applying the guarded update...'
sudo python3 -B "$temporary/apply-security-update.py" "$temporary/release.tar.gz"
