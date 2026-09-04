#!/usr/bin/env bash
# Dependency-light lifecycle gate for the agent-index payload and installed shim.
set -u
export PYTHONUTF8=1
unset PYTHONPATH PYTHONHOME AGENT_INDEX_RUNTIME_VERSION

if [ -z "${AGENT_INDEX_REPO:-}" ] && command -v git >/dev/null 2>&1; then
    ORIGINAL_REPO="$(
        git -C "$PWD" rev-parse --show-toplevel 2>/dev/null || true
    )"
    if [ -n "$ORIGINAL_REPO" ] && [ -d "$ORIGINAL_REPO" ]; then
        ORIGINAL_REPO="$(
            CDPATH= cd -- "$ORIGINAL_REPO" 2>/dev/null && pwd -P
        )" || ORIGINAL_REPO=""
        if [ -n "$ORIGINAL_REPO" ]; then
            export AGENT_INDEX_REPO="$ORIGINAL_REPO"
        fi
    fi
fi

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PLUGIN_DIR="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd -P)"
PAYLOAD_ROOT="${AGENT_INDEX_PAYLOAD_ROOT:-$PLUGIN_DIR}"
MODE_RUNNER="$SCRIPT_DIR/installation-context/installation-context.sh"
JSON_QUERY="$SCRIPT_DIR/installation-context/json-query.awk"
LEGACY_ROOT="${AGENT_INDEX_HOME:-$HOME/.agent-index}"
RESOLVER="$SCRIPT_DIR/resolve-runtime.sh"
COMMAND="${1:-status}"
PY_GATE="$(command -v python3 || command -v python || true)"
EFFECTIVE_RESOLVER="$SCRIPT_DIR/resolve_effective_config.py"

_emit_inactive() {
    printf '%s\n' \
        '{"configured":false,"opted_in":false,"plugin":"agent-index","running":false,"schema":"agent-index.lifecycle","schema_version":1,"state":"inactive"}'
}

if [ -z "$PY_GATE" ] || [ ! -f "$EFFECTIVE_RESOLVER" ]; then
    if [ "$COMMAND" = installer-readiness ]; then
        printf '%s\n' \
            '{"detail":"No effective repository configuration is available; session startup remains non-mutating.","module":"agent-index/runtime","schema":"copilot-extensions.module-readiness","state":"configuration-empty","version":1}'
        exit 0
    fi
    _emit_inactive
    [ "$COMMAND" = status ] && exit 0
    exit 2
fi
EFFECTIVE_JSON="$("$PY_GATE" -E -X utf8 "$EFFECTIVE_RESOLVER" \
    --cwd "${AGENT_INDEX_REPO:-$PWD}" 2>/dev/null || true)"
