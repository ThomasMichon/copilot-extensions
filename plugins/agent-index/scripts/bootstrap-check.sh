#!/usr/bin/env bash
# Only the dependency-light metadata adapter is admitted at session start.
set -uo pipefail
script_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
emit_session_start_json() { printf '{}'; }
trap 'emit_session_start_json' EXIT
# The machine switch governs only the default installation, never another cell.
if [ -z "${COPILOT_EXTENSIONS_CONTEXT:-}" ]; then
  _bks_guard="$script_dir/bootstrap-killswitch-guard.sh"
  if [ ! -f "$_bks_guard" ]; then
    printf 'agent-index bootstrap-killswitch guard missing: %s\n' "$_bks_guard" >&2
    exit 1
  fi
  if ! bash -n "$_bks_guard" >&2; then
    printf 'agent-index bootstrap-killswitch guard malformed: %s\n' "$_bks_guard" >&2
    exit 1
  fi
  bash "$_bks_guard" check >&2
  guard_rc=$?
  case "$guard_rc" in
    0) exit 0 ;;
    1) ;;
    *) printf 'agent-index bootstrap-killswitch guard failed (exit %s)\n' "$guard_rc" >&2
       exit "$guard_rc" ;;
  esac
fi
bash "$script_dir/runtime-gate.sh" __session-stamp >/dev/null
rc=$?
exit "$rc"
