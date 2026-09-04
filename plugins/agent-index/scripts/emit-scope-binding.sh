#!/usr/bin/env bash
# Emit repository-scoped agent-index guidance without provisioning a runtime.
set -eu

emit_empty() { printf '%s\n' '{}'; exit 0; }

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
py="$(command -v python3 || command -v python || true)"
[ -n "$py" ] || emit_empty
"$py" -E -X utf8 "$script_dir/emit_scope_binding.py" --cwd "$PWD" 2>/dev/null ||
    emit_empty
