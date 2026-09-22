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
_awr="${AGENT_RT_ROOT:-$HOME/.agent-worktrees}"
_awv=""
_aw_trace_source=""
_aw_trace_version=""

_aw_boot_trace_ms() {
  _aw_bt_raw=""
  if _aw_bt_raw="$(date +%s%3N 2>/dev/null)"; then
    case "$_aw_bt_raw" in
      [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9])
        printf '%s\n' "$_aw_bt_raw"
        return 0
        ;;
    esac
  fi
  if _aw_bt_raw="$(date +%s%N 2>/dev/null)"; then
    case "$_aw_bt_raw" in
      [0-9]*)
        if [ "${#_aw_bt_raw}" -eq 19 ]; then
          printf '%s\n' "${_aw_bt_raw%??????}"
          return 0
        fi
        ;;
    esac
  fi
  if _aw_bt_raw="$(date +%s 2>/dev/null)"; then
    case "$_aw_bt_raw" in
      [0-9]*) printf '%s000\n' "$_aw_bt_raw"; return 0 ;;
    esac
  fi
  printf '0000000000000\n'
}

_aw_boot_trace() {
  [ -n "${COPILOT_EXTENSIONS_BOOT_TRACE:-}" ] || return 0
  _aw_bt_plugin="${COPILOT_EXTENSIONS_BOOT_TRACE_PLUGIN:-${_awr##*/}}"
  _aw_bt_plugin="${_aw_bt_plugin#.}"
  _aw_bt_extra="${2-}"
  if [ -n "$_aw_bt_extra" ]; then
    printf '::boot-trace:: plugin=%s phase=%s t=%s %s\n' \
      "$_aw_bt_plugin" "$1" "$(_aw_boot_trace_ms)" "$_aw_bt_extra" >&2
  else
    printf '::boot-trace:: plugin=%s phase=%s t=%s\n' \
      "$_aw_bt_plugin" "$1" "$(_aw_boot_trace_ms)" >&2
  fi
}

_aw_marker_valid() {
  [ -n "$1" ] || return 1
  awk -v expected="$1" '
    NR != 1 { bad = 1 }
    NR == 1 {
      if ($0 !~ /^\{"version": "[^"\\]+", "completed_at": "[^"\\]+", "pid": (0|[1-9][0-9]*)(, "payload_hash": "[^"\\]+")?\}$/) {
        bad = 1; next
      }
      version = $0
      sub(/^\{"version": "/, "", version)
      sub(/".*$/, "", version)
    }
    END { exit !(NR == 1 && !bad && version == expected) }
  ' "$_awr/versions/$1/.install-complete.json" 2>/dev/null
}

# -- helper: set AW_PY from a complete version's slot python --
_aw_try_slot() {
  [ -n "$1" ] || return 1
  _aw_marker_valid "$1" || return 1
  for _sub in bin/python Scripts/python.exe; do
    if [ -x "$_awr/versions/$1/$_sub" ]; then AW_PY="$_awr/versions/$1/$_sub"; return 0; fi
  done
  return 1
}

_aw_version_key() {
  awk '
    {
      original = $0
      if (original ~ /^[0-9]+\.[0-9]+\.[0-9]+(-dev[0-9]+)?$/) {
        count = split(original, part, /[.-]/)
        phase = (count == 4) ? 0 : 1
        dev = (count == 4) ? part[4] : "dev0"
        sub(/^dev/, "", dev)
        printf "0:%020d.%020d.%020d.%d.%020d\t%s\n", \
          part[1] + 0, part[2] + 0, part[3] + 0, phase, dev + 0, original
        next
      }
      key = ""; rest = $0
      while (match(rest, /[0-9]+/)) {
        key = key substr(rest, 1, RSTART - 1)
        number = substr(rest, RSTART, RLENGTH)
        key = key sprintf("%020d", number + 0)
        rest = substr(rest, RSTART + RLENGTH)
      }
      print "1:" key rest "\t" original
    }
  '
}

# Tier 1: the `current-version` marker (source of truth; atomically written).
_aw_boot_trace resolver-marker-start 'source=current-version'
[ -f "$_awr/current-version" ] && _awv=$(tr -d ' \t\r\n' < "$_awr/current-version" 2>/dev/null)
[ -n "$_awv" ] && _aw_try_slot "$_awv"
if [ -n "$AW_PY" ]; then
  _aw_trace_source="current-version"
  _aw_trace_version="$_awv"
  _aw_boot_trace resolver-marker-result \
    "source=current-version result=hit version=$_awv"
else
  _aw_boot_trace resolver-marker-result 'source=current-version result=miss'
fi

# Tier 2: marker absent/stale -> the last version the installer activated
# (`last-known-good`), preferred over a newest-slot guess. Read only here, so the
# common tier-1 path pays nothing.
if [ -z "$AW_PY" ] && [ -f "$_awr/last-known-good" ]; then
  _aw_boot_trace resolver-marker-start 'source=last-known-good'
  _awlkg=$(tr -d ' \t\r\n' < "$_awr/last-known-good" 2>/dev/null)
  _aw_try_slot "$_awlkg"
  if [ -n "$AW_PY" ]; then
    _aw_trace_source="last-known-good"
    _aw_trace_version="$_awlkg"
    _aw_boot_trace resolver-marker-result \
      "source=last-known-good result=hit version=$_awlkg"
  else
    _aw_boot_trace resolver-marker-result 'source=last-known-good result=miss'
  fi
fi

# Tier 3: true first-run -> newest complete installed slot.
if [ -z "$AW_PY" ]; then
  for _awv in $(
    for _slot in "$_awr"/versions/*; do
      [ -d "$_slot" ] || continue
      printf '%s\n' "${_slot##*/}"
    done | _aw_version_key | LC_ALL=C sort | cut -f2-
  ); do
    if _aw_try_slot "$_awv"; then
      _aw_trace_source="newest"
      _aw_trace_version="$_awv"
    fi
  done
fi
if [ -n "$AW_PY" ]; then
  _aw_boot_trace resolver-slot-result \
    "source=$_aw_trace_source result=resolved version=$_aw_trace_version"
else
  _aw_boot_trace resolver-slot-result 'source=none result=miss'
fi
AGENT_RT_PY="$AW_PY"
unset _awr _awv _awlkg _sub _slot _aw_trace_source _aw_trace_version \
  _aw_bt_raw _aw_bt_plugin _aw_bt_extra 2>/dev/null || true
unset -f _aw_marker_valid _aw_try_slot _aw_version_key \
  _aw_boot_trace_ms _aw_boot_trace 2>/dev/null || true
