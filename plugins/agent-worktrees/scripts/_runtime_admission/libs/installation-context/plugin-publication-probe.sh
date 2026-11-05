#!/usr/bin/env bash
# Compose the canonical read-only deployment and installation-mode decisions.
set -euo pipefail
script_dir="$(cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
helper="$script_dir/installation-context.sh"
query="$script_dir/json-query.awk"
payload="${1:?payload root required}"
plugin="${2:?plugin id required}"
root="${3:?legacy root required}"
probe="" code=0
probe="$(bash "$helper" probe-plugin-publication --payload-root "$payload" \
    --plugin-id "$plugin" --legacy-root "$root")" || code=$?
value() {
    LC_ALL=C awk -f "$query" -v mode="$1" -v query_path="$2" <<<"$3"
}
allowed="$(value get $'deployment\034allowPluginPublication' "$probe" 2>/dev/null || true)"
allowed_type="$(value type $'deployment\034allowPluginPublication' "$probe" 2>/dev/null || true)"
path="$(value get $'deployment\034path' "$probe" 2>/dev/null || true)"
selected_root="$(value get $'legacy\034root' "$probe" 2>/dev/null || true)"
if [[ "$code" == 0 && "$allowed_type" == boolean && "$allowed" == true &&
      "$selected_root" == /* && "$path" == "$selected_root/runtime-deployment.json" ]]; then
    legacy="" legacy_code=0
    legacy="$(bash "$helper" probe-legacy --payload-root "$payload" \
        --plugin-id "$plugin" --legacy-root "$root")" || legacy_code=$?
    mutation="$(value get allowMutation "$legacy" 2>/dev/null || true)"
    mutation_type="$(value type allowMutation "$legacy" 2>/dev/null || true)"
    legacy_root="$(value get $'legacy\034root' "$legacy" 2>/dev/null || true)"
    if [[ "$legacy_code" == 0 && "$mutation_type" == boolean &&
          "$mutation" == true && "$legacy_root" == "$selected_root" ]]; then
        exit 0
    fi
    probe="$probe $legacy"
fi
printf '[%s] Runtime publication refused: %s Use explicit installation management to install or repair this runtime.\n' "$plugin" "$probe" >&2
exit 3
