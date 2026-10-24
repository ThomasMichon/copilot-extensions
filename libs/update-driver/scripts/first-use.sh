#!/usr/bin/env bash
# Installation driver lifecycle contract: vault-stamp-v1
# Fixed first-adopter launch; ordinary acceptance appears only in the canonical queue.
set -euo pipefail
if [[ -v UPDATE_DRIVER_WITNESS ]]; then
    printf 'Nested first-use submission is unsupported; retain the current installation owner.\n' >&2
    exit 1
fi
[[ "$(uname -s)" == Linux && "$#" -ge 4 ]] || exit 2
root="$1" payload="$2" runtime="$3" project="$4"
shift 4
service_mode=()
installer_action=provision
wait_for_owner=0
run_owner=0
expected_source=""
while [[ "$#" -gt 0 ]]; do
    case "$1" in
        --client-only) service_mode=(--client-only); shift ;;
        --installer-action)
            [[ "$#" -ge 2 && ( "$2" == provision || "$2" == stamp ) ]] || exit 2
            installer_action="$2"; shift 2 ;;
        --wait) wait_for_owner=1; shift ;;
        --run-owner)
            [[ "$#" -ge 2 && "$run_owner" == 0 ]] || exit 2
            run_owner=1; expected_source="$2"; shift 2 ;;
        *) exit 2 ;;
    esac
