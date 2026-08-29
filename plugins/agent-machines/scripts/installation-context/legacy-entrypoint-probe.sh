#!/usr/bin/env bash
# Gate a legacy installer/bootstrap mutation through installation-mode policy.
set -euo pipefail

SCRIPT_DIR="$(cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
RESOLVER="$SCRIPT_DIR/installation-context.sh"
JSON_QUERY="$SCRIPT_DIR/json-query.awk"
SEP=$'\034'

fail() {
    printf 'legacy-entrypoint-probe: %s\n' "$1" >&2
    exit 1
}

json_query() {
    local mode="$1" file="$2" path="$3" variable
    if [[ "$file" == @* ]]; then
        variable="${file#@}"
        LC_ALL=C awk -f "$JSON_QUERY" -v "mode=$mode" -v "query_path=$path" - <<<"${!variable-}"
        return
    fi
    LC_ALL=C awk -f "$JSON_QUERY" -v "mode=$mode" -v "query_path=$path" "$file"
}

json_path() {
    local result="" component
    for component in "$@"; do
        if [[ -n "$result" ]]; then
            result+="$SEP"
        fi
        result+="$component"
    done
    printf '%s' "$result"
}

json_string() {
    local target="$1" file="$2" path="$3" encoded="" value="" byte
    if ! encoded="$(json_query hex "$file" "$path" 2>/dev/null)"; then
        return 1
    fi
    while [[ -n "$encoded" ]]; do
        [[ "${#encoded}" -ge 2 ]] || return 1
        printf -v byte '%b' "\\x${encoded:0:2}"
        value+="$byte"
        encoded="${encoded:2}"
    done
    printf -v "$target" '%s' "$value"
}

