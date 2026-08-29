#!/usr/bin/env bash
# Dependency-light lifecycle gate for the agent-index payload and installed shim.
set -u
export PYTHONUTF8=1

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PLUGIN_DIR="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd -P)"
ROOT="${AGENT_INDEX_HOME:-$HOME/.agent-index}"
RESOLVER="$SCRIPT_DIR/resolve-runtime.sh"
COMMAND="${1:-status}"

_configured_role() {
    local role=""
    role="$(printf '%s' "${AGENT_INDEX_ROLE:-}" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"
    case "$role" in host|client) printf '%s' "$role"; return 0 ;; esac
    local config="${AGENT_INDEX_CONFIG:-$ROOT/config.yaml}"
    if [ -f "$config" ]; then
        role="$(sed -n 's/^[[:space:]]*\(role\|engine\)[[:space:]]*:[[:space:]]*["'\'']\?\([A-Za-z]*\)["'\'']\?.*/\2/p' "$config" | head -n1 | tr '[:upper:]' '[:lower:]')"
    fi
    case "$role" in
        host|engine|server|indexer) printf '%s' host ;;
        client|none|consumer) printf '%s' client ;;
        *) return 1 ;;
    esac
}

_resolve_ready_runtime() {
    AGENT_RT_PY=""
    if [ -f "$RESOLVER" ]; then
        AGENT_RT_ROOT="$ROOT"
        export AGENT_RT_ROOT
        # shellcheck source=/dev/null
        . "$RESOLVER"
    fi
    if [ -n "${AGENT_RT_PY:-}" ] \
        && ! "$AGENT_RT_PY" -c 'import agent_index' >/dev/null 2>&1; then
        AGENT_RT_PY=""
    fi
}

_runtime_state() {
    if [ -n "${AGENT_RT_PY:-}" ]; then
        printf '%s' ready
    elif [ -f "$ROOT/current-version" ] || [ -f "$ROOT/last-known-good" ] \
        || { [ -d "$ROOT/versions" ] && find "$ROOT/versions" -mindepth 1 -maxdepth 1 -type d -print -quit 2>/dev/null | grep -q .; }; then
        printf '%s' broken
    elif [ -f "$ROOT/payload-dir" ] || [ -f "$ROOT/stamped-version" ] \
        || [ -f "$ROOT/deploy-manifest.json" ]; then
        printf '%s' stamped
    else
        printf '%s' absent
    fi
}

_version() {
    sed -n 's/^version[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' \
        "$PLUGIN_DIR/pyproject.toml" 2>/dev/null | head -n1
}

_emit_setup_required() {
    local runtime_state="$1" error="${2:-}"
    if [ -n "$error" ]; then
        printf '%s\n' \
            '{"configured":false,"error":"Non-interactive setup requires an explicit role choice: pass --single or --indexer <machine>.","plugin":"agent-index","role":null,"running":false,"runtime":{"state":"'"$runtime_state"'"},"schema":"agent-index.lifecycle","schema_version":1,"setup":{"interactive":"agent-index setup","noninteractive":["agent-index setup --single --yes","agent-index setup --indexer <machine> --ssh <alias> --yes"]},"setup_required":true,"state":"setup_required","version":"'"$(_version)"'"}'
    else
        printf '%s\n' \
            '{"configured":false,"plugin":"agent-index","role":null,"running":false,"runtime":{"state":"'"$runtime_state"'"},"schema":"agent-index.lifecycle","schema_version":1,"setup":{"interactive":"agent-index setup","noninteractive":["agent-index setup --single --yes","agent-index setup --indexer <machine> --ssh <alias> --yes"]},"setup_required":true,"state":"setup_required","version":"'"$(_version)"'"}'
    fi
}

_emit_runtime_unavailable() {
    local runtime_state="$1" role="$2"
    printf '%s\n' \
        '{"configured":true,"plugin":"agent-index","role":"'"$role"'","running":false,"runtime":{"state":"'"$runtime_state"'"},"schema":"agent-index.lifecycle","schema_version":1,"setup_required":false,"state":"runtime_unavailable","version":"'"$(_version)"'"}'
}

_emit_readiness() {
    local state="$1" detail="$2"
    printf '{"detail":"%s","module":"agent-index/runtime","schema":"copilot-extensions.module-readiness","state":"%s","version":1}\n' \
        "$detail" "$state"
}

_setup_has_choice() {
    local arg expect_indexer=0
    for arg in "$@"; do
        if [ "$expect_indexer" = 1 ]; then
            case "$arg" in ""|-*) return 1 ;; *) return 0 ;; esac
        fi
        case "$arg" in
            --single) return 0 ;;
            --indexer) expect_indexer=1 ;;
            --indexer=*) [ -n "${arg#--indexer=}" ] && return 0 || return 1 ;;
        esac
    done
    return 1
}

