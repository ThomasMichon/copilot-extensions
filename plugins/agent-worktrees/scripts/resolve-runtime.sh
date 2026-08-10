# shellcheck shell=sh
# Canonical agent-worktrees runtime resolver -- sourced by the POSIX hooks and
# binstubs. Sets AW_PY to the runtime slot python resolved SOLELY via the
# junction-free `current-version` marker (the single source of truth; #1106):
#
#   ~/.agent-worktrees/current-version  ->  versions/<ver>/{bin/python|Scripts/python.exe}
#
# Nothing resolves through the retired `.venv` link. AW_PY is empty when no
# runtime slot is installed (callers degrade gracefully / no-op).
#
# Handles both slot layouts so it also works under git-bash on Windows (the git
# pre-commit/pre-push shims run there): POSIX slots keep python at bin/python,
# Windows slots at Scripts/python.exe.
AW_PY=""
_awr="$HOME/.agent-worktrees"
_awv=""
[ -f "$_awr/current-version" ] && _awv=$(tr -d ' \t\r\n' < "$_awr/current-version" 2>/dev/null)
if [ -n "$_awv" ]; then
  for _sub in bin/python Scripts/python.exe; do
    if [ -x "$_awr/versions/$_awv/$_sub" ]; then AW_PY="$_awr/versions/$_awv/$_sub"; break; fi
  done
fi
if [ -z "$AW_PY" ]; then
  # Marker absent/stale -> newest installed slot (best-effort belt).
  for _p in "$_awr"/versions/*/bin/python "$_awr"/versions/*/Scripts/python.exe; do
    [ -x "$_p" ] && AW_PY="$_p"
  done
fi
unset _awr _awv _sub _p 2>/dev/null || true
