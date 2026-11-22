#!/usr/bin/env bash
# Python-free Linux progress under actual inherited installation custody.

__foundation_assets="$(CDPATH= cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$__foundation_assets/foundation-source.sh"

_native_foundation_query() {
    printf '%s' "$__foundation_document" |
        LC_ALL=C awk -v mode="$1" -v query_path="$2" -f "$__foundation_assets/json-query.awk"
}

_native_foundation_boot_valid() {
    [[ "$1" =~ ^(linux|windows|darwin):[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ &&
        "${1#*:}" != 00000000-0000-0000-0000-000000000000 ]]
}

_native_foundation_kernel_boot() {
    local identifier
    [[ "$(uname -s)" == Linux ]] || return 1
    IFS= read -r identifier </proc/sys/kernel/random/boot_id || return 1
    _native_foundation_boot_valid "linux:$identifier" ||
        { _native_foundation_error 'native boot identity malformed'; return 1; }
    printf 'linux:%s' "$identifier"
}

_native_foundation_int64() {
    local value="$1" bound=9223372036854775807 LC_ALL=C
    [[ "$value" =~ ^-?(0|[1-9][0-9]*)$ ]] || return 1
    if [[ "$value" == -* ]]; then value="${value#-}"; bound=9223372036854775808; fi
    [[ "${#value}" -lt 19 ]] ||
        [[ "${#value}" == 19 && ( "$value" == "$bound" || "$value" < "$bound" ) ]]
}

_native_foundation_validate() {
    local key keys kind value expected hex
    expected=$'boot_id\nerror\nexit_code\nfinished_at\noperation_id\nschema\nsettled_at\nsettled_boot_id\nsource_hash\nstarted_at\nstate\nversion'
    [[ "$(_native_foundation_query type '')" == object ]] || return 1
    keys="$(_native_foundation_query keys '')" || return 1
    keys="$(printf '%s\n' "$keys" | LC_ALL=C sort)" || return 1
    [[ "$keys" == "$expected" ]] ||
        { _native_foundation_error 'unsupported operation schema'; return 1; }
    declare -gA __foundation_value=() __foundation_type=()
    for key in schema version operation_id source_hash boot_id state started_at \
        finished_at exit_code error settled_boot_id settled_at; do
        kind="$(_native_foundation_query type "$key")" || return 1
        if [[ "$kind" == string ]]; then
            hex="$(_native_foundation_query hex "$key")" || return 1
            [[ "$hex" =~ ^([2-6][0-9a-f]|7[0-e])*$ ]] || return 1
        fi
        value="$(_native_foundation_query get "$key" && printf '.')" || return 1
        value="${value%.}"
        __foundation_type[$key]="$kind"
        __foundation_value[$key]="$value"
        case "$key" in
            schema|operation_id|source_hash|boot_id|state)
                [[ "$kind" == string ]] || return 1 ;;
            version) [[ "$kind" == number && "$value" == 1 ]] || return 1 ;;
            exit_code)
                [[ "$kind" == null ]] ||
                    { [[ "$kind" == number ]] && _native_foundation_int64 "$value"; } || return 1 ;;
            error|settled_boot_id) [[ "$kind" == null || "$kind" == string ]] || return 1 ;;
            started_at|finished_at|settled_at)
                if [[ "$key" != started_at && "$kind" == null ]]; then continue; fi
                [[ "$kind" == number ]] || return 1
                LC_ALL=C awk -v value="$value" 'BEGIN {
                    number = value + 0
                    exit !(number >= 0 && number <= 1.7976931348623157e308)
                }' || return 1 ;;
        esac
    done
    [[ "${__foundation_value[schema]}" == copilot-extensions.installation-foundation-operation &&
        "${__foundation_value[operation_id]}" =~ ^[0-9a-f]{32}$ &&
        "${__foundation_value[source_hash]}" =~ ^[0-9a-f]{64}$ ]] || return 1
    _native_foundation_boot_valid "${__foundation_value[boot_id]}" || return 1
    [[ "${__foundation_type[error]}" == null ||
        "${__foundation_value[error]}" =~ ^[a-z][a-z0-9_]{0,63}$ ]] || return 1
    case "${__foundation_value[state]}" in
        running)
            [[ "${__foundation_type[finished_at]}" == null &&
                "${__foundation_type[exit_code]}" == null && "${__foundation_type[error]}" == null ]] || return 1 ;;
        completed)
            [[ "${__foundation_type[finished_at]}" == number &&
                "${__foundation_type[exit_code]}" == number &&
                ( "${__foundation_value[exit_code]}" == 0 || "${__foundation_value[exit_code]}" == -0 ) &&
                "${__foundation_type[error]}" == null ]] || return 1 ;;
        failed)
            [[ "${__foundation_type[finished_at]}" == number && "${__foundation_type[exit_code]}" == number &&
                "${__foundation_value[exit_code]}" != 0 && "${__foundation_value[exit_code]}" != -0 &&
                "${__foundation_type[error]}" == string ]] || return 1 ;;
        interrupted)
            [[ "${__foundation_type[finished_at]}" == number &&
                "${__foundation_type[exit_code]}" == null && "${__foundation_type[error]}" == string ]] || return 1 ;;
        *) return 1 ;;
    esac
    if [[ "${__foundation_type[settled_boot_id]}" == null ]]; then
        [[ "${__foundation_type[settled_at]}" == null ]] || return 1
    else
        value="${__foundation_value[settled_boot_id]}"
        _native_foundation_boot_valid "$value" || return 1
        [[ "${__foundation_value[state]}" == interrupted && "${__foundation_type[settled_at]}" == number &&
            "$value" != "${__foundation_value[boot_id]}" &&
            "${value%%:*}" == "${__foundation_value[boot_id]%%:*}" ]] || return 1
    fi
}