EFFECTIVE_FIELDS=()
while IFS= read -r field; do
    EFFECTIVE_FIELDS[${#EFFECTIVE_FIELDS[@]}]="$field"
done < <(
    printf '%s' "$EFFECTIVE_JSON" | "$PY_GATE" -E -X utf8 -c '
import json, sys
try:
    value = json.load(sys.stdin)
except Exception:
    value = {}
print("1" if value.get("opted_in") else "0")
print(value.get("config") if isinstance(value.get("config"), str) else "")
print(value.get("repo_root") if isinstance(value.get("repo_root"), str) else "")
print(value.get("source") if isinstance(value.get("source"), str) else "")
'
)
if [ "${EFFECTIVE_FIELDS[0]:-0}" != 1 ]; then
    if [ "$COMMAND" = installer-readiness ]; then
        printf '%s\n' \
            '{"detail":"No effective .agent-index/config.yaml opts this repository in; session startup remains non-mutating.","module":"agent-index/runtime","schema":"copilot-extensions.module-readiness","state":"configuration-empty","version":1}'
        exit 0
    fi
    _emit_inactive
    [ "$COMMAND" = status ] && exit 0
    exit 2
fi
[ -n "${EFFECTIVE_FIELDS[1]:-}" ] &&
    export AGENT_INDEX_EFFECTIVE_CONFIG="${EFFECTIVE_FIELDS[1]}"
[ -n "${EFFECTIVE_FIELDS[2]:-}" ] &&
    export AGENT_INDEX_REPO="${EFFECTIVE_FIELDS[2]}"
if [ "${EFFECTIVE_FIELDS[3]:-}" = forwarded ]; then
    export AGENT_INDEX_FORWARDED=1
    unset AGENT_INDEX_REPO
fi
SEP=$'\034'

_json_path() {
    local result="" component
    for component in "$@"; do
        [ -z "$result" ] || result+="$SEP"
        result+="$component"
    done
    printf '%s' "$result"
}

_json_get() {
    LC_ALL=C awk -f "$JSON_QUERY" -v mode=get -v "query_path=$2" <<<"$1"
}

_json_type() {
    LC_ALL=C awk -f "$JSON_QUERY" -v mode=type -v "query_path=$2" "$3"
}

_json_len() {
    LC_ALL=C awk -f "$JSON_QUERY" -v mode=len -v "query_path=$2" "$3"
}

_profile_home() {
    local uid entry="" home_path="" user=""
    uid="$(id -u 2>/dev/null)" || return 1
    if command -v getent >/dev/null 2>&1; then
        entry="$(getent passwd "$uid" 2>/dev/null || true)"
    fi
    if [ -z "$entry" ] && [ -r /etc/passwd ]; then
        entry="$(LC_ALL=C awk -F: -v uid="$uid" '$3 == uid { print; exit }' /etc/passwd)"
    fi
    if [ -n "$entry" ]; then
        home_path="$(printf '%s' "$entry" | LC_ALL=C cut -d: -f6)"
    elif command -v dscl >/dev/null 2>&1; then
        user="$(id -un 2>/dev/null || true)"
        if [ -n "$user" ]; then
            home_path="$(dscl . -read "/Users/$user" NFSHomeDirectory 2>/dev/null |
                LC_ALL=C awk '$1 == "NFSHomeDirectory:" { $1 = ""; sub(/^[[:space:]]+/, ""); print; exit }' || true)"
        fi
    fi
    [ "${home_path#/}" != "$home_path" ] && [ -d "$home_path" ] || return 1
    (cd -P -- "$home_path" && pwd)
}

[ -d "$PAYLOAD_ROOT" ] || {
    printf '%s\n' '[agent-index] owning payload root is unavailable.' >&2
    exit 126
}
PAYLOAD_ROOT="$(CDPATH= cd -- "$PAYLOAD_ROOT" && pwd -P)"
if [ "$PAYLOAD_ROOT" != "$PLUGIN_DIR" ]; then
    printf '%s\n' '[agent-index] owning payload root does not match the dispatcher.' >&2
    exit 126
fi
[ -f "$MODE_RUNNER" ] && [ -f "$JSON_QUERY" ] && [ -f "$RESOLVER" ] || {
    printf '%s\n' '[agent-index] installation-context runtime resolver is unavailable.' >&2
    exit 126
}

PROFILE_HOME="$(_profile_home)" || {
    printf '%s\n' '[agent-index] cannot determine the canonical account home.' >&2
    exit 126
}
POLICY="$PROFILE_HOME/.copilot-extensions/installation-mode.json"
POLICY_PRESENT=0
if [ -e "$POLICY" ] || [ -L "$POLICY" ]; then
    POLICY_PRESENT=1
fi
PROVENANCE_BOUNDARY=0
case "${PAYLOAD_ROOT//\\//}" in
    */.copilot/installed-plugins/*/*) PROVENANCE_BOUNDARY=1 ;;
esac
if [ "$PROVENANCE_BOUNDARY" = 0 ]; then
    PROBE_ROOT="$PAYLOAD_ROOT"
    while [ "$PROBE_ROOT" != "/" ]; do
        if [ -f "$PROBE_ROOT/.github/plugin/marketplace.json" ]; then
            PROVENANCE_BOUNDARY=1
            break
        fi
        PROBE_ROOT="$(dirname -- "$PROBE_ROOT")"
    done
fi

ROOT="$LEGACY_ROOT"
CONTEXT=""
MARKETPLACE_ID=""
RESOLUTION_STATUS=ready
RESOLUTION_REASON=policy-default-false
ACTUAL_MODE=legacy
DESIRED_MODE=legacy
STATUS_ARGS=(
    status
    --payload-root "$PAYLOAD_ROOT"
    --plugin-id agent-index
    --legacy-root "$LEGACY_ROOT"
)
if [ -n "${COPILOT_EXTENSIONS_CONTEXT:-}" ]; then
    STATUS_ARGS+=(--context "$COPILOT_EXTENSIONS_CONTEXT")
    CONTEXT_DURABLE_HOME="$COPILOT_EXTENSIONS_CONTEXT"
    for _part in 1 2 3 4 5; do
        CONTEXT_DURABLE_HOME="$(dirname -- "$CONTEXT_DURABLE_HOME")"
    done
    STATUS_ARGS+=(--durable-home "$CONTEXT_DURABLE_HOME")
fi
RESOLUTION="$(bash "$MODE_RUNNER" "${STATUS_ARGS[@]}" 2>&1)"
RESOLUTION_RC=$?
if [ "$RESOLUTION_RC" -ne 0 ]; then
    printf '[agent-index] installation context could not be resolved: %s\n' "$RESOLUTION" >&2
    exit 126
fi
RESOLUTION_STATUS="$(_json_get "$RESOLUTION" "$(_json_path status)" 2>/dev/null || true)"
RESOLUTION_REASON="$(_json_get "$RESOLUTION" "$(_json_path reason)" 2>/dev/null || true)"
ACTUAL_MODE="$(_json_get "$RESOLUTION" "$(_json_path actualMode)" 2>/dev/null || true)"
DESIRED_MODE="$(_json_get "$RESOLUTION" "$(_json_path desiredMode)" 2>/dev/null || true)"
SIMPLE_POLICY_LEGACY=0
if [ -z "${COPILOT_EXTENSIONS_CONTEXT:-}" ] &&
   [ "$POLICY_PRESENT" = 0 ] &&
   [ "$PROVENANCE_BOUNDARY" = 0 ] &&
   [ "$RESOLUTION_STATUS" = provenance-blocked ]; then
    SIMPLE_POLICY_LEGACY=1
elif [ -z "${COPILOT_EXTENSIONS_CONTEXT:-}" ] &&
     [ "$RESOLUTION_STATUS" = provenance-blocked ] &&
     [ "$(_json_get "$RESOLUTION" "$(_json_path policy state)" 2>/dev/null || true)" = valid ] &&
     [ "$(_json_get "$RESOLUTION" "$(_json_path policy enabled)" 2>/dev/null || true)" = false ]; then
    MARKETPLACES_PATH="$(_json_path installationMode marketplaces)"
    MARKETPLACES_TYPE="$(_json_type "$RESOLUTION" "$MARKETPLACES_PATH" "$POLICY" 2>/dev/null || true)"
    if [ -z "$MARKETPLACES_TYPE" ] ||
       { [ "$MARKETPLACES_TYPE" = object ] &&
         [ "$(_json_len "$RESOLUTION" "$MARKETPLACES_PATH" "$POLICY" 2>/dev/null || true)" = 0 ]; }; then
        SIMPLE_POLICY_LEGACY=1
    fi
fi
if { [ "$RESOLUTION_STATUS" = ready ] &&
     [ "$ACTUAL_MODE" = legacy ] &&
     [ "$DESIRED_MODE" = legacy ]; } ||
   [ "$SIMPLE_POLICY_LEGACY" = 1 ]; then
    if [ -n "${COPILOT_EXTENSIONS_CONTEXT:-}" ]; then
        printf '%s\n' '[agent-index] requested installation context is not active.' >&2
        exit 126
    fi
elif { [ "$RESOLUTION_STATUS" = ready ] &&
       [ "$RESOLUTION_REASON" = namespaced-active ]; } ||
     [ "$RESOLUTION_STATUS" = deactivation-required ]; then
    if [ "$ACTUAL_MODE" != namespaced ]; then
        printf '[agent-index] installation context blocks invocation: status=%s reason=%s.\n' \
            "$RESOLUTION_STATUS" "$RESOLUTION_REASON" >&2
        exit 126
    fi
    CONTEXT="$(_json_get "$RESOLUTION" "$(_json_path context)" 2>/dev/null || true)"
    MARKETPLACE_ID="$(_json_get "$RESOLUTION" "$(_json_path marketplaceId)" 2>/dev/null || true)"
    [ -n "$CONTEXT" ] && [ -n "$MARKETPLACE_ID" ] || {
        printf '%s\n' '[agent-index] active installation context is incomplete.' >&2
        exit 126
    }
    CONTEXT_DURABLE_HOME="$CONTEXT"
    for _part in 1 2 3 4 5; do
        CONTEXT_DURABLE_HOME="$(dirname -- "$CONTEXT_DURABLE_HOME")"
    done
    VALIDATED="$(bash "$MODE_RUNNER" validate \
        --context "$CONTEXT" \
        --expected-marketplace-id "$MARKETPLACE_ID" \
        --expected-plugin-id agent-index \
        --expected-payload-root "$PAYLOAD_ROOT" \
        --durable-home "$CONTEXT_DURABLE_HOME")" || {
        printf '%s\n' '[agent-index] active installation context validation failed.' >&2
        exit 126
    }
    ROOT="$(_json_get "$VALIDATED" "$(_json_path pluginRoot)" 2>/dev/null || true)"
    STATE_ROOT="$(_json_get "$VALIDATED" "$(_json_path stateRoot)" 2>/dev/null || true)"
    RUN_ROOT="$(_json_get "$VALIDATED" "$(_json_path runRoot)" 2>/dev/null || true)"
    LOGS_ROOT="$(_json_get "$VALIDATED" "$(_json_path logsRoot)" 2>/dev/null || true)"
    CACHE_ROOT="$(_json_get "$VALIDATED" "$(_json_path cacheRoot)" 2>/dev/null || true)"
    [ -n "$ROOT" ] && [ -n "$STATE_ROOT" ] && [ -n "$RUN_ROOT" ] &&
        [ -n "$LOGS_ROOT" ] && [ -n "$CACHE_ROOT" ] || {
        printf '%s\n' '[agent-index] active installation context roots are incomplete.' >&2
        exit 126
    }
    export COPILOT_EXTENSIONS_CONTEXT="$CONTEXT"
    export AGENT_INDEX_HOME="$ROOT"
    export AGENT_INDEX_STATE_DIR="$STATE_ROOT"
    export AGENT_INDEX_DATA_DIR="$STATE_ROOT"
    export AGENT_INDEX_RUN_DIR="$RUN_ROOT"
    export AGENT_INDEX_LOG_DIR="$LOGS_ROOT"
    export AGENT_INDEX_CACHE_DIR="$CACHE_ROOT"
    export AGENT_INDEX_CONFIG_ROOT="$ROOT/config"
    export AGENT_INDEX_CONFIG="$ROOT/config/config.yaml"
    export AGENT_INDEX_ROUTING_DIR="$RUN_ROOT/zdd"
    export AGENT_INDEX_HOST=127.0.0.1
    export AGENT_INDEX_PORT=0
    export AGENT_INDEX_ENGINE_HOME="$ROOT/engine"
    export AGENT_INDEX_ENGINE_HOST=127.0.0.1
    export AGENT_INDEX_ENGINE_PORT=0
    export AGENT_INDEX_ENGINE_MODE=external
    export AGENT_INDEX_BACKUP_DIR="$ROOT/backups"
    export AGENT_INDEX_BACKUP_MOUNT_ROOT="$ROOT"
    export AGENT_INDEX_INSTALLATION_ID="$MARKETPLACE_ID/agent-index"
    export XDG_CACHE_HOME="$CACHE_ROOT"
    unset AGENT_INDEX_ENDPOINT
    unset PYTHONPATH PYTHONHOME
    cd "$ROOT"
else
    printf '[agent-index] installation context blocks invocation: status=%s reason=%s.\n' \
        "${RESOLUTION_STATUS:-invalid}" "${RESOLUTION_REASON:-invalid}" >&2
    exit 126
fi

_find_management_python() {
    local candidate
    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1; then
            command -v "$candidate"
            return 0
        fi
    done
    return 1
}

if [ "$COMMAND" = __dispatch-companion-mode ]; then
    supported=false
    if [ "$ACTUAL_MODE" = legacy ] && [ "$DESIRED_MODE" = legacy ]; then
        supported=true
    fi
    printf '{"mode":"%s","schema_version":1,"supported":%s}\n' \
        "$ACTUAL_MODE" "$supported"
    exit 0
fi

if [ "$ACTUAL_MODE" = namespaced ] &&
   [ "$COMMAND" = engine ] &&
   { [ "${2:-status}" = start ] || [ "${2:-status}" = run ]; }; then
    printf '%s\n' '[agent-index] the installation-cell exemplar does not provision or start the heavy embedding engine.' >&2
    exit 2
fi

if [ "$ACTUAL_MODE" = namespaced ] &&
   { [ "$COMMAND" = start ] || [ "$COMMAND" = serve ]; }; then
    printf '%s\n' '[agent-index] public start/serve is unavailable for an active namespaced installation.' >&2
    exit 126
fi

if [ "$ACTUAL_MODE" = namespaced ] && [ "$COMMAND" = deploy ]; then
    if [ -z "${AGENT_INDEX_CELL_TRANSACTION:-}" ] ||
       [ -z "${AGENT_INDEX_CELL_TRANSACTION_TOKEN:-}" ] ||
       [ -z "${AGENT_INDEX_CELL_TRANSACTION_ID:-}" ]; then
        printf '%s\n' '[agent-index] namespaced deploy/recovery requires the owning cell transaction.' >&2
        exit 126
    fi
fi

if [ "$COMMAND" = "__cell-bootstrap" ] ||
   [ "$COMMAND" = "__cell-service-ensure" ]; then
    if [ "$ACTUAL_MODE" != namespaced ]; then
        exit 10
    fi
    if [ "$RESOLUTION_STATUS" != ready ] ||
       [ "$RESOLUTION_REASON" != namespaced-active ]; then
        exit 0
    fi
    CELL_RUNTIME="$PAYLOAD_ROOT/scripts/cell-runtime.py"
    [ -f "$CELL_RUNTIME" ] || exit 126
    CELL_PYTHON="$(_find_management_python 2>/dev/null || true)"
    [ -n "$CELL_PYTHON" ] || exit 126
    if [ "$COMMAND" = "__cell-bootstrap" ]; then
        nohup "$CELL_PYTHON" -I -X utf8 "$CELL_RUNTIME" bootstrap \
            --context "$CONTEXT" \
            --expected-marketplace-id "$MARKETPLACE_ID" \
            --durable-home "$CONTEXT_DURABLE_HOME" >/dev/null 2>&1 &
        exit 0
    fi
    "$CELL_PYTHON" -I -X utf8 "$CELL_RUNTIME" service-ensure-kick \
        --context "$CONTEXT" \
        --expected-marketplace-id "$MARKETPLACE_ID" \
        --durable-home "$CONTEXT_DURABLE_HOME"
    exit $?
fi

_configured_role() {
    local role=""
    role="$(printf '%s' "${AGENT_INDEX_ROLE:-}" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"
    case "$role" in host|client) printf '%s' "$role"; return 0 ;; esac
    local machine="${AGENT_INDEX_MACHINE:-$(hostname -s 2>/dev/null || true)}"
    local args=(--machine "$machine")
    if [ -n "${AGENT_INDEX_CONFIG_DATA_B64:-}" ]; then
        args+=(--data-b64 "$AGENT_INDEX_CONFIG_DATA_B64")
    elif [ -n "${AGENT_INDEX_EFFECTIVE_CONFIG:-}" ]; then
        args+=(--config "$AGENT_INDEX_EFFECTIVE_CONFIG")
    fi
    if [ "${#args[@]}" -gt 2 ]; then
        role="$("$PY_GATE" -E -X utf8 "$SCRIPT_DIR/resolve-activation-role.py" \
            "${args[@]}" 2>/dev/null || true)"
        case "$role" in host|client) printf '%s' "$role"; return 0 ;; esac
    fi
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

_runtime_origin_ok() {
    local python="$1" expected_versions="${2:-}"
    local slot origin origin_dir origin_abs slot_abs expected_abs
    slot="$(dirname -- "$(dirname -- "$python")")"
    [ -d "$slot" ] || return 1
    slot_abs="$(CDPATH= cd -- "$slot" 2>/dev/null && pwd -P)" || return 1
    if [ -n "$expected_versions" ]; then
        [ -d "$expected_versions" ] || return 1
        expected_abs="$(
            CDPATH= cd -- "$expected_versions" 2>/dev/null && pwd -P
        )" || return 1
        case "$slot_abs" in
            "$expected_abs"/*) ;;
            *) return 1 ;;
        esac
    fi
    origin="$(CDPATH= cd -- "$slot_abs" 2>/dev/null &&
        "$python" -I -X utf8 -c \
        'from pathlib import Path; import agent_index; print(Path(agent_index.__file__).resolve())' \
        2>/dev/null)" || return 1
    [ -n "$origin" ] && [ -f "$origin" ] || return 1
    origin_dir="$(dirname -- "$origin")"
    origin_abs="$(CDPATH= cd -- "$origin_dir" 2>/dev/null && pwd -P)/$(basename -- "$origin")" ||
        return 1
    case "$origin_abs" in
        "$slot_abs"/*) return 0 ;;
        *) return 1 ;;
    esac
}

_resolve_ready_runtime() {
    AGENT_RT_PY=""
    if [ "$ACTUAL_MODE" = namespaced ]; then
        local cell_runtime="$PAYLOAD_ROOT/scripts/cell-runtime.py"
        local cell_python validation
        [ -f "$cell_runtime" ] || {
            printf '%s\n' '[agent-index] installation-cell runtime validator is unavailable.' >&2
            exit 126
        }
        cell_python="$(_find_management_python 2>/dev/null || true)"
        [ -n "$cell_python" ] || {
            printf '%s\n' '[agent-index] Python is unavailable for installation-cell validation.' >&2
            exit 126
        }
        validation="$("$cell_python" -I -X utf8 "$cell_runtime" launch-validate \
            --context "$CONTEXT" \
            --expected-marketplace-id "$MARKETPLACE_ID" \
            --durable-home "$CONTEXT_DURABLE_HOME" \
            --command "$COMMAND" 2>/dev/null)" || {
                printf '%s\n' '[agent-index] selected installation runtime failed operative validation.' >&2
                exit 126
            }
        AGENT_RT_PY="$(_json_get "$validation" "$(_json_path interpreter)" 2>/dev/null || true)"
        AGENT_RT_VERSION="$(_json_get "$validation" "$(_json_path runtimeVersion)" 2>/dev/null || true)"
        if [ -z "${AGENT_INDEX_CELL_START_TOKEN:-}" ]; then
            unset AGENT_INDEX_CELL_LOCK_TOKEN AGENT_INDEX_CELL_LOCK_ROOT
        fi
        if [ -n "${AGENT_RT_PY:-}" ] &&
           _runtime_origin_ok "$AGENT_RT_PY" "$ROOT/versions"; then
            if [ -n "$AGENT_RT_VERSION" ]; then
                export AGENT_INDEX_RUNTIME_VERSION="$AGENT_RT_VERSION"
            fi
            return 0
        fi
        AGENT_RT_PY=""
        unset AGENT_INDEX_RUNTIME_VERSION
        return 0
    fi
    if [ -f "$RESOLVER" ]; then
        AGENT_RT_ROOT="$ROOT"
        export AGENT_RT_ROOT
        # shellcheck source=/dev/null
        . "$RESOLVER"
    fi
    if [ -n "${AGENT_RT_PY:-}" ] \
        && ! _runtime_origin_ok "$AGENT_RT_PY"; then
        AGENT_RT_PY=""
    fi
    unset AGENT_INDEX_RUNTIME_VERSION
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
    if [ "$ACTUAL_MODE" = namespaced ]; then
        if [ "$RESOLUTION_STATUS" != ready ] ||
           [ "$RESOLUTION_REASON" != namespaced-active ]; then
            printf '%s\n' '[agent-index] deactivation-pending installation cannot provision a new runtime.' >&2
            return 126
        fi
        local installer="$PAYLOAD_ROOT/scripts/install.sh"
        [ -f "$installer" ] || return 127
        printf '%s\n' '[agent-index] provisioning the active installation cell after explicit setup/configuration.' >&2
        printf '%s\n' '::agent-provisioning:: plugin=agent-index eta_seconds=120 reason=setup' >&2
        local provision_role="${SETUP_ROLE:-$(_configured_role 2>/dev/null || true)}"
        if [ -n "$provision_role" ]; then
            AGENT_INDEX_NO_ENGINE_DEPS=1 AGENT_INDEX_ROLE="$provision_role" \
                bash "$installer" cell-provision \
                --context "$CONTEXT" \
                --expected-marketplace-id "$MARKETPLACE_ID" >&2 || return $?
        else
            AGENT_INDEX_NO_ENGINE_DEPS=1 bash "$installer" cell-provision \
                --context "$CONTEXT" \
                --expected-marketplace-id "$MARKETPLACE_ID" >&2 || return $?
        fi
        _resolve_ready_runtime
        [ -n "${AGENT_RT_PY:-}" ]
        return $?
    fi
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
    local provision_role="${SETUP_ROLE:-$(_configured_role 2>/dev/null || true)}"
    export AGENT_INDEX_ROLE="${provision_role:-client}"
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

SETUP_ROLE=""
SETUP_PROVISIONED=0
SETUP_ROLE_TEMPORARY=0
SETUP_ROLE_HAD_ENV=0
SETUP_ROLE_PRIOR="${AGENT_INDEX_ROLE-}"
if [ "$COMMAND" = setup ]; then
    SETUP_ROLE="$(_setup_role "$@" 2>/dev/null || true)"
    if [ -n "$SETUP_ROLE" ]; then
        [ -n "${AGENT_INDEX_ROLE+x}" ] && SETUP_ROLE_HAD_ENV=1
        export AGENT_INDEX_ROLE="$SETUP_ROLE"
        SETUP_ROLE_TEMPORARY=1
    fi
fi

_restore_setup_role() {
    [ "$SETUP_ROLE_TEMPORARY" -eq 1 ] || return 0
    if [ "$SETUP_ROLE_HAD_ENV" -eq 1 ]; then
        export AGENT_INDEX_ROLE="$SETUP_ROLE_PRIOR"
    else
        unset AGENT_INDEX_ROLE
    fi
    SETUP_ROLE_TEMPORARY=0
}

_resolve_ready_runtime
ROLE="$(_configured_role 2>/dev/null || true)"
RUNTIME_STATE="$(_runtime_state)"

case "$COMMAND" in
    --version|version)
        if [ -n "${AGENT_RT_PY:-}" ]; then exec "$AGENT_RT_PY" -I -X utf8 -m agent_index "$@"; fi
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
        exec "$AGENT_RT_PY" -I -X utf8 -m agent_index "$@"
        ;;
    installer-readiness)
        _emit_readiness configuration-empty 'agent-index runtime activation is explicit; session startup does not provision packages or start services.'
        exit 0
        ;;
    role)
        if [ -z "$ROLE" ]; then
            case " $* " in
                *" --json "*) printf '%s\n' '{"role":null,"setup_required":true,"state":"setup_required"}' ;;
                *) printf '%s\n' unconfigured ;;
            esac
            exit 0
        fi
        if [ -n "${AGENT_RT_PY:-}" ]; then exec "$AGENT_RT_PY" -I -X utf8 -m agent_index "$@"; fi
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
    if [ -n "${AGENT_INDEX_FORWARDED:-}" ]; then
        _emit_runtime_unavailable "$RUNTIME_STATE" "$ROLE"
        exit 1
    fi
    if [ -n "${AGENT_INDEX_NO_SELFPROVISION:-}" ]; then
        printf '%s\n' '[agent-index] runtime is not ready and self-provisioning is disabled.' >&2
        exit 1
    fi
    _provision_runtime || {
        provision_rc=$?
        _restore_setup_role
        exit "$provision_rc"
    }
    [ "$COMMAND" = setup ] && [ -n "$SETUP_ROLE" ] && SETUP_PROVISIONED=1
fi

if [ "$COMMAND" = setup ]; then
    SETUP_OUTPUT="$("$AGENT_RT_PY" -I -X utf8 -m agent_index "$@")"
    SETUP_RC=$?
    _restore_setup_role
    [ -n "$SETUP_OUTPUT" ] && printf '%s\n' "$SETUP_OUTPUT"
    [ "$SETUP_RC" -eq 0 ] || exit "$SETUP_RC"
    CONFIGURED_SETUP_ROLE="$(_configured_role 2>/dev/null || true)"
    if [ "$SETUP_PROVISIONED" -eq 0 ] ||
       { [ -n "$SETUP_ROLE" ] && [ "$CONFIGURED_SETUP_ROLE" != "$SETUP_ROLE" ]; }; then
        SETUP_ROLE="$CONFIGURED_SETUP_ROLE"
        AGENT_INDEX_REBUILD_CURRENT=1 _provision_runtime || exit $?
    fi
    exit 0
fi

exec "$AGENT_RT_PY" -I -X utf8 -m agent_index "$@"
