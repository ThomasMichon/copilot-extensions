#!/usr/bin/env bash
set -euo pipefail

PAYLOAD_ROOT="${AGENT_WORKTREES_PAYLOAD_ROOT:-}"
[[ "$PAYLOAD_ROOT" == /* && -d "$PAYLOAD_ROOT" ]] || {
    printf '[agent-worktrees] owning payload root is unavailable.\n' >&2
    exit 126
}

SCRIPT_DIR="$PAYLOAD_ROOT/scripts"
MODE_RUNNER="$SCRIPT_DIR/installation-context/installation-context.sh"
JSON_QUERY="$SCRIPT_DIR/installation-context/json-query.awk"
RUNTIME_RESOLVER="$SCRIPT_DIR/resolve-runtime.sh"
INSTALLER="$SCRIPT_DIR/install.sh"
LEGACY_ROOT="$HOME/.agent-worktrees" # marketplace-isolation: allow legacy compatibility root
SEP=$'\034'

boot_trace_ms() {
    local raw=""
    if raw="$(date +%s%3N 2>/dev/null)"; then
        case "$raw" in
            [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9])
                printf '%s\n' "$raw"
                return 0
                ;;
        esac
    fi
    if raw="$(date +%s%N 2>/dev/null)"; then
        case "$raw" in
            [0-9]*)
                if [[ "${#raw}" -eq 19 ]]; then
                    printf '%s\n' "${raw%??????}"
                    return 0
                fi
                ;;
        esac
    fi
    if raw="$(date +%s 2>/dev/null)"; then
        case "$raw" in
            [0-9]*) printf '%s000\n' "$raw"; return 0 ;;
        esac
    fi
    printf '0000000000000\n'
}

boot_trace_iso() {
    local raw=""
    if raw="$(date -u +%Y-%m-%dT%H:%M:%S+00:00 2>/dev/null)"; then
        printf '%s\n' "$raw"
        return 0
    fi
    printf '1970-01-01T00:00:00+00:00\n'
}

boot_trace_log() {
    [[ -n "${RUNTIME_ROOT:-}" ]] || return 0
    # Never force-create the LEGACY root purely to write a boot-trace
    # record -- see invoke-payload-runtime.ps1's own identical guard for
    # the full rationale (Copilot review follow-up, PR #3310).
    if [[ "$RUNTIME_ROOT" == "$LEGACY_ROOT" && ! -d "$RUNTIME_ROOT" ]]; then
        return 0
    fi
    local phase="$1" now_ms="$2" dispatch_path="${3-}"
    local log_path="$RUNTIME_ROOT/logs/activity.jsonl"
    local log_dir="${log_path%/*}"
    [[ -d "$log_dir" ]] || mkdir -p "$log_dir" 2>/dev/null || return 0
    local line
    line="{\"ts\":\"$(boot_trace_iso)\",\"event\":\"boot_trace\",\"plugin\":\"agent-worktrees\",\"phase\":\"$(boot_trace_escape_json "$phase")\",\"t_ms\":$now_ms,\"pid\":$$"
    [[ -n "${HOSTNAME:-}" ]] && line="$line,\"host\":\"$(boot_trace_escape_json "$HOSTNAME")\""
    line="$line,\"source\":\"launcher\""
    [[ -n "$dispatch_path" ]] && line="$line,\"path\":\"$(boot_trace_escape_json "$dispatch_path")\""
    line="$line}"
    { printf '%s\n' "$line" >> "$log_path"; } 2>/dev/null || true
    boot_trace_maybe_prune "$log_path"
}

# Escapes backslash and double-quote so an inherited/environment-sourced
# value (HOSTNAME) can never produce a malformed JSONL line (Copilot
# review, PR #3310). Bash builtin substitution -- no subprocess spawn,
# unlike the plain-POSIX-sh templates' sed-based equivalent.
boot_trace_escape_json() {
    local v="$1"
    v="${v//\\/\\\\}"
    v="${v//\"/\\\"}"
    printf '%s' "$v"
}

# Mirrors agent_worktrees.activity._maybe_prune/_prune's own retention window
# (RETENTION_DAYS=7, _PRUNE_SIZE_BYTES=512*1024) so a launch that emits
# boot_trace lines but never triggers a later Python-side log_event() call
# (e.g. it exits before the real module runs any activity-logging code path)
# does not grow activity.jsonl unbounded. Best-effort and lossy under a
# concurrent writer during the rewrite, same posture as the Python pruner's
# own documented tradeoff. Never lets a pruning failure affect the caller.
boot_trace_maybe_prune() {
    local log_path="$1" size
    size="$(wc -c < "$log_path" 2>/dev/null)" || return 0
    size="${size//[[:space:]]/}"
    [[ "$size" =~ ^[0-9]+$ ]] || return 0
    (( size >= 524288 )) || return 0
    local cutoff
    cutoff="$(boot_trace_prune_cutoff_iso)" || return 0
    local tmp="${log_path}.prune.$$"
    if LC_ALL=C awk -v cutoff="$cutoff" '
        {
            # Tolerate both the compact form this shell writer produces
            # ("ts":"...") and the space-after-colon form Python'"'"'s
            # json.dumps (default separators) writes for every OTHER
            # activity.jsonl event ("ts": "...") -- matching only the
            # compact form previously treated every real Python-emitted
            # record as unparseable, retaining them forever and defeating
            # the whole point of this prune (Copilot review, PR #3310).
            match($0, /"ts"[[:space:]]*:[[:space:]]*"/)
            if (RSTART == 0) { print; next }
            rest = substr($0, RSTART + RLENGTH)
            j = index(rest, "\"")
            if (j == 0) { print; next }
            ts = substr(rest, 1, j - 1)
            if (ts >= cutoff) print
        }
    ' "$log_path" > "$tmp" 2>/dev/null; then
        mv -f "$tmp" "$log_path" 2>/dev/null || rm -f "$tmp" 2>/dev/null || true
    else
        rm -f "$tmp" 2>/dev/null || true
    fi
}

boot_trace_prune_cutoff_iso() {
    local now cutoff raw
    now="$(date +%s 2>/dev/null)" || return 1
    cutoff=$((now - 7 * 86400))
    if raw="$(date -u -d "@$cutoff" +%Y-%m-%dT%H:%M:%S+00:00 2>/dev/null)"; then
        printf '%s\n' "$raw"
        return 0
    fi
    if raw="$(date -u -r "$cutoff" +%Y-%m-%dT%H:%M:%S+00:00 2>/dev/null)"; then
        printf '%s\n' "$raw"
        return 0
    fi
    return 1
}

boot_trace() {
    local phase="$1" dispatch_path="${2-}" now_ms
    now_ms="$(boot_trace_ms)"
    boot_trace_log "$phase" "$now_ms" "$dispatch_path"
    [[ -n "${COPILOT_EXTENSIONS_BOOT_TRACE:-}" ]] || return 0
    local plugin="${COPILOT_EXTENSIONS_BOOT_TRACE_PLUGIN:-agent-worktrees}"
    if [[ -n "$dispatch_path" ]]; then
        printf '::boot-trace:: plugin=%s phase=%s t=%s path=%s\n' \
            "$plugin" "$phase" "$now_ms" "$dispatch_path" >&2
    else
        printf '::boot-trace:: plugin=%s phase=%s t=%s\n' \
            "$plugin" "$phase" "$now_ms" >&2
    fi
}

json_path() {
    local result="" component
    for component in "$@"; do
        [[ -z "$result" ]] || result+="$SEP"
        result+="$component"
    done
    printf '%s' "$result"
}

json_get() {
    LC_ALL=C awk -f "$JSON_QUERY" -v mode=get -v "query_path=$2" <<<"$1"
}

json_type() {
    LC_ALL=C awk -f "$JSON_QUERY" -v mode=type -v "query_path=$2" "$3"
}

json_len() {
    LC_ALL=C awk -f "$JSON_QUERY" -v mode=len -v "query_path=$2" "$3"
}

profile_home() {
    local uid entry="" home_path="" user=""
    uid="$(id -u 2>/dev/null)" || return 1
    if command -v getent >/dev/null 2>&1; then
        entry="$(getent passwd "$uid" 2>/dev/null || true)"
    fi
    if [[ -z "$entry" && -r /etc/passwd ]]; then
        entry="$(LC_ALL=C awk -F: -v uid="$uid" '$3 == uid { print; exit }' /etc/passwd)"
    fi
    if [[ -n "$entry" ]]; then
        home_path="$(printf '%s' "$entry" | LC_ALL=C cut -d: -f6)"
    elif command -v dscl >/dev/null 2>&1; then
        user="$(id -un 2>/dev/null || true)"
        if [[ -n "$user" ]]; then
            home_path="$(dscl . -read "/Users/$user" NFSHomeDirectory 2>/dev/null |
                LC_ALL=C awk '$1 == "NFSHomeDirectory:" { $1 = ""; sub(/^[[:space:]]+/, ""); print; exit }' || true)"
        fi
    fi
    [[ "$home_path" == /* && -d "$home_path" ]] || return 1
    (cd -P -- "$home_path" && pwd)
}

resolve_runtime() {
    AGENT_RT_PY=""
    if [[ -n "${COPILOT_EXTENSIONS_BOOT_TRACE:-}" ]]; then
        export COPILOT_EXTENSIONS_BOOT_TRACE_PLUGIN="agent-worktrees"
    fi
    AGENT_RT_ROOT="$RUNTIME_ROOT"
    export AGENT_RT_ROOT
    # shellcheck source=/dev/null
    . "$RUNTIME_RESOLVER"
}

run_runtime() {
    if [[ -n "$CONTEXT" ]]; then
        export COPILOT_EXTENSIONS_CONTEXT="$CONTEXT"
    else
        unset COPILOT_EXTENSIONS_CONTEXT
    fi
    exec "$AGENT_RT_PY" -m agent_worktrees "$@"
}

[[ -f "$MODE_RUNNER" && -f "$JSON_QUERY" && -f "$RUNTIME_RESOLVER" &&
   -f "$INSTALLER" ]] || {
    printf '[agent-worktrees] installation-context runtime support is unavailable.\n' >&2
    exit 126
}

PROFILE_HOME="$(profile_home)" || {
    printf '[agent-worktrees] cannot determine the canonical account home.\n' >&2
    exit 126
}
POLICY="$PROFILE_HOME/.copilot-extensions/installation-mode.json"
POLICY_PRESENT=0
[[ -e "$POLICY" || -L "$POLICY" ]] && POLICY_PRESENT=1
RUNTIME_ROOT="$LEGACY_ROOT"
CONTEXT=""
RESOLUTION_STATUS=ready
RESOLUTION_REASON=policy-default-false
ACTUAL_MODE=legacy
DESIRED_MODE=legacy

STATUS_ARGS=(
    status
    --payload-root "$PAYLOAD_ROOT"
    --plugin-id agent-worktrees
    --legacy-root "$LEGACY_ROOT"
)
if [[ -n "${COPILOT_EXTENSIONS_CONTEXT:-}" ]]; then
    STATUS_ARGS+=(--context "$COPILOT_EXTENSIONS_CONTEXT")
    CONTEXT_DURABLE_HOME="$COPILOT_EXTENSIONS_CONTEXT"
    for _part in 1 2 3 4 5; do
        CONTEXT_DURABLE_HOME="$(dirname -- "$CONTEXT_DURABLE_HOME")"
    done
    STATUS_ARGS+=(--durable-home "$CONTEXT_DURABLE_HOME")
fi
set +e
RESOLUTION="$(bash "$MODE_RUNNER" "${STATUS_ARGS[@]}" 2>&1)"
RESOLUTION_RC=$?
set -e
if [[ "$RESOLUTION_RC" -ne 0 ]]; then
    printf '[agent-worktrees] installation context could not be resolved: %s\n' \
        "$RESOLUTION" >&2
    exit 126
fi
RESOLUTION_STATUS="$(json_get "$RESOLUTION" "$(json_path status)" 2>/dev/null || true)"
RESOLUTION_REASON="$(json_get "$RESOLUTION" "$(json_path reason)" 2>/dev/null || true)"
ACTUAL_MODE="$(json_get "$RESOLUTION" "$(json_path actualMode)" 2>/dev/null || true)"
DESIRED_MODE="$(json_get "$RESOLUTION" "$(json_path desiredMode)" 2>/dev/null || true)"
ACTIVATION_GENERATION="$(json_get "$RESOLUTION" "$(json_path activationGeneration)" 2>/dev/null || true)"
NAMESPACE_GENERATION="$(json_get "$RESOLUTION" "$(json_path namespaceGeneration)" 2>/dev/null || true)"
INSTALL_GENERATION="$(json_get "$RESOLUTION" "$(json_path installGeneration)" 2>/dev/null || true)"
SIMPLE_POLICY_LEGACY=0
if [[ -z "${COPILOT_EXTENSIONS_CONTEXT:-}" &&
      "$POLICY_PRESENT" == 0 &&
      "$RESOLUTION_STATUS" == provenance-blocked &&
      "$(json_get "$RESOLUTION" "$(json_path policy state)" 2>/dev/null || true)" == missing &&
      "$(json_get "$RESOLUTION" "$(json_path policy enabled)" 2>/dev/null || true)" == false &&
      "$(json_get "$RESOLUTION" "$(json_path policy reason)" 2>/dev/null || true)" == policy-default-false &&
      -z "$(json_get "$RESOLUTION" "$(json_path legacy tombstone)" 2>/dev/null || true)" &&
      "$(json_get "$RESOLUTION" "$(json_path legacy disposition)" 2>/dev/null || true)" == active ]]; then
    SIMPLE_POLICY_LEGACY=1
elif [[ -z "${COPILOT_EXTENSIONS_CONTEXT:-}" &&
      "$RESOLUTION_STATUS" == provenance-blocked &&
      "$(json_get "$RESOLUTION" "$(json_path policy state)" 2>/dev/null || true)" == valid &&
      "$(json_get "$RESOLUTION" "$(json_path policy enabled)" 2>/dev/null || true)" == false &&
      -z "$(json_get "$RESOLUTION" "$(json_path legacy tombstone)" 2>/dev/null || true)" &&
      "$(json_get "$RESOLUTION" "$(json_path legacy disposition)" 2>/dev/null || true)" == active ]]; then
    MARKETPLACES_PATH="$(json_path installationMode marketplaces)"
      set +e
      MARKETPLACES_TYPE="$(
          json_type "$RESOLUTION" "$MARKETPLACES_PATH" "$POLICY" 2>/dev/null
      )"
      MARKETPLACES_TYPE_RC=$?
      set -e
      if [[ "$MARKETPLACES_TYPE_RC" == 3 ]]; then
          SIMPLE_POLICY_LEGACY=1
      elif [[ "$MARKETPLACES_TYPE_RC" == 0 &&
              "$MARKETPLACES_TYPE" == object ]]; then
          set +e
          MARKETPLACES_LEN="$(
              json_len "$RESOLUTION" "$MARKETPLACES_PATH" "$POLICY" 2>/dev/null
          )"
          MARKETPLACES_LEN_RC=$?
          set -e
          if [[ "$MARKETPLACES_LEN_RC" == 0 && "$MARKETPLACES_LEN" == 0 ]]; then
              SIMPLE_POLICY_LEGACY=1
          fi
      fi
fi
if [[ ( "$RESOLUTION_STATUS" == ready &&
      "$ACTUAL_MODE" == legacy &&
      "$DESIRED_MODE" == legacy ) ||
      "$SIMPLE_POLICY_LEGACY" == 1 ]]; then
    if [[ -n "${COPILOT_EXTENSIONS_CONTEXT:-}" ]]; then
        printf '[agent-worktrees] requested installation context is not active.\n' >&2
        exit 126
    fi
elif [[ ( "$RESOLUTION_STATUS" == ready &&
          "$RESOLUTION_REASON" == namespaced-active ) ||
        "$RESOLUTION_STATUS" == deactivation-required ]] &&
     [[ "$ACTUAL_MODE" == namespaced ]]; then
    RUNTIME_ROOT="$(json_get "$RESOLUTION" "$(json_path runtimeRoot)" 2>/dev/null || true)"
    CONTEXT="$(json_get "$RESOLUTION" "$(json_path context)" 2>/dev/null || true)"
    if [[ -z "$RUNTIME_ROOT" || -z "$CONTEXT" ]]; then
        printf '[agent-worktrees] active installation context is incomplete.\n' >&2
        exit 126
    fi
else
    printf '[agent-worktrees] installation context blocks invocation: status=%s reason=%s.\n' \
        "${RESOLUTION_STATUS:-invalid}" "${RESOLUTION_REASON:-invalid}" >&2
    exit 126
fi

VALIDATED_NAMESPACE_GENERATION=""
if [[ "$ACTUAL_MODE" == namespaced ]]; then
    VALIDATION_DURABLE_HOME="$CONTEXT"
    for _part in 1 2 3 4 5; do
        VALIDATION_DURABLE_HOME="$(dirname -- "$VALIDATION_DURABLE_HOME")"
    done
    VALIDATION_ARGS=(
        validate
        --context "$CONTEXT"
        --durable-home "$VALIDATION_DURABLE_HOME"
        --expected-plugin-id agent-worktrees
        --expected-payload-root "$PAYLOAD_ROOT"
    )
    VALIDATION="$(bash "$MODE_RUNNER" "${VALIDATION_ARGS[@]}")" || {
        printf '[agent-worktrees] installation context validation failed.\n' >&2
        exit 126
    }
    VALIDATED_NAMESPACE_GENERATION="$(
        json_get "$VALIDATION" "$(json_path namespaceGeneration)" 2>/dev/null || true
    )"
    VALIDATED_INSTALL_GENERATION="$(
        json_get "$VALIDATION" "$(json_path generation)" 2>/dev/null || true
    )"
    [[ "$VALIDATED_INSTALL_GENERATION" == "$INSTALL_GENERATION" ]] || {
        printf '[agent-worktrees] installation context generation does not match governance.\n' >&2
        exit 126
    }
fi

installation_resolution_current() {
    local current current_status current_reason current_actual current_desired
    local current_root current_context current_activation_generation
    local current_namespace_generation current_install_generation
    local current_validation current_validated_namespace current_validated_install
    current="$(bash "$MODE_RUNNER" "${STATUS_ARGS[@]}" 2>/dev/null)" || return 1
    current_status="$(json_get "$current" "$(json_path status)" 2>/dev/null || true)"
    current_reason="$(json_get "$current" "$(json_path reason)" 2>/dev/null || true)"
    current_actual="$(json_get "$current" "$(json_path actualMode)" 2>/dev/null || true)"
    current_desired="$(json_get "$current" "$(json_path desiredMode)" 2>/dev/null || true)"
    current_activation_generation="$(json_get "$current" "$(json_path activationGeneration)" 2>/dev/null || true)"
    current_namespace_generation="$(json_get "$current" "$(json_path namespaceGeneration)" 2>/dev/null || true)"
    current_install_generation="$(json_get "$current" "$(json_path installGeneration)" 2>/dev/null || true)"
    [[ "$current_status" == "$RESOLUTION_STATUS" &&
       "$current_reason" == "$RESOLUTION_REASON" &&
       "$current_actual" == "$ACTUAL_MODE" &&
       "$current_desired" == "$DESIRED_MODE" &&
       "$current_activation_generation" == "$ACTIVATION_GENERATION" &&
       "$current_namespace_generation" == "$NAMESPACE_GENERATION" &&
       "$current_install_generation" == "$INSTALL_GENERATION" ]] || return 1
    if [[ "$ACTUAL_MODE" == namespaced ]]; then
        current_root="$(json_get "$current" "$(json_path runtimeRoot)" 2>/dev/null || true)"
        current_context="$(json_get "$current" "$(json_path context)" 2>/dev/null || true)"
        [[ "$current_root" == "$RUNTIME_ROOT" && "$current_context" == "$CONTEXT" ]] ||
            return 1
        current_validation="$(
            bash "$MODE_RUNNER" "${VALIDATION_ARGS[@]}" 2>/dev/null
        )" || return 1
        current_validated_namespace="$(
            json_get "$current_validation" "$(json_path namespaceGeneration)" 2>/dev/null || true
        )"
        current_validated_install="$(
            json_get "$current_validation" "$(json_path generation)" 2>/dev/null || true
        )"
        [[ "$current_validated_namespace" == "$VALIDATED_NAMESPACE_GENERATION" &&
           "$current_validated_install" == "$INSTALL_GENERATION" ]] || return 1
    fi
}

resolve_runtime
boot_trace resolver-loaded
# Log the outer dispatcher template's own `shim-start` phase here, now
# that RUNTIME_ROOT reflects whichever root (legacy or an active
# namespaced context) is genuinely active -- the outer template forwards
# its own timestamp via this env var but never writes the durable record
# itself, precisely because it cannot know which root is correct
# (Copilot review, PR #3310; see the outer dispatcher-posix.tmpl's own
# comment for the full rationale).
if [[ -n "${COPILOT_EXTENSIONS_BOOT_TRACE_SHIM_START_MS:-}" ]]; then
    boot_trace_log "shim-start" "$COPILOT_EXTENSIONS_BOOT_TRACE_SHIM_START_MS" ""
fi
if [[ -n "${AGENT_RT_PY:-}" ]]; then
    boot_trace dispatch fast
    run_runtime "$@"
fi
if [[ -n "${AGENT_WORKTREES_NO_SELFPROVISION:-}" ]]; then
    printf '[agent-worktrees] runtime not provisioned (AGENT_WORKTREES_NO_SELFPROVISION set).\n' >&2
    exit 1
fi

printf '[agent-worktrees] runtime not provisioned -- provisioning from the owning payload.\n' >&2
printf '::agent-provisioning:: plugin=agent-worktrees eta_seconds=120 reason=first-use\n' >&2
boot_trace provision-start
mkdir -p "$RUNTIME_ROOT"
LOCK_LINK=""
PROVISION_PID=""
unlock_provision() {
    if [[ -n "$LOCK_LINK" ]]; then
        owner="$(readlink "$LOCK_LINK" 2>/dev/null || true)"
        [[ "$owner" != "$$" ]] || rm -f "$LOCK_LINK"
        LOCK_LINK=""
    else
        flock -u 9 2>/dev/null || true
        exec 9>&-
    fi
}
stop_provision() {
    local exit_code="$1"
    if [[ -n "$PROVISION_PID" ]]; then
        kill -- -"$PROVISION_PID" 2>/dev/null ||
            kill "$PROVISION_PID" 2>/dev/null || true
        wait "$PROVISION_PID" 2>/dev/null || true
        PROVISION_PID=""
    fi
    unlock_provision
    exit "$exit_code"
}
run_provision() {
    local result
    set -m
    "$@" &
    PROVISION_PID=$!
    set +m
    if wait "$PROVISION_PID"; then result=0; else result=$?; fi
    PROVISION_PID=""
    return "$result"
}
if command -v flock >/dev/null 2>&1 && [[ "${COPILOT_EXT_NO_FLOCK:-}" != 1 ]]; then
    exec 9>"$RUNTIME_ROOT/.provision.lock"
    flock 9
else
    LOCK_LINK="$RUNTIME_ROOT/.provision.lock.pid"
    until ln -s "$$" "$LOCK_LINK" 2>/dev/null; do
        owner="$(readlink "$LOCK_LINK" 2>/dev/null || true)"
        if [[ "$owner" =~ ^[0-9]+$ ]] && kill -0 "$owner" 2>/dev/null; then
            sleep 1
        elif [[ "$(readlink "$LOCK_LINK" 2>/dev/null || true)" == "$owner" ]]; then
            rm -f "$LOCK_LINK"
        fi
    done
fi
trap unlock_provision EXIT
trap 'stop_provision 130' INT
trap 'stop_provision 143' TERM

installation_resolution_current || {
    printf '[agent-worktrees] installation governance changed while waiting; retry.\n' >&2
    exit 126
}
resolve_runtime
if [[ -n "${AGENT_RT_PY:-}" ]]; then
    unlock_provision
    trap - EXIT INT TERM
    boot_trace resolver-loaded
    boot_trace dispatch locked-fast
    run_runtime "$@"
fi

if [[ "$ACTUAL_MODE" == namespaced ]]; then
    [[ "$RESOLUTION_STATUS" == ready && "$RESOLUTION_REASON" == namespaced-active ]] || {
        printf '[agent-worktrees] deactivation-pending installation cannot provision a new runtime.\n' >&2
        exit 126
    }
    export COPILOT_EXTENSIONS_CONTEXT="$CONTEXT"
    run_provision bash "$INSTALLER" install --install-dir "$RUNTIME_ROOT" >&2
else
    run_provision bash "$INSTALLER" stamp >&2
    SNAPSHOT="$(cat "$LEGACY_ROOT/payload-dir" 2>/dev/null || true)"
    SNAPSHOT_INSTALLER="$SNAPSHOT/scripts/install.sh"
    [[ -f "$SNAPSHOT_INSTALLER" ]] || {
        printf '[agent-worktrees] stamped snapshot installer not found: %s\n' \
            "$SNAPSHOT_INSTALLER" >&2
        exit 127
    }
    run_provision bash "$SNAPSHOT_INSTALLER" provision >&2
fi
boot_trace provision-end
installation_resolution_current || {
    printf '[agent-worktrees] installation governance changed during provisioning.\n' >&2
    exit 126
}
resolve_runtime
boot_trace resolver-loaded
if [[ -n "${AGENT_RT_PY:-}" ]]; then
    unlock_provision
    trap - EXIT INT TERM
    boot_trace dispatch provisioned
    run_runtime "$@"
fi
printf '[agent-worktrees] provisioning completed without a resolvable runtime.\n' >&2
exit 1
