#!/usr/bin/env bash
# Linux shell binding of agent_procutil.daemon_instances inventory.
# Sourcing is inert. No installer, service, process scan or implicit sweep.

daemon_instance_root() {
    if [[ -n "${1:-}" ]]; then printf '%s\n' "$1"; return; fi
    local entry home
    entry="$(getent passwd "$(id -u)")" || return 1
    IFS=: read -r _ _ _ _ _ home _ <<<"$entry"
    [[ "$home" == /* ]] || return 1
    printf '%s\n' "$home/.local/state/copilot-extensions/daemon-instances"
}

_di_python() {
    local target owner mode
    if [[ -n "${COPILOT_EXTENSIONS_DAEMON_PYTHON:-}" ]]; then
        target="$COPILOT_EXTENSIONS_DAEMON_PYTHON"
        [[ "$target" == /* ]] || {
            printf '%s\n' 'daemon lifecycle interpreter must be absolute' >&2; return 1;
        }
    else
        target="$(command -v python3)" || {
            printf '%s\n' 'daemon lifecycle requires a supported Python interpreter' >&2; return 1;
        }
    fi
    target="$(readlink -e -- "$target")" || return 1
    [[ -f "$target" && -x "$target" ]] || return 1
    owner="$(stat -c '%u' -- "$target")"; mode="$(stat -c '%a' -- "$target")"
    if [[ "$owner" == 0 || "$owner" == "$(id -u)" ]] && (( (8#$mode & 06022) == 0 )); then
        printf '%s\n' "$target"; return
    fi
    printf '%s\n' 'daemon lifecycle interpreter custody unavailable' >&2
    return 1
}

_di_lifecycle_source() {
    local target owner mode source_root
    source_root="$(readlink -e -- "$(dirname -- "${BASH_SOURCE[0]}")")" || return 1
    target="$source_root/daemon_instance_runtime.pyz"
    if [[ -f "$target" && ! -L "$target" && "$(readlink -e -- "$target")" == "$target" ]]; then
        owner="$(stat -c '%u' -- "$target")"; mode="$(stat -c '%a' -- "$target")"
        if [[ "$owner" == 0 || "$owner" == "$(id -u)" ]] && (( (8#$mode & 0022) == 0 )); then
            printf '%s\n' "$target"; return
        fi
    fi
    printf '%s\n' 'daemon lifecycle source custody unavailable' >&2
    return 1
}

_di_lifecycle() {
    local python target
    python="$(_di_python)" || return 1
    target="$(_di_lifecycle_source)" || return 1
    "$python" -I -S -B "$target" "$@"
}

_di_plain() {
    local path="$1" kind="${2:-file}" ownership="${3:-owned}"
    [[ "$path" == /* && ! -L "$path" && "$(readlink -m -- "$path")" == "$path" ]] || return 1
    if [[ "$ownership" != borrowed ]]; then
        [[ "$(stat -c '%u' -- "$path")" == "$(id -u)" ]] || return 1
    fi
    if [[ "$kind" == directory ]]; then [[ -d "$path" ]]; else [[ -f "$path" ]]; fi
}

_di_birth() {
    local process="$1" data boot namespace
    local -a fields
    [[ "$process" =~ ^[1-9][0-9]*$ && -r "/proc/$process/stat" ]] || return 2
    IFS= read -r data <"/proc/$process/stat" || return 2
    read -r -a fields <<<"${data##*) }"
    [[ "${fields[0]}" != Z ]] || return 2
    IFS= read -r boot </proc/sys/kernel/random/boot_id || return 1
    namespace="$(stat -Lc '%i' -- "/proc/$process/ns/pid")" || return 1
    printf 'linux:%s:%s:%s\n' "$boot" "$namespace" "${fields[19]}"
}

_di_decode_fields() {
    local -n target="$1"
    local key value extra decoded
    target=()
    while IFS=$'\t' read -r key value extra; do
        [[ "$key" =~ ^[a-z][a-z0-9_]{0,63}$ && -z "$extra" && ! -v "target[$key]" ]] || return 1
        decoded="$(printf '%s' "$value" | base64 --decode)" || return 1
        target["$key"]="$decoded"
    done
}

_di_read() {
    local path="$1" name="$2" size
    local -n result="$name"
    _di_plain "$path" || return 1
    size="$(stat -c '%s' -- "$path")" || return 1
    [[ "$size" -le 65536 ]] || return 1
    _di_decode_fields "$name" <"$path" || return 1
    [[ ( "${result[schema]:-}" == 1 || "${result[schema]:-}" == 2 ) &&
       "${result[instance_id]:-}" == "$(basename -- "$path" .instance)" ]]
}

_di_write() {
    local path="$1" name="$2" key pending
    local -n fields="$name"
    pending="$path.$(tr -d '-' </proc/sys/kernel/random/uuid).pending"
    (
        umask 077
        set -o noclobber
        for key in "${!fields[@]}"; do
            printf '%s\t' "$key"
            printf '%s' "${fields[$key]}" | base64 --wrap=0
            printf '\n'
        done >"$pending"
    ) || return 1
    if [[ -e "$path" ]]; then _di_plain "$path" || { rm -- "$pending"; return 1; }; fi
    mv -fT -- "$pending" "$path"
}

new_daemon_instance_reservation() {
    local service="$1" runtime="$2" source="$3" root="${4:-}"
    local purpose="${5:-production}" ttl="${6:-0}" maximum="${7:-0}"
    local owner_pid="$BASHPID" launcher_pid="$BASHPID" owner_birth owner run='' now hard deadline lockfd
    local -A record context timing
    local clock_fields key
    [[ "$(uname -s)" == Linux && -n "$service" && "$source" == /* && -f "$source" &&
        "$runtime" == /* && "$(readlink -m -- "$runtime")" == "$runtime" ]] || return 1
    owner_birth="$(_di_birth "$owner_pid")" || return 1
    owner="process:$owner_pid"
    if [[ -n "${COPILOT_EXTENSIONS_DAEMON_TEST_CONTEXT:-}" ]]; then
        _di_decode_fields context < <(printf '%s' "$COPILOT_EXTENSIONS_DAEMON_TEST_CONTEXT" | base64 --decode) || return 1
        [[ "$(_di_birth "${context[pid]}")" == "${context[birth]}" ]] || return 1
        if [[ "$purpose" == interactive-test && "${context[purpose]}" != interactive-test ]]; then
            printf '%s\n' 'interactive daemon requires a pause-tolerant lifecycle owner' >&2
            return 1
        fi
        owner_pid="${context[pid]}"; owner_birth="${context[birth]}"; owner="${context[owner]}"
        purpose="${context[purpose]}"; root="${context[registry_root]}"
        ttl="${context[ttl]}"; maximum="${context[max_lifetime]}"; run="${context[run_id]}"
    fi
    if [[ "$purpose" == unattended-test && "$ttl" == 0 ]]; then ttl=600; maximum=14400; fi
    case "$purpose" in
        production) [[ "$ttl" == 0 && "$maximum" == 0 ]] || return 1 ;;
        unattended-test|interactive-test)
            awk -v ttl="$ttl" -v cap="$maximum" 'BEGIN { exit !(ttl>0 && cap>=ttl && cap<1e15) }' || return 1 ;;
        *) return 1 ;;
    esac
    if [[ -d "$runtime" ]]; then
        _di_plain "$runtime" directory borrowed || return 1
    else
        (umask 077; mkdir -p -- "$runtime") || return 1
        _di_plain "$runtime" directory || return 1
    fi
    root="$(daemon_instance_root "$root")" || return 1
    [[ "$root" == /* && "$(readlink -m -- "$root")" == "$root" ]] || return 1
    (umask 077; mkdir -p -- "$root") || return 1
    _di_plain "$root" directory || return 1
    __daemon_instance_id="$(tr -d '-' </proc/sys/kernel/random/uuid)"
    __daemon_instance_root="$root"
    __daemon_instance_record="$root/$__daemon_instance_id.instance"
    now="$(date +%s.%N)"
    deadline="$(awk -v now="$now" -v ttl="$ttl" 'BEGIN { printf "%.9f", ttl ? now+ttl : 0 }')"
    hard="$(awk -v now="$now" -v ttl="$ttl" -v cap="$maximum" 'BEGIN { printf "%.9f", ttl ? now+cap : 0 }')"
    record=(
        [schema]=1 [instance_id]="$__daemon_instance_id" [state]=reserved
        [service]="$service" [purpose]="$purpose" [owner]="$owner" [lifecycle]=resident
        [owner_pid]="$owner_pid" [owner_birth]="$owner_birth"
        [launcher_pid]="$launcher_pid" [launcher_birth]="$(_di_birth "$launcher_pid")"
        [runtime_root]="$runtime" [runtime_device]="$(stat -c '%d' -- "$runtime")"
        [runtime_inode]="$(stat -c '%i' -- "$runtime")" [source]="$(readlink -e -- "$source")"
        [created]="$now" [updated]="$now" [artifact_count]=0 [ttl]="$ttl"
        [deadline]="$deadline" [hard_deadline]="$hard" [run_id]="$run" [protected]=0
    )
    if [[ "$purpose" != production ]]; then
        clock_fields="$(_di_lifecycle lease-fields "$ttl" "$maximum")" || return 1
        _di_decode_fields timing <<<"$clock_fields" || return 1
        for key in "${!timing[@]}"; do record["$key"]="${timing[$key]}"; done
    fi
    (umask 077; : >>"${__daemon_instance_record%.instance}.lock") || return 1
    _di_plain "${__daemon_instance_record%.instance}.lock" || return 1
    exec {lockfd}>>"${__daemon_instance_record%.instance}.lock" || return 1
    if ! flock -n "$lockfd"; then exec {lockfd}>&-; return 1; fi
    _di_write "$__daemon_instance_record" record
    local status=$?
    exec {lockfd}>&-
    return "$status"
}

enter_daemon_instance() {
    local service="$1" runtime="$2" source="$3" lockfd birth self="$BASHPID"
    local -A record
    if [[ -n "${COPILOT_EXTENSIONS_DAEMON_INSTANCE:-}" ]]; then
        [[ "$COPILOT_EXTENSIONS_DAEMON_INSTANCE" =~ ^[a-f0-9]{32}$ ]] || return 1
        __daemon_instance_id="$COPILOT_EXTENSIONS_DAEMON_INSTANCE"
        __daemon_instance_root="$COPILOT_EXTENSIONS_DAEMON_REGISTRY"
        __daemon_instance_record="$__daemon_instance_root/$__daemon_instance_id.instance"
    else
        if [[ -z "${5:-}" && -z "${COPILOT_EXTENSIONS_DAEMON_TEST_CONTEXT:-}" ]]; then
            printf '%s\n' 'direct daemon startup requires an explicit purpose or lifecycle reservation' >&2
            return 1
        fi
        new_daemon_instance_reservation "$@" || return 1
    fi
    _di_read "$__daemon_instance_record" record || return 1
    if [[ "${record[purpose]}" != production ]]; then
        printf '%s\n' 'daemon instance disposable admission requires run_daemon_instance' >&2
        return 1
    fi
    birth="$(_di_birth "$self")" || return 1
    if [[ -n "${record[pid]:-}" ]]; then
        [[ "${record[pid]}" == "$self" && "${record[birth]}" == "$birth" ]] || return 1
    fi
    exec {lockfd}>>"${__daemon_instance_record%.instance}.lock" || return 1
    if ! flock -n "$lockfd"; then exec {lockfd}>&-; return 1; fi
    record[pid]="$self"; record[birth]="$birth"; record[state]=running
    _di_write "$__daemon_instance_record" record
    local status=$?
    exec {lockfd}>&-
    unset COPILOT_EXTENSIONS_DAEMON_INSTANCE COPILOT_EXTENSIONS_DAEMON_REGISTRY
    return "$status"
}

run_daemon_instance() {
    local service="$1" runtime="$2" source="$3"
    shift 3
    [[ "${1:-}" == -- ]] || return 1
    shift
    if [[ -n "${COPILOT_EXTENSIONS_DAEMON_INSTANCE:-}" ]]; then
        __daemon_instance_id="$COPILOT_EXTENSIONS_DAEMON_INSTANCE"
        __daemon_instance_root="$COPILOT_EXTENSIONS_DAEMON_REGISTRY"
        __daemon_instance_record="$__daemon_instance_root/$__daemon_instance_id.instance"
    else
        new_daemon_instance_reservation "$service" "$runtime" "$source" || return 1
    fi
    export COPILOT_EXTENSIONS_DAEMON_INSTANCE="$__daemon_instance_id"
    export COPILOT_EXTENSIONS_DAEMON_REGISTRY="$__daemon_instance_root"
    local -A record
    _di_read "$__daemon_instance_record" record || return 1
    if [[ "${record[purpose]}" == production ]]; then
        enter_daemon_instance "$service" "$runtime" "$source" || return 1
        export COPILOT_EXTENSIONS_DAEMON_INSTANCE="$__daemon_instance_id"
        export COPILOT_EXTENSIONS_DAEMON_REGISTRY="$__daemon_instance_root"
        exec "$@"
    fi
    local -a admission=()
    if [[ -n "${__daemon_instance_ready_fd:-}" ]]; then
        admission=(--ready-fd "$__daemon_instance_ready_fd")
    fi
    # Replace this shell, rather than inserting a wrapper with another PID.
    local python target
    python="$(_di_python)" || return 1
    target="$(_di_lifecycle_source)" || return 1
    exec "$python" -I -S -B "$target" supervise "$__daemon_instance_record" "${admission[@]}" -- "$@"
}

_di_read_cursor() {
    local path="$1/.reconcile.cursor" header key extra size
    [[ -e "$path" || -L "$path" ]] || return 0
    _di_plain "$path" || return 1
    size="$(stat -c '%s' -- "$path")" || return 1
    [[ "$size" -le 96 ]] || return 1
    {
        IFS= read -r header && IFS= read -r key
    } <"$path" || return 1
    [[ "$header" == copilot-daemon-reconcile-v1 &&
       ( -z "$key" || "$key" =~ ^[a-f0-9]{32}$ ) &&
       "$size" -eq $((${#header}+${#key}+2)) ]] || return 1
    printf '%s' "$key"
}

_di_write_cursor() {
    local root="$1" key="$2" path="$1/.reconcile.cursor" pending
    if [[ -e "$path" || -L "$path" ]]; then _di_plain "$path" || return 1; fi
    pending="$root/.reconcile.$(tr -d '-' </proc/sys/kernel/random/uuid).pending"
    (umask 077; set -o noclobber; printf 'copilot-daemon-reconcile-v1\n%s\n' "$key" >"$pending") || return 1
    if [[ -e "$path" || -L "$path" ]]; then
        _di_plain "$path" || { rm -- "$pending"; return 1; }
    fi
    mv -fT -- "$pending" "$path"
}

reconcile_daemon_instances() (
    local limit="${1:-128}" seconds="${2:-5}" root="${3:-}"
    local path id start now count=0 lockfd reason birth status
    local LC_ALL=C cursor visited='' collector_fd
    local -a after=() before=()
    local -A record
    read -r start _ </proc/uptime || return 1
    [[ "$limit" =~ ^[0-9]+$ && "$limit" -ge 1 && "$limit" -le 4096 ]] || return 1
    awk -v n="$seconds" 'BEGIN { exit !(n>0 && n<1e9) }' || return 1
    root="$(daemon_instance_root "$root")" || return 1
    if [[ "$root" != /* || -L "$root" || "$(readlink -m -- "$root")" != "$root" ]]; then
        printf '%s\n' 'daemon instance inventory path unsafe' >&2
        return 1
    fi
    if [[ ! -e "$root" ]]; then
        local ancestor="$root" parent
        while [[ ! -e "$ancestor" && ! -L "$ancestor" ]]; do
            parent="${ancestor%/*}"; [[ -n "$parent" ]] || parent=/
            [[ "$parent" != "$ancestor" ]] || break
            ancestor="$parent"
        done
        if [[ -L "$ancestor" || ! -d "$ancestor" || ! -x "$ancestor" ]]; then
            printf '%s\n' 'daemon instance inventory parent unreadable' >&2
            return 1
        fi
        return 0
    fi
    _di_plain "$root" directory || return 1
    if [[ ! -r "$root" || ! -x "$root" ]]; then
        printf '%s\n' 'daemon instance inventory unreadable' >&2
        return 1
    fi
    cursor="$(_di_read_cursor "$root")" || {
        printf '%s\n' 'daemon instance inventory cursor invalid or unavailable' >&2; return 1;
    }
    for path in "$root"/*.instance; do
        [[ -e "$path" || -L "$path" ]] || continue
        id="${path##*/}"; id="${id%.instance}"
        [[ "$id" =~ ^[a-f0-9]{32}$ ]] || {
            printf '%s\n' 'daemon instance inventory key noncanonical' >&2; return 1;
        }
        after+=("$path")
    done
    [[ "${#after[@]}" -gt 0 ]] || return 0
    local collector="$root/.reconcile.lock"
    if [[ -e "$collector" || -L "$collector" ]]; then _di_plain "$collector" || return 1; fi
    (umask 077; : >>"$collector") || return 1
    _di_plain "$collector" || return 1
    exec {collector_fd}>>"$collector" || return 1
    flock -n "$collector_fd" || {
        printf '%s\n' 'daemon instance inventory collection already owned' >&2; return 1;
    }
    [[ "$(stat -c '%d:%i' -- "$collector")" == "$(stat -Lc '%d:%i' -- "/proc/self/fd/$collector_fd")" ]] || return 1
    cursor="$(_di_read_cursor "$root")" || {
        printf '%s\n' 'daemon instance inventory cursor invalid or unavailable' >&2; return 1;
    }
    local -a candidates=("${after[@]}")
    after=()
    for path in "${candidates[@]}"; do
        id="${path##*/}"; id="${id%.instance}"
        if [[ "$id" > "$cursor" ]]; then after+=("$path"); else before+=("$path"); fi
    done
    for path in "${after[@]}" "${before[@]}"; do
        read -r now _ </proc/uptime || return 1
        [[ "$count" -lt "$limit" ]] || break
        awk -v a="$start" -v b="$now" -v limit="$seconds" 'BEGIN { exit !(b-a<limit) }' || break
        count=$((count+1)); id="$(basename -- "$path" .instance)"; reason=invalid-record; status=held
        visited="$id"
        if [[ "$id" =~ ^[a-f0-9]{32}$ ]] && _di_read "$path" record; then
            if [[ "${record[purpose]:-}" == production ]]; then
                [[ "${record[lifecycle]:-resident}" == finite-worker ]] || continue
            fi
            local python target remaining
            if ! python="$(_di_python)" || ! target="$(_di_lifecycle_source)"; then
                printf 'held\tlifecycle-binding-unavailable\t%s\n' "$id"
                printf 'daemon instance held: %s lifecycle-binding-unavailable\n' "$id" >&2
                continue
            fi
            read -r now _ </proc/uptime || return 1
            remaining="$(awk -v a="$start" -v b="$now" -v limit="$seconds" \
                'BEGIN { printf "%.6f", limit-(b-a) }')"
            awk -v n="$remaining" 'BEGIN { exit !(n>0) }' || break
            "$python" -I -S -B "$target" cleanup "$path" "$remaining" || {
                printf 'daemon instance reconciliation unavailable: %s\n' "$id" >&2
            }
            continue
        fi
        [[ "$status" != held ]] || printf 'daemon instance held: %s %s\n' "$id" "$reason" >&2
        printf '%s\t%s\t%s\n' "$status" "$reason" "$id"
    done
    if [[ -n "$visited" && "$visited" != "$cursor" ]]; then
        _di_write_cursor "$root" "$visited" || {
            printf '%s\n' 'daemon instance inventory cursor publication unconfirmed' >&2; return 1;
        }
    fi
    return 0
)
