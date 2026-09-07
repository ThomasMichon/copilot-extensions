#!/usr/bin/env bash
set -u

script_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
python="$(command -v python3 || command -v python || true)"
if [ -n "$python" ] && [ -f "$script_dir/register-dispatch-companion.py" ]; then
    "$python" -E -X utf8 "$script_dir/register-dispatch-companion.py"
fi
exit 0
