#!/usr/bin/env bash

# --- bootstrap-killswitch guard (vendored; see libs/bootstrap-killswitch/README.md) ---
# One shared, repo-wide switch (not per-plugin) that pauses EVERY adopting
# plugin's reconcile-on-session-start at once, for when an operator/agent is
# hand-diagnosing a venv/install and a background reconcile must not race it.
# Legacy/default installation ONLY: a namespaced marketplace cell
# (COPILOT_EXTENSIONS_CONTEXT set) reconciles through its own cell-scoped
# mechanism, never this global state file -- crossing that installation-cell
# boundary would let one marketplace's switch pause an unrelated,
# independently-owned cell's reconcile (visions/plugin-services/
# installation-cells). This guard is therefore a deliberate no-op under a
# cell context, same as this hook's own existing cell-context exit below.
if [ -z "${COPILOT_EXTENSIONS_CONTEXT:-}" ]; then
  _bks_guard_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  _bks_guard="$_bks_guard_dir/bootstrap-killswitch-guard.sh"
  if [ -f "$_bks_guard" ] && bash "$_bks_guard" check >&2; then
    printf '{}'
    exit 0
  fi
  unset _bks_guard_dir _bks_guard
fi
# --- end bootstrap-killswitch guard ---

# Vault's first stamp and version-drift reconcile share the installation driver.
set -euo pipefail
ScriptDir="$(cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PluginDir="$(cd -P -- "$ScriptDir/.." && pwd)"
session_start_json_emitted=0
emit_session_start_json() {
  if [ "${session_start_json_emitted:-0}" -eq 0 ]; then
    printf '{}'
    session_start_json_emitted=1
  fi
}
trap 'emit_session_start_json' EXIT
if [[ -n "${COPILOT_EXTENSIONS_CONTEXT:-}" ]]; then exit 0; fi
# Retain the existing no-Python hook no-op; payload commands own native genesis.
py="$(command -v python3 || command -v python || true)"; [ -n "$py" ] || exit 0
if ! name="$("$py" -I -c 'import json,sys;print(json.load(open(sys.argv[1])).get("name",""))' "$PluginDir/plugin.json")"; then
  printf '[agent-vault] Runtime publication refused: owning payload metadata could not be read.\n' >&2
  exit 3
fi
[[ "$name" == agent-vault ]] || {
  printf '[agent-vault] Runtime publication refused: the owning payload is not Agent Vault.\n' >&2; exit 3;
}
. "$ScriptDir/runtime-activation.sh"
activation_code=0
vault_runtime_activation >/dev/null || activation_code=$?
if [[ "$activation_code" == 2 ]]; then exit 0; fi
if [[ "$activation_code" != 0 ]]; then
  printf '[agent-vault] Runtime publication refused: activation configuration could not be resolved.\n' >&2
  exit 3
fi
InstallDir="$HOME/.$name"
Manifest="$InstallDir/deploy-manifest.json"
installer_action=stamp
if [[ -f "$Manifest" ]]; then
  if ! deployment="$("$py" -I -c '
import json, sys
data = json.load(open(sys.argv[1]))
version = data["source"]["version"]
if not isinstance(version, str) or not version:
    raise ValueError("deployed runtime version is unavailable")
