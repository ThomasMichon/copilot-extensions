#!/usr/bin/env bash
set -euo pipefail

PAYLOAD_ROOT="${AGENT_MACHINES_PAYLOAD_ROOT:-}"
[[ "$PAYLOAD_ROOT" == /* && -d "$PAYLOAD_ROOT" ]] || {
    printf '[agent-machines] owning payload root is unavailable.\n' >&2
    exit 126
}

SCRIPT_DIR="$PAYLOAD_ROOT/scripts"
MODE_RUNNER="$SCRIPT_DIR/installation-context/installation-context.sh"
JSON_QUERY="$SCRIPT_DIR/installation-context/json-query.awk"
RUNTIME_RESOLVER="$SCRIPT_DIR/resolve-runtime.sh"
LEGACY_ROOT="$HOME/.agent-machines" # marketplace-isolation: allow legacy compatibility root
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
    if [[ -f "$RUNTIME_RESOLVER" ]]; then
        AGENT_RT_ROOT="$RUNTIME_ROOT"
        export AGENT_RT_ROOT
        # shellcheck source=/dev/null
        . "$RUNTIME_RESOLVER"
    fi
}

run_runtime() {
    if [[ -n "$CONTEXT" ]]; then
        export COPILOT_EXTENSIONS_CONTEXT="$CONTEXT"
    else
        unset COPILOT_EXTENSIONS_CONTEXT
    fi
    exec "$AGENT_RT_PY" -m agent_machines "$@"
}

[[ -f "$MODE_RUNNER" && -f "$JSON_QUERY" && -f "$RUNTIME_RESOLVER" ]] || {
    printf '[agent-machines] installation-context runtime resolver is unavailable.\n' >&2
    exit 126
}

PROFILE_HOME="$(profile_home)" || {
    printf '[agent-machines] cannot determine the canonical account home.\n' >&2
    exit 126
}
POLICY="$PROFILE_HOME/.copilot-extensions/installation-mode.json"
POLICY_PRESENT=0
[[ -e "$POLICY" || -L "$POLICY" ]] && POLICY_PRESENT=1
PROVENANCE_BOUNDARY=0
case "${PAYLOAD_ROOT//\\//}" in
    */.copilot/installed-plugins/*/*) PROVENANCE_BOUNDARY=1 ;;
esac
if [[ "$PROVENANCE_BOUNDARY" == 0 ]]; then
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
MARKETPLACE_ID=""
RESOLUTION_STATUS=ready
RESOLUTION_REASON=policy-default-false
ACTUAL_MODE=legacy
DESIRED_MODE=legacy

    STATUS_ARGS=(
        status
        --payload-root "$PAYLOAD_ROOT"
        --plugin-id agent-machines
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
        printf '[agent-machines] installation context could not be resolved: %s\n' "$RESOLUTION" >&2
        exit 126
    fi
    RESOLUTION_STATUS="$(json_get "$RESOLUTION" "$(json_path status)" 2>/dev/null || true)"
    RESOLUTION_REASON="$(json_get "$RESOLUTION" "$(json_path reason)" 2>/dev/null || true)"
    ACTUAL_MODE="$(json_get "$RESOLUTION" "$(json_path actualMode)" 2>/dev/null || true)"
    DESIRED_MODE="$(json_get "$RESOLUTION" "$(json_path desiredMode)" 2>/dev/null || true)"
    SIMPLE_POLICY_LEGACY=0
    if [[ -z "${COPILOT_EXTENSIONS_CONTEXT:-}" &&
          "$POLICY_PRESENT" == 0 &&
          "$PROVENANCE_BOUNDARY" == 0 &&
          "$RESOLUTION_STATUS" == provenance-blocked ]]; then
        SIMPLE_POLICY_LEGACY=1
    elif [[ -z "${COPILOT_EXTENSIONS_CONTEXT:-}" &&
          "$RESOLUTION_STATUS" == provenance-blocked &&
          "$(json_get "$RESOLUTION" "$(json_path policy state)" 2>/dev/null || true)" == valid &&
          "$(json_get "$RESOLUTION" "$(json_path policy enabled)" 2>/dev/null || true)" == false ]]; then
        MARKETPLACES_PATH="$(json_path installationMode marketplaces)"
        MARKETPLACES_TYPE="$(json_type "$RESOLUTION" "$MARKETPLACES_PATH" "$POLICY" 2>/dev/null || true)"
        if [[ -z "$MARKETPLACES_TYPE" ]] ||
           [[ "$MARKETPLACES_TYPE" == object &&
              "$(json_len "$RESOLUTION" "$MARKETPLACES_PATH" "$POLICY" 2>/dev/null || true)" == 0 ]]; then
            SIMPLE_POLICY_LEGACY=1
        fi
    fi
    if [[ ( "$RESOLUTION_STATUS" == ready &&
          "$ACTUAL_MODE" == legacy &&
          "$DESIRED_MODE" == legacy ) ||
          "$SIMPLE_POLICY_LEGACY" == 1 ]]; then
        if [[ -n "${COPILOT_EXTENSIONS_CONTEXT:-}" ]]; then
            printf '[agent-machines] requested installation context is not active.\n' >&2
            exit 126
        fi
        RUNTIME_ROOT="$LEGACY_ROOT"
    elif [[ ( "$RESOLUTION_STATUS" == ready &&
              "$RESOLUTION_REASON" == namespaced-active ) ||
            "$RESOLUTION_STATUS" == deactivation-required ]] &&
         [[ "$ACTUAL_MODE" == namespaced ]]; then
        RUNTIME_ROOT="$(json_get "$RESOLUTION" "$(json_path runtimeRoot)" 2>/dev/null || true)"
        CONTEXT="$(json_get "$RESOLUTION" "$(json_path context)" 2>/dev/null || true)"
        MARKETPLACE_ID="$(json_get "$RESOLUTION" "$(json_path marketplaceId)" 2>/dev/null || true)"
        if [[ -z "$RUNTIME_ROOT" || -z "$CONTEXT" || -z "$MARKETPLACE_ID" ]]; then
            printf '[agent-machines] active installation context is incomplete.\n' >&2
            exit 126
        fi
    else
        printf '[agent-machines] installation context blocks invocation: status=%s reason=%s.\n' \
            "${RESOLUTION_STATUS:-invalid}" "${RESOLUTION_REASON:-invalid}" >&2
        exit 126
    fi

resolve_runtime
if [[ -n "${AGENT_RT_PY:-}" ]]; then
    run_runtime "$@"
fi
if [[ -n "${AGENT_MACHINES_NO_SELFPROVISION:-}" ]]; then
    printf '[agent-machines] runtime not provisioned (AGENT_MACHINES_NO_SELFPROVISION set).\n' >&2
    exit 1
fi

INSTALLER="$SCRIPT_DIR/init.sh"
[[ -f "$INSTALLER" ]] || {
    printf '[agent-machines] payload installer not found: %s\n' "$INSTALLER" >&2
    exit 127
}
printf '[agent-machines] runtime not provisioned -- provisioning from the owning payload.\n' >&2
printf '::agent-provisioning:: plugin=agent-machines eta_seconds=120 reason=first-use\n' >&2
if [[ "$ACTUAL_MODE" == namespaced ]]; then
    [[ "$RESOLUTION_STATUS" == ready && "$RESOLUTION_REASON" == namespaced-active ]] || {
        printf '[agent-machines] deactivation-pending installation cannot provision a new runtime.\n' >&2
        exit 126
    }
    bash "$INSTALLER" cell-provision \
        --context "$CONTEXT" \
        --expected-marketplace-id "$MARKETPLACE_ID" >&2
    resolve_runtime
    if [[ -n "${AGENT_RT_PY:-}" ]]; then
        run_runtime "$@"
    fi
    printf '[agent-machines] provisioning completed without a resolvable runtime.\n' >&2
    exit 1
fi

mkdir -p "$RUNTIME_ROOT"
LOCK_LINK=""
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
if command -v flock >/dev/null 2>&1 && [[ "${COPILOT_EXT_NO_FLOCK:-}" != 1 ]]; then
    exec 9>"$RUNTIME_ROOT/.payload-provision.lock"
    flock 9
else
    LOCK_LINK="$RUNTIME_ROOT/.payload-provision.lock.pid"
    until ln -s "$$" "$LOCK_LINK" 2>/dev/null; do
        owner="$(readlink "$LOCK_LINK" 2>/dev/null || true)"
        if [[ "$owner" =~ ^[0-9]+$ ]] && kill -0 "$owner" 2>/dev/null; then
            sleep 1
        elif [[ "$(readlink "$LOCK_LINK" 2>/dev/null || true)" == "$owner" ]]; then
            rm -f "$LOCK_LINK"
        fi
    done
fi
trap unlock_provision EXIT INT TERM

resolve_runtime
if [[ -n "${AGENT_RT_PY:-}" ]]; then
    unlock_provision
    trap - EXIT INT TERM
    run_runtime "$@"
fi

bash "$INSTALLER" stamp >&2
SNAPSHOT="$(cat "$LEGACY_ROOT/payload-dir" 2>/dev/null || true)"
SNAPSHOT_INSTALLER="$SNAPSHOT/scripts/init.sh"
[[ -f "$SNAPSHOT_INSTALLER" ]] || {
    printf '[agent-machines] stamped snapshot installer not found: %s\n' "$SNAPSHOT_INSTALLER" >&2
    exit 127
}
bash "$SNAPSHOT_INSTALLER" provision >&2

resolve_runtime
if [[ -n "${AGENT_RT_PY:-}" ]]; then
    unlock_provision
    trap - EXIT INT TERM
    run_runtime "$@"
fi
printf '[agent-machines] provisioning completed without a resolvable runtime.\n' >&2
exit 1
