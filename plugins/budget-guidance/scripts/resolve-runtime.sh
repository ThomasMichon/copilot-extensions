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
  _rt_trace_source=""
  _rt_trace_version=""
  _rt_bt_plugin="${COPILOT_EXTENSIONS_BOOT_TRACE_PLUGIN:-${_rt_root##*/}}"
  _rt_bt_plugin="${_rt_bt_plugin#.}"
  _rt_bt_log_path="${COPILOT_EXTENSIONS_BOOT_TRACE_LOG_PATH:-$_rt_root/logs/boot-trace.jsonl}"

  _rt_boot_trace_ms() {
    _rt_bt_raw=""
    if _rt_bt_raw="$(date +%s%3N 2>/dev/null)"; then
      case "$_rt_bt_raw" in
        [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9])
          printf '%s\n' "$_rt_bt_raw"
          return 0
          ;;
      esac
    fi
    if _rt_bt_raw="$(date +%s%N 2>/dev/null)"; then
      case "$_rt_bt_raw" in
        [0-9]*)
          if [ "${#_rt_bt_raw}" -eq 19 ]; then
            printf '%s\n' "${_rt_bt_raw%??????}"
            return 0
          fi
          ;;
      esac
    fi
    if _rt_bt_raw="$(date +%s 2>/dev/null)"; then
      case "$_rt_bt_raw" in
        [0-9]*) printf '%s000\n' "$_rt_bt_raw"; return 0 ;;
      esac
    fi
    printf '0000000000000\n'
  }

  _rt_boot_trace_iso() {
    if _rt_bt_iso="$(date -u +%Y-%m-%dT%H:%M:%S+00:00 2>/dev/null)"; then
      printf '%s\n' "$_rt_bt_iso"
      return 0
    fi
    printf '1970-01-01T00:00:00+00:00\n'
  }

  _rt_boot_trace_log() {
    [ -n "${_rt_bt_log_path:-}" ] || return 0
    [ -d "$_rt_root" ] || return 0
    _rt_phase="$1"
    _rt_now_ms="$2"
    _rt_resolution_source="${3-}"
    _rt_result="${4-}"
    _rt_version="${5-}"
    _rt_bt_log_dir="${_rt_bt_log_path%/*}"
    if [ ! -d "$_rt_bt_log_dir" ]; then
      mkdir -p "$_rt_bt_log_dir" 2>/dev/null || return 0
    fi
    _rt_bt_line="{\"ts\":\"$(_rt_boot_trace_iso)\",\"event\":\"boot_trace\",\"plugin\":\"$_rt_bt_plugin\",\"phase\":\"$_rt_phase\",\"t_ms\":$_rt_now_ms,\"pid\":$$"
    if [ -n "${HOSTNAME:-}" ]; then
      _rt_bt_line="$_rt_bt_line,\"host\":\"$HOSTNAME\""
    fi
    _rt_bt_line="$_rt_bt_line,\"source\":\"resolver\""
    if [ -n "$_rt_resolution_source" ]; then
      _rt_bt_line="$_rt_bt_line,\"resolution_source\":\"$_rt_resolution_source\""
    fi
    if [ -n "$_rt_result" ]; then
      _rt_bt_line="$_rt_bt_line,\"result\":\"$_rt_result\""
    fi
    if [ -n "$_rt_version" ]; then
      _rt_bt_line="$_rt_bt_line,\"version\":\"$_rt_version\""
    fi
    _rt_bt_line="$_rt_bt_line}"
    { printf '%s\n' "$_rt_bt_line" >> "$_rt_bt_log_path"; } 2>/dev/null || true
  }

  _rt_boot_trace() {
    _rt_phase="$1"
    _rt_resolution_source="${2-}"
    _rt_result="${3-}"
    _rt_version="${4-}"
    _rt_now_ms="$(_rt_boot_trace_ms)"
    _rt_boot_trace_log \
      "$_rt_phase" "$_rt_now_ms" "$_rt_resolution_source" "$_rt_result" "$_rt_version"
    [ -n "${COPILOT_EXTENSIONS_BOOT_TRACE:-}" ] || return 0
    _rt_bt_extra=""
    if [ -n "$_rt_resolution_source" ]; then
      _rt_bt_extra="source=$_rt_resolution_source"
    fi
    if [ -n "$_rt_result" ]; then
      _rt_bt_extra="${_rt_bt_extra}${_rt_bt_extra:+ }result=$_rt_result"
    fi
    if [ -n "$_rt_version" ]; then
      _rt_bt_extra="${_rt_bt_extra}${_rt_bt_extra:+ }version=$_rt_version"
    fi
    if [ -n "$_rt_bt_extra" ]; then
      printf '::boot-trace:: plugin=%s phase=%s t=%s %s\n' \
        "$_rt_bt_plugin" "$_rt_phase" "$_rt_now_ms" "$_rt_bt_extra" >&2
    else
      printf '::boot-trace:: plugin=%s phase=%s t=%s\n' \
        "$_rt_bt_plugin" "$_rt_phase" "$_rt_now_ms" >&2
    fi
  }

  _rt_marker_valid() {
    [ -n "$1" ] || return 1
    awk -v expected="$1" '
      NR != 1 { bad = 1 }
      NR == 1 {
        if ($0 !~ /^\{"version": "[^"\\]+", "completed_at": "[^"\\]+", "pid": (0|[1-9][0-9]*)(, "payload_hash": "[^"\\]+")?\}$/) {
          bad = 1
          next
        }
        version = $0
        sub(/^\{"version": "/, "", version)
        sub(/".*$/, "", version)
      }
      END { exit !(NR == 1 && !bad && version == expected) }
    ' "$_rt_root/versions/$1/.install-complete.json" 2>/dev/null
  }

  # -- helper: set AGENT_RT_PY from a complete version's slot python --
  _rt_try_slot() {
    [ -n "$1" ] || return 1
    _rt_marker_valid "$1" || return 1
    for _rt_sub in bin/python Scripts/python.exe; do
      if [ -x "$_rt_root/versions/$1/$_rt_sub" ]; then
        AGENT_RT_PY="$_rt_root/versions/$1/$_rt_sub"; return 0
      fi
    done
    return 1
  }

  # Portable numeric-token key: plain POSIX awk + sort, never GNU `sort -V`.
  _rt_version_key() {
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
        key = ""
        rest = $0
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
  _rt_boot_trace resolver-marker-start current-version
  [ -f "$_rt_root/current-version" ] && \
    _rt_ver=$(tr -d ' \t\r\n' < "$_rt_root/current-version" 2>/dev/null)
  [ -n "$_rt_ver" ] && _rt_try_slot "$_rt_ver"
  if [ -n "$AGENT_RT_PY" ]; then
    _rt_trace_source="current-version"
    _rt_trace_version="$_rt_ver"
    _rt_boot_trace resolver-marker-result current-version hit "$_rt_ver"
  else
    _rt_boot_trace resolver-marker-result current-version miss
  fi

  # Tier 2: marker absent/stale -> the last version the installer activated.
  if [ -z "$AGENT_RT_PY" ] && [ -f "$_rt_root/last-known-good" ]; then
    _rt_boot_trace resolver-marker-start last-known-good
    _rt_lkg=$(tr -d ' \t\r\n' < "$_rt_root/last-known-good" 2>/dev/null)
    _rt_try_slot "$_rt_lkg"
    if [ -n "$AGENT_RT_PY" ]; then
      _rt_trace_source="last-known-good"
      _rt_trace_version="$_rt_lkg"
      _rt_boot_trace resolver-marker-result last-known-good hit "$_rt_lkg"
    else
      _rt_boot_trace resolver-marker-result last-known-good miss
    fi
  fi

  # Tier 3: true first-run (no marker, no last-known-good) -> newest complete
  # slot, matching versioned_runtime.resolve_python. The key transform makes
  # plain POSIX sort version-aware (dev185 > dev50) without GNU `sort -V`.
  if [ -z "$AGENT_RT_PY" ]; then
    # shellcheck disable=SC2046  # version names never contain whitespace
    for _rt_v in $(
      for _rt_dir in "$_rt_root"/versions/*; do
        [ -d "$_rt_dir" ] || continue
        printf '%s\n' "${_rt_dir##*/}"
      done | _rt_version_key | LC_ALL=C sort | cut -f2-
    ); do
      if _rt_try_slot "$_rt_v"; then
        _rt_trace_source="newest"
        _rt_trace_version="$_rt_v"
      fi
    done
  fi

  if [ -n "$AGENT_RT_PY" ]; then
    _rt_boot_trace resolver-slot-result "$_rt_trace_source" resolved "$_rt_trace_version"
  else
    _rt_boot_trace resolver-slot-result none miss
  fi

  unset _rt_root _rt_ver _rt_lkg _rt_sub _rt_v _rt_dir \
    _rt_trace_source _rt_trace_version _rt_bt_raw _rt_bt_plugin _rt_bt_extra \
    _rt_bt_log_path _rt_bt_log_dir _rt_bt_line _rt_bt_iso \
    _rt_phase _rt_now_ms _rt_resolution_source _rt_result _rt_version \
    2>/dev/null || true
  unset -f _rt_marker_valid _rt_try_slot _rt_version_key \
    _rt_boot_trace_ms _rt_boot_trace_iso _rt_boot_trace_log _rt_boot_trace \
    2>/dev/null || true
fi
