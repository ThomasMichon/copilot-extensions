# shellcheck shell=sh
# Canonical agent-worktrees runtime resolver -- sourced by the POSIX hooks and
# binstubs. Sets AW_PY and the payload-invocation contract's AGENT_RT_PY to the
# runtime slot python resolved via the junction-free `current-version` marker
# (the single source of truth; #1106):
#
#   ~/.agent-worktrees/current-version  ->  versions/<ver>/{bin/python|Scripts/python.exe}
#
# Nothing resolves through the retired `.venv` link. Both variables are empty
# when no runtime slot is installed (callers degrade gracefully / no-op).
#
# Resolution order (#742): the marker is written atomically (temp + rename), so
# it is never observed half-written or transiently absent during a swap. When it
# IS absent, a call must not silently bind a *different* (possibly still-
# installing) slot -- so the fallback prefers the `last-known-good` version (the
# last version the installer activated) over a newest-slot guess, and only
# guesses the newest slot on a true first-run (no marker and no last-known-good).
# The hot path (a present, resolvable marker) is unchanged: last-known-good is
# read only when the marker fails.
#
# Handles both slot layouts so it also works under git-bash on Windows (the git
# pre-commit/pre-push shims run there): POSIX slots keep python at bin/python,
# Windows slots at Scripts/python.exe.
AW_PY=""
_awr="$HOME/.agent-worktrees"
_awv=""

# -- helper: set AW_PY from a version's slot python, if executable --
_aw_try_slot() {
  [ -n "$1" ] || return 1
  for _sub in bin/python Scripts/python.exe; do
    if [ -x "$_awr/versions/$1/$_sub" ]; then AW_PY="$_awr/versions/$1/$_sub"; return 0; fi
  done
  return 1
}

# Tier 1: the `current-version` marker (source of truth; atomically written).
[ -f "$_awr/current-version" ] && _awv=$(tr -d ' \t\r\n' < "$_awr/current-version" 2>/dev/null)
[ -n "$_awv" ] && _aw_try_slot "$_awv"

# Tier 2: marker absent/stale -> the last version the installer activated
# (`last-known-good`), preferred over a newest-slot guess. Read only here, so the
# common tier-1 path pays nothing.
if [ -z "$AW_PY" ] && [ -f "$_awr/last-known-good" ]; then
  _awlkg=$(tr -d ' \t\r\n' < "$_awr/last-known-good" 2>/dev/null)
  _aw_try_slot "$_awlkg"
fi

# Tier 3: true first-run (no marker, no last-known-good) -> newest installed slot.
if [ -z "$AW_PY" ]; then
  for _p in "$_awr"/versions/*/bin/python "$_awr"/versions/*/Scripts/python.exe; do
    [ -x "$_p" ] && AW_PY="$_p"
  done
fi
AGENT_RT_PY="$AW_PY"
unset _awr _awv _awlkg _sub _p 2>/dev/null || true
unset -f _aw_try_slot 2>/dev/null || true
