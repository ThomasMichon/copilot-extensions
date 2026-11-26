#!/usr/bin/env bash
# Shell-native selection; the caller owns preparation and the selected worker.
invoke_uv_bootstrap_environment() (
    unset UV_PYTHON UV_PYTHON_PREFERENCE UV_MANAGED_PYTHON UV_NO_MANAGED_PYTHON
    unset VIRTUAL_ENV CONDA_PREFIX PYTHONHOME PYTHONPATH UV_WORKING_DIR UV_WORKING_DIRECTORY
    unset UV_ISOLATED UV_ENV_FILE
    unset UV_PROJECT UV_FROZEN UV_LOCKED UV_NO_SYNC
    exec "$@"
)

_uv_runtime_posix_address() {
    case "$1" in
        /*|[a-zA-Z]:[\\/]*|\\\\*) cygpath -au -- "$1" ;;
        *) return 1 ;;
    esac
}

resolve_uv_runtime_python() (
    local expected="${1:-}" uv_path python addressed="${1:-}" windows_bash=0
    case "${OSTYPE:-}" in
        msys*|cygwin*)
            windows_bash=1
            expected="$(_uv_runtime_posix_address "$expected")" || return 1
            ;;
    esac
    [[ "$expected" == /* && "$expected" != *$'\n'* && "$expected" != *$'\r'* &&
        -x "$expected" ]] || {
        printf 'The selected application runtime is unavailable; use explicit installation management.\n' >&2
        return 1
    }
    if [[ "$#" -gt 1 ]]; then
        uv_path="$2"
        if [[ "$windows_bash" == 1 ]]; then
            uv_path="$(_uv_runtime_posix_address "$uv_path")" || return 127
        fi
        [[ "$uv_path" == /* && "$uv_path" != *$'\n'* && "$uv_path" != *$'\r'* &&
            -f "$uv_path" && -x "$uv_path" ]] || {
            printf 'Bound uv is unavailable; prepare the command prerequisites before launch.\n' >&2
            return 127
        }
    else
        uv_path="$(command -v uv)" || {
            printf 'uv is unavailable; prepare the command prerequisites before launch.\n' >&2
            return 127
        }
    fi
    [[ "$uv_path" == /* && -x "$uv_path" ]] || return 127
    unset UV_CONFIG_FILE
    export UV_NO_CONFIG=1
    python="$(invoke_uv_bootstrap_environment "$uv_path" python find --no-project --managed-python \
        --no-python-downloads "$expected")" || return
    if [[ "$windows_bash" == 1 ]]; then
        python="$(_uv_runtime_posix_address "$python")" || return 1
    fi
    [[ "$python" == "$expected" ]] || {
        printf 'uv did not select the addressed application runtime; use explicit installation management.\n' >&2
        return 1
    }
    printf '%s\n' "$addressed"
)

resolve_uv_bootstrap_python() (
    local request_file="$1" operation="${2:-Inspect}" requirement uv_path version major minor size
    export LC_ALL=C
    [[ "$operation" == Inspect || "$operation" == Prepare ]] || return 2
    [[ "$request_file" == /* && -f "$request_file" && ! -L "$request_file" ]] || {
        printf 'Bootstrap Python request must be an ordinary absolute file.\n' >&2
        return 1
    }
    size="$(wc -c < "$request_file")" || return 1
    [[ "$size" -le 64 ]] || return 1
    requirement="$(cat "$request_file"; printf x)"
    requirement="${requirement%x}"
    [[ "$size" -eq "${#requirement}" &&
        "$requirement" =~ ^cpython@3\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$'\r'?$'\n'$ ]] || {
        printf 'Bootstrap Python request must declare one exact stable CPython version.\n' >&2
        return 1
    }
    requirement="${requirement%$'\n'}"
    requirement="${requirement%$'\r'}"
    unset UV_CONFIG_FILE
    export UV_NO_CONFIG=1
    uv_path="$(command -v uv)" || {
        printf 'uv is unavailable; prepare the command prerequisites before setup.\n' >&2
        return 1
    }
    [[ "$uv_path" == /* && -x "$uv_path" ]] || return 1
    version="$(invoke_uv_bootstrap_environment "$uv_path" --version)" || return 1
    [[ "$version" =~ ^uv[[:space:]]+([0-9]+)\.([0-9]+)\.([0-9]+)($|[[:space:]\(+]) ]] || return 1
    major="${BASH_REMATCH[1]}" minor="${BASH_REMATCH[2]}"
    if ((10#$major == 0 && 10#$minor < 8)); then
        printf 'Bootstrap Python requires uv 0.8.0 or newer.\n' >&2
        return 1
    fi
    if [[ "$operation" == Prepare ]]; then
        invoke_uv_bootstrap_environment "$uv_path" python install --no-bin --no-registry "$requirement" >&2 || return
    fi
    local python
    python="$(invoke_uv_bootstrap_environment "$uv_path" python find --no-project --system --managed-python \
        --no-python-downloads "$requirement")" || return
    [[ "$python" == /* && "$python" != *$'\n'* && "$python" != *$'\r'* && -x "$python" ]] || {
        printf 'uv did not select one existing absolute bootstrap Python executable.\n' >&2
        return 1
    }
    printf '%s\n' "$python"
)
