#!/usr/bin/env bash
# Linux's fixed foundation inventory; no package/dependency selection policy.

_native_foundation_error() {
    printf 'foundation %s\n' "$1" >&2
    return 1
}

_native_foundation_plain() {
    local path="$1" kind="$2" canonical
    [[ "$(uname -s)" == Linux && "$path" == /* && ! -L "$path" ]] ||
        { _native_foundation_error 'requires an absolute unlinked Linux path'; return 1; }
    canonical="$(readlink -e -- "$path" && printf '.')" || return 1
    [[ "$canonical" == "$path"$'\n.' ]] ||
        { _native_foundation_error 'path must be normalized and unlinked'; return 1; }
    if [[ "$kind" == directory ]]; then
        [[ -d "$path" ]] || { _native_foundation_error 'requires an ordinary directory'; return 1; }
    else
        [[ -f "$path" ]] || { _native_foundation_error 'requires an ordinary file'; return 1; }
    fi
}

_native_foundation_open() {
    local path="$1" identity opened current
    _native_foundation_plain "$path" file || return 1
    identity="$(stat -Lc '%d:%i' -- "$path")" || return 1
    exec {__foundation_fd}<"$path" || return 1
    opened="$(stat -Lc '%d:%i' "/proc/self/fd/$__foundation_fd")" || {
        exec {__foundation_fd}<&-; return 1;
    }
    if [[ ! -f "/proc/self/fd/$__foundation_fd" ]] ||
        ! _native_foundation_plain "$path" file; then
        exec {__foundation_fd}<&-
        _native_foundation_error 'opened input must remain plain and unlinked'
        return 1
    fi
    current="$(stat -Lc '%d:%i' -- "$path")" || { exec {__foundation_fd}<&-; return 1; }
    if [[ "$identity" != "$opened" || "$current" != "$opened" ]]; then
        exec {__foundation_fd}<&-
        return 76
    fi
}

_native_foundation_files() {
    local plugin="$1" package path name last
    local -a discovered=()
    [[ "${plugin##*/}" == update-driver ]] ||
        { _native_foundation_error 'requires exact update-driver source'; return 1; }
    _native_foundation_plain "$plugin" directory || return 1
    __foundation_files=()
    for name in update-driver agent-procutil single-instance-lease; do
        package="${plugin%/*}/$name"
        _native_foundation_plain "$package" directory || return 1
        _native_foundation_plain "$package/pyproject.toml" file || return 1
        # The terminal status is outside the NUL-delimited absolute-path namespace.
        mapfile -d '' -t discovered < <(
            set -o pipefail
            LC_ALL=C find -P "$package" \
                \( -type d \( -name .git -o -name .venv -o -name __pycache__ \
                -o -name .pytest_cache -o -name .ruff_cache -o -name build -o -name dist \
                -o -name node_modules -o -name '*.egg-info' -o -name '*.dist-info' \) \
                -prune -print0 \) -o -print0 | LC_ALL=C sort -z
            printf '%s\0' "$?"
        )
        last=$((${#discovered[@]} - 1))
        [[ "$last" -ge 0 && "${discovered[$last]}" == 0 ]] ||
            { _native_foundation_error 'source enumeration failed'; return 1; }
        unset 'discovered[last]'
        for path in "${discovered[@]}"; do
            if [[ -d "$path" && ! -L "$path" ]]; then
                _native_foundation_plain "$path" directory || return 1
            else
                _native_foundation_plain "$path" file || return 1
                case "$path" in *.pyc|*.pyo) continue ;; esac
                __foundation_files+=("$path")
            fi
        done
    done
    for name in installer-engine.ps1 installer-engine.sh \
        installer_engine.py installer_engine_process.py installer_dependency_policy.py \
        installer_dependency_config.py runtime_launcher.py versioned_runtime.py \
        versioned_runtime_leases.py versioned_runtime_construction.py \
        versioned_runtime_source.py versioned_runtime_publication.py; do
        _native_foundation_plain "$plugin/scripts/$name" file || return 1
    done
    mapfile -d '' -t discovered < <(
        set -o pipefail
        printf '%s\0' "${__foundation_files[@]}" | LC_ALL=C sort -z
        printf '%s\0' "$?"
    )
    last=$((${#discovered[@]} - 1))
    [[ "$last" -ge 0 && "${discovered[$last]}" == 0 ]] ||
        { _native_foundation_error 'source ordering failed'; return 1; }
    unset 'discovered[last]'
    __foundation_files=("${discovered[@]}")
}

native_foundation_source_digest() (
    set -o pipefail
    local plugin="$1" path relative hash inventory
    _native_foundation_files "$plugin" || return 1
    inventory="$({
        for path in "${__foundation_files[@]}"; do
            relative="${path#"${plugin%/*}/"}"
            printf '%s' "$relative" | iconv -f UTF-8 -t UTF-8 >/dev/null || return 1
            _native_foundation_open "$path" ||
                { _native_foundation_error 'source input changed or became unavailable'; return 1; }
            hash="$(sha256sum <&"$__foundation_fd")" || {
                exec {__foundation_fd}<&-; return 1;
            }
            exec {__foundation_fd}<&-
            printf '%s\0%s\n' "$relative" "${hash%% *}" || return 1
        done
    } | sha256sum)" || return 1
    printf '%s\n' "${inventory%% *}"
)

_native_foundation_outside_sources() {
    local path="$1" plugin="$2" name package
    for name in update-driver agent-procutil single-instance-lease; do
        package="${plugin%/*}/$name"
        [[ "$path" != "$package" && "$path" != "$package/"* ]] ||
            { _native_foundation_error 'execution paths cannot mutate source closure'; return 1; }
    done
}

check_native_foundation_execution_root() {
    _native_foundation_plain "$1" directory || return 1
    _native_foundation_plain "$2" directory || return 1
    _native_foundation_outside_sources "$1" "$2"
}

freeze_native_foundation_source() (
    local root="$1" plugin="$2" destination="$3" expected="$4" path target
    assert_native_operation_lock "$root/execution.lock" || return 1
    check_native_foundation_execution_root "$root" "$plugin" || return 1
    [[ "$destination" == "$root/"* && ! -e "$destination" && ! -L "$destination" ]] ||
        { _native_foundation_error 'source freeze must be new and inside execution root'; return 1; }
    _native_foundation_plain "${destination%/*}" directory || return 1
    _native_foundation_outside_sources "$destination" "$plugin" || return 1
    [[ "$expected" =~ ^[0-9a-f]{64}$ && "$(native_foundation_source_digest "$plugin")" == "$expected" ]] ||
        { _native_foundation_error 'source changed before native freeze'; return 1; }
    _native_foundation_files "$plugin" || return 1
    (umask 077; mkdir -- "$destination") || return 1
    for path in "${__foundation_files[@]}"; do
        assert_native_operation_lock "$root/execution.lock" || return 1
        target="$destination/${path#"${plugin%/*}/"}"
        mkdir -p -- "${target%/*}" || return 1
        _native_foundation_plain "${target%/*}" directory || return 1
        _native_foundation_open "$path" ||
            { _native_foundation_error 'source input changed or became unavailable'; return 1; }
        if ! (umask 077; set -o noclobber; cat <&"$__foundation_fd" >"$target"); then
            exec {__foundation_fd}<&-; return 1
        fi
        exec {__foundation_fd}<&-
        sync -f -- "$target" || return 1
    done
    assert_native_operation_lock "$root/execution.lock" || return 1
    [[ "$(native_foundation_source_digest "$destination/update-driver")" == "$expected" &&
        "$(native_foundation_source_digest "$plugin")" == "$expected" ]] ||
        { _native_foundation_error 'source changed during native freeze'; return 1; }
    sync -f -- "$destination" || return 1
    printf '%s\n' "$destination/update-driver"
)
