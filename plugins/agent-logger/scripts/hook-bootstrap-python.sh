#!/usr/bin/env bash

logger_hook_python() (
    local scripts="$1" provider request line
    provider="$scripts/_bootstrap_python/bootstrap-uv.sh"
    request="$scripts/bootstrap-python-version.txt"
    # Repository support is valid only for the unmaterialized installer reference.
    if [[ ! -e "$provider" && ! -L "$provider" && ! -e "$request" && ! -L "$request" &&
        -f "$scripts/install.sh" ]]; then
        while IFS= read -r line; do
            if [[ "$line" == '. "$SCRIPT_DIR/../../../libs/installer-engine/installer-engine.sh"' ]]; then
                provider="$scripts/../../../libs/installer-engine/bootstrap-uv.sh"
                request="$scripts/../../../libs/installer-engine/installer-instance-python-version.txt"
                break
            fi
        done < "$scripts/install.sh"
    fi
    [[ -f "$provider" ]] || return 126
    . "$provider" || return 126
    resolve_uv_bootstrap_python "$request" Inspect </dev/null
)
