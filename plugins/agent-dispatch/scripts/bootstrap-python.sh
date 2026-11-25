#!/usr/bin/env bash

dispatch_bootstrap_python() (
    local reader_dir selector request source_line python
    reader_dir="$(cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)" || return 126
    selector="$reader_dir/_bootstrap_python/bootstrap-uv.sh"
    request="$reader_dir/bootstrap-python-version.txt"
    # Only an unmaterialized source installer may use its repository support.
    if [[ ! -e "$selector" && ! -L "$selector" && ! -e "$request" && ! -L "$request" &&
        -f "$reader_dir/install.sh" ]]; then
        while IFS= read -r source_line; do
            if [[ "$source_line" == '. "$SCRIPT_DIR/../../../libs/installer-engine/installer-engine.sh"' ]]; then
                selector="$reader_dir/../../../libs/installer-engine/bootstrap-uv.sh"
                request="$reader_dir/../../../libs/installer-engine/installer-instance-python-version.txt"
                break
            fi
        done < "$reader_dir/install.sh"
    fi
    if [[ -f "$selector" ]] &&
        . "$selector" &&
        python="$(resolve_uv_bootstrap_python "$request" Inspect </dev/null)" &&
        "$python" -I -S -B -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' </dev/null >/dev/null 2>&1; then
        printf '%s\n' "$python"
        return 0
    fi
    printf '%s\n' '[agent-dispatch] prepared uv-managed bootstrap Python is unavailable; use explicit prerequisite setup. No installation was attempted.' >&2
    return 126
)