print(version)
print(data.get("runtime_version") or version)
print(data.get("runtime_profile", "base"))
print("source" if data.get("runtime_source") is not None else "ordinary")
' "$Manifest")"; then
    printf '[agent-vault] Runtime publication refused: deployed runtime metadata could not be read.\n' >&2
    exit 3
  fi
  deployed="${deployment%%$'\n'*}"
  deployment="${deployment#*$'\n'}"
  expected_runtime="${deployment%%$'\n'*}"
  deployment="${deployment#*$'\n'}"
  deployed_profile="${deployment%%$'\n'*}"
  deployed_source="${deployment#*$'\n'}"
  if ! current="$(grep -m1 -E '^[[:space:]]*version[[:space:]]*=' "$PluginDir/pyproject.toml" |
      sed -E 's/.*=[[:space:]]*"([^"]+)".*/\1/')"; then
    printf '[agent-vault] Runtime publication refused: owning payload version is unavailable.\n' >&2
    exit 3
  fi
  [[ -n "$deployed" && -n "$current" ]] || {
    printf '[agent-vault] Runtime publication refused: runtime or payload version is unavailable.\n' >&2; exit 3;
  }
  special_selection=0
  if [[ -e "$InstallDir/runtime-profile.json" || -L "$InstallDir/runtime-profile.json" ||
      -e "$PluginDir/runtime-source.json" || -L "$PluginDir/runtime-source.json" ||
      -n "${COPILOT_EXTENSIONS_RUNTIME_SOURCE_PIN+x}" ]]; then
    special_selection=1
    minimum_minor=10
    if [[ -e "$InstallDir/runtime-profile.json" || -L "$InstallDir/runtime-profile.json" ]]; then
      minimum_minor=11
    fi
    profile_python=""
    for candidate in python3 python python3.13 python3.12 python3.11; do
      if command -v "$candidate" >/dev/null 2>&1 &&
          "$candidate" -I -S -B -c 'import sys; assert sys.version_info >= (3, int(sys.argv[1]))' \
              "$minimum_minor" 2>/dev/null; then
        profile_python="$(command -v "$candidate")"
        break
      fi
    done
    if [[ -z "$profile_python" ]]; then
      printf '[agent-vault] Runtime publication refused: source/profile inspection requires an existing Python 3.%s+ interpreter.\n' "$minimum_minor" >&2
      exit 3
    fi
    profile_code=0
    "$profile_python" -I -B "$ScriptDir/runtime_profile.py" --root "$InstallDir" \
        current --package-version "$current" || profile_code=$?
    if [[ "$profile_code" == 0 ]]; then exit 0; fi
    if [[ "$profile_code" != 1 ]]; then
      printf '[agent-vault] Runtime publication refused: source/profile metadata could not be read.\n' >&2
      exit 3
    fi
  fi
  provisioned=0
  [[ ! -e "$InstallDir/.venv" ]] || provisioned=1
  if [[ "$provisioned" == 0 && -f "$InstallDir/current-version" ]]; then
    selected="$(tr -d '[:space:]' <"$InstallDir/current-version")"
    if [[ -n "$selected" && "$selected" == "$current" ]] &&
        { [[ -x "$InstallDir/versions/$selected/bin/python" ]] ||
          [[ -f "$InstallDir/versions/$selected/Scripts/python.exe" ]]; }; then provisioned=1; fi
  fi
  if [[ "$special_selection" == 0 && "$provisioned" == 1 && "$deployed" == "$current" &&
      "$expected_runtime" == "$current" && "$deployed_profile" == "base" &&
      "$deployed_source" == "ordinary" ]]; then
    exit 0
  fi
  installer_action=provision
fi
if [[ -n "${AGENT_VAULT_NO_SELFPROVISION:-}" || -n "${AGENT_VAULT_PINNED_CONTEXT+x}" ]]; then
  printf '[agent-vault] bootstrap deferred: self-provisioning is disabled or identity reads are pinned.\n' >&2
  exit 0
fi
if [[ -n "${UPDATE_DRIVER_WITNESS+x}" ]]; then
  printf '[agent-vault] Runtime publication refused: nested bootstrap submission is unsupported; retain the current installation owner.\n' >&2
  exit 3
fi
if [[ "$(uname -s)" != Linux ]] || (( BASH_VERSINFO[0] < 4 ||
    (BASH_VERSINFO[0] == 4 && BASH_VERSINFO[1] < 2) )); then
  printf '[agent-vault] Runtime publication refused: this native installation driver requires qualified Linux Bash, not the stock legacy shell.\n' >&2
  exit 3
fi
environment_helper="$ScriptDir/runtime-environment.sh"
first_use="$PluginDir/libs/update-driver/scripts/first-use.sh"
if [[ ! -f "$environment_helper" || ! -f "$first_use" ]] ||
    ! grep -Fq '# Installation driver lifecycle contract: vault-stamp-v1' "$first_use"; then
  printf '[agent-vault] Runtime publication refused: packaged driver/runtime-environment support is unavailable or lacks the Vault stamp lifecycle contract.\n' >&2
  exit 3
fi
# shellcheck source=runtime-environment.sh
. "$environment_helper"
apply_runtime_env "$InstallDir"
execution_root="$HOME/.copilot-extensions/update-driver"
project="$(pwd -P)"
if ! acknowledgement="$(bash "$first_use" "$execution_root" "$PluginDir" "$InstallDir" "$project" \
    --installer-action "$installer_action")"; then
  printf '[agent-vault] Runtime publication refused: installation driver admission failed.\n' >&2
  exit 3
fi
if [[ ! "$acknowledgement" =~ ^launched:([1-9][0-9]*)\ log:(.+)$ ]]; then
  printf '[agent-vault] Runtime publication refused: installation driver launch acknowledgement is malformed.\n' >&2
  exit 3
fi
acknowledged_log="${BASH_REMATCH[2]}"
log_name="${acknowledged_log#"$execution_root/"}"
if [[ "$acknowledged_log" == "$log_name" ||
    ! "$log_name" =~ ^first-use-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.log$ ]]; then
  printf '[agent-vault] Runtime publication refused: installation driver launch diagnostics name another execution root.\n' >&2
  exit 3
fi
printf '[agent-vault] bootstrap %s supervision launched; inspect first-use diagnostics in %s. Launch is not durable request acceptance.\n' \
  "$installer_action" "$execution_root" >&2
printf '[agent-vault] driver launch acknowledgement: %s\n' "$acknowledgement" >&2
exit 0