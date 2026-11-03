#!/usr/bin/env bash
# bootstrap-killswitch-guard.sh -- canonical, vendored byte-identically into
# every plugin that ships a sessionStart bootstrap-check hook (see
# tools/sync-bootstrap-killswitch.py). Lets an operator/agent pause EVERY
# plugin's automatic reconcile-on-session-start at once, with one switch, so
# a hand-diagnosis of a venv/install (walking it manually, comparing paths,
# re-running an installer step by step) never races a background reconcile
# silently redoing/undoing the same work underneath it.
#
# State lives in ONE shared, cross-plugin file -- not per-plugin -- so a
# single on/off affects every plugin's hook uniformly:
#   ~/.copilot-extensions/killSwitches/bootstrap-killswitch.json
#   {"active": true, "reason": "...", "set_by": "...", "set_at": "..."}
#
# Primary usage -- as a subprocess, from the FIRST lines of any
# bootstrap-check.sh, before any version/reconcile logic runs:
#   if bash "$ScriptDir/bootstrap-killswitch-guard.sh" check >&2; then
#     printf '{}'; exit 0
#   fi
# Exit 0  -> killswitch is ACTIVE; caller must skip its own reconcile.
# Exit 1  -> killswitch is INACTIVE, or the state file is missing/unreadable/
#            malformed. Fails OPEN to "inactive" deliberately: a corrupt or
#            stale state file must never permanently wedge every plugin's
#            reconcile -- the failure mode is "reconcile still runs", not
#            "every plugin is silently stuck forever".
#
# Secondary usage -- direct operator/agent control (also reachable via
# `agent-machines bootstrap-killswitch {on,off,status}`, which writes/reads
# this exact same state file):
#   bootstrap-killswitch-guard.sh on "<reason>"
#   bootstrap-killswitch-guard.sh off
#   bootstrap-killswitch-guard.sh status
#
# `on`/`off` run under `set -e`: a failed mkdir/write/rm now aborts BEFORE
# the success message prints, so an operator is never told the switch
# changed when it didn't. The state write itself is atomic (write to a
# same-directory temp file, then rename) so a concurrent `check` never
# observes a torn/partial write mid-update -- it was a real race before:
# writing the file in place left a window where every hook's `check` would
# see empty/truncated JSON, fail OPEN, and let a reconcile through even
# though the switch was (or was about to be) active.
set -euo pipefail

if [ "${1:-check}" = "capabilities" ]; then
  if [ "$#" -ne 1 ]; then
    echo "[bootstrap-killswitch] capabilities does not accept mutation arguments." >&2
    exit 2
  fi
  [ -f "$(dirname "${BASH_SOURCE[0]}")/killswitch_mutation.py" ] ||
    { echo "canonical mutation helper unavailable" >&2; exit 1; }
  printf '{"schema":"copilot-extensions.bootstrap-killswitch-controller","version":1,"capabilities":["conditional-mutation-v1"]}\n'
  exit 0
fi

_py() { command -v python3 2>/dev/null || command -v python 2>/dev/null || true; }

StateFile="${BOOTSTRAP_KILLSWITCH_STATE_FILE:-$HOME/.copilot-extensions/killSwitches/bootstrap-killswitch.json}"
if [ -n "${BOOTSTRAP_KILLSWITCH_STATE_FILE:-}" ]; then
  py="$(_py)"
  if [ -n "$py" ]; then
    StateFile="$("$py" -c '
from pathlib import Path
import sys
try:
    path = Path(sys.argv[1]).expanduser()
    print(path.parent.resolve() / path.name)
except (OSError, RuntimeError) as error:
    print(f"[bootstrap-killswitch] invalid state-file override: {error}", file=sys.stderr)
    raise SystemExit(2)
' "$StateFile")"
  elif [[ "$StateFile" == '~'* ]]; then
    echo "[bootstrap-killswitch] error: Python is required to resolve the state-file home override." >&2
    exit 1
  fi
fi
LegacyStateFile=""
if [ -z "${BOOTSTRAP_KILLSWITCH_STATE_FILE:-}" ]; then
  LegacyStateFile="$HOME/.copilot-extensions/bootstrap-killswitch.json"
fi
ReadStateFile="$StateFile"
if [ ! -e "$StateFile" ] && [ -n "$LegacyStateFile" ] && [ -f "$LegacyStateFile" ]; then
  ReadStateFile="$LegacyStateFile"
fi

_is_active() {
  local py; py="$(_py)"
  [ -n "$py" ] || { echo "false"; return; }
  "$py" -c '
import json, sys
try:
    with open(sys.argv[1], "r", encoding="utf-8") as fh:
        data = json.load(fh)
    print("true" if data.get("active") is True else "false")
except Exception:
    print("false")
' "$ReadStateFile" 2>/dev/null || echo "false"
}

