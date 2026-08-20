#!/usr/bin/env bash
# readiness-context -- agent-* runtime sessionStart hook (bash).
#
# Emits an AFFIRMATIVE readiness confirmation as {"additionalContext": "..."} so
# a session (especially one where ONLY this plugin was installed) knows whether
# the plugin's CLI is actually usable, or what to do next.
#
# FAIL-CLOSED by design: only an explicit READY -- binstub present AND a
# current-version marker AND the versioned venv interpreter all found -- is
# reported ready. Anything else (a hook that half-ran, a background provision
# still in flight, a fresh install with no runtime) is reported NOT READY with
# the next correct step. Absence of an affirmative "ready" is treated as "not
# set up"; never infer ready from the mere absence of an error.
#
# MUST run even when the plugin's OWN runtime is not provisioned -- that is
# exactly the case it exists to report -- so it is pure shell + stdlib python
# (used ONLY to read plugin.json's name), never the plugin's venv. Generic and
# self-locating: byte-identical across the agent-* runtime plugins.
set -uo pipefail

# JSON-encode a plain string (backslashes, quotes, newlines) and emit the object.
jstr() { local s=${1//\\/\\\\}; s=${s//\"/\\\"}; s=${s//$'\n'/\\n}; printf '"%s"' "$s"; }
emit() { printf '{"additionalContext": %s}' "$(jstr "$1")"; exit 0; }

ScriptDir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PluginDir="$(cd "$ScriptDir/.." && pwd)"
py="$(command -v python3 || command -v python || true)"
name=""
[ -n "$py" ] && name="$("$py" -c 'import json,sys;print(json.load(open(sys.argv[1])).get("name",""))' "$PluginDir/plugin.json" 2>/dev/null)"
[ -n "$name" ] || name="$(basename "$PluginDir")"   # fallback: dir name
[ -n "$name" ] || exit 0

InstallDir="$HOME/.$name"
Binstub="$HOME/.local/bin/$name"
ver=""
[ -f "$InstallDir/current-version" ] && ver="$(tr -d ' \t\r\n' < "$InstallDir/current-version" 2>/dev/null)"

# READY iff the binstub exists AND a version is published AND its venv interpreter exists.
venv_ok=0
if [ -n "$ver" ]; then
  # READY iff the current-version marker's slot interpreter exists (marker-only;
  # the retired `.venv` link is no longer probed -- uniform-runtime-resolution #765).
  for sub in "versions/$ver/bin/python" "versions/$ver/Scripts/python.exe"; do
    [ -x "$InstallDir/$sub" ] && { venv_ok=1; break; }
  done
fi
if [ -x "$Binstub" ] && [ "$venv_ok" = 1 ]; then
  emit "$name: READY -- runtime $ver provisioned; the '$name' CLI is on PATH and usable."
fi

# NOT READY (fail-closed). Distinguish an in-flight/incomplete provision from a fresh install.
setup="bash \"$PluginDir/scripts/install.sh\" install"
if [ -f "$InstallDir/deploy-manifest.json" ] || [ -n "$ver" ]; then
  emit "$name: NOT READY -- runtime is provisioning or incomplete (its CLI is not yet on PATH). RESTART this session to pick it up; if it persists after a restart, run: $setup . Do NOT attempt $name operations until it reports READY."
fi
emit "$name: NOT READY -- runtime is not provisioned (fresh install; no CLI on PATH). Provision it by RESTARTING this session (first-session provisioning), or run: $setup . Do NOT attempt $name operations until it reports READY."
