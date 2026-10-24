#!/usr/bin/env bash
set -euo pipefail

export PYTHONUTF8=1
unset PYTHONPATH PYTHONHOME
SCRIPT_DIR="$(cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
MODE_RUNNER="$SCRIPT_DIR/installation-context/installation-context.sh"
JSON_QUERY="$SCRIPT_DIR/installation-context/json-query.awk"
RESOLVER="$SCRIPT_DIR/resolve-runtime.sh"
GLOBAL=0
MODULE=agent_dispatch
if [[ "${SCRIPT_DIR##*/}" == bin ]]; then
    GLOBAL=1
    LEGACY_ROOT="$(cd -P -- "$SCRIPT_DIR/.." && pwd)"
    PAYLOAD_ROOT="$(cat "$LEGACY_ROOT/payload-dir" 2>/dev/null || true)"
    if [[ ! -f "$PAYLOAD_ROOT/snapshot-provenance.json" &&
          -n "${AGENT_DISPATCH_PAYLOAD_ROOT:-}" &&
          -f "$AGENT_DISPATCH_PAYLOAD_ROOT/plugin.json" ]]; then
        PAYLOAD_ROOT="$AGENT_DISPATCH_PAYLOAD_ROOT"
    fi
    case "${AGENT_DISPATCH_COMMAND_MODULE:-agent_dispatch}" in
        agent_dispatch|agent_dispatch.board_cli) MODULE="${AGENT_DISPATCH_COMMAND_MODULE:-agent_dispatch}" ;;
        *) printf '[agent-dispatch] unsupported command module.\n' >&2; exit 126 ;;
    esac
else
    PAYLOAD_ROOT="${AGENT_DISPATCH_PAYLOAD_ROOT:-}"
    [[ "$PAYLOAD_ROOT" == "$(cd -P -- "$SCRIPT_DIR/.." && pwd)" ]] || {
        printf '[agent-dispatch] owning payload root is unavailable.\n' >&2; exit 126
    }
    LEGACY_ROOT="${AGENT_DISPATCH_INSTALL_DIR:-$HOME/.agent-dispatch}" # marketplace-isolation: allow legacy compatibility root
fi

RESOLVER_CAPABILITY="$RESOLVER"
if [[ "$GLOBAL" == 1 && ! -f "$RESOLVER" ]]; then
    RESOLVER_CAPABILITY="$PAYLOAD_ROOT/scripts/resolve-runtime.sh"
fi
for support in "$MODE_RUNNER" "$JSON_QUERY" "$RESOLVER_CAPABILITY"; do
    [[ -f "$support" ]] || {
        printf '[agent-dispatch] required runtime admission support is unavailable: %s\n' "$support" >&2
        exit 126
    }
done
grep -Fq '# Trace contract: normal/read-only/ready-v1' "$RESOLVER_CAPABILITY" || {
    printf '[agent-dispatch] read-only runtime resolver is unavailable; update the owning installation.\n' >&2
    exit 126
}
value() { LC_ALL=C awk -f "$JSON_QUERY" -v mode="${3:-get}" -v "query_path=$2" <<<"$1"; }
fail_context() {
    printf '[agent-dispatch] installation context blocks invocation: %s\n' "$*" >&2
    exit 126
}
legacy_invocation_allowed() {
    [[ "$(value "$1" status)" == ready && "$(value "$1" actualMode)" == legacy &&
       "$(value "$1" desiredMode)" == legacy ]] ||
    [[ "$(value "$1" status)" == provenance-blocked && -z "${COPILOT_EXTENSIONS_CONTEXT:-}" &&
       "$(value "$1" $'policy\034enabled' type)" == boolean &&
       "$(value "$1" $'policy\034enabled')" == false &&
       "$(value "$1" $'legacy\034disposition')" == active &&
       -z "$(value "$1" $'legacy\034tombstone')" ]]
}
CONTEXT=""
RUNTIME_ROOT="$LEGACY_ROOT"
STATUS_ARGS=(status --payload-root "$PAYLOAD_ROOT" --plugin-id agent-dispatch --legacy-root "$LEGACY_ROOT")
if [[ -n "${COPILOT_EXTENSIONS_CONTEXT:-}" ]]; then
    DURABLE_HOME="$COPILOT_EXTENSIONS_CONTEXT"
    for _part in 1 2 3 4 5; do DURABLE_HOME="$(dirname -- "$DURABLE_HOME")"; done
    STATUS_ARGS+=(--context "$COPILOT_EXTENSIONS_CONTEXT" --durable-home "$DURABLE_HOME")
