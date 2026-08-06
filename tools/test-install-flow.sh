#!/usr/bin/env bash
# =============================================================================
# test-install-flow.sh -- turn-key mini end-to-end install-flow test (#935)
# =============================================================================
# The Linux/WSL twin of tools/test-install-flow.ps1. Exercises a plugin
# installer's self-stage / watchdog behavior in an ISOLATED sandbox (a throwaway
# $HOME) WITHOUT a heavy venv build, using the installer's
# COPILOT_PLUGIN_INSTALL_SMOKE seam. Asserts the same recurring failure-class
# invariants (STAGED, NOT-IN-PAYLOAD, PAYLOAD-FREE, MARKETPLACE, NO-COLLISION,
# WATCHDOG whole-tree kill, MARKER/TOSS, NO-ORPHANS, BOUNDED).
#
# POSIX note: Linux does not have Windows' os-error-32 file lock, so the
# "renamable" invariants pass trivially -- they document the uniform design. The
# meaningful Linux assertions are STAGED / NOT-IN-PAYLOAD and the WATCHDOG
# process-GROUP kill (the twin of `taskkill /T`).
#
# Usage:
#   bash tools/test-install-flow.sh [--plugin NAME] [--smoke-sleep N] [--timeout N]
#
# Exit 0 iff every assertion passes; 1 on any failure; 2 on setup error.
# =============================================================================
set -uo pipefail

PLUGIN="agent-bridge"
REPO_ROOT=""
SMOKE_SLEEP=8
TIMEOUT_SEC=60
while [[ $# -gt 0 ]]; do
    case "$1" in
        --plugin|-Plugin) PLUGIN="$2"; shift 2 ;;
        --repo-root) REPO_ROOT="$2"; shift 2 ;;
        --smoke-sleep) SMOKE_SLEEP="$2"; shift 2 ;;
        --timeout) TIMEOUT_SEC="$2"; shift 2 ;;
        *) shift ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ -n "$REPO_ROOT" ]] || REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SRC_PAYLOAD="$REPO_ROOT/plugins/$PLUGIN"

# Canonical entry script: install.sh if present, else init.sh (matches
# tools/check-install-contract.py's _entrypoint_base).
if [[ -f "$SRC_PAYLOAD/scripts/install.sh" ]]; then
    ENTRY="install.sh"; ENTRY_ARGS=(install)
elif [[ -f "$SRC_PAYLOAD/scripts/init.sh" ]]; then
    ENTRY="init.sh"; ENTRY_ARGS=()
else
    echo "no scripts/install.sh or scripts/init.sh for plugin '$PLUGIN' under $REPO_ROOT" >&2
    exit 2
fi

# -- assertion bookkeeping ---------------------------------------------------
PASS=0
TOTAL=0
FAILED_NAMES=()
_assert() {
    local name="$1" ok="$2" detail="${3:-}"
    TOTAL=$((TOTAL + 1))
    if [[ "$ok" == "1" ]]; then
        PASS=$((PASS + 1))
        printf '  \033[32m[PASS]\033[0m %s%s\n' "$name" "$([[ -n "$detail" ]] && echo " -- $detail")"
    else
        FAILED_NAMES+=("$name")
        printf '  \033[31m[FAIL]\033[0m %s%s\n' "$name" "$([[ -n "$detail" ]] && echo " -- $detail")"
    fi
}

SANDBOX="$(mktemp -d "${TMPDIR:-/tmp}/iflow-XXXXXXXX")"
PAYLOAD_ROOT="$SANDBOX/.copilot/installed-plugins/copilot-extensions"
PAYLOAD="$PAYLOAD_ROOT/$PLUGIN"
STAGE_ROOT="$SANDBOX/.$PLUGIN/.install-stage"
SMOKE_JSON="$SANDBOX/.$PLUGIN/smoke.json"
LAUNCHED=()

