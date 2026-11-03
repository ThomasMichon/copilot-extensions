#!/usr/bin/env bash

vault_runtime_activation() {
    local reader_dir python="${2:-}"
    reader_dir="$(cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)" || return 3
    if [[ -z "$python" ]]; then python="$(command -v python3 || command -v python || true)"; fi
    if [[ -z "$python" ]]; then
        printf '%s\n' '{"schema":"copilot-extensions.module-readiness","version":1,"module":"agent-vault/runtime","state":"failed","detail":"Agent Vault activation requires an available Python reader; no installation was attempted."}'
        return 3
    fi
    local project="${1:-$PWD}"
    if [[ $# -ge 2 ]]; then shift 2; elif [[ $# -gt 0 ]]; then shift; fi
    "$python" -I -B -X utf8 "$reader_dir/runtime-activation.py" --cwd "$project" "$@"
}
