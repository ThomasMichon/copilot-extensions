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

# Session-start runtime bootstrap + reconcile -- generic, self-locating. Invoked
# (via hooks.json) from the plugin's scripts/ dir. Derives the install dir from
# plugin.json's name (~/.<name>). Two jobs, both grace-window-cheap:
#   1. FIRST install (unprovisioned): if the installer supports a cheap 'stamp'
#      action, publish the prepared-only binstub now (deferring the venv build
#      to explicit snapshot provision) so the CLI is on PATH this session -- no venv
#      build on the hook. Installers without 'stamp' keep the old no-op.
#   2. RECONCILE (already provisioned): re-run the installer in the BACKGROUND
#      only when the deployed version drifts. Reconciles the TOOL, never state.
#
# NO OPT-IN GATE (agent-bridge-unified-zdd-cutover Phase 0): background
# reconcile used to require a checked-in, per-project opt-in flag in
# <project>/.copilot-extensions/config.yaml, because a raw reconcile could
# race a live daemon/session. Now that every reconcile-capable plugin's
# update path is always-ZDD (safe to run unattended), that justification is
# gone; the gate was removed rather than kept as a redundant consent
# checkbox. Frequency/trigger stays deliberately bounded -- still only once
# per session start, only on a real version drift. A legacy reconcile record
# defers automatic work; it cannot prove process ownership or authorize retirement.
ScriptDir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PluginDir="$(cd "$ScriptDir/.." && pwd)"
session_start_json_emitted=0
emit_session_start_json() {
  if [ "${session_start_json_emitted:-0}" -eq 0 ]; then
    printf '{}'
    session_start_json_emitted=1
  fi
}
trap 'emit_session_start_json' EXIT
name="budget-guidance"
InstallDir="$HOME/.$name"
if [[ -n "${COPILOT_EXTENSIONS_CONTEXT:-}" ]]; then exit 0; fi
defer_if_disabled() {
  if [[ -n "${BUDGET_GUIDANCE_NO_SELFPROVISION:-}" || -n "${BUDGET_GUIDANCE_PINNED_CONTEXT+x}" ]]; then
    printf '[%s] bootstrap deferred: self-provisioning is disabled or identity reads are pinned.\n' "$name" >&2
    exit 0
  fi
}
assert_bootstrap_publication() {
  local helper="$ScriptDir/installation-context/plugin-publication-probe.sh"
  if [[ -f "$helper" ]]; then
    bash "$helper" "$PluginDir" "$name" "$InstallDir" && return 0
  else
    local policy="$InstallDir/runtime-deployment.json" ancestor="$InstallDir"
    while [[ ! -e "$ancestor" && ! -L "$ancestor" ]]; do ancestor="$(dirname -- "$ancestor")"; done
    if [[ -d "$ancestor" && -r "$ancestor" && -x "$ancestor" &&
          ! -e "$policy" && ! -L "$policy" ]]; then return 0; fi
    printf '[%s] Runtime publication refused: deployment policy requires a capable installation helper.\n' "$name" >&2
  fi
  printf '[%s] Runtime publication refused: bootstrap admission did not succeed. Use explicit installation management to repair it.\n' "$name" >&2
  exit 3
}
Manifest="$InstallDir/deploy-manifest.json"
if [[ ! -e "$Manifest" && ! -L "$Manifest" ]]; then
  defer_if_disabled
  # Not provisioned yet -- do the cheap FIRST install (stamp) so the binstub is
  # callable this session; it reports the required explicit provision action. Fully
  # back-compatible: only fires when the installer declares a 'stamp' action.
  installer=""
  for candidate in "$PluginDir/scripts/init.sh" "$PluginDir/scripts/install.sh"; do
    if [ -f "$candidate" ] && grep -qE '^[[:space:]]*([[:alnum:]_-]+\|)*stamp(\|[[:alnum:]_-]+)*\)' "$candidate" 2>/dev/null; then installer="$candidate"; break; fi
  done
  if [ -n "$installer" ]; then
    assert_bootstrap_publication
    bash "$installer" stamp >/dev/null 2>&1 || true
  fi
  exit 0
