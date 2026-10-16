# shellcheck shell=bash
# Native first-touch helpers, packaged independently of the Python installer engine.
_install_integer_seconds() {
    local raw="$1" label="$2" digits limit=2147483647
    digits="${raw#-}"
    digits="${digits#+}"
    digits="$(printf '%s' "$digits" | sed 's/^0*//')"
    digits="${digits:-0}"
    if [[ "$raw" == -* ]]; then limit=2147483648; fi
    if [[ "${#digits}" -gt 10 ||
          ( "${#digits}" -eq 10 && "$digits" > "$limit" ) ]]; then
        printf 'Invalid %s: expected 32-bit integer seconds\n' "$label" >&2
        return 1
    fi
    if [[ "$raw" == -* && "$digits" != 0 ]]; then printf -- '-%s\n' "$digits"
    else printf '%s\n' "$digits"; fi
}

_install_manifest_seconds() {
    local manifest="$1" field="$2" fallback="$3" value
    if ! grep -q "\"$field\"[[:space:]]*:" <<<"$manifest"; then
        printf '%s\n' "$fallback"
        return
    fi
    value="$(printf '%s' "$manifest" |
        sed -n "s/.*\"$field\"[[:space:]]*:[[:space:]]*\([0-9][0-9]*\)[[:space:]]*[,}].*/\1/p")"
    if [[ ! "$value" =~ ^[0-9]+$ || "$value" =~ ^0+$ ]]; then
        printf 'Invalid %s: expected positive integer seconds\n' "$field" >&2
        return 1
    fi
    _install_integer_seconds "$value" "$field"
}

get_install_watchdog_budget() {
    local payload="$1" name="$2" build cutover variable raw manifest
    manifest="$(tr '\r\n' '  ' <"$payload/plugin.json")" || return $?
    if [[ ! "$manifest" =~ ^[[:space:]]*\{.*\}[[:space:]]*$ ]]; then
        printf 'Installer manifest must be an object\n' >&2
        return 1
    fi
    cutover="$(_install_manifest_seconds "$manifest" cutoverDeadlineSeconds 300)" || return $?
    variable="$(printf '%s' "$name" | sed 's/[^A-Za-z0-9][^A-Za-z0-9]*/_/g' |
        tr '[:lower:]' '[:upper:]')_INSTALL_DEADLINE_SEC"
    raw="${!variable:-${COPILOT_PLUGIN_INSTALL_DEADLINE_SEC:-}}"
    if [[ -n "$raw" ]]; then
        raw="$(printf '%s' "$raw" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
        if [[ ! "$raw" =~ ^[+-]?[0-9]+$ ]]; then
            printf 'Invalid installer deadline: expected integer seconds\n' >&2
            return 1
        fi
        build="$(_install_integer_seconds "$raw" installer-deadline)" || return $?
    else
        build="$(_install_manifest_seconds "$manifest" installerDeadlineSeconds 480)" || return $?
    fi
    printf '%s %s\n' "$build" "$cutover"
}

enter_install_phase() {
    local name="$1" budget="${2:-${COPILOT_PLUGIN_INSTALL_CUTOVER_BUDGET_SEC:-0}}"
    local path="${COPILOT_PLUGIN_INSTALL_PHASE_FILE:-}" temporary
    [[ -n "$path" ]] || return 0
    if [[ "$name" != cutover || ! "$budget" =~ ^[0-9]+$ || "$budget" =~ ^0+$ ]]; then
        printf 'Install phase requires cutover and a positive budget\n' >&2
        return 1
    fi
    budget="$(_install_integer_seconds "$budget" cutover-budget)" || return $?
    temporary="$path.${BASHPID:-$$}.tmp"
    printf '{"phase":"cutover","extend_until":%s}\n' \
        "$(($(date +%s) + 10#$budget))" >"$temporary" &&
        mv -f "$temporary" "$path"
}