resolve_profile_home() {
    local uid user passwd_entry="" home_path=""
    uid="$(id -u 2>/dev/null)" || fail "cannot determine the current account identity"
    if command -v getent >/dev/null 2>&1; then
        passwd_entry="$(getent passwd "$uid" 2>/dev/null || true)"
    fi
    if [[ -z "$passwd_entry" && -r /etc/passwd ]]; then
        passwd_entry="$(LC_ALL=C awk -F: -v uid="$uid" '$3 == uid { print; exit }' /etc/passwd)"
    fi
    if [[ -n "$passwd_entry" ]]; then
        home_path="$(printf '%s' "$passwd_entry" | LC_ALL=C cut -d: -f6)"
    elif command -v dscl >/dev/null 2>&1; then
        # macOS ships no `getent`, and keeps ordinary accounts in
        # DirectoryService rather than /etc/passwd (which holds only system
        # accounts) -- so both lookups above miss for every normal user and the
        # probe used to fail outright. `dscl` is the authoritative lookup there,
        # and is the shell equivalent of the `pwd.getpwuid()` this library's
        # Python implementation already uses.
        user="$(id -un 2>/dev/null || true)"
        if [[ -n "$user" ]]; then
            # `|| true` is load-bearing: the script runs under
            # `set -euo pipefail`, so without it a non-zero dscl (e.g. no such
            # account) would propagate out of the assignment and abort the
            # script here, skipping the explicit diagnostic below.
            home_path="$(dscl . -read "/Users/$user" NFSHomeDirectory 2>/dev/null |
                LC_ALL=C awk '$1 == "NFSHomeDirectory:" { $1 = ""; sub(/^[[:space:]]+/, ""); print; exit }' || true)"
        fi
    fi
    [[ -n "$home_path" ]] ||
        fail "cannot determine the current account home from the account database (passwd or DirectoryService)"
    [[ "$home_path" == /* && -d "$home_path" ]] ||
        fail "the resolved account home is unavailable: $home_path"
    (cd -P -- "$home_path" && pwd)
}

PAYLOAD_ROOT=""
LEGACY_ROOT=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --payload-root)
            [[ $# -ge 2 ]] || fail "--payload-root requires a value"
            PAYLOAD_ROOT="$2"
            shift 2
            ;;
        --legacy-root)
            [[ $# -ge 2 ]] || fail "--legacy-root requires a value"
            LEGACY_ROOT="$2"
            shift 2
            ;;
        *)
            fail "unknown argument: $1"
            ;;
    esac
done

[[ "$PAYLOAD_ROOT" == /* ]] || fail "--payload-root must be absolute"
[[ "$LEGACY_ROOT" == /* ]] || fail "--legacy-root must be absolute"
[[ -f "$RESOLVER" && -f "$JSON_QUERY" ]] || fail "installation-context resolver is unavailable"

MANIFEST="$PAYLOAD_ROOT/payload-invocation.json"
PROFILE_HOME="$(resolve_profile_home)"
PLUGIN_ID=""
DECLARED=false
RESULT=unknown

if [[ -f "$MANIFEST" ]] \
    && json_string PLUGIN_ID "$MANIFEST" "$(json_path command)" \
    && [[ -n "$PLUGIN_ID" ]] \
    && [[ "$(json_query type "$MANIFEST" "$(json_path installation legacyFootprint)" 2>/dev/null || true)" == object ]] \
    && [[ "$(json_query type "$MANIFEST" "$(json_path installation legacyFootprint paths)" 2>/dev/null || true)" == array ]] \
    && [[ "$(json_query type "$MANIFEST" "$(json_path installation legacyFootprint services)" 2>/dev/null || true)" == array ]] \
    && [[ "$(json_query type "$MANIFEST" "$(json_path installation legacyFootprint tasks)" 2>/dev/null || true)" == array ]]; then
    DECLARED=true
    RESULT=absent
    UNKNOWN=0

    path_count="$(json_query len "$MANIFEST" "$(json_path installation legacyFootprint paths)")"
    for ((index = 0; index < path_count; index++)); do
        footprint_path=""
        path_query="$(json_path installation legacyFootprint paths "$index")"
        if [[ "$(json_query type "$MANIFEST" "$path_query" 2>/dev/null || true)" != string ]] \
            || ! json_string footprint_path "$MANIFEST" "$path_query" \
            || [[ -z "$footprint_path" ]]; then
            UNKNOWN=1
            continue
        fi
        case "$footprint_path" in
            /*) resolved_path="$footprint_path" ;;
            "~/"*) resolved_path="$PROFILE_HOME/${footprint_path#~/}" ;;
            *) resolved_path="$PROFILE_HOME/$footprint_path" ;;
        esac
        if [[ -e "$resolved_path" || -L "$resolved_path" ]]; then
            RESULT=present
            break
        fi
    done

    if [[ "$RESULT" != present && ( -e "$LEGACY_ROOT" || -L "$LEGACY_ROOT" ) ]]; then
        RESULT=present
    fi

    service_count="$(json_query len "$MANIFEST" "$(json_path installation legacyFootprint services)")"
    for ((index = 0; index < service_count; index++)); do
        [[ "$RESULT" != present ]] || break
        platform=""; manager=""; name=""
        if ! json_string platform "$MANIFEST" "$(json_path installation legacyFootprint services "$index" platform)" \
            || ! json_string manager "$MANIFEST" "$(json_path installation legacyFootprint services "$index" manager)" \
            || ! json_string name "$MANIFEST" "$(json_path installation legacyFootprint services "$index" name)"; then
            UNKNOWN=1
            continue
        fi
        case "$platform" in
            windows) continue ;;
            posix) ;;
            *) UNKNOWN=1; continue ;;
        esac
        case "$manager" in
            systemd-user)
                if ! command -v systemctl >/dev/null 2>&1; then
                    continue
                fi
                set +e
                load_state="$(systemctl --user show "$name" --property=LoadState --value 2>/dev/null)"
                query_status=$?
                set -e
                if [[ "$query_status" -ne 0 ]]; then
                    UNKNOWN=1
                elif [[ -n "$load_state" && "$load_state" != not-found ]]; then
                    RESULT=present
                fi
                ;;
            *) UNKNOWN=1 ;;
        esac
    done

    task_count="$(json_query len "$MANIFEST" "$(json_path installation legacyFootprint tasks)")"
    for ((index = 0; index < task_count; index++)); do
        [[ "$RESULT" != present ]] || break
        platform=""; manager=""; name=""
        if ! json_string platform "$MANIFEST" "$(json_path installation legacyFootprint tasks "$index" platform)" \
            || ! json_string manager "$MANIFEST" "$(json_path installation legacyFootprint tasks "$index" manager)" \
            || ! json_string name "$MANIFEST" "$(json_path installation legacyFootprint tasks "$index" name)"; then
            UNKNOWN=1
            continue
        fi
        case "$platform" in
            windows) continue ;;
            posix) UNKNOWN=1 ;;
            *) UNKNOWN=1 ;;
        esac
    done

    if [[ "$RESULT" == absent && "$UNKNOWN" -eq 1 ]]; then
        RESULT=unknown
    fi
fi

[[ -n "$PLUGIN_ID" ]] || fail "payload-invocation.json has no usable command identity"
CHECKED_AT="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
LEGACY_PROBE_JSON="{\"declared\":$DECLARED,\"result\":\"$RESULT\",\"checkedAt\":\"$CHECKED_AT\"}"

run_resolver() {
    bash "$RESOLVER" probe-legacy \
        --payload-root "$PAYLOAD_ROOT" \
        --plugin-id "$PLUGIN_ID" \
        --legacy-root "$LEGACY_ROOT" \
        --legacy-probe-json "$LEGACY_PROBE_JSON"
}

CLEAR_CONTEXT=0
if [[ -n "${COPILOT_EXTENSIONS_CONTEXT:-}" && -f "${COPILOT_EXTENSIONS_CONTEXT:-}" ]]; then
    CONTEXT_DURABLE_HOME="$COPILOT_EXTENSIONS_CONTEXT"
    for _part in 1 2 3 4 5; do
        CONTEXT_DURABLE_HOME="$(dirname -- "$CONTEXT_DURABLE_HOME")"
    done
    set +e
    VALIDATED_CONTEXT="$(bash "$RESOLVER" validate \
        --context "$COPILOT_EXTENSIONS_CONTEXT" \
        --durable-home "$CONTEXT_DURABLE_HOME" 2>/dev/null)"
    VALIDATION_STATUS=$?
    set -e
    if [[ "$VALIDATION_STATUS" -eq 0 ]]; then
        CONTEXT_PLUGIN_ID=""
        if json_string CONTEXT_PLUGIN_ID @VALIDATED_CONTEXT "$(json_path pluginId)" 2>/dev/null \
            && [[ -n "$CONTEXT_PLUGIN_ID" && "$CONTEXT_PLUGIN_ID" != "$PLUGIN_ID" ]]; then
            CLEAR_CONTEXT=1
        fi
    fi
fi

set +e
if [[ "$CLEAR_CONTEXT" -eq 1 ]]; then
    DECISION="$(unset COPILOT_EXTENSIONS_CONTEXT; run_resolver)"
else
    DECISION="$(run_resolver)"
fi
STATUS=$?
set -e

if [[ "$STATUS" -eq 0 ]]; then
    exit 0
fi
if [[ "$STATUS" -eq 3 ]]; then
    REASON=""
    json_string REASON @DECISION "$(json_path probeReason)" 2>/dev/null || REASON=blocked
    printf '[%s] legacy mutation blocked by installation governance: %s\n' "$PLUGIN_ID" "$REASON" >&2
    exit 3
fi
printf '[%s] legacy mutation probe failed before a safe decision could be made.\n' "$PLUGIN_ID" >&2
exit 1
