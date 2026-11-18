#!/bin/sh
set -eu
release=${1:?Pass the full reviewed commit SHA}
case "$release" in *[!0-9a-f]*|'') echo 'Full lowercase commit SHA required' >&2; exit 1;; esac
[ "${#release}" -eq 40 ] || { echo 'Full commit SHA required' >&2; exit 1; }
gh auth status --hostname github.com >/dev/null 2>&1 || { echo 'Log in to GitHub CLI first' >&2; exit 1; }
umask 077
temporary=$(mktemp -d /tmp/oxxom-backup-audit.XXXXXX)
trap 'rm -rf "$temporary"' EXIT HUP INT TERM
collector_sha='46e5b13556167d58219a57833eccd8e983094a4109587079d03d17f6c668e5d5'
gh api -H 'Accept: application/vnd.github.raw+json' "repos/oxxom-platform/oxxom-oblik/contents/deploy/ubuntu/audit-backups.py?ref=$release" > "$temporary/audit-backups.py"
printf '%s  %s\n' "$collector_sha" "$temporary/audit-backups.py" | sha256sum --check --status
sudo python3 -I -B "$temporary/audit-backups.py"