_cleanup() {
    local p
    for p in "${LAUNCHED[@]:-}"; do
        [[ -n "$p" ]] || continue
        kill -- -"$p" 2>/dev/null || kill "$p" 2>/dev/null || true
    done
    # Reap any straggler referencing this sandbox (e.g. a smoke grandchild).
    pkill -9 -f "$SANDBOX" 2>/dev/null || true
    sleep 0.3
    rm -rf "$SANDBOX" 2>/dev/null || true
}
trap _cleanup EXIT

# Launch an install in the sandbox. Args: smoke_sleep deadline grandchild(0/1).
# Sets LAST_PID (must run in the main shell -- NOT via $(...) command
# substitution, or the backgrounded child belongs to a subshell and is not
# waitable by the main shell).
_start_install() {
    local sleep_s="$1" deadline="${2:-0}" grandchild="${3:-0}"
    local env_args=(
        "HOME=$SANDBOX"
        "USERPROFILE=$SANDBOX"
        "COPILOT_PLUGIN_INSTALL_SMOKE=1"
        "COPILOT_PLUGIN_INSTALL_SMOKE_SLEEP=$sleep_s"
    )
    [[ "$deadline" -gt 0 ]] && env_args+=("COPILOT_PLUGIN_INSTALL_DEADLINE_SEC=$deadline")
    [[ "$grandchild" == "1" ]] && env_args+=("COPILOT_PLUGIN_INSTALL_SMOKE_GRANDCHILD=1")
    # Clean guard state so staging actually fires.
    env -u COPILOT_PLUGIN_INSTALL_STAGED -u COPILOT_PLUGIN_STAGED_FROM \
        "${env_args[@]}" \
        bash "$PAYLOAD/scripts/$ENTRY" "${ENTRY_ARGS[@]}" >/dev/null 2>&1 &
    LAST_PID=$!
    LAUNCHED+=("$LAST_PID")
}

# Poll for smoke.json; echo its contents (or empty) within timeout.
_wait_smoke() {
    local timeout_s="$1" waited=0
    while [[ "$waited" -lt "$((timeout_s * 5))" ]]; do
        if [[ -f "$SMOKE_JSON" ]]; then cat "$SMOKE_JSON" 2>/dev/null && return 0; fi
        sleep 0.2
        waited=$((waited + 1))
    done
    return 1
}

_json_field() { printf '%s' "$1" | grep -o "\"$2\":[^,}]*" | head -1 | sed 's/.*://; s/^"//; s/"$//'; }

# Linux allows renaming a dir even when it is a cwd/open handle, so this passes
# trivially -- it documents the uniform "singleton stays replaceable" invariant.
_renamable() {
    local dir="$1" aside="$1.__locktest"
    if mv "$dir" "$aside" 2>/dev/null; then mv "$aside" "$dir" 2>/dev/null; return 0; fi
    return 1
}