fi
query="$ScriptDir/installation-context/json-query.awk"
if [[ ! -f "$Manifest" || -L "$Manifest" || ! -f "$query" ||
      "$(LC_ALL=C wc -c < "$Manifest")" -gt 65536 ]]; then
  printf '[%s] Runtime publication refused: bounded deployment metadata is unavailable.\n' "$name" >&2
  exit 3
fi
deployed="$(LC_ALL=C awk -f "$query" -v mode=deployment-string \
  -v query_path=$'source\034version' "$Manifest" 2>/dev/null)" || {
    printf '[%s] Runtime publication refused: the deployed runtime version is unavailable.\n' "$name" >&2
    exit 3
  }
[ -n "$deployed" ] || {
  echo "[$name] deploy manifest has no readable source version; skipping reconcile." >&2
  exit 0
}
current="$deployed"
pyproj="$PluginDir/pyproject.toml"
if [ -f "$pyproj" ]; then
  v="$(grep -m1 -E '^[[:space:]]*version[[:space:]]*=' "$pyproj" | sed -E 's/.*=[[:space:]]*"([^"]+)".*/\1/')"
  [ -n "$v" ] && current="$v"
fi
# "Provisioned" no longer implies a .venv: the marker runtime model (#581) publishes
# the active slot via a current-version marker (POSIX keeps a .venv symlink, but a
# marker+slot is authoritative). Treat a marker whose slot python exists as
# provisioned too, so a current runtime is a clean no-op, not a needless rebuild.
runtime_version="$(LC_ALL=C awk -f "$query" -v mode=deployment-string \
  -v query_path=runtime_version "$Manifest" 2>/dev/null || true)"
managed_runtime_current() {
  local bootstrap="${COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON:-}" result=0
  if [[ -z "$bootstrap" ]]; then
    [[ "$(LC_ALL=C awk -f "$query" -v mode=deployment-string -v query_path=service "$Manifest")" == "$name" ]] ||
      return 1
    bootstrap="$(LC_ALL=C awk -f "$query" -v mode=deployment-string \
      -v query_path=bootstrap_python "$Manifest")" || return 1
  fi
  [[ "$bootstrap" == /* && -x "$bootstrap" && "$bootstrap" != *$'\n'* &&
      "$bootstrap" != *$'\r'* ]] || return 1
  . "$ScriptDir/bootstrap-uv.sh" || return 1
  invoke_uv_bootstrap_environment "$bootstrap" -I -S -B "$ScriptDir/bootstrap_uv_capture.py" \
    --timeout 6 --cleanup-reserve 1 --parent-pid "$$" -- \
    "$BASH" -c '
      set -euo pipefail
      . "$1/bootstrap-uv.sh"
      selected="$(resolve_uv_bootstrap_python "$1/bootstrap-python-version.txt" Inspect </dev/null)" || exit 78
      invoke_uv_bootstrap_environment "$selected" -I -B "$1/install_source.py" \
        --native-current --root "$2" --payload "$3" --component budget-guidance
    ' budget-guidance-readiness "$ScriptDir" "$InstallDir" "$PluginDir" >/dev/null || result=$?
  case "$result" in
    0) return 0 ;;
    1|78) return 1 ;;
    *) printf '[%s] Managed runtime readiness failed (exit %s).\n' "$name" "$result" >&2; exit 3 ;;
  esac
}
provisioned=0
if [[ "$runtime_version" =~ \.profile\.[0-9a-f]{16} ]]; then
  if managed_runtime_current; then provisioned=1; fi
else
[ -e "$InstallDir/.venv" ] && provisioned=1
if [ "$provisioned" = 0 ] && [ -f "$InstallDir/current-version" ]; then
  cv="$(tr -d '[:space:]' < "$InstallDir/current-version")"
  # ...and only when it names the CURRENT payload version (the marker is authoritative
  # for the active slot; a stale marker must not suppress reconcile).
  if [ -n "$cv" ] && [ "$cv" = "$current" ] && { [ -x "$InstallDir/versions/$cv/bin/python" ] || [ -f "$InstallDir/versions/$cv/Scripts/python.exe" ]; }; then provisioned=1; fi
fi
fi
if [ "$provisioned" = 1 ] && [ "$deployed" = "$current" ]; then exit 0; fi
defer_if_disabled

if [ -f "$PluginDir/scripts/init.sh" ]; then
  target=("$PluginDir/scripts/init.sh")
elif [ -f "$PluginDir/scripts/install.sh" ]; then
  target=("$PluginDir/scripts/install.sh" install)
else
  exit 0
fi

assert_bootstrap_publication

# A PID and file age cannot identify an owned process, even if it is live.
# Keep automatic work off an unsettled legacy owner; explicit setup is separate.
lockFile="$InstallDir/reconcile.lock"
if [[ -e "$lockFile" || -L "$lockFile" ]]; then
  printf '[%s] bootstrap deferred: legacy reconcile ownership is unsettled; use explicit installation management.\n' "$name" >&2
  exit 0
fi

if [[ "$(uname -s)" == Linux ]]; then
  [[ -z "${UPDATE_DRIVER_WITNESS+x}" ]] || {
    printf '[%s] Nested bootstrap submission is unsupported; retain the current installation owner.\n' "$name" >&2
    exit 3
  }
  if (( BASH_VERSINFO[0] < 4 || (BASH_VERSINFO[0] == 4 && BASH_VERSINFO[1] < 2) )); then
    printf '[%s] Native installation admission requires qualified Linux Bash.\n' "$name" >&2
    exit 3
  fi
  if [[ "$(dirname -- "$(dirname -- "$PluginDir")")" != "$HOME/.copilot/installed-plugins" ||
        "$(basename -- "$PluginDir")" != "$name" ]]; then
    printf '[%s] Budget bootstrap publication requires its canonical installed payload.\n' "$name" >&2
    exit 3
  fi
  first_use="$PluginDir/libs/update-driver/scripts/first-use.sh"
  if [[ ! -f "$first_use" ]] ||
      ! grep -Fq '# Installation driver lifecycle contract: budget-install-v1' "$first_use"; then
    printf '[%s] The packaged installation driver lacks the Budget install lifecycle contract.\n' "$name" >&2
    exit 3
  fi
  execution_root="$HOME/.copilot-extensions/update-driver"
  project="$(pwd -P)" || exit 3
  if ! acknowledgement="$(bash "$first_use" "$execution_root" "$PluginDir" "$InstallDir" "$project" \
      --installer-action install </dev/null)"; then
    printf '[%s] Installation driver admission failed.\n' "$name" >&2
    exit 3
  fi
  if [[ ! "$acknowledgement" =~ ^launched:([1-9][0-9]*)\ log:(.+)$ ]]; then
    printf '[%s] Installation driver launch acknowledgement is malformed.\n' "$name" >&2
    exit 3
  fi
  acknowledged_log="${BASH_REMATCH[2]}"
  log_name="${acknowledged_log#"$execution_root/"}"
  if [[ "$acknowledged_log" == "$log_name" ||
        ! "$log_name" =~ ^first-use-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.log$ ]]; then
    printf '[%s] Installation driver launch diagnostics name another execution root.\n' "$name" >&2
    exit 3
  fi
  printf '[%s] bootstrap install supervision launched; inspect first-use diagnostics in %s. Launch is not durable request acceptance.\n' \
    "$name" "$execution_root" >&2
  printf '[%s] driver launch acknowledgement: %s\n' "$name" "$acknowledgement" >&2
  exit 0
fi

# macOS retains its existing installer path until native driver admission is qualified.
echo "[$name] runtime $deployed -> $current; reconciling in background..." >&2
cd "$HOME" || { echo "reconcile launch failed: HOME is unavailable" >&2; exit 1; }
nohup bash "${target[@]}" >/dev/null 2>&1 &
echo $! > "$lockFile" 2>/dev/null || true
exit 0