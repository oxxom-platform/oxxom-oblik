#!/bin/zsh
cd "$(dirname "$0")" || exit 1
exec sh scripts/start-core.sh