read_native_foundation_progress() {
    local attempt code
    for attempt in 1 2 3; do
        if _read_native_foundation_snapshot "$1"; then return 0; else code=$?; fi
        [[ "$code" == 76 ]] || return "$code"
    done
    _native_foundation_error 'operation changed during repeated observation'
}

_read_native_foundation_snapshot() {
    local root="$1" path="$1/foundation-operation.json" size code parent
    __foundation_document=''
    __foundation_present=0
    if [[ ! -e "$root" && ! -L "$root" ]]; then
        [[ "$root" == /* && "$(readlink -m -- "$root" && printf '.')" == "$root"$'\n.' ]] ||
            { _native_foundation_error 'root must be absolute and unlinked'; return 1; }
        parent="$root"
        while [[ ! -e "$parent" && ! -L "$parent" ]]; do
            parent="${parent%/*}"
            [[ -n "$parent" ]] || parent=/
        done
        _native_foundation_plain "$parent" directory || return 1
        return 0
    fi
    _native_foundation_plain "$root" directory || return 1
    if [[ ! -e "$path" && ! -L "$path" ]]; then
        [[ ! -e "$root/foundation" && ! -L "$root/foundation" ]] ||
            { _native_foundation_error 'progress missing for existing artifacts'; return 1; }
        return 0
    fi
    _native_foundation_open "$path" || return "$?"
    size="$(stat -Lc '%s' "/proc/self/fd/$__foundation_fd")" || {
        exec {__foundation_fd}<&-; return 1;
    }
    [[ "$size" -le 32768 ]] || {
        exec {__foundation_fd}<&-; _native_foundation_error 'progress exceeds size bound'; return 1;
    }
    if IFS= LC_ALL=C read -r -d '' -n 32769 __foundation_document <&"$__foundation_fd"; then code=0; else code=$?; fi
    exec {__foundation_fd}<&-
    [[ "$code" == 1 ]] ||
        { _native_foundation_error 'progress contains NUL or exceeds size bound'; return 1; }
    _native_foundation_validate ||
        { _native_foundation_error 'malformed operation progress'; return 1; }
    __foundation_present=1
}

_native_foundation_encode() {
    local key separator=''
    printf '{'
    for key in schema version operation_id source_hash boot_id state started_at \
        finished_at exit_code error settled_boot_id settled_at; do
        printf '%s"%s":' "$separator" "$key"
        if [[ "${__foundation_type[$key]}" == string ]]; then
            printf '"%s"' "${__foundation_value[$key]}"
        elif [[ "${__foundation_type[$key]}" == null ]]; then
            printf null
        else
            printf '%s' "${__foundation_value[$key]}"
        fi
        separator=,
    done
    printf '}\n'
}

_native_foundation_write() {
    local root="$1" temporary code=0
    assert_native_operation_lock "$root/execution.lock" || return 1
    __foundation_document="$(_native_foundation_encode)" || return 1
    [[ $((${#__foundation_document} + 1)) -le 32768 ]] ||
        { _native_foundation_error 'encoded progress exceeds size bound'; return 1; }
    _native_foundation_validate || { _native_foundation_error 'invalid progress mutation'; return 1; }
    temporary="$(umask 077; mktemp -- "$root/.foundation-operation-XXXXXXXX.tmp")" || return 1
    if ! printf '%s\n' "$__foundation_document" >"$temporary" ||
        ! sync -f -- "$temporary" ||
        ! assert_native_operation_lock "$root/execution.lock" ||
        ! _native_foundation_plain "$root" directory ||
        ! mv -T -- "$temporary" "$root/foundation-operation.json" ||
        ! sync -f -- "$root"; then code=1; fi
    if [[ -e "$temporary" ]]; then rm -- "$temporary" || return 1; fi
    [[ "$code" == 0 ]] || { _native_foundation_error 'progress persistence failed'; return 1; }
    __foundation_present=1
}

_native_foundation_fenced() {
    [[ "$__foundation_present" == 1 && "${__foundation_type[settled_at]}" == null &&
        ( "${__foundation_value[state]}" == running || "${__foundation_value[state]}" == interrupted ) ]]
}

start_native_foundation_progress() {
    local root="$1" plugin="$2" expected="${3:-}" digest name identifier
    assert_native_operation_lock "$root/execution.lock" || return 1
    check_native_foundation_execution_root "$root" "$plugin" || return 1
    digest="$(native_foundation_source_digest "$plugin")" || return 1
    [[ -z "$expected" || "$digest" == "$expected" ]] ||
        { _native_foundation_error 'source differs from requested inventory'; return 1; }
    read_native_foundation_progress "$root" || return 1
    if _native_foundation_fenced; then
        _native_foundation_error 'fenced; positive settlement required'; return 1
    fi
    for name in queue.sqlite3 queue.sqlite3-wal queue.sqlite3-shm queue.sqlite3-journal; do
        [[ ! -e "$root/$name" && ! -L "$root/$name" ]] ||
            { _native_foundation_error 'existing queue requires queue-aware authority'; return 1; }
    done
    IFS= read -r identifier </proc/sys/kernel/random/uuid || return 1
    declare -gA __foundation_value=() __foundation_type=()
    for name in schema operation_id source_hash boot_id state error settled_boot_id; do
        __foundation_type[$name]=string
    done
    for name in version started_at; do __foundation_type[$name]=number; done
    for name in finished_at exit_code error settled_boot_id settled_at; do __foundation_type[$name]=null; done
    __foundation_value[schema]=copilot-extensions.installation-foundation-operation
    __foundation_value[version]=1
    __foundation_value[operation_id]="${identifier//-/}"
    __foundation_value[source_hash]="$digest"
    __foundation_value[boot_id]="$(_native_foundation_kernel_boot)" || return 1
    __foundation_value[state]=running
    __foundation_value[started_at]="$(LC_ALL=C date +%s.%N)" || return 1
    _native_foundation_write "$root"
}

complete_native_foundation_progress() {
    local root="$1" operation="$2" plugin="$3" code="$4" reason="${5:-}"
    assert_native_operation_lock "$root/execution.lock" || return 1
    __foundation_document="$operation"
    _native_foundation_validate || return 1
    operation="$(_native_foundation_encode)" || return 1
    read_native_foundation_progress "$root" || return 1
    [[ "$__foundation_present" == 1 && "$operation" == "$(_native_foundation_encode)" &&
        "${__foundation_value[state]}" == running ]] ||
        { _native_foundation_error 'completion does not match active operation'; return 1; }
    if [[ "$code" == 0 || "$code" == -0 ]]; then
        [[ "$(native_foundation_source_digest "$plugin")" == "${__foundation_value[source_hash]}" ]] ||
            { _native_foundation_error 'source changed after admission'; return 1; }
        __foundation_value[state]=completed
    elif [[ "$code" == null ]]; then __foundation_value[state]=interrupted
    else __foundation_value[state]=failed; fi
    __foundation_type[exit_code]=number
    [[ "$code" != null ]] || __foundation_type[exit_code]=null
    __foundation_value[exit_code]="$code"
    __foundation_type[error]=null
    if [[ -n "$reason" ]]; then __foundation_type[error]=string; __foundation_value[error]="$reason"; fi
    __foundation_type[finished_at]=number
    __foundation_value[finished_at]="$(LC_ALL=C date +%s.%N)" || return 1
    _native_foundation_write "$root"
}

reconcile_native_foundation_progress() {
    local root="$1"
    assert_native_operation_lock "$root/execution.lock" || return 1
    read_native_foundation_progress "$root" || return 1
    if [[ "$__foundation_present" == 1 && "${__foundation_value[state]}" == running ]]; then
        __foundation_value[state]=interrupted
        __foundation_type[finished_at]=number
        __foundation_value[finished_at]="$(LC_ALL=C date +%s.%N)" || return 1
        __foundation_type[error]=string
        __foundation_value[error]=owner_lost_custody
        _native_foundation_write "$root" || return 1
    fi
}

recover_native_foundation_after_restart() {
    local root="$1" current
    assert_native_operation_lock "$root/execution.lock" || return 1
    read_native_foundation_progress "$root" || return 1
    if ! _native_foundation_fenced; then return 0; fi
    current="$(_native_foundation_kernel_boot)" || return 1
    [[ "$current" != "${__foundation_value[boot_id]}" &&
        "${__foundation_value[boot_id]%%:*}" == linux ]] ||
        { _native_foundation_error 'recovery requires a different native kernel boot'; return 1; }
    __foundation_value[state]=interrupted
    if [[ "${__foundation_type[finished_at]}" == null ]]; then
        __foundation_type[finished_at]=number
        __foundation_value[finished_at]="$(LC_ALL=C date +%s.%N)" || return 1
    fi
    if [[ "${__foundation_type[error]}" == null ]]; then
        __foundation_type[error]=string
        __foundation_value[error]=owner_lost_custody
    fi
    __foundation_type[settled_boot_id]=string
    __foundation_value[settled_boot_id]="$current"
    __foundation_type[settled_at]=number
    __foundation_value[settled_at]="$(LC_ALL=C date +%s.%N)" || return 1
    _native_foundation_write "$root"
}
