#!/usr/bin/env bash

vault_runtime_activation() {
    local reader_dir python="${2:-}"
    local installed_python="$python"
    reader_dir="$(cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)" || return 3
    if [[ -z "$python" ]]; then
        if [[ -f "$reader_dir/bootstrap-uv.sh" ]] &&
            . "$reader_dir/bootstrap-uv.sh" &&
            python="$(resolve_uv_bootstrap_python "$reader_dir/installer-instance-python-version.txt" Inspect </dev/null)"; then
            if ! "$python" -I -S -B -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' </dev/null >/dev/null 2>&1; then
                python=""
            fi
        else
            python=""
        fi
    fi
    if [[ -z "$python" ]]; then
        printf '%s\n' '{"schema":"copilot-extensions.module-readiness","version":1,"module":"agent-vault/runtime","state":"failed","detail":"Agent Vault activation requires prepared uv-managed Python from its prerequisite setup owner; no installation was attempted."}'
        return 3
    fi
    local project="${1:-$PWD}"
    if [[ $# -ge 2 ]]; then shift 2; elif [[ $# -gt 0 ]]; then shift; fi
    if [[ -n "${COPILOT_EXTENSIONS_READONLY_PROBE+x}" ]]; then
        local probe_args=(
            --plugin agent-vault --module agent_vault --script "$reader_dir/runtime-activation.py"
        )
        [[ -n "$installed_python" ]] || probe_args+=(--payload-only)
        "$python" -I -B -X utf8 "$reader_dir/versioned_runtime_probe.py" \
            "${probe_args[@]}" -- --cwd "$project" "$@" </dev/null
    else
        "$python" -I -B -X utf8 "$reader_dir/runtime-activation.py" --cwd "$project" "$@" </dev/null
    fi
}