get_install_watchdog_deadline() {
    local build="$1" cutover="$2" started="$3" file="$4"
    local deadline=$((started + build)) phase=build extension=""
    if [[ -f "$file" ]]; then
        extension="$(tr '\r\n' '  ' <"$file" |
            sed -n 's/^[[:space:]]*{[[:space:]]*"phase"[[:space:]]*:[[:space:]]*"cutover"[[:space:]]*,[[:space:]]*"extend_until"[[:space:]]*:[[:space:]]*\([0-9][0-9]*\)[[:space:]]*}[[:space:]]*$/\1/p')"
        if [[ "$extension" =~ ^[0-9]+$ && ! "$extension" =~ ^0+$ ]]; then
            phase=cutover
            extension=$((10#$extension))
            if [[ "$extension" -gt "$deadline" ]]; then deadline="$extension"; fi
            if [[ "$deadline" -gt $((started + build + cutover)) ]]; then
                deadline=$((started + build + cutover))
            fi
        else
            phase=invalid
        fi
    fi
    printf '%s %s\n' "$deadline" "$phase"
}

run_install_watchdog() {
    local entry="$1" name="$2" stage="$3" root="$4" build="$5" cutover="$6"
    shift 6
    local started child watcher="" rc
    export COPILOT_PLUGIN_INSTALL_PHASE_FILE="$stage/phase.json"
    export COPILOT_PLUGIN_INSTALL_CUTOVER_BUDGET_SEC="$cutover"
    started="$(date +%s)"
    set -m
    bash "$entry" "$@" &
    child=$!
    set +m
    _install_watchdog_cancel() {
        local signal_rc="$1"
        kill -KILL -- -"$child" 2>/dev/null || kill -KILL "$child" 2>/dev/null || true
        wait "$child" 2>/dev/null || true
        if [[ -n "$watcher" ]]; then
            kill -- -"$watcher" 2>/dev/null || kill "$watcher" 2>/dev/null || true
            wait "$watcher" 2>/dev/null || true
        fi
        if [[ "${COPILOT_PLUGIN_INSTALL_STAGED:-}" == context-install ]]; then
            rm -rf "$stage"
        fi
        trap - INT TERM
        exit "$signal_rc"
    }
    trap '_install_watchdog_cancel 130' INT
    trap '_install_watchdog_cancel 143' TERM
    if [[ "$build" -gt 0 ]]; then
        set -m
        (
            state="" deadline=0 phase=build remaining=0 invalid_reported=0
            while kill -0 "$child" 2>/dev/null; do
                state="$(get_install_watchdog_deadline "$build" "$cutover" "$started" \
                    "$COPILOT_PLUGIN_INSTALL_PHASE_FILE")"
                read -r deadline phase <<<"$state"
                if [[ "$phase" == invalid && "$invalid_reported" == 0 ]]; then
                    printf 'Invalid install-phase receipt; retaining the build deadline.\n' >&2
                    invalid_reported=1
                fi
                remaining=$((deadline - $(date +%s)))
                if [[ "$remaining" -le 0 ]]; then
                    : >"$stage/.watchdog-fired"
                    message="$(printf '[%sZ] WATCHDOG-KILL %s: phase=%s, build=%ss, cutover=%ss (child pid %s); terminating tree. Stage: %s' \
                        "$(date -u +%Y-%m-%dT%H:%M:%S)" "$name" "$phase" "$build" \
                        "$cutover" "$child" "$stage")"
                    printf '%s\n' "$message" >&2
                    printf '%s\n' "$message" >>"$root/reconcile.err.log" ||
                        printf 'Watchdog log write failed\n' >&2
                    kill -KILL -- -"$child" 2>/dev/null || kill -KILL "$child" 2>/dev/null || true
                    break
                fi
                if [[ "$remaining" -gt 5 ]]; then remaining=5; fi
                sleep "$remaining"
            done
        ) &
        watcher=$!
        set +m
    fi
    if wait "$child"; then rc=0; else rc=$?; fi
    if [[ -n "$watcher" ]]; then
        kill -- -"$watcher" 2>/dev/null || kill "$watcher" 2>/dev/null || true
        wait "$watcher" 2>/dev/null || true
    fi
    if [[ -e "$stage/.watchdog-fired" ]]; then rc=124; fi
    if [[ "${COPILOT_PLUGIN_INSTALL_STAGED:-}" == context-install ]]; then
        rm -rf "$stage"
    fi
    trap - INT TERM
    return "$rc"
}

start_install_watchdog() {
    local entry="$1" name="$2" stage="$3" root="$4" payload="$5"
    shift 5
    local budget build cutover
    budget="$(get_install_watchdog_budget "$payload" "$name")" || return $?
    read -r build cutover <<<"$budget"
    # Re-exec from inline functions, closing the replaceable installer's script descriptor.
    exec bash -c "$(declare -f get_install_watchdog_deadline run_install_watchdog)
run_install_watchdog \"\$@\"" bash "$entry" "$name" "$stage" "$root" "$build" "$cutover" "$@"
}
