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

# Logger drift retains install, including its task/timer reconciliation.
set -euo pipefail
session_start_json_emitted=0
emit_session_start_json() {
  if [ "${session_start_json_emitted:-0}" -eq 0 ]; then
    printf '{}'
    session_start_json_emitted=1
  fi
}
trap 'emit_session_start_json' EXIT
refuse() { printf '[agent-logger] Runtime publication refused: %s\n' "$1" >&2; exit 3; }
if [[ -n "${COPILOT_EXTENSIONS_CONTEXT:-}" ]]; then exit 0; fi
project="$(pwd -P)"
ScriptDir="$(cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PluginDir="$(cd -P -- "$ScriptDir/.." && pwd)"
InstallDir="$HOME/.agent-logger"
Manifest="$InstallDir/deploy-manifest.json"
query="$ScriptDir/installation-context/json-query.awk"
installer_action=stamp
if [[ -f "$Manifest" ]]; then
  [[ -f "$query" ]] || refuse 'native deployed runtime metadata reader is unavailable.'
  if [[ "$(LC_ALL=C awk -f "$query" -v mode=type -v query_path=$'source\034version' "$Manifest")" != string ]] ||
      ! deployed="$(LC_ALL=C awk -f "$query" -v mode=get -v query_path=$'source\034version' "$Manifest")" ||
      [[ -z "$deployed" ]]; then
    refuse 'deployed runtime metadata could not be read.'
  fi
  if ! current="$(grep -m1 -E '^[[:space:]]*version[[:space:]]*=' "$PluginDir/pyproject.toml" |
      sed -E 's/.*=[[:space:]]*"([^"]+)".*/\1/')"; then
    refuse 'owning payload version is unavailable.'
  fi
  [[ -n "$current" ]] || refuse 'owning payload version is unavailable.'
  provisioned=0
  [[ ! -e "$InstallDir/.venv" ]] || provisioned=1
  if [[ "$provisioned" == 0 && -f "$InstallDir/current-version" ]]; then
    selected="$(tr -d '[:space:]' <"$InstallDir/current-version")"
    if [[ -n "$selected" && "$selected" == "$current" ]] &&
        { [[ -x "$InstallDir/versions/$selected/bin/python" ]] ||
          [[ -f "$InstallDir/versions/$selected/Scripts/python.exe" ]]; }; then provisioned=1; fi
  fi
  if [[ "$provisioned" == 1 && "$deployed" == "$current" ]]; then exit 0; fi
  installer_action=install
fi
if [[ -n "${AGENT_LOGGER_NO_SELFPROVISION:-}" || -n "${AGENT_LOGGER_PINNED_CONTEXT+x}" ]]; then
  printf '[agent-logger] bootstrap deferred: self-provisioning is disabled or identity reads are pinned.\n' >&2
  exit 0
fi
[[ -z "${UPDATE_DRIVER_WITNESS+x}" ]] ||
  refuse 'nested bootstrap submission is unsupported; retain the current installation owner.'
if [[ "$(uname -s)" != Linux ]] || (( BASH_VERSINFO[0] < 4 ||
    (BASH_VERSINFO[0] == 4 && BASH_VERSINFO[1] < 2) )); then
  refuse 'this native installation driver requires qualified Linux Bash, not the stock legacy shell.'
fi
if [[ "$(dirname -- "$(dirname -- "$PluginDir")")" != "$HOME/.copilot/installed-plugins" ||
    "$(basename -- "$PluginDir")" != agent-logger ]]; then
  refuse 'Logger bootstrap publication requires its canonical installed payload.'
fi
first_use="$PluginDir/libs/update-driver/scripts/first-use.sh"
if [[ ! -f "$first_use" ]] ||
    ! grep -Fq '# Installation driver lifecycle contract: logger-install-v1' "$first_use"; then
  refuse 'packaged driver support is unavailable or lacks the Logger install lifecycle contract.'
fi
if [[ ! -f "$query" ]] ||
    [[ "$(LC_ALL=C awk -f "$query" -v mode=type -v query_path=name "$PluginDir/plugin.json")" != string ]] ||
    [[ "$(LC_ALL=C awk -f "$query" -v mode=get -v query_path=name "$PluginDir/plugin.json")" != agent-logger ]]; then
  refuse 'the selected payload is not Agent Logger.'
fi
execution_root="$HOME/.copilot-extensions/update-driver"
if ! acknowledgement="$(bash "$first_use" "$execution_root" "$PluginDir" "$InstallDir" "$project" \
    --installer-action "$installer_action")"; then
  refuse 'installation driver admission failed.'
fi
if [[ ! "$acknowledgement" =~ ^launched:([1-9][0-9]*)\ log:(.+)$ ]]; then
  refuse 'installation driver launch acknowledgement is malformed.'
fi
acknowledged_log="${BASH_REMATCH[2]}"
log_name="${acknowledged_log#"$execution_root/"}"
if [[ "$acknowledged_log" == "$log_name" ||
    ! "$log_name" =~ ^first-use-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.log$ ]]; then
  refuse 'installation driver launch diagnostics name another execution root.'
fi
printf '[agent-logger] bootstrap %s supervision launched; inspect first-use diagnostics in %s. Launch is not durable request acceptance.\n' \
  "$installer_action" "$execution_root" >&2
printf '[agent-logger] driver launch acknowledgement: %s\n' "$acknowledgement" >&2
exit 0
