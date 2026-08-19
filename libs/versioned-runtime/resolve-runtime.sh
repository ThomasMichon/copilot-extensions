# shellcheck shell=sh
# Canonical versioned-runtime resolver (POSIX sh) -- the single, uniform way a
# binstub, hook, or service launcher resolves a plugin's versioned interpreter.
# Source it after exporting the service root; it sets AGENT_RT_PY:
#
#   AGENT_RT_ROOT="$HOME/.agent-<svc>"
#   . <path>/resolve-runtime.sh
#   [ -n "$AGENT_RT_PY" ] && exec "$AGENT_RT_PY" -m <module> "$@"
#
# Junction-free and identical everywhere: resolves SOLELY the versioned slot
# python via the `current-version` marker, then `last-known-good`, then the
# newest installed slot. It NEVER resolves through a `venv`/`.venv` link (a
# reparse point on Windows that RedirectionGuard blocks, WinError 448) and NEVER
# falls back to a PATH python -- AGENT_RT_PY is empty when no runtime is
# installed, so the caller degrades deliberately (self-provision) instead of
# silently binding the system interpreter. Handles both slot layouts so it also
# works under git-bash on Windows: POSIX slots keep python at bin/python, Windows
# slots at Scripts/python.exe.
AGENT_RT_PY=""
_rt_root="${AGENT_RT_ROOT:-}"
if [ -n "$_rt_root" ]; then
  _rt_ver=""

  # -- helper: set AGENT_RT_PY from a version's slot python, if executable --
  _rt_try_slot() {
    [ -n "$1" ] || return 1
    for _rt_sub in bin/python Scripts/python.exe; do
      if [ -x "$_rt_root/versions/$1/$_rt_sub" ]; then
        AGENT_RT_PY="$_rt_root/versions/$1/$_rt_sub"; return 0
      fi
    done
    return 1
  }

  # Tier 1: the `current-version` marker (source of truth; atomically written).
  [ -f "$_rt_root/current-version" ] && \
    _rt_ver=$(tr -d ' \t\r\n' < "$_rt_root/current-version" 2>/dev/null)
  [ -n "$_rt_ver" ] && _rt_try_slot "$_rt_ver"

  # Tier 2: marker absent/stale -> the last version the installer activated.
  if [ -z "$AGENT_RT_PY" ] && [ -f "$_rt_root/last-known-good" ]; then
    _rt_lkg=$(tr -d ' \t\r\n' < "$_rt_root/last-known-good" 2>/dev/null)
    _rt_try_slot "$_rt_lkg"
  fi

  # Tier 3: true first-run (no marker, no last-known-good) -> newest slot.
  if [ -z "$AGENT_RT_PY" ]; then
    for _rt_p in "$_rt_root"/versions/*/bin/python "$_rt_root"/versions/*/Scripts/python.exe; do
      [ -x "$_rt_p" ] && AGENT_RT_PY="$_rt_p"
    done
  fi

  unset _rt_root _rt_ver _rt_lkg _rt_sub _rt_p 2>/dev/null || true
  unset -f _rt_try_slot 2>/dev/null || true
fi