_setup_is_interactive() {
    local arg
    for arg in "$@"; do [ "$arg" = --yes ] && return 1; done
    [ -t 0 ]
}

_setup_role() {
    local arg next=0 indexer="" this="${AGENT_INDEX_MACHINE:-$(hostname 2>/dev/null || true)}"
    for arg in "$@"; do
        if [ "$next" = 1 ]; then indexer="$arg"; next=0; continue; fi
        case "$arg" in
            --single) printf '%s' host; return 0 ;;
            --indexer) next=1 ;;
            --indexer=*) indexer="${arg#--indexer=}" ;;
        esac
    done
    if [ -n "$indexer" ]; then
        this="${this%%.*}"
        if [ "$(printf '%s' "$indexer" | tr '[:upper:]' '[:lower:]')" = "$(printf '%s' "$this" | tr '[:upper:]' '[:lower:]')" ]; then
            printf '%s' host
        else
            printf '%s' client
        fi
        return 0
    fi
    return 1
}

SNAPSHOT_INSTALLER=""
_select_snapshot_installer() {
    local marker="$ROOT/payload-dir" snapshot=""
    [ -f "$marker" ] && snapshot="$(cat "$marker" 2>/dev/null || true)"
    SNAPSHOT_INSTALLER="$snapshot/scripts/install.sh"
    if [ -f "$SNAPSHOT_INSTALLER" ]; then return 0; fi
    local installer="$PLUGIN_DIR/scripts/install.sh"
    [ -f "$installer" ] || return 127
    bash "$installer" stamp >&2 || return $?
    snapshot="$(cat "$marker" 2>/dev/null || true)"
    SNAPSHOT_INSTALLER="$snapshot/scripts/install.sh"
    [ -f "$SNAPSHOT_INSTALLER" ] || return 127
}

PROVISION_LOCK_DIR=""
_acquire_provision_lock() {
    mkdir -p "$ROOT"
    if command -v flock >/dev/null 2>&1; then
        exec 9>"$ROOT/.provision.lock"
        flock 9
        return 0
    fi
    PROVISION_LOCK_DIR="$ROOT/.provision.lock.d"
    until mkdir "$PROVISION_LOCK_DIR" 2>/dev/null; do
        local owner=""
        [ -f "$PROVISION_LOCK_DIR/pid" ] && owner="$(cat "$PROVISION_LOCK_DIR/pid" 2>/dev/null || true)"
        case "$owner" in
            *[!0-9]*|"") live=0 ;;
            *) if kill -0 "$owner" 2>/dev/null; then live=1; else live=0; fi ;;
        esac
        [ "$live" = 0 ] && rm -rf "$PROVISION_LOCK_DIR"
        [ "$live" = 1 ] && sleep 1
    done
    printf '%s\n' "$$" > "$PROVISION_LOCK_DIR/pid"
}

_release_provision_lock() {
    if [ -n "$PROVISION_LOCK_DIR" ]; then
        rm -rf "$PROVISION_LOCK_DIR"
        PROVISION_LOCK_DIR=""
    else
        flock -u 9 2>/dev/null || true
        exec 9>&-
    fi
}

_provision_runtime() {
    local origin=""
    [ -f "$ROOT/payload-origin" ] && origin="$(cat "$ROOT/payload-origin" 2>/dev/null || true)"
    [ -n "$origin" ] && export COPILOT_PLUGIN_STAGED_FROM="$origin"
    local probe="$PLUGIN_DIR/scripts/installation-context/legacy-entrypoint-probe.sh"
    [ -f "$probe" ] || {
        printf '%s\n' '[agent-index] legacy mutation probe is unavailable.' >&2
        return 1
    }
    bash "$probe" --payload-root "${COPILOT_PLUGIN_STAGED_FROM:-$PLUGIN_DIR}" \
        --legacy-root "$ROOT" || return $?
    _acquire_provision_lock || return $?
    _resolve_ready_runtime
    if [ -n "${AGENT_RT_PY:-}" ] && [ -z "${SETUP_ROLE:-}" ]; then
        _release_provision_lock
        return 0
    fi
    _select_snapshot_installer || {
        local select_rc=$?
        _release_provision_lock
        return "$select_rc"
    }
    local prior_role="${AGENT_INDEX_ROLE-}" had_role=0
    [ -n "${AGENT_INDEX_ROLE+x}" ] && had_role=1
    if [ -n "${SETUP_ROLE:-}" ]; then
        export AGENT_INDEX_ROLE="$SETUP_ROLE"
    elif ! _configured_role >/dev/null 2>&1; then
        export AGENT_INDEX_ROLE=client
    fi
    printf '%s\n' '[agent-index] provisioning the runtime after explicit setup/configuration.' >&2
    printf '%s\n' '::agent-provisioning:: plugin=agent-index eta_seconds=120 reason=setup' >&2
    bash "$SNAPSHOT_INSTALLER" provision >&2
    local rc=$?
    if [ "$had_role" -eq 1 ]; then export AGENT_INDEX_ROLE="$prior_role"; else unset AGENT_INDEX_ROLE; fi
    if [ "$rc" -ne 0 ]; then
        _release_provision_lock
        return "$rc"
    fi
    _resolve_ready_runtime
    rc=1
    [ -n "${AGENT_RT_PY:-}" ] && rc=0
    _release_provision_lock
    return "$rc"
}