_reason() {
  local py; py="$(_py)"
  [ -n "$py" ] || { echo ""; return; }
  "$py" -c '
import json, sys
try:
    with open(sys.argv[1], "r", encoding="utf-8") as fh:
        print(json.load(fh).get("reason", "") or "")
except Exception:
    print("")
' "$ReadStateFile" 2>/dev/null || echo ""
}

_live_reconciling_plugins() {
  # Best-effort: the switch only prevents a NEW reconcile from starting; it
  # cannot stop one already running (a detached background process that
  # outlives the session-start hook that spawned it). Most adopters guard
  # their own background reconcile with a `~/.<plugin>/reconcile.lock`
  # single-flight file naming the live PID -- check that convention across
  # every such lock this host knows about. Not exhaustive: a handful of
  # adopters (agent-bridge, agent-machines, agent-worktrees) don't use this
  # exact lock convention and aren't detected here -- see README.md's Known
  # limitations.
  local scan_root lock pid name
  scan_root="${BOOTSTRAP_KILLSWITCH_RECONCILE_SCAN_ROOT:-$HOME}"
  for lock in "$scan_root"/.*/reconcile.lock; do
    [ -f "$lock" ] || continue
    pid="$(tr -d '[:space:]' < "$lock" 2>/dev/null)"
    [ -n "$pid" ] || continue
    kill -0 "$pid" 2>/dev/null || continue
    name="$(basename "$(dirname "$lock")")"
    echo "${name#.}"
  done
}

cmd="${1:-check}"
reason=""
expected=""
if [ "$#" -gt 0 ]; then shift; fi
if [ "$cmd" = "on" ] && [ "$#" -gt 0 ] && [ "$1" != "--expected-state" ]; then
  reason="$1"; shift
fi
if [ "$#" -gt 0 ]; then
  if { [ "$cmd" != "on" ] && [ "$cmd" != "off" ]; } ||
      [ "$#" -ne 2 ] || [ "$1" != "--expected-state" ] ||
      [[ ! "$2" =~ ^[a-f0-9]{64}$ ]]; then
    echo "usage: $(basename "$0") {check|status|capabilities|on <reason>|off} [--expected-state REVISION]" >&2
    exit 2
  fi
  expected="$2"
fi
mutation_helper="$(dirname "${BASH_SOURCE[0]}")/killswitch_mutation.py"
case "$cmd" in
  check)
    if [ -f "$ReadStateFile" ] && [ "$(_is_active)" = "true" ]; then
      r="$(_reason)"
      echo "[bootstrap-killswitch] ACTIVE${r:+ -- $r}; skipping automatic reconcile." >&2
      exit 0
    fi
    exit 1
    ;;
  status)
    if [ -f "$ReadStateFile" ]; then cat "$ReadStateFile"; else printf '{"active": false}\n'; fi
    ;;
  on)
    py="$(_py)"
    if [ -z "$py" ]; then
      echo "[bootstrap-killswitch] error: no python3/python on PATH; cannot write state." >&2
      exit 1
    fi
    "$py" "$mutation_helper" on "$StateFile" "" "$expected" \
      "${USER:-unknown}@$(hostname 2>/dev/null || echo unknown)" "$reason"
    echo "[bootstrap-killswitch] ACTIVE${reason:+ -- $reason}."
    echo "Every plugin's sessionStart reconcile is now paused on this machine."
    _live="$(_live_reconciling_plugins)"
    if [ -n "$_live" ]; then
      echo "WARNING: the switch only prevents a NEW reconcile from starting -- it"
      echo "cannot stop one already running. These plugin(s) have a reconcile in"
      echo "flight right now (detached; outlives this command):"
      echo "$_live" | sed 's/^/  - /'
      echo "Wait for it/them to finish before treating their venv as settled."
    fi
    echo "Reset with: agent-machines bootstrap-killswitch off"
    ;;
  off)
    py="$(_py)"
    if [ -z "$py" ]; then
      echo "[bootstrap-killswitch] error: no python3/python on PATH; cannot clear state." >&2
      exit 1
    fi
    if [ -n "$expected" ] && [ -n "$LegacyStateFile" ]; then
      echo "[bootstrap-killswitch] conditional mutation requires an explicit state-file override." >&2
      exit 2
    fi
    "$py" "$mutation_helper" off "$StateFile" "$LegacyStateFile" "$expected" "" ""
    echo "[bootstrap-killswitch] cleared. Plugins resume normal sessionStart reconcile."
    ;;
  *)
    echo "usage: $(basename "$0") {check|status|capabilities|on <reason>|off} [--expected-state REVISION]" >&2
    exit 2
    ;;
esac
