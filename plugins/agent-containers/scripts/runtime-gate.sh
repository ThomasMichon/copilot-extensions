#!/usr/bin/env bash
set -euo pipefail

export PYTHONUTF8=1
unset PYTHONPATH PYTHONHOME

PAYLOAD_ROOT="${AGENT_CONTAINERS_PAYLOAD_ROOT:-}"
[[ "$PAYLOAD_ROOT" == /* && -d "$PAYLOAD_ROOT" ]] || {
    printf '[agent-containers] owning payload root is unavailable.\n' >&2
    exit 126
}

SCRIPT_DIR="$PAYLOAD_ROOT/scripts"
MODE_RUNNER="$SCRIPT_DIR/installation-context/installation-context.sh"
JSON_QUERY="$SCRIPT_DIR/installation-context/json-query.awk"
RUNTIME_RESOLVER="$SCRIPT_DIR/resolve-runtime.sh"
INSTALLER="$SCRIPT_DIR/init.sh"
LEGACY_ROOT="${AGENT_CONTAINERS_HOME:-$HOME/.agent-containers}" # marketplace-isolation: allow legacy compatibility root
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

legacy_invocation_allowed() {
    [[ "$(json_get "$1" status)" == ready &&
       "$(json_get "$1" actualMode)" == legacy &&
       "$(json_get "$1" desiredMode)" == legacy ]] ||
    [[ -z "${COPILOT_EXTENSIONS_CONTEXT:-}" &&
       "$(json_get "$1" status)" == provenance-blocked &&
       "$(json_type "$1" "$(json_path policy enabled)")" == boolean &&
       "$(json_get "$1" "$(json_path policy enabled)")" == false &&
       -z "$(json_get "$1" "$(json_path legacy tombstone)")" &&
       "$(json_get "$1" "$(json_path legacy disposition)")" == active ]]
}

resolve_runtime() {
    AGENT_RT_PY=""
    AGENT_RT_ROOT="$RUNTIME_ROOT"
    export AGENT_RT_ROOT
    local AGENT_RT_RESOLVE_TRACE_MODE="${1:-ready}"
    # shellcheck source=/dev/null
    if ! . "$RUNTIME_RESOLVER"; then
        printf '[agent-containers] runtime resolver failed; update the owning installation.\n' >&2
        exit 126
    fi
}

run_runtime() {
    export AGENT_CONTAINERS_HOME="$RUNTIME_ROOT"
    export AGENT_CONTAINERS_STATE_DIR="$RUNTIME_ROOT"
    export AGENT_CONTAINERS_PAYLOAD_ROOT="$PAYLOAD_ROOT"
    if [[ -n "$CONTEXT" ]]; then
        export COPILOT_EXTENSIONS_CONTEXT="$CONTEXT"
    else
        unset COPILOT_EXTENSIONS_CONTEXT
    fi
    exec "$AGENT_RT_PY" -m agent_containers "$@"
}

[[ -f "$MODE_RUNNER" && -f "$JSON_QUERY" && -f "$RUNTIME_RESOLVER" &&
   -f "$INSTALLER" ]] || {
    printf '[agent-containers] installation-context runtime support is unavailable.\n' >&2
    exit 126
}
grep -Fq '# Trace contract: normal/read-only/ready-v1' "$RUNTIME_RESOLVER" || {
    printf '[agent-containers] read-only runtime resolver is unavailable; update the owning installation.\n' >&2
    exit 126
}

RUNTIME_ROOT="$LEGACY_ROOT"
CONTEXT=""
STATUS_ARGS=(
    status
    --payload-root "$PAYLOAD_ROOT"
    --plugin-id agent-containers
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
    printf '[agent-containers] installation context could not be resolved: %s\n' \
        "$RESOLUTION" >&2
    exit 126
fi

RESOLUTION_STATUS="$(json_get "$RESOLUTION" "$(json_path status)" 2>/dev/null || true)"
RESOLUTION_REASON="$(json_get "$RESOLUTION" "$(json_path reason)" 2>/dev/null || true)"
ACTUAL_MODE="$(json_get "$RESOLUTION" "$(json_path actualMode)" 2>/dev/null || true)"
INSTALL_GENERATION="$(json_get "$RESOLUTION" "$(json_path installGeneration)" 2>/dev/null || true)"

if legacy_invocation_allowed "$RESOLUTION"; then
    if [[ -n "${COPILOT_EXTENSIONS_CONTEXT:-}" ]]; then
        printf '[agent-containers] requested installation context is not active.\n' >&2
        exit 126
    fi
elif [[ ( "$RESOLUTION_STATUS" == ready &&
          "$RESOLUTION_REASON" == namespaced-active ) ||
        "$RESOLUTION_STATUS" == deactivation-required ]] &&
     [[ "$ACTUAL_MODE" == namespaced ]]; then
    RUNTIME_ROOT="$(json_get "$RESOLUTION" "$(json_path runtimeRoot)" 2>/dev/null || true)"
    CONTEXT="$(json_get "$RESOLUTION" "$(json_path context)" 2>/dev/null || true)"
    if [[ -z "$RUNTIME_ROOT" || -z "$CONTEXT" ]]; then
        printf '[agent-containers] active installation context is incomplete.\n' >&2
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
            --expected-plugin-id agent-containers \
            --expected-payload-root "$PAYLOAD_ROOT"
    )" || {
        printf '[agent-containers] installation context validation failed.\n' >&2
        exit 126
    }
    VALIDATED_INSTALL_GENERATION="$(
        json_get "$VALIDATION" "$(json_path generation)" 2>/dev/null || true
    )"
    [[ "$VALIDATED_INSTALL_GENERATION" == "$INSTALL_GENERATION" &&
       "$(json_get "$VALIDATION" "$(json_path pluginRoot)")" == "$RUNTIME_ROOT" ]] || {
        printf '[agent-containers] installation context generation does not match governance.\n' >&2
        exit 126
    }
else
    printf '[agent-containers] installation context blocks invocation: status=%s reason=%s.\n' \
        "${RESOLUTION_STATUS:-invalid}" "${RESOLUTION_REASON:-invalid}" >&2
    exit 126
fi

if [[ -z "$CONTEXT" ]]; then
    RUNTIME_ROOT="$(json_get "$RESOLUTION" "$(json_path legacy root)")"
fi
[[ "$RUNTIME_ROOT" == /* ]] || {
    printf '[agent-containers] installation context is missing the canonical runtime root.\n' >&2
    exit 126
}

resolve_runtime
if [[ -n "$AGENT_RT_PY" ]]; then
    run_runtime "$@"
fi

if [[ -n "${AGENT_CONTAINERS_NO_SELFPROVISION:-}" ]]; then
    printf '[agent-containers] runtime not provisioned (AGENT_CONTAINERS_NO_SELFPROVISION set).\n' >&2
    exit 1
fi

assert_runtime_publication() {
    local -a arguments=(
        probe-plugin-publication --payload-root "$PAYLOAD_ROOT"
        --plugin-id agent-containers --legacy-root "$RUNTIME_ROOT"
    )
    local probe="" rc=0 root_path
    root_path="$(json_path legacy root)"
    if [[ -n "$CONTEXT" ]]; then
        arguments+=(--context "$CONTEXT" --durable-home "$VALIDATION_DURABLE_HOME")
        root_path=runtimeRoot
    fi
    probe="$(bash "$MODE_RUNNER" "${arguments[@]}")" || rc=$?
    if [[ "$rc" != 0 ||
          "$(json_type "$probe" "$(json_path deployment allowPluginPublication)")" != boolean ||
          "$(json_get "$probe" "$(json_path deployment allowPluginPublication)")" != true ||
          "$(json_get "$probe" "$root_path")" != "$RUNTIME_ROOT" ||
          "$(json_get "$probe" "$(json_path deployment path)")" != "$RUNTIME_ROOT/runtime-deployment.json" ]]; then
        printf '[agent-containers] Runtime publication refused: %s Use explicit installation management to install or repair this runtime.\n' "$probe" >&2
        exit 3
    fi
    if [[ -n "$CONTEXT" ]]; then
        if [[ "$(json_get "$probe" status)" != ready &&
              "$(json_get "$probe" status)" != deactivation-required ]] ||
           [[ "$(json_get "$probe" actualMode)" != namespaced ||
              "$(json_get "$probe" context)" != "$CONTEXT" ||
              "$(json_get "$probe" installGeneration)" != "$INSTALL_GENERATION" ]]; then
            printf '[agent-containers] Runtime publication refused: %s Decision no longer admits the validated installation context.\n' "$probe" >&2
            exit 3
        fi
    else
        if ! legacy_invocation_allowed "$probe"; then
            printf '[agent-containers] Runtime publication refused: %s Use explicit installation management to migrate this runtime.\n' "$probe" >&2
            exit 3
        fi
        probe="$(bash "$MODE_RUNNER" probe-legacy --payload-root "$PAYLOAD_ROOT" \
            --plugin-id agent-containers --legacy-root "$RUNTIME_ROOT")" || rc=$?
        if [[ "$rc" != 0 ||
              "$(json_type "$probe" allowMutation)" != boolean ||
              "$(json_get "$probe" allowMutation)" != true ||
              "$(json_get "$probe" "$(json_path legacy root)")" != "$RUNTIME_ROOT" ]] ||
           ! legacy_invocation_allowed "$probe"; then
            printf '[agent-containers] Runtime publication refused: %s Use explicit installation management to install or repair this runtime.\n' "$probe" >&2
            exit 3
        fi
    fi
}
assert_runtime_publication
resolve_runtime normal
if [[ -n "$AGENT_RT_PY" ]]; then
    run_runtime "$@"
fi

mkdir -p "$RUNTIME_ROOT"
STATUS_PATH="$RUNTIME_ROOT/.provision-status"
_lock_link=""
_reap_owned=0
_unlock_provision() {
    if [[ "$_reap_owned" == 1 ]]; then
        _reap_owned=0
        rmdir "$_reap_mutex" 2>/dev/null || true
    fi
    if [[ -n "$_lock_link" ]]; then
        _owner="$(readlink "$_lock_link" 2>/dev/null || true)"
        [[ "$_owner" == "$$" ]] && rm -f "$_lock_link"
        _lock_link=""
    else
        flock -u 9 2>/dev/null || true
        exec 9>&-
    fi
}
trap '_unlock_provision' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
if command -v flock >/dev/null 2>&1 && [[ "${COPILOT_EXT_NO_FLOCK:-}" != "1" ]]; then
    exec 9>"$RUNTIME_ROOT/.provision.lock"
    flock 9
else
    _lock_link="$RUNTIME_ROOT/.provision.lock.pid"
    _reap_mutex="$RUNTIME_ROOT/.provision.reap.mutex"
    _reap_waits=0
    until ln -s "$$" "$_lock_link" 2>/dev/null; do
        _owner="$(readlink "$_lock_link" 2>/dev/null || true)"
        case "$_owner" in
            *[!0-9]*|"") _live=0 ;;
            *) if kill -0 "$_owner" 2>/dev/null; then _live=1; else _live=0; fi ;;
        esac
        if [[ "$_live" == 1 ]]; then
            sleep 1
            continue
        fi
        # Serialize stale-link revalidation/removal with the canonical shim.
        if ! mkdir "$_reap_mutex" 2>/dev/null; then
            if [[ "$_reap_waits" -ge 30 ]]; then
                printf '[agent-containers] provisioning reaper mutex is unavailable: %s; retry or repair it through explicit installation management.\n' \
                    "$_reap_mutex" >&2
                exit 126
            fi
            _reap_waits=$((_reap_waits + 1))
            sleep 1
            continue
        fi
        _reap_owned=1
        _reap_waits=0
        _owner2="$(readlink "$_lock_link" 2>/dev/null || true)"
        if [[ -n "$_owner2" && "$_owner2" == "$_owner" ]]; then
            case "$_owner2" in
                *[!0-9]*) rm -f -- "$_lock_link" ;;
                *) kill -0 "$_owner2" 2>/dev/null || rm -f -- "$_lock_link" ;;
            esac
        fi
        _reap_owned=0
        rmdir "$_reap_mutex" 2>/dev/null || true
    done
fi
resolve_runtime
if [[ -n "$AGENT_RT_PY" ]]; then
    _unlock_provision
    trap - EXIT INT TERM
    run_runtime "$@"
fi
assert_runtime_publication
printf '%s\n' '[agent-containers] runtime not provisioned -- provisioning on first use (may take ~30-120s: acquires uv + builds a venv). Do not kill; extend your timeout.' >&2
printf '::agent-provisioning:: plugin=%s eta_seconds=120 reason=first-use status=%s\n' \
    'agent-containers' "$STATUS_PATH" >&2
printf 'provisioning %s\n' "$(date -u +%FT%TZ 2>/dev/null)" > "$STATUS_PATH" 2>/dev/null || true

set +e
export AGENT_CONTAINERS_HOME="$RUNTIME_ROOT"
export AGENT_CONTAINERS_STATE_DIR="$RUNTIME_ROOT"
if [[ -n "$CONTEXT" ]]; then
    export COPILOT_EXTENSIONS_CONTEXT="$CONTEXT"
else
    unset COPILOT_EXTENSIONS_CONTEXT
fi
bash "$INSTALLER" provision --install-dir "$RUNTIME_ROOT" >&2
PROVISION_EXIT=$?
set -e
if [[ "$PROVISION_EXIT" -ne 0 ]]; then
    printf 'failed rc=%s %s\n' "$PROVISION_EXIT" "$(date -u +%FT%TZ 2>/dev/null)" > "$STATUS_PATH" 2>/dev/null || true
    printf '[agent-containers] provisioning FAILED. See the log above; retry, or run: bash "%s" provision --install-dir "%s"\n' \
        "$INSTALLER" "$RUNTIME_ROOT" >&2
    exit "$PROVISION_EXIT"
fi

resolve_runtime
if [[ -n "$AGENT_RT_PY" ]]; then
    printf 'ready %s\n' "$(date -u +%FT%TZ 2>/dev/null)" > "$STATUS_PATH" 2>/dev/null || true
    _unlock_provision
    trap - EXIT INT TERM
    run_runtime "$@"
fi
printf 'failed rc=1 %s\n' "$(date -u +%FT%TZ 2>/dev/null)" > "$STATUS_PATH" 2>/dev/null || true
printf '[agent-containers] provisioning completed without a resolvable runtime.\n' >&2
exit 1