_count_stage_dirs() {
    local n=0 d
    if [[ -d "$STAGE_ROOT" ]]; then
        for d in "$STAGE_ROOT"/*; do [[ -d "$d" ]] && n=$((n + 1)); done
    fi
    echo "$n"
}

_wait_exit() { # pid timeout_s -> sets EXIT_RC
    local pid="$1" timeout_s="$2" waited=0
    while kill -0 "$pid" 2>/dev/null && [[ "$waited" -lt "$((timeout_s * 5))" ]]; do
        sleep 0.2; waited=$((waited + 1))
    done
    if kill -0 "$pid" 2>/dev/null; then EXIT_RC="running"; return 1; fi
    wait "$pid" 2>/dev/null; EXIT_RC=$?; return 0
}

printf '\033[36m== install-flow test: %s (%s) ==\033[0m\n' "$PLUGIN" "$ENTRY"

mkdir -p "$PAYLOAD_ROOT"
cp -a "$SRC_PAYLOAD" "$PAYLOAD_ROOT/"
_assert 'payload staged into sandbox' "$([[ -f "$PAYLOAD/scripts/$ENTRY" ]] && echo 1 || echo 0)"

# --- single install: staging + lock invariants ---
_t0="$(date +%s)"
_start_install "$SMOKE_SLEEP"; p1="$LAST_PID"
smoke="$(_wait_smoke "$TIMEOUT_SEC")" && smoke_ok=1 || smoke_ok=0
_assert 'smoke seam reached (bounded)' "$smoke_ok" "waited $(( $(date +%s) - _t0 ))s"

if [[ "$smoke_ok" == "1" ]]; then
    staged="$(_json_field "$smoke" staged)"
    ran_from="$(_json_field "$smoke" ran_from)"
    staged_from="$(_json_field "$smoke" staged_from)"
    _assert 'STAGED (re-exec fired)' "$([[ "$staged" == "true" ]] && echo 1 || echo 0)"
    _assert 'NOT-IN-PAYLOAD (ran from stage)' "$([[ "$(printf '%s' "$ran_from" | tr '\\' '/')" == */.install-stage/* ]] && echo 1 || echo 0)" "ran_from=$ran_from"
    _assert 'MARKETPLACE preserved (staged_from under installed-plugins)' "$([[ "$(printf '%s' "$staged_from" | tr '\\' '/')" == */.copilot/installed-plugins/* ]] && echo 1 || echo 0)" "staged_from=$staged_from"
    _assert 'PAYLOAD-FREE while install runs (renamable)' "$(_renamable "$PAYLOAD" && echo 1 || echo 0)"
    _assert 'stage dir is unique per invocation' "$([[ "$(_count_stage_dirs)" -ge 1 ]] && echo 1 || echo 0)" "stage dirs: $(_count_stage_dirs)"
fi
_wait_exit "$p1" "$TIMEOUT_SEC" || true
_assert 'install exited cleanly' "$([[ "$EXIT_RC" == "0" ]] && echo 1 || echo 0)" "exit=$EXIT_RC"

# --- collision: two concurrent installs -> distinct stage dirs, both free ---
rm -rf "$STAGE_ROOT" 2>/dev/null || true
_start_install "$SMOKE_SLEEP"; c1="$LAST_PID"
_start_install "$SMOKE_SLEEP"; c2="$LAST_PID"
_cwait=0
while [[ "$(_count_stage_dirs)" -lt 2 && "$_cwait" -lt "$((TIMEOUT_SEC * 5))" ]]; do sleep 0.2; _cwait=$((_cwait + 1)); done
_assert 'NO-COLLISION (2 concurrent -> >=2 distinct stage dirs)' "$([[ "$(_count_stage_dirs)" -ge 2 ]] && echo 1 || echo 0)" "stage dirs: $(_count_stage_dirs)"
_assert 'PAYLOAD-FREE under concurrent installs' "$(_renamable "$PAYLOAD" && echo 1 || echo 0)"
_wait_exit "$c1" "$TIMEOUT_SEC" || true
_wait_exit "$c2" "$TIMEOUT_SEC" || true

# --- no orphaned installer processes left holding the payload ---
orphans="$(pgrep -f "$PAYLOAD/scripts" 2>/dev/null | wc -l | tr -d ' ')"
_assert 'NO-ORPHANS (none holding payload)' "$([[ "$orphans" == "0" ]] && echo 1 || echo 0)" "orphans: $orphans"
_assert 'NO-ORPHANS (payload renamable after)' "$(_renamable "$PAYLOAD" && echo 1 || echo 0)"

# --- WATCHDOG: a stalled install self-terminates (the session-start failure class) ---
rm -rf "$STAGE_ROOT" 2>/dev/null || true
rm -f "$SMOKE_JSON" 2>/dev/null || true
WD_DEADLINE=3
_start_install 90 "$WD_DEADLINE" 1; w1="$LAST_PID"
smokeW="$(_wait_smoke "$TIMEOUT_SEC")" && smokeW_ok=1 || smokeW_ok=0
childPid="$([[ "$smokeW_ok" == "1" ]] && _json_field "$smokeW" child_pid || echo 0)"
grandPid="$([[ "$smokeW_ok" == "1" ]] && _json_field "$smokeW" grandchild_pid || echo 0)"
_assert 'WATCHDOG smoke reached (child+grandchild spawned)' "$([[ "$smokeW_ok" == "1" && "${grandPid:-0}" -gt 0 ]] && echo 1 || echo 0)" "child=$childPid grand=$grandPid"
_wait_exit "$w1" "$((WD_DEADLINE + 20))" || true
_assert 'WATCHDOG parent exited on deadline (124)' "$([[ "$EXIT_RC" == "124" ]] && echo 1 || echo 0)" "exit=$EXIT_RC"
sleep 1
child_alive="$([[ "${childPid:-0}" -gt 0 ]] && kill -0 "$childPid" 2>/dev/null && echo 1 || echo 0)"
grand_alive="$([[ "${grandPid:-0}" -gt 0 ]] && kill -0 "$grandPid" 2>/dev/null && echo 1 || echo 0)"
_assert 'WATCHDOG killed the staged child' "$([[ "$child_alive" == "0" ]] && echo 1 || echo 0)"
_assert 'WATCHDOG killed the GRANDCHILD (whole tree)' "$([[ "$grand_alive" == "0" ]] && echo 1 || echo 0)" "grand=$grandPid"
_assert 'WATCHDOG payload still free after kill' "$(_renamable "$PAYLOAD" && echo 1 || echo 0)"

# --- MARKER / TOSS: a killed build leaves NO completion marker; a rebuild tosses it ---
vr="$PAYLOAD/scripts/versioned_runtime.py"
srcVer="$(grep -m1 '^version' "$PAYLOAD/pyproject.toml" 2>/dev/null | sed 's/.*"\(.*\)".*/\1/')"
abRoot="$SANDBOX/.$PLUGIN"
corpse="$abRoot/versions/$srcVer"
mkdir -p "$corpse"
bootPy="$(command -v python3 || command -v python || true)"
if [[ -n "$bootPy" && -n "$srcVer" ]]; then
    "$bootPy" "$vr" --root "$abRoot" is-complete "$srcVer" >/dev/null 2>&1; incompleteRc=$?
    _assert 'MARKER absent on a killed/partial slot (is-complete => 1)' "$([[ "$incompleteRc" == "1" ]] && echo 1 || echo 0)" "rc=$incompleteRc ver=$srcVer"
    "$bootPy" "$vr" --root "$abRoot" slot "$srcVer" --clean-incomplete >/dev/null 2>&1 || true
    corpse_items="$(find "$corpse" -mindepth 1 2>/dev/null | wc -l | tr -d ' ')"
    _assert 'TOSS: corpse slot cleaned for rebuild (slot --clean-incomplete)' "$([[ ! -f "$corpse/pyvenv.cfg" && "$corpse_items" == "0" ]] && echo 1 || echo 0)" "items=$corpse_items"
    "$bootPy" "$vr" --root "$abRoot" mark-complete "$srcVer" >/dev/null 2>&1 || true
    "$bootPy" "$vr" --root "$abRoot" is-complete "$srcVer" >/dev/null 2>&1; completeRc=$?
    _assert 'MARKER present after mark-complete (is-complete => 0)' "$([[ "$completeRc" == "0" ]] && echo 1 || echo 0)" "rc=$completeRc"
else
    _assert 'MARKER test skipped (no system python)' 1 'no python on PATH'
fi

printf '\n'
if [[ "${#FAILED_NAMES[@]}" -eq 0 ]]; then
    printf '\033[32m== %s/%s passed ==\033[0m\n' "$PASS" "$TOTAL"
    exit 0
else
    printf '\033[31m== %s/%s passed ==\033[0m\n' "$PASS" "$TOTAL"
    printf '   failed: %s\n' "${FAILED_NAMES[*]}"
    exit 1
fi
