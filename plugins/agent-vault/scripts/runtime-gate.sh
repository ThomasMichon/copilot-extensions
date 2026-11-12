#!/usr/bin/env bash
# Installation driver contract: vault-first-use-v1
# Configuration root contract: vault-context-v1
set -euo pipefail

export PYTHONUTF8=1
unset PYTHONPATH PYTHONHOME

PAYLOAD_ROOT="${AGENT_VAULT_PAYLOAD_ROOT:-}"
[[ "$PAYLOAD_ROOT" == /* && -d "$PAYLOAD_ROOT" ]] || {
    printf '[agent-vault] owning payload root is unavailable.\n' >&2
    exit 126
}

SCRIPT_DIR="$PAYLOAD_ROOT/scripts"
PROJECT_ROOT="$(pwd -P)"
INVOCATION_DIRECTORY="$PROJECT_ROOT"
has_configuration_root=false
invocation_args=("$@")
argument_count=$#
command_arguments=()
command_argument_count=0
for ((index=0; index<argument_count; index++)); do
    argument="${invocation_args[$index]}"
    if [[ "$argument" == -- ]]; then
        for ((remaining=index; remaining<argument_count; remaining++)); do
            command_arguments+=("${invocation_args[$remaining]}")
            command_argument_count=$((command_argument_count + 1))
        done
        break
    fi
    selected_root=""
    case "$argument" in
        --configuration-root)
            ((index + 1 < argument_count)) || {
                printf '[agent-vault] --configuration-root requires a directory.\n' >&2
                exit 2
            }
            index=$((index + 1))
            selected_root="${invocation_args[$index]}"
            ;;
        --configuration-root=*) selected_root="${argument#*=}" ;;
        *)
            command_arguments+=("$argument")
            command_argument_count=$((command_argument_count + 1))
            continue ;;
    esac
    if [[ "$has_configuration_root" == true ]]; then
        printf '[agent-vault] --configuration-root may be supplied only once.\n' >&2
        exit 2
    fi
    [[ "$selected_root" == /* && -d "$selected_root" ]] || {
        printf '[agent-vault] --configuration-root must name an existing absolute directory.\n' >&2
        exit 2
    }
    PROJECT_ROOT="$selected_root"
    has_configuration_root=true
done
if [[ "$has_configuration_root" == true ]]; then
    if [[ "${_AGENT_WORKTREES_VAULT_RUNTIME_GUARD:-}" == 1 ]]; then
        unset AGENT_VAULT_NO_SELFPROVISION
    fi
    unset _AGENT_WORKTREES_VAULT_RUNTIME_GUARD
fi
MODE_RUNNER="$SCRIPT_DIR/installation-context/installation-context.sh"
JSON_QUERY="$SCRIPT_DIR/installation-context/json-query.awk"
RUNTIME_RESOLVER="$SCRIPT_DIR/resolve-runtime.sh"
RUNTIME_ENVIRONMENT="$SCRIPT_DIR/runtime-environment.sh"
INSTALLER="$SCRIPT_DIR/install.sh"
LEGACY_ROOT="${AGENT_VAULT_HOME:-$HOME/.agent-vault}" # marketplace-isolation: allow legacy compatibility root
SEP=$'\034'
IDENTITY_SOURCE_HOME="${AGENT_VAULT_HOME:-}"
IDENTITY_SOURCE_ID="${AGENT_VAULT_INSTALLATION_ID:-}"
IDENTITY_CACHE_DIR="${AGENT_VAULT_CACHE_DIR:-}"

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
    . "$RUNTIME_RESOLVER"
}

run_runtime() {
    apply_runtime_env "$RUNTIME_ROOT" "$CONTEXT" "$INSTALLATION_ID" "$SERVICE_SUFFIX"
    if [[ -n "${AGENT_VAULT_PINNED_CONTEXT+x}" ]]; then
        local validated_cache
        validated_cache="$(
            "$AGENT_RT_PY" -I "$SCRIPT_DIR/validate_identity_cache_context.py" \
                "--runtime-root=$RUNTIME_ROOT" "--installation-id=$INSTALLATION_ID" \
                "--source-home=$IDENTITY_SOURCE_HOME" "--source-id=$IDENTITY_SOURCE_ID" \
                "--cache-dir=$IDENTITY_CACHE_DIR"
        )" || exit 126
        export AGENT_VAULT_CACHE_DIR="$validated_cache"
    fi
    case "${command_arguments[0]:-}" in
        installer-readiness|invocation-contract) exec "$AGENT_RT_PY" -I -B -X utf8 -m agent_vault "$@" ;;
    esac
    exec "$AGENT_RT_PY" -I -X utf8 -m agent_vault "$@"
}

[[ -f "$MODE_RUNNER" && -f "$JSON_QUERY" && -f "$RUNTIME_RESOLVER" &&
   -f "$RUNTIME_ENVIRONMENT" && -f "$INSTALLER" ]] || {
    printf '[agent-vault] installation-context runtime support is unavailable.\n' >&2
    exit 126
}
# shellcheck source=runtime-environment.sh
. "$RUNTIME_ENVIRONMENT"
if ! grep -Fq '# Trace contract: normal/read-only/ready-v1' "$RUNTIME_RESOLVER"; then
    printf '[agent-vault] read-only runtime resolver is unavailable. Re-enable/update the plugin, then retry.\n' >&2
    exit 126
fi

RUNTIME_ROOT="$LEGACY_ROOT"
CONTEXT=""
INSTALLATION_ID=""
SERVICE_SUFFIX=""
STATUS_ARGS=(
    status
    --payload-root "$PAYLOAD_ROOT"
    --plugin-id agent-vault
    --legacy-root "$LEGACY_ROOT"
    --project-root "$PROJECT_ROOT"
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
    printf '[agent-vault] installation context could not be resolved: %s\n' \
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
        printf '[agent-vault] requested installation context is not active.\n' >&2
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
        printf '[agent-vault] active installation context is incomplete.\n' >&2
        exit 126
    fi
    SERVICE_SUFFIX="$(scoped_identity_suffix "$RUNTIME_ROOT")"
    INSTALLATION_ID="${MARKETPLACE_ID:+$MARKETPLACE_ID/agent-vault}"
    VALIDATION_DURABLE_HOME="$CONTEXT"
    for _part in 1 2 3 4 5; do
        VALIDATION_DURABLE_HOME="$(dirname -- "$VALIDATION_DURABLE_HOME")"
    done
    VALIDATION="$(
        bash "$MODE_RUNNER" validate \
            --context "$CONTEXT" \
            --durable-home "$VALIDATION_DURABLE_HOME" \
            --expected-plugin-id agent-vault \
            --expected-payload-root "$PAYLOAD_ROOT"
    )" || {
        printf '[agent-vault] installation context validation failed.\n' >&2
        exit 126
    }
    VALIDATED_INSTALL_GENERATION="$(
        json_get "$VALIDATION" "$(json_path generation)" 2>/dev/null || true
    )"
    [[ "$VALIDATED_INSTALL_GENERATION" == "$INSTALL_GENERATION" ]] || {
        printf '[agent-vault] installation context generation does not match governance.\n' >&2
        exit 126
    }
else
    printf '[agent-vault] installation context blocks invocation: status=%s reason=%s.\n' \
        "${RESOLUTION_STATUS:-invalid}" "${RESOLUTION_REASON:-invalid}" >&2
    exit 126
fi

if [[ -z "$CONTEXT" ]]; then
    RUNTIME_ROOT="$(json_get "$RESOLUTION" "$(json_path legacy root)" 2>/dev/null || true)"
    if [[ "$RUNTIME_ROOT" != /* ]]; then
        printf '[agent-vault] installation context is missing the canonical legacy root.\n' >&2
        exit 126
    fi
fi

resolve_runtime
if [[ "${command_arguments[0]:-}" == invocation-contract ]]; then
    if [[ "$command_argument_count" != 2 || "${command_arguments[1]:-}" != --json ]]; then
        printf '[agent-vault] usage: invocation-contract --json\n' >&2
        exit 2
    fi
    if [[ -n "$AGENT_RT_PY" ]]; then run_runtime "$@"; fi
    printf '%s\n' '{"schema":"agent-vault.invocation","version":1,"capabilities":[],"runtime_prepared":false}'
    exit 0
fi
if [[ -z "${AGENT_VAULT_PINNED_CONTEXT+x}" ]]; then
    . "$SCRIPT_DIR/runtime-activation.sh"
    activation_code=0
    activation="$(vault_runtime_activation "$PROJECT_ROOT" "$AGENT_RT_PY" "--runtime-root=$RUNTIME_ROOT" "--context=$CONTEXT" "--invocation-cwd=$INVOCATION_DIRECTORY" --retain-native-selector)" || activation_code=$?
    if [[ "$activation_code" == 2 ]]; then
        if [[ "${command_arguments[0]:-}" == installer-readiness ]]; then
            printf '%s\n' "$activation"
            exit 0
        fi
        if [[ "${command_arguments[0]:-}" == stop || "${command_arguments[0]:-}" == lock ]]; then
            export AGENT_VAULT_NO_SELFPROVISION=1
        else
            printf '%s\n' "$activation" >&2
            exit 2
        fi
    elif [[ "$activation_code" != 0 ]]; then
        if [[ "$(json_get "$activation" "$(json_path reason)" 2>/dev/null || true)" == repository-context-missing ]]; then
            printf "%s\n" "[agent-vault] repository context is missing. Run from an adopted repository or use '<repo> vault unlock' from any directory." >&2
        else
            printf '[agent-vault] activation configuration could not be resolved; correct its configuration, binding or payload.\n' >&2
        fi
        exit 3
    fi
fi

if [[ -n "$AGENT_RT_PY" ]]; then
    run_runtime "$@"
fi
if [[ ( "${command_arguments[0]:-}" == identity-key && "${command_arguments[1]:-}" == setup ) ||
      ( "${command_arguments[0]:-}" == ssh-key && ( "${command_arguments[1]:-}" == prepare || "${command_arguments[1]:-}" == install ) ) ]]; then
    for argument in "${command_arguments[@]}"; do
        if [[ "$argument" == --require-running ]]; then
            printf '%s\n' '{"version":1,"error":"service_unavailable","message":"Provision and start the selected vault independently before delegated setup."}'
            exit 1
        fi
    done
fi
if [[ -n "${AGENT_VAULT_PINNED_CONTEXT+x}" ]]; then
    printf '[agent-vault] pinned identity reads require a prepared owning runtime; provision independently.\n' >&2
    exit 126
fi

if [[ -n "${AGENT_VAULT_NO_SELFPROVISION:-}" ]]; then
    printf '[agent-vault] runtime not provisioned (AGENT_VAULT_NO_SELFPROVISION set).\n' >&2
    exit 1
fi

PUBLICATION_ARGS=(
    probe-plugin-publication --payload-root "$PAYLOAD_ROOT"
    --plugin-id agent-vault --legacy-root "$RUNTIME_ROOT" --project-root "$PROJECT_ROOT"
)
if [[ -n "$CONTEXT" ]]; then
    PUBLICATION_ARGS+=(--context "$CONTEXT" --durable-home "$VALIDATION_DURABLE_HOME")
fi
PUBLICATION="" PUBLICATION_RC=0
PUBLICATION="$(bash "$MODE_RUNNER" "${PUBLICATION_ARGS[@]}")" || PUBLICATION_RC=$?
PUBLICATION_ROOT_PATH="$(json_path legacy root)"
[[ -z "$CONTEXT" ]] || PUBLICATION_ROOT_PATH="$(json_path runtimeRoot)"
if [[ "$PUBLICATION_RC" != 0 ||
      "$(json_type "$PUBLICATION" "$(json_path deployment allowPluginPublication)" 2>/dev/null || true)" != boolean ||
      "$(json_get "$PUBLICATION" "$(json_path deployment allowPluginPublication)" 2>/dev/null || true)" != true ||
      "$(json_get "$PUBLICATION" "$PUBLICATION_ROOT_PATH" 2>/dev/null || true)" != "$RUNTIME_ROOT" ||
      "$(json_get "$PUBLICATION" "$(json_path deployment path)" 2>/dev/null || true)" != "$RUNTIME_ROOT/runtime-deployment.json" ]]; then
    printf '[agent-vault] Runtime publication refused: %s Use explicit installation management to install or repair this runtime.\n' \
        "$PUBLICATION" >&2
    exit 3
fi
resolve_runtime
if [[ -n "$AGENT_RT_PY" ]]; then
    run_runtime "$@"
fi
if [[ -n "$CONTEXT" ]]; then
    printf '[agent-vault] cold namespaced provisioning requires a qualified installation driver; use explicit installation management.\n' >&2
    exit 126
fi
FIRST_USE="$PAYLOAD_ROOT/libs/update-driver/scripts/first-use.sh"
if [[ ! -f "$FIRST_USE" ]]; then
    printf '[agent-vault] packaged installation driver is unavailable; update or repair the owning payload.\n' >&2
    exit 126
fi
EXECUTION_ROOT="$HOME/.copilot-extensions/update-driver"
NATIVE_SELECTOR="$(json_get "${activation:-}" "$(json_path native_configuration_file)" 2>/dev/null || true)"
if [[ -n "$NATIVE_SELECTOR" ]]; then
    export AGENT_VAULT_CONFIG="$NATIVE_SELECTOR"
fi
printf '[agent-vault] submitting first-use provisioning to the installation driver: %s\n' "$EXECUTION_ROOT" >&2
apply_runtime_env "$RUNTIME_ROOT" "$CONTEXT" "$INSTALLATION_ID" "$SERVICE_SUFFIX"
bash "$FIRST_USE" "$EXECUTION_ROOT" "$PAYLOAD_ROOT" "$RUNTIME_ROOT" "$PROJECT_ROOT" --wait || exit "$?"
resolve_runtime
if [[ -n "$AGENT_RT_PY" ]]; then
    run_runtime "$@"
fi
printf '[agent-vault] driver completed without a resolvable runtime.\n' >&2
exit 1
