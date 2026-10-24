#!/usr/bin/env bash
# Installation driver contract: ssh-first-use-v1
set -euo pipefail

export PYTHONUTF8=1
unset PYTHONPATH PYTHONHOME

PAYLOAD_ROOT="${AGENT_SSH_PAYLOAD_ROOT:-}"
[[ "$PAYLOAD_ROOT" == /* && -d "$PAYLOAD_ROOT" ]] || {
    printf '[agent-ssh] owning payload root is unavailable.\n' >&2
    exit 126
}

SCRIPT_DIR="$PAYLOAD_ROOT/scripts"
MODE_RUNNER="$SCRIPT_DIR/installation-context/installation-context.sh"
JSON_QUERY="$SCRIPT_DIR/installation-context/json-query.awk"
RUNTIME_RESOLVER="$SCRIPT_DIR/resolve-runtime.sh"
INSTALLER="$SCRIPT_DIR/install.sh"
LEGACY_ROOT="$HOME/.agent-ssh" # marketplace-isolation: allow legacy compatibility root
SEP=$'\034'

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

resolve_runtime() {
    AGENT_RT_PY=""
    AGENT_RT_ROOT="$RUNTIME_ROOT"
    export AGENT_RT_ROOT
    if ! grep -Fq '# Trace contract: normal/read-only/ready-v1' "$RUNTIME_RESOLVER"; then
        printf '[agent-ssh] read-only runtime resolver is unavailable; update or repair the owning payload.\n' >&2
        exit 126
    fi
    local AGENT_RT_RESOLVE_TRACE_MODE=ready
    # shellcheck source=/dev/null
    . "$RUNTIME_RESOLVER"
}

run_runtime() {
    export AGENT_SSH_HOME="$RUNTIME_ROOT"
    export AGENT_SSH_PAYLOAD_ROOT="$PAYLOAD_ROOT"
    if [[ -n "$CONTEXT" ]]; then
        export COPILOT_EXTENSIONS_CONTEXT="$CONTEXT"
    else
        unset COPILOT_EXTENSIONS_CONTEXT
    fi
    exec "$AGENT_RT_PY" -m agent_ssh "$@"
}

[[ -f "$MODE_RUNNER" && -f "$JSON_QUERY" && -f "$RUNTIME_RESOLVER" &&
   -f "$INSTALLER" ]] || {
    printf '[agent-ssh] installation-context runtime support is unavailable.\n' >&2
    exit 126
}

RUNTIME_ROOT="$LEGACY_ROOT"
CONTEXT=""
STATUS_ARGS=(
    status
    --payload-root "$PAYLOAD_ROOT"
    --plugin-id agent-ssh
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
    printf '[agent-ssh] installation context could not be resolved: %s\n' \
        "$RESOLUTION" >&2
    exit 126
fi

RESOLUTION_STATUS="$(json_get "$RESOLUTION" "$(json_path status)" 2>/dev/null || true)"
RESOLUTION_REASON="$(json_get "$RESOLUTION" "$(json_path reason)" 2>/dev/null || true)"
ACTUAL_MODE="$(json_get "$RESOLUTION" "$(json_path actualMode)" 2>/dev/null || true)"
DESIRED_MODE="$(json_get "$RESOLUTION" "$(json_path desiredMode)" 2>/dev/null || true)"
INSTALL_GENERATION="$(json_get "$RESOLUTION" "$(json_path installGeneration)" 2>/dev/null || true)"
POLICY_ENABLED="$(json_get "$RESOLUTION" "$(json_path policy enabled)" 2>/dev/null || true)"
LEGACY_TOMBSTONE="$(json_get "$RESOLUTION" "$(json_path legacy tombstone)" 2>/dev/null || true)"
LEGACY_DISPOSITION="$(json_get "$RESOLUTION" "$(json_path legacy disposition)" 2>/dev/null || true)"

if [[ "$RESOLUTION_STATUS" == ready &&
      "$ACTUAL_MODE" == legacy &&
      "$DESIRED_MODE" == legacy ]]; then
    if [[ -n "${COPILOT_EXTENSIONS_CONTEXT:-}" ]]; then
        printf '[agent-ssh] requested installation context is not active.\n' >&2
        exit 126
    fi
elif [[ -z "${COPILOT_EXTENSIONS_CONTEXT:-}" &&
        "$RESOLUTION_STATUS" == provenance-blocked &&
        "$POLICY_ENABLED" == false &&
        -z "$LEGACY_TOMBSTONE" &&
        "$LEGACY_DISPOSITION" == active ]]; then
    :
elif [[ ( "$RESOLUTION_STATUS" == ready &&
          "$RESOLUTION_REASON" == namespaced-active ) ||
        "$RESOLUTION_STATUS" == deactivation-required ]] &&
     [[ "$ACTUAL_MODE" == namespaced ]]; then
    RUNTIME_ROOT="$(json_get "$RESOLUTION" "$(json_path runtimeRoot)" 2>/dev/null || true)"
    CONTEXT="$(json_get "$RESOLUTION" "$(json_path context)" 2>/dev/null || true)"
    if [[ -z "$RUNTIME_ROOT" || -z "$CONTEXT" ]]; then
        printf '[agent-ssh] active installation context is incomplete.\n' >&2
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
            --expected-plugin-id agent-ssh \
            --expected-payload-root "$PAYLOAD_ROOT"
    )" || {
        printf '[agent-ssh] installation context validation failed.\n' >&2
        exit 126
    }
    VALIDATED_INSTALL_GENERATION="$(
        json_get "$VALIDATION" "$(json_path generation)" 2>/dev/null || true
    )"
    [[ "$VALIDATED_INSTALL_GENERATION" == "$INSTALL_GENERATION" ]] || {
        printf '[agent-ssh] installation context generation does not match governance.\n' >&2
        exit 126
    }
else
    printf '[agent-ssh] installation context blocks invocation: status=%s reason=%s.\n' \
        "${RESOLUTION_STATUS:-invalid}" "${RESOLUTION_REASON:-invalid}" >&2
    exit 126
fi

resolve_runtime
if [[ -n "$AGENT_RT_PY" ]]; then
    run_runtime "$@"
fi

if [[ -n "${AGENT_SSH_NO_SELFPROVISION:-}" ]]; then
    printf '[agent-ssh] runtime not provisioned (AGENT_SSH_NO_SELFPROVISION set).\n' >&2
    exit 1
fi

if [[ "${AGENT_SSH_PINNED_CONTEXT+x}" == x ]]; then
    printf '[agent-ssh] pinned invocation requires a prepared owning runtime; provision independently.\n' >&2
    exit 126
fi
if [[ -n "$CONTEXT" || "$RUNTIME_ROOT" != "$LEGACY_ROOT" ||
      ( -n "${AGENT_SSH_HOME:-}" && "$AGENT_SSH_HOME" != "$LEGACY_ROOT" ) ]]; then
    printf '[agent-ssh] cold namespaced/custom-root provisioning requires explicit installation management.\n' >&2
    exit 126
fi
if [[ "${UPDATE_DRIVER_WITNESS+x}" == x ]]; then
    printf '[agent-ssh] nested first-use submission is unsupported; retain the current installation owner.\n' >&2
    exit 126
fi
PROJECT="$(pwd -P)"
EXECUTION_ROOT="$HOME/.copilot-extensions/update-driver"
first_use() {
    local payload="$1" action="$2"
    local driver="$payload/libs/update-driver/scripts/first-use.sh"
    if [[ ! -f "$driver" ]] ||
       ! grep -Fq '# Installation driver lifecycle contract: ssh-command-v1' "$driver"; then
        printf '[agent-ssh] packaged installation driver lacks the SSH stamp lifecycle contract.\n' >&2
        exit 126
    fi
    bash "$driver" "$EXECUTION_ROOT" "$payload" "$RUNTIME_ROOT" "$PROJECT" \
        --installer-action "$action" --wait
}
first_use "$PAYLOAD_ROOT" stamp-provision
resolve_runtime
[[ -z "$AGENT_RT_PY" ]] || run_runtime "$@"
printf '[agent-ssh] driver completed without a resolvable runtime.\n' >&2
exit 1
