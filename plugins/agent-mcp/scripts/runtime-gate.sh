#!/usr/bin/env bash
set -euo pipefail

export PYTHONUTF8=1
unset PYTHONPATH PYTHONHOME

PAYLOAD_ROOT="${AGENT_MCP_PAYLOAD_ROOT:-}"
[[ "$PAYLOAD_ROOT" == /* && -d "$PAYLOAD_ROOT" ]] || {
    printf '[agent-mcp] owning payload root is unavailable.\n' >&2
    exit 126
}

SCRIPT_DIR="$PAYLOAD_ROOT/scripts"
MODE_RUNNER="$SCRIPT_DIR/installation-context/installation-context.sh"
JSON_QUERY="$SCRIPT_DIR/installation-context/json-query.awk"
RUNTIME_RESOLVER="$SCRIPT_DIR/resolve-runtime.sh"
INSTALLER="$SCRIPT_DIR/init.sh"
LEGACY_ROOT="${AGENT_MCP_HOME:-$HOME/.agent-mcp}" # marketplace-isolation: allow legacy compatibility root
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
    # shellcheck source=/dev/null
    . "$RUNTIME_RESOLVER"
}

run_runtime() {
    export AGENT_MCP_HOME="$RUNTIME_ROOT"
    if [[ -n "$MARKETPLACE_ROOT" ]]; then
        export AGENT_MCP_MARKETPLACE_ROOT="$MARKETPLACE_ROOT"
    else
        unset AGENT_MCP_MARKETPLACE_ROOT
    fi
    if [[ -n "$CONTEXT" ]]; then
        export COPILOT_EXTENSIONS_CONTEXT="$CONTEXT"
    else
        unset COPILOT_EXTENSIONS_CONTEXT
    fi
    exec "$AGENT_RT_PY" -m agent_mcp "$@"
}

[[ -f "$MODE_RUNNER" && -f "$JSON_QUERY" && -f "$RUNTIME_RESOLVER" &&
   -f "$INSTALLER" ]] || {
    printf '[agent-mcp] installation-context runtime support is unavailable.\n' >&2
    exit 126
}

RUNTIME_ROOT="$LEGACY_ROOT"
MARKETPLACE_ROOT=""
CONTEXT=""
STATUS_ARGS=(
    status
    --payload-root "$PAYLOAD_ROOT"
    --plugin-id agent-mcp
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
    printf '[agent-mcp] installation context could not be resolved: %s\n' \
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
        printf '[agent-mcp] requested installation context is not active.\n' >&2
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
        printf '[agent-mcp] active installation context is incomplete.\n' >&2
        exit 126
    fi
    MARKETPLACE_ROOT="$(dirname -- "$PAYLOAD_ROOT")"
    VALIDATION_DURABLE_HOME="$CONTEXT"
    for _part in 1 2 3 4 5; do
        VALIDATION_DURABLE_HOME="$(dirname -- "$VALIDATION_DURABLE_HOME")"
    done
    VALIDATION="$(
        bash "$MODE_RUNNER" validate \
            --context "$CONTEXT" \
            --durable-home "$VALIDATION_DURABLE_HOME" \
            --expected-plugin-id agent-mcp \
            --expected-payload-root "$PAYLOAD_ROOT"
    )" || {
        printf '[agent-mcp] installation context validation failed.\n' >&2
        exit 126
    }
    VALIDATED_INSTALL_GENERATION="$(
        json_get "$VALIDATION" "$(json_path generation)" 2>/dev/null || true
    )"
    [[ "$VALIDATED_INSTALL_GENERATION" == "$INSTALL_GENERATION" ]] || {
        printf '[agent-mcp] installation context generation does not match governance.\n' >&2
        exit 126
    }
else
    printf '[agent-mcp] installation context blocks invocation: status=%s reason=%s.\n' \
        "${RESOLUTION_STATUS:-invalid}" "${RESOLUTION_REASON:-invalid}" >&2
    exit 126
fi

resolve_runtime
if [[ -n "$AGENT_RT_PY" ]]; then
    run_runtime "$@"
fi

if [[ -n "${AGENT_MCP_NO_SELFPROVISION:-}" ]]; then
    printf '[agent-mcp] runtime not provisioned (AGENT_MCP_NO_SELFPROVISION set).\n' >&2
    exit 1
fi

mkdir -p "$RUNTIME_ROOT"
STATUS_PATH="$RUNTIME_ROOT/.provision-status"
printf '%s\n' '[agent-mcp] runtime not provisioned -- provisioning on first use (may take ~30-120s: acquires uv + builds a venv). Do not kill; extend your timeout.' >&2
printf '::agent-provisioning:: plugin=%s eta_seconds=120 reason=first-use status=%s\n' \
    'agent-mcp' "$STATUS_PATH" >&2
printf 'provisioning %s\n' "$(date -u +%FT%TZ 2>/dev/null)" > "$STATUS_PATH" 2>/dev/null || true

set +e
bash "$INSTALLER" provision --install-dir "$RUNTIME_ROOT" >&2
PROVISION_EXIT=$?
set -e
if [[ "$PROVISION_EXIT" -ne 0 ]]; then
    printf 'failed rc=%s %s\n' "$PROVISION_EXIT" "$(date -u +%FT%TZ 2>/dev/null)" > "$STATUS_PATH" 2>/dev/null || true
    printf '[agent-mcp] provisioning FAILED. See the log above; retry, or run: bash "%s" provision --install-dir "%s"\n' \
        "$INSTALLER" "$RUNTIME_ROOT" >&2
    exit "$PROVISION_EXIT"
fi

resolve_runtime
if [[ -n "$AGENT_RT_PY" ]]; then
    printf 'ready %s\n' "$(date -u +%FT%TZ 2>/dev/null)" > "$STATUS_PATH" 2>/dev/null || true
    run_runtime "$@"
fi

printf 'failed rc=1 %s\n' "$(date -u +%FT%TZ 2>/dev/null)" > "$STATUS_PATH" 2>/dev/null || true
printf '[agent-mcp] provisioning reported success but no runtime slot resolved.\n' >&2
exit 1
