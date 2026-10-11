#!/usr/bin/env bash
# Only the dependency-light metadata adapter is admitted at session start.
set -uo pipefail
script_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
bash "$script_dir/runtime-gate.sh" __session-stamp >/dev/null
rc=$?
printf '{}'
exit "$rc"