fi
RESOLUTION="$(bash "$MODE_RUNNER" "${STATUS_ARGS[@]}")" || fail_context 'status could not be resolved'
STATUS="$(value "$RESOLUTION" status)"
ACTUAL_MODE="$(value "$RESOLUTION" actualMode)"
INSTALL_GENERATION="$(value "$RESOLUTION" installGeneration)"
if legacy_invocation_allowed "$RESOLUTION"; then
    [[ -z "${COPILOT_EXTENSIONS_CONTEXT:-}" ]] || fail_context 'requested context is not active'
    RUNTIME_ROOT="$(value "$RESOLUTION" $'legacy\034root')"
elif [[ "$ACTUAL_MODE" == namespaced ]] &&
     { [[ "$STATUS" == ready && "$(value "$RESOLUTION" reason)" == namespaced-active ]] ||
       [[ "$STATUS" == deactivation-required ]]; }; then
    RUNTIME_ROOT="$(value "$RESOLUTION" runtimeRoot)"
    CONTEXT="$(value "$RESOLUTION" context)"
    [[ -n "$CONTEXT" ]] || fail_context 'active context is incomplete'
    DURABLE_HOME="$CONTEXT"
    for _part in 1 2 3 4 5; do DURABLE_HOME="$(dirname -- "$DURABLE_HOME")"; done
    VALIDATION="$(bash "$MODE_RUNNER" validate --context "$CONTEXT" --durable-home "$DURABLE_HOME" \
        --expected-plugin-id agent-dispatch --expected-payload-root "$PAYLOAD_ROOT")" ||
        fail_context 'validation failed'
    [[ "$(value "$VALIDATION" generation)" == "$INSTALL_GENERATION" &&
       "$(value "$VALIDATION" pluginRoot)" == "$RUNTIME_ROOT" ]] ||
        fail_context 'generation does not match governance'
else
    fail_context "$RESOLUTION"
