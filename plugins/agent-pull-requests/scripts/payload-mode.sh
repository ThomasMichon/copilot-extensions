#!/usr/bin/env bash
# Native governance admission must work before any Python has been installed.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
payload="$(cd "$here/.." && pwd)"
legacy="${HOME:?}/.agent-pull-requests"
stage_root="$legacy/.install-stage"
stage="$(dirname "$payload")"
if [[ "$(dirname "$stage")" == "$stage_root" ]]; then
    # Staging hints locate evidence, never waive native governance admission.
    [[ "${COPILOT_PLUGIN_INSTALL_STAGED:-}" == 1 &&
        "${COPILOT_PLUGIN_STAGED_FROM:-}" == /* &&
        "$(basename "$payload")" == agent-pull-requests ]] || {
        printf '%s\n' 'Staged installer provenance is incomplete' >&2; exit 126;
    }
    origin="$(cd -P -- "$COPILOT_PLUGIN_STAGED_FROM" && pwd)"
    marketplace="$(dirname "$origin")"
    [[ "$(dirname "$marketplace")" == "$HOME/.copilot/installed-plugins" &&
        "$(basename "$origin")" == agent-pull-requests ]] || {
        printf '%s\n' 'Staged installer origin is not a canonical installed payload' >&2; exit 126;
    }
    for root in "$payload" "$COPILOT_PLUGIN_STAGED_FROM"; do
        cursor="$root"
        while [[ "$cursor" != / ]]; do
            [[ ! -L "$cursor" ]] || {
                printf '%s\n' 'Staged installer provenance may not traverse links' >&2; exit 126;
            }
            cursor="$(dirname "$cursor")"
        done
    done
    links="$(find "$payload" "$origin" -type l -print)"
    [[ -z "$links" ]] || {
        printf '%s\n' 'Staged installer payload differs from its origin or contains links' >&2; exit 126;
    }
    if ! diff -qr "$payload" "$origin" >/dev/null; then
        # A frozen copy has exactly the canonical input files, without caches.
        frozen_files() {
            local code=0
            if [[ "$2" == source ]]; then
                find "$1" -type d \( -name .git -o -name .venv -o -name __pycache__ \
                    -o -name .pytest_cache -o -name .ruff_cache -o -name build \
                    -o -name dist -o -name node_modules -o -name '*.egg-info' \
                    -o -name '*.dist-info' \) -prune -o -type f \
                    ! -name '*.pyc' ! -name '*.pyo' -print0 || code=$?
            else
                find "$1" -type f -print0 || code=$?
            fi
            printf '%s\0' "$code"
        }
        compare_frozen_files() {
            local file relative expected=0 actual=0 complete=0
            while IFS= read -r -d '' file; do
                if [[ "$file" == 0 ]]; then complete=1; continue; fi
                [[ "$complete" == 0 && "$file" == "$origin/"* ]] || return 1
                relative="${file#"$origin/"}"
                [[ -f "$payload/$relative" ]] &&
                    diff -q "$payload/$relative" "$file" >/dev/null || return 1
                expected=$((expected + 1))
            done < <(frozen_files "$origin" source)
            [[ "$complete" == 1 ]] || return 1
            complete=0
            while IFS= read -r -d '' file; do
                if [[ "$file" == 0 ]]; then complete=1; continue; fi
                [[ "$complete" == 0 && "$file" == "$payload/"* ]] || return 1
                actual=$((actual + 1))
            done < <(frozen_files "$payload" staged)
            [[ "$complete" == 1 && "$actual" == "$expected" ]]
        }
        compare_frozen_files || {
            printf '%s\n' 'Staged installer payload differs from its origin' >&2; exit 126;
        }
    fi
    payload="$origin"
fi
forward=(status --payload-root "$payload" --plugin-id agent-pull-requests --legacy-root "$legacy")
if [[ ! -e "$legacy" && ! -L "$legacy" ]]; then
    forward+=(--legacy-probe-json '{"declared":true,"result":"absent","checkedAt":null}')
fi
if [[ -n "${COPILOT_EXTENSIONS_CONTEXT:-}" ]]; then
    receipt="$COPILOT_EXTENSIONS_CONTEXT"
    case "$receipt" in
        /*/marketplaces/*/plugins/agent-pull-requests/install.json) ;;
        *) printf '%s\n' 'Explicit context must be an absolute canonical install receipt' >&2; exit 126 ;;
    esac
    durable="$receipt"
    for _ in 1 2 3 4 5; do durable="$(dirname "$durable")"; done
    forward+=(--context "$receipt" --durable-home "$durable"
        --expected-plugin-id agent-pull-requests --expected-payload-root "$payload")
fi
status="$(bash "$here/installation-context/installation-context.sh" "${forward[@]}")"
query() {
    printf '%s\n' "$status" | LC_ALL=C awk -v mode=get -v query_path="$1" \
        -f "$here/installation-context/json-query.awk"
}
field() { query "$1"$'\034'"$2"; }
legacy_allowed=false
if [[ -z "${COPILOT_EXTENSIONS_CONTEXT:-}" &&
    "$(query status)" == ready && "$(query actualMode)" == legacy &&
    "$(query desiredMode)" == legacy ]]; then
    legacy_allowed=true
fi
if [[ -z "${COPILOT_EXTENSIONS_CONTEXT:-}" &&
    "$(query status)" == provenance-blocked &&
    "$(field policy enabled)" == false &&
    "$(field legacy disposition)" == active && -z "$(field legacy tombstone)" ]]; then
    policy_state="$(field policy state)"
    if [[ "$policy_state" == missing ]]; then
        legacy_allowed=true
    elif [[ "$policy_state" == valid ]]; then
        policy="$(field policy path)"
        # The native validator has already checked the full document.
        overrides="$(LC_ALL=C awk -v mode=len \
            -v query_path=$'installationMode\034marketplaces' \
            -f "$here/installation-context/json-query.awk" "$policy")" || {
            rc=$?
            [[ "$rc" == 3 ]] || exit "$rc"
            overrides=0
        }
        [[ "$overrides" != 0 ]] || legacy_allowed=true
    fi
fi
if [[ "$legacy_allowed" == true ]]; then printf 'legacy\n'; exit 0; fi
if [[ "$(query status)" == ready &&
    ( "$(query reason)" == namespaced-active || "$(query reason)" == activation-required ) ]]; then
    printf 'cell\n'; exit 0
fi
printf 'Installation blocks invocation: status=%s reason=%s\n' "$(query status)" "$(query reason)" >&2
exit 126