_resolve_ready_runtime
ROLE="$(_configured_role 2>/dev/null || true)"
RUNTIME_STATE="$(_runtime_state)"
SETUP_ROLE=""
SETUP_PROVISIONED=0

case "$COMMAND" in
    --version|version)
        if [ -n "${AGENT_RT_PY:-}" ]; then exec "$AGENT_RT_PY" -m agent_index "$@"; fi
        _version
        exit 0
        ;;
    status)
        if [ -z "${AGENT_RT_PY:-}" ]; then
            if [ -n "$ROLE" ]; then
                _emit_runtime_unavailable "$RUNTIME_STATE" "$ROLE"
            else
                _emit_setup_required "$RUNTIME_STATE"
            fi
            exit 0
        fi
        exec "$AGENT_RT_PY" -m agent_index "$@"
        ;;
    installer-readiness)
        if [ -z "$ROLE" ]; then
            _emit_readiness configuration-empty 'agent-index is dormant until a role is selected; readiness did not provision or start anything.'
            exit 0
        fi
        if [ -z "${AGENT_RT_PY:-}" ]; then
            _emit_readiness failed "agent-index role is configured, but the runtime is $RUNTIME_STATE; run setup again to repair it."
            exit 1
        fi
        exec "$AGENT_RT_PY" -m agent_index "$@"
        ;;
    role)
        if [ -z "$ROLE" ]; then
            case " $* " in
                *" --json "*) printf '%s\n' '{"role":null,"setup_required":true,"state":"setup_required"}' ;;
                *) printf '%s\n' unconfigured ;;
            esac
            exit 0
        fi
        if [ -n "${AGENT_RT_PY:-}" ]; then exec "$AGENT_RT_PY" -m agent_index "$@"; fi
        case " $* " in
            *" --json "*) printf '%s\n' '{"role":"'"$ROLE"'","setup_required":false,"state":"ready"}' ;;
            *) printf '%s\n' "$ROLE" ;;
        esac
        exit 0
        ;;
    setup)
        if ! _setup_has_choice "$@" && ! _setup_is_interactive "$@"; then
            _emit_setup_required "$RUNTIME_STATE" explicit-choice-required
            exit 2
        fi
        ;;
    *)
        if [ -z "$ROLE" ]; then
            _emit_setup_required "$RUNTIME_STATE"
            exit 2
        fi
        ;;
esac

if [ -z "${AGENT_RT_PY:-}" ]; then
    if [ -n "${AGENT_INDEX_NO_SELFPROVISION:-}" ]; then
        printf '%s\n' '[agent-index] runtime is not ready and self-provisioning is disabled.' >&2
        exit 1
    fi
    if [ "$COMMAND" = setup ]; then SETUP_ROLE="$(_setup_role "$@" 2>/dev/null || true)"; fi
    _provision_runtime || exit $?
    [ "$COMMAND" = setup ] && [ -n "$SETUP_ROLE" ] && SETUP_PROVISIONED=1
fi

if [ "$COMMAND" = setup ]; then
    SETUP_OUTPUT="$("$AGENT_RT_PY" -m agent_index "$@")"
    SETUP_RC=$?
    [ -n "$SETUP_OUTPUT" ] && printf '%s\n' "$SETUP_OUTPUT"
    [ "$SETUP_RC" -eq 0 ] || exit "$SETUP_RC"
    if [ "$SETUP_PROVISIONED" -eq 0 ]; then
        SETUP_ROLE="$(_configured_role 2>/dev/null || true)"
        AGENT_INDEX_REBUILD_CURRENT=1 _provision_runtime || exit $?
    fi
    exit 0
fi

exec "$AGENT_RT_PY" -m agent_index "$@"
