#!/usr/bin/env bash
set -euo pipefail

export PYTHONUTF8=1
unset PYTHONPATH PYTHONHOME

PAYLOAD_ROOT="${AGENT_LOGGER_PAYLOAD_ROOT:-}"
[[ "$PAYLOAD_ROOT" == /* && -d "$PAYLOAD_ROOT" ]] || {
    printf '[agent-logger] owning payload root is unavailable.\n' >&2
    exit 126
}

SCRIPT_DIR="$PAYLOAD_ROOT/scripts"
MODE_RUNNER="$SCRIPT_DIR/installation-context/installation-context.sh"
JSON_QUERY="$SCRIPT_DIR/installation-context/json-query.awk"
RUNTIME_RESOLVER="$SCRIPT_DIR/resolve-runtime.sh"
INSTALLER="$SCRIPT_DIR/install.sh"
LEGACY_ROOT="${AGENT_LOGGER_HOME:-$HOME/.agent-logger}" # marketplace-isolation: allow legacy compatibility root
COMMAND="${COPILOT_EXTENSIONS_PAYLOAD_COMMAND:-agent-logger}"
MODULE="${COPILOT_EXTENSIONS_PAYLOAD_MODULE:-agent_logger}"
SEP=$'\034'

case "$COMMAND:$MODULE" in
    agent-logger:agent_logger|\
    collate-session:agent_logger.segmenter.collate|\
    read-session-digest:agent_logger.segmenter.read_digest|\
    prepare-session-log:agent_logger.segmenter.prepare_log|\
    ramp-up-session:agent_logger.segmenter.ramp_up|\
    session-sync:agent_logger.sync.engine)
        ;;
    *)
        printf '[agent-logger] unsupported payload dispatch target: %s -> %s\n' \
            "$COMMAND" "$MODULE" >&2
        exit 126
        ;;
esac

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
            home_path="$(
                dscl . -read "/Users/$user" NFSHomeDirectory 2>/dev/null |
                    LC_ALL=C awk '$1 == "NFSHomeDirectory:" { $1 = ""; sub(/^[[:space:]]+/, ""); print; exit }' ||
                    true
            )"
        fi
    fi
    [[ "${home_path#/}" != "$home_path" && -d "$home_path" ]] || return 1
    (cd -P -- "$home_path" && pwd)
}

scoped_identity_suffix() {
    local normalized="$1"
    normalized="$(printf '%s' "$normalized" | tr '\\' '/' | tr '[:upper:]' '[:lower:]')"
    if command -v sha256sum >/dev/null 2>&1; then
        printf '%s' "$normalized" | sha256sum | awk '{print substr($1,1,12)}'
        return 0
    fi
    if command -v shasum >/dev/null 2>&1; then
        printf '%s' "$normalized" | shasum -a 256 | awk '{print substr($1,1,12)}'
        return 0
    fi
    printf '%s' "$normalized" | cksum | awk '{print $1}'
}

apply_runtime_env() {
    export AGENT_LOGGER_HOME="$RUNTIME_ROOT"
    if [[ -n "$CONTEXT" ]]; then
        export COPILOT_EXTENSIONS_CONTEXT="$CONTEXT"
    else
        unset COPILOT_EXTENSIONS_CONTEXT
    fi
    if [[ -n "$INSTALLATION_ID" ]]; then
        export AGENT_LOGGER_INSTALLATION_ID="$INSTALLATION_ID"
        export AGENT_LOGGER_TIMER_NAME="agent-logger-sync-$SERVICE_SUFFIX"
        export AGENT_LOGGER_TASK_NAME="Agent Logger Session Sync - $SERVICE_SUFFIX"
    else
        unset AGENT_LOGGER_INSTALLATION_ID AGENT_LOGGER_TIMER_NAME AGENT_LOGGER_TASK_NAME
    fi
}

resolve_runtime() {
    AGENT_RT_PY=""
    AGENT_RT_ROOT="$RUNTIME_ROOT"
    export AGENT_RT_ROOT
    # shellcheck source=/dev/null
    . "$RUNTIME_RESOLVER"
}

run_runtime() {
    apply_runtime_env
    exec "$AGENT_RT_PY" -m "$MODULE" "$@"
}

[[ -f "$MODE_RUNNER" && -f "$JSON_QUERY" && -f "$RUNTIME_RESOLVER" && -f "$INSTALLER" ]] || {
    printf '[agent-logger] installation-context runtime support is unavailable.\n' >&2
    exit 126
}

PROFILE_HOME="$(profile_home)" || {
    printf '[agent-logger] cannot determine the canonical account home.\n' >&2
    exit 126
}
POLICY="$PROFILE_HOME/.copilot-extensions/installation-mode.json"
POLICY_PRESENT=0
if [[ -e "$POLICY" || -L "$POLICY" ]]; then
    POLICY_PRESENT=1
fi
PROVENANCE_BOUNDARY=0
case "${PAYLOAD_ROOT//\\//}" in
    */.copilot/installed-plugins/*/*) PROVENANCE_BOUNDARY=1 ;;
esac
if [[ "$PROVENANCE_BOUNDARY" = 0 ]]; then
    PROBE_ROOT="$PAYLOAD_ROOT"
    while [[ "$PROBE_ROOT" != "/" ]]; do
        if [[ -f "$PROBE_ROOT/.github/plugin/marketplace.json" ]]; then
            PROVENANCE_BOUNDARY=1
            break
        fi
        PROBE_ROOT="$(dirname -- "$PROBE_ROOT")"
    done
fi

RUNTIME_ROOT="$LEGACY_ROOT"
CONTEXT=""
INSTALLATION_ID=""
SERVICE_SUFFIX=""
RESOLUTION_STATUS=ready
RESOLUTION_REASON=policy-default-false
ACTUAL_MODE=legacy
DESIRED_MODE=legacy
STATUS_ARGS=(
    status
    --payload-root "$PAYLOAD_ROOT"
    --plugin-id agent-logger
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
    printf '[agent-logger] installation context could not be resolved: %s\n' \
        "$RESOLUTION" >&2
    exit 126
fi

RESOLUTION_STATUS="$(json_get "$RESOLUTION" "$(json_path status)" 2>/dev/null || true)"
RESOLUTION_REASON="$(json_get "$RESOLUTION" "$(json_path reason)" 2>/dev/null || true)"
ACTUAL_MODE="$(json_get "$RESOLUTION" "$(json_path actualMode)" 2>/dev/null || true)"
DESIRED_MODE="$(json_get "$RESOLUTION" "$(json_path desiredMode)" 2>/dev/null || true)"
INSTALL_GENERATION="$(json_get "$RESOLUTION" "$(json_path installGeneration)" 2>/dev/null || true)"
SIMPLE_POLICY_LEGACY=0
if [[ -z "${COPILOT_EXTENSIONS_CONTEXT:-}" &&
      "$POLICY_PRESENT" = 0 &&
      "$PROVENANCE_BOUNDARY" = 0 &&
      "$RESOLUTION_STATUS" = provenance-blocked ]]; then
    SIMPLE_POLICY_LEGACY=1
elif [[ -z "${COPILOT_EXTENSIONS_CONTEXT:-}" &&
        "$RESOLUTION_STATUS" = provenance-blocked &&
        "$(json_get "$RESOLUTION" "$(json_path policy state)" 2>/dev/null || true)" = valid &&
        "$(json_get "$RESOLUTION" "$(json_path policy enabled)" 2>/dev/null || true)" = false ]]; then
    MARKETPLACES_PATH="$(json_path installationMode marketplaces)"
    MARKETPLACES_TYPE="$(json_type "$RESOLUTION" "$MARKETPLACES_PATH" "$POLICY" 2>/dev/null || true)"
    if [[ -z "$MARKETPLACES_TYPE" ||
          ( "$MARKETPLACES_TYPE" = object &&
            "$(json_len "$RESOLUTION" "$MARKETPLACES_PATH" "$POLICY" 2>/dev/null || true)" = 0 ) ]]; then
        SIMPLE_POLICY_LEGACY=1
    fi
fi

if { [[ "$RESOLUTION_STATUS" = ready &&
        "$ACTUAL_MODE" = legacy &&
        "$DESIRED_MODE" = legacy ]]; } ||
   [[ "$SIMPLE_POLICY_LEGACY" = 1 ]]; then
    if [[ -n "${COPILOT_EXTENSIONS_CONTEXT:-}" ]]; then
        printf '[agent-logger] requested installation context is not active.\n' >&2
        exit 126
    fi
elif { [[ "$RESOLUTION_STATUS" = ready &&
          "$RESOLUTION_REASON" = namespaced-active ]]; } ||
     [[ "$RESOLUTION_STATUS" = deactivation-required ]]; then
    if [[ "$ACTUAL_MODE" != namespaced ]]; then
        printf '[agent-logger] installation context blocks invocation: status=%s reason=%s.\n' \
            "$RESOLUTION_STATUS" "$RESOLUTION_REASON" >&2
        exit 126
    fi
    CONTEXT="$(json_get "$RESOLUTION" "$(json_path context)" 2>/dev/null || true)"
    MARKETPLACE_ID="$(json_get "$RESOLUTION" "$(json_path marketplaceId)" 2>/dev/null || true)"
    if [[ -z "$CONTEXT" || -z "$MARKETPLACE_ID" ]]; then
        printf '[agent-logger] active installation context is incomplete.\n' >&2
        exit 126
    fi
    VALIDATION_DURABLE_HOME="$CONTEXT"
    for _part in 1 2 3 4 5; do
        VALIDATION_DURABLE_HOME="$(dirname -- "$VALIDATION_DURABLE_HOME")"
    done
    VALIDATION="$(
        bash "$MODE_RUNNER" validate \
            --context "$CONTEXT" \
            --durable-home "$VALIDATION_DURABLE_HOME" \
            --expected-plugin-id agent-logger \
            --expected-payload-root "$PAYLOAD_ROOT"
    )" || {
        printf '[agent-logger] installation context validation failed.\n' >&2
        exit 126
    }
    VALIDATED_INSTALL_GENERATION="$(
        json_get "$VALIDATION" "$(json_path generation)" 2>/dev/null || true
    )"
    [[ -z "$INSTALL_GENERATION" || "$VALIDATED_INSTALL_GENERATION" = "$INSTALL_GENERATION" ]] || {
        printf '[agent-logger] installation context generation does not match governance.\n' >&2
        exit 126
    }
    RUNTIME_ROOT="$(json_get "$VALIDATION" "$(json_path pluginRoot)" 2>/dev/null || true)"
    [[ -n "$RUNTIME_ROOT" ]] || {
        printf '[agent-logger] active installation context is missing the plugin root.\n' >&2
        exit 126
    }
    SERVICE_SUFFIX="$(scoped_identity_suffix "$RUNTIME_ROOT")"
    INSTALLATION_ID="$MARKETPLACE_ID/agent-logger"
else
    printf '[agent-logger] installation context blocks invocation: status=%s reason=%s.\n' \
        "${RESOLUTION_STATUS:-invalid}" "${RESOLUTION_REASON:-invalid}" >&2
    exit 126
fi

resolve_runtime
if [[ -n "${AGENT_RT_PY:-}" ]]; then
    run_runtime "$@"
fi

if [[ -n "${AGENT_LOGGER_NO_SELFPROVISION:-}" ]]; then
    printf '[%s] runtime not provisioned (AGENT_LOGGER_NO_SELFPROVISION set).\n' \
        "$COMMAND" >&2
    exit 1
fi

mkdir -p "$RUNTIME_ROOT"
STATUS_PATH="$RUNTIME_ROOT/.provision-status"
_lock_link=""
_unlock_provision() {
    if [[ -n "$_lock_link" ]]; then
        _owner="$(readlink "$_lock_link" 2>/dev/null || true)"
        [[ "$_owner" == "$$" ]] && rm -f "$_lock_link"
        _lock_link=""
    else
        flock -u 9 2>/dev/null || true
        exec 9>&-
    fi
}
if command -v flock >/dev/null 2>&1 && [[ "${COPILOT_EXT_NO_FLOCK:-}" != "1" ]]; then
    exec 9>"$RUNTIME_ROOT/.provision.lock"
    flock 9
else
    _lock_link="$RUNTIME_ROOT/.provision.lock.pid"
    until ln -s "$$" "$_lock_link" 2>/dev/null; do
        _owner="$(readlink "$_lock_link" 2>/dev/null || true)"
        case "$_owner" in
            *[!0-9]*|"") _live=0 ;;
            *) if kill -0 "$_owner" 2>/dev/null; then _live=1; else _live=0; fi ;;
        esac
        if [[ "$_live" == 0 && "$(readlink "$_lock_link" 2>/dev/null || true)" == "$_owner" ]]; then
            rm -f "$_lock_link"
        else
            sleep 1
        fi
    done
fi
trap '_unlock_provision' EXIT INT TERM
resolve_runtime
if [[ -n "${AGENT_RT_PY:-}" ]]; then
    _unlock_provision
    trap - EXIT INT TERM
    run_runtime "$@"
fi
printf '[%s] runtime not provisioned -- provisioning on first use (may take ~30-120s: acquires uv + builds a venv). Do not kill; extend your timeout.\n' \
    "$COMMAND" >&2
printf '::agent-provisioning:: plugin=%s eta_seconds=120 reason=first-use status=%s\n' \
    "$COMMAND" "$STATUS_PATH" >&2
printf 'provisioning %s\n' "$(date -u +%FT%TZ 2>/dev/null)" > "$STATUS_PATH" 2>/dev/null || true

apply_runtime_env
set +e
bash "$INSTALLER" provision --install-dir "$RUNTIME_ROOT" >&2
PROVISION_EXIT=$?
set -e
if [[ "$PROVISION_EXIT" -ne 0 ]]; then
    printf 'failed rc=%s %s\n' "$PROVISION_EXIT" "$(date -u +%FT%TZ 2>/dev/null)" > "$STATUS_PATH" 2>/dev/null || true
    printf '[%s] provisioning FAILED. See the log above; retry, or run: bash "%s" provision --install-dir "%s"\n' \
        "$COMMAND" "$INSTALLER" "$RUNTIME_ROOT" >&2
    exit "$PROVISION_EXIT"
fi

resolve_runtime
if [[ -n "${AGENT_RT_PY:-}" ]]; then
    printf 'ready %s\n' "$(date -u +%FT%TZ 2>/dev/null)" > "$STATUS_PATH" 2>/dev/null || true
    _unlock_provision
    trap - EXIT INT TERM
    run_runtime "$@"
fi

_unlock_provision
trap - EXIT INT TERM
printf 'failed rc=1 %s\n' "$(date -u +%FT%TZ 2>/dev/null)" > "$STATUS_PATH" 2>/dev/null || true
printf '[%s] provisioning reported success but no runtime slot resolved.\n' "$COMMAND" >&2
exit 1