done
[[ "$run_owner" == 0 || "$wait_for_owner" == 0 ]] || exit 2
[[ "$installer_action" == provision || "${#service_mode[@]}" == 0 ]] || {
    printf 'Client-only selection requires the provision action.\n' >&2; exit 2;
}
foundation="$(CDPATH= cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
source "$foundation/src/update_driver/foundation.sh"
for path in "$root" "$payload" "$runtime" "$project"; do
    [[ "$path" == /* && "$(readlink -m -- "$path")" == "$path" ]] || {
        printf 'Native first-use paths must be absolute, normalized and unlinked.\n' >&2; exit 2;
    }
done
for path in "$payload" "$runtime"; do
    [[ "$root" != "$path" && "$root" != "$path/"* && "$path" != "$root/"* ]] || {
        printf 'Execution root must be separate from owning payload and installation.\n' >&2; exit 1;
    }
done
[[ -d "$payload" && -d "$project" ]] || {
    printf 'Owning plugin first-use provisioning is unavailable or disabled.\n' >&2; exit 1;
}
query() {
    printf '%s' "$1" | LC_ALL=C awk -f "$__foundation_assets/json-query.awk" -v mode=get -v "query_path=$2"
}
query_type() {
    printf '%s' "$1" | LC_ALL=C awk -f "$__foundation_assets/json-query.awk" -v mode=type -v "query_path=$2"
}
manifest="$(cat -- "$payload/payload-invocation.json")"
identity="$(cat -- "$payload/plugin.json")"
plugin="$(query "$identity" name)"
case "$plugin" in
    agent-vault) prefix=AGENT_VAULT ;;
    agent-pull-requests) prefix=AGENT_PULL_REQUESTS ;;
    *) printf 'Owning payload does not declare the fixed first-use provision recipe.\n' >&2; exit 1 ;;
esac
[[ "$plugin" != agent-pull-requests || (
    "$(dirname -- "$(dirname -- "$payload")")" == "$HOME/.copilot/installed-plugins" &&
    "$runtime" == "$HOME/.agent-pull-requests" ) ]] || {
    printf 'Pull Requests first use requires its canonical installed payload and legacy root.\n' >&2; exit 1;
}
[[ "$plugin" == agent-vault || "${#service_mode[@]}" == 0 ]] || {
    printf 'Client-only selection is unsupported by the owning installer.\n' >&2; exit 1;
}
no_self="${prefix}_NO_SELFPROVISION"
pinned="${prefix}_PINNED_CONTEXT"
[[ -z "${!no_self:-}" && ! -v "$pinned" &&
    ( "$plugin" != agent-pull-requests || ! -v "$no_self" ) ]] || {
    printf 'Owning plugin first-use provisioning is unavailable or disabled.\n' >&2; exit 1;
}
if installer_type="$(query_type "$manifest" installer)"; then
    [[ "$installer_type" == string ]] || {
        printf 'Owning payload does not declare the fixed first-use provision recipe.\n' >&2; exit 1;
    }
    installer="$(query "$manifest" installer)"
else
    code=$?
    [[ "$code" == 3 ]] || exit "$code"
    installer=install
fi
[[ "$(query "$manifest" schema)" == copilot-extensions.payload-invocation &&
    "$(query_type "$manifest" version)" == number &&
    "$(query "$manifest" version)" == 2 && "$installer" == install &&
    "$(query "$manifest" noSelfProvisionEnv)" == "$no_self" &&
    "$(query "$manifest" command)" == "$plugin" &&
    "$(query "$manifest" provisionMode)" == direct &&
    "$(query "$manifest" installationContext)" == required ]] || {
    printf 'Owning payload does not declare the fixed first-use provision recipe.\n' >&2; exit 1;
}
admission="$(COPILOT_PLUGIN_ROOT="$payload" bash \
    "$payload/scripts/installation-context/installation-context.sh" probe-plugin-publication \
    --payload-root "$payload" --plugin-id "$plugin" --legacy-root "$runtime" \
    --project-root "$project")"
selected_root="$(query "$admission" runtimeRoot)"
if [[ -z "$selected_root" ]]; then selected_root="$(query "$admission" $'legacy\034root')"; fi
provenance="$(COPILOT_PLUGIN_ROOT="$payload" bash \
    "$payload/scripts/installation-context/installation-context.sh" resolve \
    --payload-root "$payload" --plugin-id "$plugin" --project-root "$project")"
source_id="$(query "$provenance" sourceFingerprint)"
[[ "$(query "$admission" status)" == ready && "$(query "$admission" actualMode)" == legacy &&
    "$(query "$admission" $'deployment\034allowPluginPublication')" == true &&
    "$selected_root" == "$runtime" && "$source_id" =~ ^sha256:[0-9a-f]{64}$ &&
    "$(query "$provenance" marketplaceId)" == "$(query "$admission" marketplaceId)" ]] || {
    printf 'Owning first-use publication admission is incomplete or names another installation.\n' >&2; exit 1;
}
if [[ "$run_owner" == 0 ]]; then
    source "${foundation%/*}/agent-procutil/src/agent_procutil/native_daemon.sh"
    _native_foundation_outside_sources "$root" "$foundation"
    mkdir -p -m 700 -- "$root"
    check_native_foundation_execution_root "$root" "$foundation"
    log="$root/first-use-$(cat /proc/sys/kernel/random/uuid).log"
    start_native_daemon "$foundation/scripts/first-use.sh" "$root" "$log" \
        "$root" "$payload" "$runtime" "$project" --run-owner "$source_id" \
        --installer-action "$installer_action" "${service_mode[@]}"
    if [[ "$wait_for_owner" == 1 ]]; then
        code=0
        wait "$__native_daemon_pid" || code=$?
        cat -- "$log" >&2
        exit "$code"
    fi
    # This line acknowledges only the positively launched finite supervisor.
    printf 'launched:%s log:%s\n' "$__native_daemon_pid" "$log"
    exit 0
fi
[[ "$expected_source" == "$source_id" ]] || {
    printf 'Owning source changed after native first-use admission.\n' >&2; exit 1;
}
digest="$(native_foundation_source_digest "$foundation")"
bash "$foundation/scripts/foundation-produce.sh" "$root" "$digest"
read_native_foundation_progress "$root"
[[ "$__foundation_present" == 1 && "${__foundation_value[state]}" == completed ]] || {
    printf 'Foundation did not publish a completed operation.\n' >&2; exit 1;
}
selector="$root/foundation/current-version"
_native_foundation_plain "$selector" file
[[ "$(stat -c '%s' -- "$selector")" -le 128 ]] || exit 1
IFS= read -r selected <"$selector"
[[ "$selected" == "foundation-${__foundation_value[source_hash]}" ]] || {
    printf 'Selected foundation differs from its completed construction.\n' >&2; exit 1;
}
slot="$root/foundation/versions/$selected"
[[ -x "$slot/bin/python" ]] || { printf 'Selected foundation interpreter is unavailable.\n' >&2; exit 1; }
for path in "$slot/.runtime-bootstrap/runtime_launcher.py" "$slot/.install-complete.json"; do
    _native_foundation_plain "$path" file
done
exec "$slot/bin/python" -I -S -B -X utf8 "$slot/.runtime-bootstrap/runtime_launcher.py" \
    -m update_driver.first_use submit --payload "$payload" --runtime-root "$runtime" \
    --project "$project" --root "$root" --expected-source "$source_id" \
    --installer-action "$installer_action" --wait "${service_mode[@]}"