fi
[[ "$RUNTIME_ROOT" == /* ]] || fail_context 'canonical runtime root is missing'

resolve_runtime() {
    AGENT_RT_PY=""
    export AGENT_RT_ROOT="$RUNTIME_ROOT"
    local AGENT_RT_RESOLVE_TRACE_MODE="${1:-ready}"
    # A missing global resolver still permits admitted exact-snapshot repair.
    [[ -f "$RESOLVER" ]] || return 0
    if ! . "$RESOLVER"; then
        printf '[agent-dispatch] runtime resolver failed; update the owning installation.\n' >&2
        exit 126
    fi
}
run_runtime() {
    export AGENT_DISPATCH_INSTALL_DIR="$RUNTIME_ROOT"
    if [[ -n "$CONTEXT" ]]; then
        export COPILOT_EXTENSIONS_CONTEXT="$CONTEXT"
    else
        unset COPILOT_EXTENSIONS_CONTEXT
    fi
    exec "$AGENT_RT_PY" -m "$MODULE" "$@"
}
resolve_runtime
[[ -z "$AGENT_RT_PY" ]] || run_runtime "$@"
if [[ -n "${AGENT_DISPATCH_NO_SELFPROVISION:-}" ]]; then
    printf '[agent-dispatch] runtime not provisioned (AGENT_DISPATCH_NO_SELFPROVISION set).\n' >&2
    exit 1
fi
assert_publication() {
    local probe="" rc=0 root_path=$'legacy\034root'
    local -a arguments=(probe-plugin-publication --payload-root "$PAYLOAD_ROOT" \
        --plugin-id agent-dispatch --legacy-root "$RUNTIME_ROOT")
    if [[ -n "$CONTEXT" ]]; then
        arguments+=(--context "$CONTEXT" --durable-home "$DURABLE_HOME")
        root_path=runtimeRoot
    fi
    probe="$(bash "$MODE_RUNNER" "${arguments[@]}")" || rc=$?
    if [[ "$rc" != 0 ||
          "$(value "$probe" $'deployment\034allowPluginPublication' type)" != boolean ||
          "$(value "$probe" $'deployment\034allowPluginPublication')" != true ||
          "$(value "$probe" "$root_path")" != "$RUNTIME_ROOT" ||
          "$(value "$probe" $'deployment\034path')" != "$RUNTIME_ROOT/runtime-deployment.json" ]]; then
        printf '[agent-dispatch] Runtime publication refused: %s Use explicit installation management to install or repair this runtime.\n' "$probe" >&2
        exit 3
    fi
    if [[ -z "$CONTEXT" ]] && ! legacy_invocation_allowed "$probe"; then
        printf '[agent-dispatch] Runtime publication refused: %s Use explicit installation management to migrate this runtime.\n' "$probe" >&2
        exit 3
    fi
    if [[ -n "$CONTEXT" ]]; then
        # Dispatch's native installer has not adopted installation-cell staging.
        # Do not let its legacy staging/default service paths mutate another root.
        printf '[agent-dispatch] installation-cell first-use provisioning is unavailable; use explicit installation management for this context.\n' >&2
        exit 126
    fi
    if [[ "$RUNTIME_ROOT" != "$(value "$RESOLUTION" $'environment\034homeRealPath')/.agent-dispatch" ]]; then
        printf '[agent-dispatch] scoped first-use provisioning is unavailable; use explicit installation management for this root.\n' >&2
        exit 126
    fi
    probe="$(bash "$MODE_RUNNER" probe-legacy --payload-root "$PAYLOAD_ROOT" \
        --plugin-id agent-dispatch --legacy-root "$RUNTIME_ROOT")" || rc=$?
    if [[ "$rc" != 0 || "$(value "$probe" allowMutation type)" != boolean ||
          "$(value "$probe" allowMutation)" != true ||
          "$(value "$probe" $'legacy\034root')" != "$RUNTIME_ROOT" ]] ||
       ! legacy_invocation_allowed "$probe"; then
        printf '[agent-dispatch] Runtime publication refused: %s\n' "$probe" >&2
        exit 3
    fi
}
assert_publication

# Keep source-qualified recovery on its exact snapshot, never upstream main.
select_provision_source() {
    SOURCE_PINNED=0
    SOURCE_PRESENT=0
    SOURCE_PAYLOAD="$(cat "$RUNTIME_ROOT/payload-dir" 2>/dev/null || true)"
    for marker in stamped-version current-version last-known-good; do
        selected="$(cat "$RUNTIME_ROOT/$marker" 2>/dev/null || true)"
        case "$selected" in
            *+source.*|*.source.*)
                SOURCE_PRESENT=1
                if [[ "$SOURCE_PAYLOAD" == "$RUNTIME_ROOT/snapshots/$selected" ]]; then
                    SOURCE_PINNED=1
                    break
                fi ;;
        esac
    done
    if [[ "$SOURCE_PRESENT" == 1 && "$SOURCE_PINNED" == 0 ]]; then
        printf 'Selected source snapshot is unavailable; refusing a different provisioning source.\n' >&2
        exit 126
    fi
    if [[ "$SOURCE_PINNED" == 0 ]]; then
        for slot in "$RUNTIME_ROOT"/versions/*+source.* "$RUNTIME_ROOT"/versions/*.source.*; do
            [[ ! -d "$slot" ]] || {
                printf 'Source runtime recovery requires an exact source selector; refusing upstream provisioning.\n' >&2
                exit 126
            }
        done
    fi
    INSTALLER="$PAYLOAD_ROOT/scripts/install.sh"
    if [[ "$GLOBAL" == 1 && -n "$SOURCE_PAYLOAD" ]]; then INSTALLER="$SOURCE_PAYLOAD/scripts/install.sh"; fi
    if [[ "$SOURCE_PINNED" == 1 ]]; then INSTALLER="$SOURCE_PAYLOAD/scripts/install.sh"; fi
    if [[ "$SOURCE_PINNED" == 1 && ! -f "$INSTALLER" ]]; then
        printf 'Selected source snapshot is unavailable; refusing a different provisioning source.\n' >&2
        exit 126
    fi
}
select_provision_source

resolve_runtime normal
[[ -z "$AGENT_RT_PY" ]] || run_runtime "$@"
mkdir -p "$RUNTIME_ROOT"
STATUS_PATH="$RUNTIME_ROOT/.provision-status"
LOCK_LINK=""
REAP_OWNED=0
BOOT_FILE=""
unlock() {
    if [[ "$REAP_OWNED" == 1 ]]; then
        REAP_OWNED=0
        rmdir "$REAP_MUTEX" 2>/dev/null || true
    fi
    if [[ -n "$LOCK_LINK" ]]; then
        [[ "$(readlink "$LOCK_LINK" 2>/dev/null || true)" != "$$" ]] || rm -f -- "$LOCK_LINK"
        LOCK_LINK=""
    else
        flock -u 9 2>/dev/null || true
        exec 9>&-
    fi
}
cleanup() {
    [[ -z "$BOOT_FILE" ]] || rm -f -- "$BOOT_FILE"
    unlock
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
if command -v flock >/dev/null 2>&1 && [[ "${COPILOT_EXT_NO_FLOCK:-}" != 1 ]]; then
    exec 9>"$RUNTIME_ROOT/.provision.lock"
    flock 9
else
    LOCK_LINK="$RUNTIME_ROOT/.provision.lock.pid"
    REAP_MUTEX="$RUNTIME_ROOT/.provision.reap.mutex"
    REAP_WAITS=0
    until ln -s "$$" "$LOCK_LINK" 2>/dev/null; do
        owner="$(readlink "$LOCK_LINK" 2>/dev/null || true)"
        case "$owner" in
            *[!0-9]*|"") live=0 ;;
            *) if kill -0 "$owner" 2>/dev/null; then live=1; else live=0; fi ;;
        esac
        if [[ "$live" == 1 ]]; then
            sleep 1
            continue
        fi
        # Match the canonical payload shim: serialize stale revalidation and
        # removal so another reaper cannot replace the link between them.
        if ! mkdir "$REAP_MUTEX" 2>/dev/null; then
            if [[ "$REAP_WAITS" -ge 30 ]]; then
                printf '[agent-dispatch] provisioning reaper mutex is unavailable: %s; retry or repair it through explicit installation management.\n' \
                    "$REAP_MUTEX" >&2
                exit 126
            fi
            REAP_WAITS=$((REAP_WAITS + 1))
            sleep 1
            continue
        fi
        REAP_OWNED=1
        REAP_WAITS=0
        owner2="$(readlink "$LOCK_LINK" 2>/dev/null || true)"
        if [[ -n "$owner2" && "$owner2" == "$owner" ]]; then
            case "$owner2" in
                *[!0-9]*) rm -f -- "$LOCK_LINK" ;;
                *) kill -0 "$owner2" 2>/dev/null || rm -f -- "$LOCK_LINK" ;;
            esac
        fi
        REAP_OWNED=0
        rmdir "$REAP_MUTEX" 2>/dev/null || true
    done
fi
resolve_runtime
[[ -z "$AGENT_RT_PY" ]] || { unlock; trap - EXIT INT TERM; run_runtime "$@"; }
assert_publication
select_provision_source
printf '[agent-dispatch] runtime not provisioned -- provisioning on first use (may take ~30-120s). Do not kill; extend your timeout.\n' >&2
printf '::agent-provisioning:: plugin=agent-dispatch eta_seconds=120 reason=first-use status=%s\n' "$STATUS_PATH" >&2
printf 'provisioning %s\n' "$(date -u +%FT%TZ)" > "$STATUS_PATH"
export AGENT_DISPATCH_INSTALL_DIR="$RUNTIME_ROOT"
rc=1
if [[ "$GLOBAL" == 1 && "$SOURCE_PINNED" == 0 && -z "${AGENT_DISPATCH_PROVISION_OFFLINE:-}" ]]; then
    BOOT_FILE="$(mktemp "${TMPDIR:-/tmp}/agent-dispatch-bootstrap.XXXXXX")"
    boot_url="https://raw.githubusercontent.com/ThomasMichon/copilot-extensions/main/install/dispatch/bootstrap.sh"
    printf '[agent-dispatch] fetching upstream installer (install/dispatch/bootstrap.sh, main)...\n' >&2
    if (command -v curl >/dev/null 2>&1 && curl -fsSL "$boot_url" -o "$BOOT_FILE") ||
       (command -v wget >/dev/null 2>&1 && wget -qO "$BOOT_FILE" "$boot_url"); then
        assert_publication
        select_provision_source
        if [[ "$SOURCE_PINNED" == 0 ]]; then
            bash "$BOOT_FILE" provision --install-dir "$RUNTIME_ROOT" >&2 && rc=0 || rc=$?
        fi
    else
        printf '[agent-dispatch] upstream installer fetch failed; falling back to the locally stamped snapshot.\n' >&2
    fi
    rm -f -- "$BOOT_FILE"
    BOOT_FILE=""
fi
if [[ "$rc" != 0 ]]; then
    assert_publication
    select_provision_source
    if [[ -f "$INSTALLER" ]]; then
        bash "$INSTALLER" provision --install-dir "$RUNTIME_ROOT" >&2 && rc=0 || rc=$?
    else
        printf '[agent-dispatch] cannot self-provision: local snapshot installer is unavailable; re-enable/update the owning plugin.\n' >&2
        rc=127
    fi
fi
resolve_runtime
if [[ "$rc" == 0 && -n "$AGENT_RT_PY" ]]; then
    printf 'ready %s\n' "$(date -u +%FT%TZ)" > "$STATUS_PATH"
    unlock; trap - EXIT INT TERM
    run_runtime "$@"
fi
[[ "$rc" != 0 ]] || rc=1
printf 'failed rc=%s %s\n' "$rc" "$(date -u +%FT%TZ)" > "$STATUS_PATH"
printf '[agent-dispatch] provisioning did not yield a runtime (rc=%s); see the installer diagnostics above.\n' "$rc" >&2
exit "$rc"
