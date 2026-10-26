#!/usr/bin/env bash
# Installation driver contract: bridge-first-use-v1
set -euo pipefail

export PYTHONUTF8=1
unset PYTHONPATH PYTHONHOME

PAYLOAD_ROOT="${AGENT_BRIDGE_PAYLOAD_ROOT:-}"
[[ "$PAYLOAD_ROOT" == /* && -d "$PAYLOAD_ROOT" ]] || {
    printf '[agent-bridge] owning payload root is unavailable.\n' >&2
    exit 126
}

SCRIPT_DIR="$PAYLOAD_ROOT/scripts"
MODE_RUNNER="$SCRIPT_DIR/installation-context/installation-context.sh"
JSON_QUERY="$SCRIPT_DIR/installation-context/json-query.awk"
RUNTIME_RESOLVER="$SCRIPT_DIR/resolve-runtime.sh"
INSTALLER="$SCRIPT_DIR/install.sh"
LEGACY_ROOT="${AGENT_BRIDGE_INSTALL_DIR:-${AGENT_BRIDGE_CONFIG_DIR:-$HOME/.agent-bridge}}" # marketplace-isolation: allow legacy compatibility root
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

json_type() {
    LC_ALL=C awk -f "$JSON_QUERY" -v mode=type -v "query_path=$2" <<<"$1"
}

resolve_runtime() {
    AGENT_RT_PY=""
    AGENT_RT_ROOT="$RUNTIME_ROOT"
    export AGENT_RT_ROOT
    local AGENT_RT_RESOLVE_TRACE_MODE="${1:-ready}"
    # shellcheck source=/dev/null
    if ! . "$RUNTIME_RESOLVER"; then
        printf '[agent-bridge] runtime resolver failed. Re-enable/update the owning plugin, then retry.\n' >&2
        exit 126
    fi
}

apply_runtime_env() {
    export AGENT_BRIDGE_INSTALL_DIR="$RUNTIME_ROOT"
    export AGENT_BRIDGE_CONFIG_DIR="$RUNTIME_ROOT"
    export AGENT_BRIDGE_CONNECT_LOG="$RUNTIME_ROOT/logs/connect.log"
    if [[ -n "$CONTEXT" ]]; then
        export COPILOT_EXTENSIONS_CONTEXT="$CONTEXT"
    else
        unset COPILOT_EXTENSIONS_CONTEXT
    fi
    if [[ -n "$INSTALLATION_ID" ]]; then
        export AGENT_BRIDGE_INSTALLATION_ID="$INSTALLATION_ID"
    else
        unset AGENT_BRIDGE_INSTALLATION_ID
    fi
}

run_runtime() {
    apply_runtime_env
    exec "$AGENT_RT_PY" -m agent_bridge "$@"
}

[[ -f "$MODE_RUNNER" && -f "$JSON_QUERY" && -f "$RUNTIME_RESOLVER" &&
   -f "$INSTALLER" ]] || {
    printf '[agent-bridge] installation-context runtime support is unavailable.\n' >&2
    exit 126
}
grep -Fq '# Trace contract: normal/read-only/ready-v1' "$RUNTIME_RESOLVER" || {
    printf '[agent-bridge] read-only runtime resolver is unavailable. Re-enable/update the plugin, then retry.\n' >&2
    exit 126
}

RUNTIME_ROOT="$LEGACY_ROOT"
CONTEXT=""
INSTALLATION_ID=""
STATUS_ARGS=(
    status
    --payload-root "$PAYLOAD_ROOT"
    --plugin-id agent-bridge
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
    printf '[agent-bridge] installation context could not be resolved: %s\n' \
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
        printf '[agent-bridge] requested installation context is not active.\n' >&2
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
    MARKETPLACE_ID="$(json_get "$RESOLUTION" "$(json_path marketplaceId)" 2>/dev/null || true)"
    if [[ -z "$RUNTIME_ROOT" || -z "$CONTEXT" ]]; then
        printf '[agent-bridge] active installation context is incomplete.\n' >&2
        exit 126
    fi
    INSTALLATION_ID="${MARKETPLACE_ID:+$MARKETPLACE_ID/agent-bridge}"
    VALIDATION_DURABLE_HOME="$CONTEXT"
    for _part in 1 2 3 4 5; do
        VALIDATION_DURABLE_HOME="$(dirname -- "$VALIDATION_DURABLE_HOME")"
    done
    VALIDATION="$(
        bash "$MODE_RUNNER" validate \
            --context "$CONTEXT" \
            --durable-home "$VALIDATION_DURABLE_HOME" \
            --expected-plugin-id agent-bridge \
            --expected-payload-root "$PAYLOAD_ROOT"
    )" || {
        printf '[agent-bridge] installation context validation failed.\n' >&2
        exit 126
    }
    VALIDATED_INSTALL_GENERATION="$(
        json_get "$VALIDATION" "$(json_path generation)" 2>/dev/null || true
    )"
    VALIDATED_RUNTIME_ROOT="$(json_get "$VALIDATION" "$(json_path pluginRoot)" 2>/dev/null || true)"
    [[ "$VALIDATED_INSTALL_GENERATION" == "$INSTALL_GENERATION" &&
       "$VALIDATED_RUNTIME_ROOT" == "$RUNTIME_ROOT" ]] || {
        printf '[agent-bridge] installation context generation does not match governance.\n' >&2
        exit 126
    }
else
    printf '[agent-bridge] installation context blocks invocation: status=%s reason=%s.\n' \
        "${RESOLUTION_STATUS:-invalid}" "${RESOLUTION_REASON:-invalid}" >&2
    exit 126
fi

if [[ -z "$CONTEXT" ]]; then
    RUNTIME_ROOT="$(json_get "$RESOLUTION" "$(json_path legacy root)" 2>/dev/null || true)"
    [[ "$RUNTIME_ROOT" == /* ]] || {
        printf '[agent-bridge] installation context is missing the canonical legacy root.\n' >&2
        exit 126
    }
fi

resolve_runtime
if [[ -n "$AGENT_RT_PY" ]]; then
    run_runtime "$@"
fi

if [[ -n "${AGENT_BRIDGE_NO_SELFPROVISION:-}" ]]; then
    printf '[agent-bridge] runtime not provisioned (AGENT_BRIDGE_NO_SELFPROVISION set).\n' >&2
    exit 1
fi

assert_runtime_publication() {
    local -a PUBLICATION_ARGS=(
        probe-plugin-publication --payload-root "$PAYLOAD_ROOT"
        --plugin-id agent-bridge --legacy-root "$RUNTIME_ROOT"
    )
    if [[ -n "$CONTEXT" ]]; then
        PUBLICATION_ARGS+=(--context "$CONTEXT" --durable-home "$VALIDATION_DURABLE_HOME")
    fi
    local PUBLICATION="" PUBLICATION_RC=0 PUBLICATION_ROOT_PATH PUBLICATION_STATUS
    PUBLICATION="$(bash "$MODE_RUNNER" "${PUBLICATION_ARGS[@]}")" || PUBLICATION_RC=$?
    PUBLICATION_ROOT_PATH="$(json_path legacy root)"
    [[ -z "$CONTEXT" ]] || PUBLICATION_ROOT_PATH="$(json_path runtimeRoot)"
    if [[ "$PUBLICATION_RC" != 0 ||
          "$(json_type "$PUBLICATION" "$(json_path deployment allowPluginPublication)" 2>/dev/null || true)" != boolean ||
          "$(json_get "$PUBLICATION" "$(json_path deployment allowPluginPublication)" 2>/dev/null || true)" != true ||
          "$(json_get "$PUBLICATION" "$PUBLICATION_ROOT_PATH" 2>/dev/null || true)" != "$RUNTIME_ROOT" ||
          "$(json_get "$PUBLICATION" "$(json_path deployment path)" 2>/dev/null || true)" != "$RUNTIME_ROOT/runtime-deployment.json" ]]; then
        printf '[agent-bridge] Runtime publication refused: %s Use explicit installation management to install or repair this runtime.\n' \
            "$PUBLICATION" >&2
        exit 3
    fi
    if [[ -n "$CONTEXT" ]]; then
        PUBLICATION_STATUS="$(json_get "$PUBLICATION" "$(json_path status)" 2>/dev/null || true)"
        if [[ ( "$PUBLICATION_STATUS" != ready && "$PUBLICATION_STATUS" != deactivation-required ) ||
              "$(json_get "$PUBLICATION" "$(json_path actualMode)" 2>/dev/null || true)" != namespaced ||
              "$(json_get "$PUBLICATION" "$(json_path context)" 2>/dev/null || true)" != "$CONTEXT" ||
              "$(json_get "$PUBLICATION" "$(json_path installGeneration)" 2>/dev/null || true)" != "$INSTALL_GENERATION" ]]; then
            printf '[agent-bridge] Runtime publication refused: %s Decision no longer admits the validated installation context.\n' \
                "$PUBLICATION" >&2
            exit 3
        fi
    else
        local LEGACY_PUBLICATION="" LEGACY_PUBLICATION_RC=0
        LEGACY_PUBLICATION="$(bash "$MODE_RUNNER" probe-legacy --payload-root "$PAYLOAD_ROOT" \
            --plugin-id agent-bridge --legacy-root "$RUNTIME_ROOT")" || LEGACY_PUBLICATION_RC=$?
        if [[ "$LEGACY_PUBLICATION_RC" != 0 ||
              "$(json_type "$LEGACY_PUBLICATION" "$(json_path allowMutation)" 2>/dev/null || true)" != boolean ||
              "$(json_get "$LEGACY_PUBLICATION" "$(json_path allowMutation)" 2>/dev/null || true)" != true ||
              "$(json_get "$LEGACY_PUBLICATION" "$(json_path legacy root)" 2>/dev/null || true)" != "$RUNTIME_ROOT" ]]; then
            printf '[agent-bridge] Runtime publication refused: %s Use explicit installation management to install or repair this runtime.\n' \
                "$LEGACY_PUBLICATION" >&2
            exit 3
        fi
    fi
}

assert_runtime_publication
if [[ -v AGENT_BRIDGE_PINNED_CONTEXT ]]; then
    printf '[agent-bridge] pinned invocation requires a prepared owning runtime; provision independently.\n' >&2
    exit 126
fi
if [[ -n "$CONTEXT" || "$RUNTIME_ROOT" != "$HOME/.agent-bridge" ||
      -v AGENT_BRIDGE_INSTALLATION_ID ]]; then
    printf '[agent-bridge] cold namespaced/custom-root provisioning requires explicit installation management.\n' >&2
    exit 126
fi
if [[ -v UPDATE_DRIVER_WITNESS ]]; then
    printf '[agent-bridge] nested first-use submission is unsupported; retain the current installation owner.\n' >&2
    exit 126
fi
FIRST_USE="$PAYLOAD_ROOT/libs/update-driver/scripts/first-use.sh"
if [[ ! -f "$FIRST_USE" ]] ||
   ! grep -Fq '# Installation driver lifecycle contract: bridge-command-v1' "$FIRST_USE"; then
    printf '[agent-bridge] packaged installation driver lacks the Bridge command contract.\n' >&2
    exit 126
fi
PROJECT="$(pwd -P)"
EXECUTION_ROOT="$HOME/.copilot-extensions/update-driver"
printf '[agent-bridge] runtime not provisioned -- waiting for the installation driver. Extend your timeout; caller exit does not cancel the operation.\n' >&2
printf '::agent-provisioning:: plugin=agent-bridge reason=first-use status=%s\n' \
    "$EXECUTION_ROOT" >&2
bash "$FIRST_USE" "$EXECUTION_ROOT" "$PAYLOAD_ROOT" "$RUNTIME_ROOT" "$PROJECT" \
    --installer-action provision --wait
resolve_runtime
[[ -z "$AGENT_RT_PY" ]] || run_runtime "$@"
printf '[agent-bridge] driver completed without a resolvable runtime.\n' >&2
exit 1
