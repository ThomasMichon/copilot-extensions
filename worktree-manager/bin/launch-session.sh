#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# Setup log — per-launch log file with PID disambiguation
# ---------------------------------------------------------------------------
_SETUP_LOG_DIR="${TMPDIR:-/tmp}/worktree-setup-logs"
mkdir -p "$_SETUP_LOG_DIR" 2>/dev/null || true
SETUP_LOG="${WORKTREE_SETUP_LOG:-$_SETUP_LOG_DIR/setup-$$.log}"
export WORKTREE_SETUP_LOG="$SETUP_LOG"

# ---------------------------------------------------------------------------
# Launch-flow correlation id -- minted once per launcher run and threaded
# through the whole flow (activity marks, the mux server env, and thus the
# in-pane session hooks) so one launch is reconstructable via
# `agent-worktrees activity --launch-id`. Best-effort, dependency-free.
# ---------------------------------------------------------------------------
if [[ -r /proc/sys/kernel/random/uuid ]]; then
    LAUNCH_ID="$(tr -d '-' < /proc/sys/kernel/random/uuid | cut -c1-12)"
else
    LAUNCH_ID="$(printf '%08x%x' "$(date +%s 2>/dev/null || echo 0)" "$$")"
fi
export WORKTREE_LAUNCH_ID="$LAUNCH_ID"

setup_log() {
    local level="$1" msg="$2"
    printf '[%s] [%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$level" "$msg" >> "$SETUP_LOG" 2>/dev/null || true
}

launch_trace() {
    local event="$1" path="${AGENT_WORKTREES_LAUNCH_TRACE:-}"
    [[ -n "$path" ]] || return 0
    case "${path,,}" in 0|false|no|off) return 0 ;; esac
    mkdir -p "$(dirname "$path")" 2>/dev/null || true
    printf '%s\n' '{"timestamp":"'"$(date -u +%Y-%m-%dT%H:%M:%SZ)"'","event":"'"$event"'","launch_id":"'"${AGENT_WORKTREES_LAUNCH_ID:-}"'","project":"'"${LAUNCH_PROJECT:-}"'"}' >>"$path" 2>/dev/null || true
}

# Launch-status line: always logged, and ALSO echoed to the terminal (stderr,
# to stay clear of any stdout capture / ACP channel) during an interactive
# launch so the operator understands what the otherwise-silent post-Picker /
# pre-mux pause is waiting on -- the staged update join + apply, which can
# block for up to 90s. Gated on _SHOW_LAUNCH_STATUS so machine/direct-dispatch
# and JSON paths stay quiet (they never enable it).
_SHOW_LAUNCH_STATUS=""
setup_status() {
    local level="$1" msg="$2"
    setup_log "$level" "$msg"
    [[ "$_SHOW_LAUNCH_STATUS" == "1" ]] && printf '  %s\n' "$msg" >&2 || true
}

# Write header
{
    echo "# Worktree Manager — session launch log"
    echo "# Started: $(date '+%Y-%m-%d %H:%M:%S %z')"
    echo "# PID: $$"
    echo "# Host: $(hostname)"
    echo ""
} > "$SETUP_LOG" 2>/dev/null || true
chmod 600 "$SETUP_LOG" 2>/dev/null || true

# Prune old logs (keep last 10)
# shellcheck disable=SC2012
ls -t "$_SETUP_LOG_DIR"/setup-*.log 2>/dev/null | tail -n +11 | xargs rm -f 2>/dev/null || true

# --recovery: bypass worktree resolution entirely, go straight to setup script
# --project: explicit project identity for CWD-neutral callers
# --: everything after this separator is copilot passthrough args (e.g. --acp --stdio)
LAUNCH_PROJECT=""
RECOVERY_MODE=0
FILTERED_ARGS=()
COPILOT_PASSTHROUGH=()
_SEEN_SEPARATOR=0
while [[ $# -gt 0 ]]; do
    arg="$1"
    shift
    if [[ $_SEEN_SEPARATOR -eq 1 ]]; then
        COPILOT_PASSTHROUGH+=("$arg")
    elif [[ "$arg" == "--" ]]; then
        _SEEN_SEPARATOR=1
    elif [[ "$arg" == "--project" ]]; then
        if [[ -n "$LAUNCH_PROJECT" ]]; then
            setup_log ERROR '--project may be specified only once'
            echo "ERROR: --project may be specified only once." >&2
            exit 2
        fi
        if [[ $# -eq 0 ]]; then
            setup_log ERROR '--project requires a value'
            echo "ERROR: --project requires a value." >&2
            exit 2
        fi
        LAUNCH_PROJECT="$1"
        shift
        if [[ ! "$LAUNCH_PROJECT" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]]; then
            setup_log ERROR "Invalid --project value: $LAUNCH_PROJECT"
            echo "ERROR: Invalid --project value '$LAUNCH_PROJECT'." >&2
            exit 2
        fi
    elif [[ "$arg" == "--recovery" || "$arg" == "-Recovery" || "$arg" == "recovery" ]]; then
        RECOVERY_MODE=1
        setup_log INFO 'Recovery mode requested via CLI arg'
    else
        FILTERED_ARGS+=("$arg")
    fi
done
set -- "${FILTERED_ARGS[@]+"${FILTERED_ARGS[@]}"}"

setup_log INFO 'launch-session.sh starting'
launch_trace launcher_start
if [[ ${#COPILOT_PASSTHROUGH[@]} -gt 0 ]]; then
    setup_log INFO "Copilot passthrough args: ${COPILOT_PASSTHROUGH[*]}"
fi

# Recovery escape hatch resolves from explicit project identity or CWD.
if [[ "$RECOVERY_MODE" == "1" ]]; then
    CANDIDATES=()
    if [[ -n "$LAUNCH_PROJECT" ]]; then
        CONFIG="$HOME/.$LAUNCH_PROJECT/config.yaml"
        if [[ -f "$CONFIG" ]]; then
            CONFIG_ANCHOR=$(sed -nE 's/^[[:space:]]+anchor:[[:space:]]+["'"'"']?([^"'"'"']+)["'"'"']?[[:space:]]*$/\1/p' "$CONFIG" | head -n 1)
            [[ -n "$CONFIG_ANCHOR" ]] && CANDIDATES+=("$CONFIG_ANCHOR")
        fi
    fi
    GIT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || true)
    [[ -n "$GIT_ROOT" ]] && CANDIDATES+=("$GIT_ROOT")
    CANDIDATES+=("$PWD")
    for ANCHOR in "${CANDIDATES[@]}"; do
        SETUP_SCRIPT="$ANCHOR/tools/setup/setup.sh"
        if [[ -f "$SETUP_SCRIPT" ]]; then
            RECOVERY_ARGS=(--recovery "$@")
            if [[ ${#COPILOT_PASSTHROUGH[@]} -gt 0 ]]; then
                RECOVERY_ARGS+=("${COPILOT_PASSTHROUGH[@]}")
            fi
            cd "$ANCHOR"
            exec bash "$SETUP_SCRIPT" "${RECOVERY_ARGS[@]}"
        fi
    done
    echo "ERROR: Cannot find a recovery setup script in the project anchor, Git root, or current directory." >&2
    exit 1
fi

# Runtime resolution (junction-free, marker-only). Prefer the `current-version`
# marker -> versions/<ver>/bin/python; fall back to the newest slot only -- the
# `.venv` symlink is retired (#1106).
RUNTIME_DIR="$HOME/.agent-worktrees"
AW_PY=""
if [[ -f "$RUNTIME_DIR/bin/resolve-runtime.sh" ]]; then
    # shellcheck source=../scripts/resolve-runtime.sh
    source "$RUNTIME_DIR/bin/resolve-runtime.sh"
fi
PYTHON="$AW_PY"

resolve_runtime_python() {
    AW_PY=""
    [[ -f "$RUNTIME_DIR/bin/resolve-runtime.sh" ]] || return 1
    source "$RUNTIME_DIR/bin/resolve-runtime.sh"
    [[ -n "$AW_PY" ]] || return 1
    printf '%s\n' "$AW_PY"
}

if [[ -n "$PYTHON" && -x "$PYTHON" ]]; then
    setup_log INFO "Venv resolved: $RUNTIME_DIR"
else
    setup_log ERROR 'Venv not found - aborting'
    echo "ERROR: Venv not found. Run the installer first." >&2
    exit 1
fi

export PYTHONPATH="$RUNTIME_DIR/lib"
unset PYTHONHOME

run_post_exit() {
    local worktree_id="$1"
    local post_args=(-m agent_worktrees)
    [[ -n "$LAUNCH_PROJECT" ]] && post_args+=(--project "$LAUNCH_PROJECT")
    post_args+=(post-exit "$worktree_id")
    "$PYTHON" "${post_args[@]}"
}

# Append a high-level lifecycle event to the persistent activity log.
# Best-effort and fully detached -- never blocks or fails the launch.
#   activity_log EVENT WORKTREE_ID [key=value ...]
activity_log() {
    local event="$1" wt="${2:-}"; shift 2 2>/dev/null || shift $# 
    [[ -z "$event" || -z "$wt" ]] && return 0
    local fields=()
    local kv
    for kv in "$@"; do
        fields+=(--field "$kv")
    done
    ( "$PYTHON" -m agent_worktrees activity-log "$event" \
        --worktree-id "$wt" --source launcher \
        ${LAUNCH_ID:+--launch-id "$LAUNCH_ID"} \
        "${fields[@]+"${fields[@]}"}" >/dev/null 2>&1 & ) || true
}

# ── Plugin auto-update ─────────────────────────────────────────────────────
# If installed from the copilot-extensions marketplace plugin, check for
# updates.  When the plugin source changes: run the full installer (which
# deploys package, launch scripts, binstubs, terminal configs), then
# re-exec into the newly deployed launch-session so the rest of the boot
# uses updated code.
#
# Guard: WORKTREE_NO_UPDATE=1 skips this block entirely (set by --no-update
# and by the re-exec below to prevent infinite loops).

_NO_UPDATE="${WORKTREE_NO_UPDATE:-}"
_STAGE_PID=""
_UPDATE_APPLIED=""

# ── Background update: stage-then-join (#1430) ─────────────────────────────
# The Picker runs from the installed runtime venv, so the slow marketplace
# download is STAGED in the background while the Picker is open, then the apply
# (installer -> runtime, pre-launch, reconcile) runs at the JOIN, after the
# Picker closes and before the tmux/Copilot handoff. The launcher script is
# applied via the installer but NOT re-exec'd mid-flight: a launcher change
# takes effect on the NEXT launch (stage-next).

start_update_stage() {
    # Spawn the background stage (marketplace download + fingerprint + plan).
    # Output is discarded so it never writes to the Picker's terminal.
    [[ "$_NO_UPDATE" == "1" ]] && return 0
    setup_log INFO 'Starting background update stage (stage-update)'
    ( "$PYTHON" -m agent_worktrees stage-update >/dev/null 2>&1 ) &
    _STAGE_PID=$!
}

invoke_update_apply() {
    # $1 = "1" to also run plugin reconcile (Picker path); "0" otherwise.
    # $2 = "1" to echo status to the terminal (interactive launch); "0" otherwise.
    # Idempotent: runs its body at most once per launch.
    local with_reconcile="${1:-0}"
    _SHOW_LAUNCH_STATUS="${2:-0}"
    [[ -n "$_UPDATE_APPLIED" ]] && return 0
    _UPDATE_APPLIED=1

    local status_file="$HOME/.agent-worktrees/updater-status.json"
    local stage_done="" plugin_changed="" skipped="" plugin_dir="" runtime_root="" runtime_apply_blocked=""

    _parse_stage_status() {
        [[ -f "$status_file" ]] || return 0
        IFS=$'\t' read -r stage_done plugin_changed skipped plugin_dir runtime_root runtime_apply_blocked < <(
            "$PYTHON" -c "
import sys, json
try:
    d = json.load(open(sys.argv[1], encoding='utf-8'))
except Exception:
    d = {}
print('\t'.join([
    str(d.get('stage_done', False)),
    str(d.get('plugin_changed', False)),
    str(d.get('skipped', '')),
    str(d.get('plugin_dir', '')),
    str(d.get('runtime_root', '')),
    str(d.get('runtime_apply_blocked', '')),
]))
" "$status_file" 2>/dev/null
        )
    }

    if [[ "$_NO_UPDATE" != "1" ]]; then
        setup_status INFO 'Finalizing launch: applying any pending plugin and runtime updates...'
        # Join the background stage. This is the step most likely to make the
        # launch look "stuck": the marketplace download staged while the Picker
        # was open is joined here, up to the stage's own timeout.
        if [[ -n "$_STAGE_PID" ]]; then
            setup_status INFO 'Waiting for the background plugin-update download to finish...'
            wait "$_STAGE_PID" 2>/dev/null || true
        fi
        _parse_stage_status
        # No usable staged result (stage failed, or a peer launch held the
        # lock): stage inline so the marketplace pull still happens.
        if [[ "$stage_done" != "True" || "$skipped" == "locked" ]]; then
            setup_status INFO 'Background download unavailable; downloading the plugin update now...'
            "$PYTHON" -m agent_worktrees stage-update >/dev/null 2>&1 || true
            _parse_stage_status
        fi
        if [[ -n "$runtime_apply_blocked" ]]; then
            setup_log WARN "Plugin runtime apply blocked: $runtime_apply_blocked"
        fi

        # (1) Marketplace installer, iff the download changed the payload.
        #     NO re-exec: a launcher-script change applies on the next launch.
        if [[ "$plugin_changed" == "True" ]]; then
            local _stage_env_text=""
            local _stage_env=()
            if ! _stage_env_text=$("$PYTHON" -c "
import sys, json
status = json.load(open(sys.argv[1], encoding='utf-8'))
unset = status.get('unset_environment', [])
environment = status.get('environment', {})
if (
    not isinstance(unset, list)
    or not all(isinstance(key, str) and key for key in unset)
    or not isinstance(environment, dict)
    or not all(
        isinstance(key, str) and key and isinstance(value, str)
        for key, value in environment.items()
    )
):
    raise SystemExit(2)
for key in unset:
    print('-u')
    print(key)
for key, value in environment.items():
    print(f'{key}={value}')
" "$status_file" 2>/dev/null); then
                setup_log WARN 'Plugin installer environment metadata is invalid -- skipping'
                plugin_changed="False"
            fi
            while IFS= read -r _stage_env_entry; do
                [[ -n "$_stage_env_entry" ]] && _stage_env+=("$_stage_env_entry")
            done <<< "$_stage_env_text"

            if [[ "$plugin_changed" == "True" ]]; then
                local _installer="$plugin_dir/scripts/install.sh"
                if [[ -n "$plugin_dir" && -f "$_installer" ]]; then
                    local _inst_args=(
                        update
                        --install-dir "$runtime_root"
                    )
                    if [[ -n "$LAUNCH_PROJECT" ]]; then
                        _inst_args+=(--project-name "$LAUNCH_PROJECT")
                    fi
                    if [[ -n "${WORKTREE_BLOCKING_INSTALL:-}" ]]; then
                        # Escape hatch (recovery/debug): apply synchronously.
                        setup_status INFO 'A new plugin version was downloaded; installing the updated runtime...'
                        if env ${_stage_env[@]+"${_stage_env[@]}"} \
                            bash "$_installer" "${_inst_args[@]}" 2>&1 | while IFS= read -r _line; do
                            setup_log INFO "installer: $_line"
                        done; then
                            setup_log INFO 'Installer update succeeded (launcher change, if any, applies next launch)'
                        else
                            setup_log WARN "Installer update failed -- continuing with existing version"
                        fi
                    else
                        # Default: DETACH the install so the launch never blocks on the
                        # (slow) venv rebuild. Immutable versioned slots make this safe
                        # -- the installer builds a NEW versions/<v> slot and flips the
                        # current-version marker atomically, never touching the slot
                        # THIS session execs from. So launch on the active slot now; the
                        # new version applies on the next launch (stage-next), the same
                        # way the runtime reconcile already runs detached. The installer
                        # carries its own single-instance lock, so a concurrent launch's
                        # background install can't collide.
                        setup_status INFO 'A new plugin version was downloaded; installing it in the background (applies on the next launch)...'
                        local _ilog="${APERTURE_SETUP_LOG:-${WORKTREE_SETUP_LOG:-/dev/null}}"
                        setsid env ${_stage_env[@]+"${_stage_env[@]}"} \
                            bash "$_installer" "${_inst_args[@]}" \
                            >>"$_ilog" 2>&1 </dev/null &
                        disown 2>/dev/null || true
                        setup_log INFO 'Background install started (new version applies on the next launch)'
                    fi
                else
                    setup_log WARN "Plugin installer not found ($_installer) -- skipping"
                fi
            fi
        fi

        # (2) Pre-launch self-update (bootstrap-service staleness; two-pass).
        setup_status INFO 'Checking bootstrap services for pending updates...'
        _log_prelaunch_diagnostics() {
            local _plan_json="$1"
            while IFS= read -r _pdiag; do
                [[ -n "$_pdiag" ]] || continue
                setup_log WARN "Pre-launch: $_pdiag"
            done < <(printf '%s' "$_plan_json" | "$PYTHON" -c '
import sys, json
for d in json.load(sys.stdin).get("diagnostics", []):
    print(f"{d.get('"'"'service'"'"', '"'"'?'"'"')} [{d.get('"'"'reason'"'"', '"'"'diagnostic'"'"')}] {d.get('"'"'message'"'"', '"'"''"'"')}")
' 2>/dev/null)
        }
        PRE_JSON=$("$PYTHON" -m agent_worktrees pre-launch 2>/dev/null) || PRE_JSON='{"action":"continue","reason":"error"}'
        _log_prelaunch_diagnostics "$PRE_JSON"
        PRE_ACTION=$(echo "$PRE_JSON" | "$PYTHON" -c "import sys,json; print(json.load(sys.stdin).get('action','continue'))" 2>/dev/null) || PRE_ACTION="continue"
        if [[ "$PRE_ACTION" == "self-update" ]]; then
            setup_status INFO 'Bootstrap services are stale; updating them...'
            UPDATE_COUNT=$("$PYTHON" -c "import sys,json; print(len(json.load(sys.stdin).get('updates',[])))" <<< "$PRE_JSON" 2>/dev/null) || UPDATE_COUNT=0
            for (( i=0; i<UPDATE_COUNT; i++ )); do
                SVC_NAME=$("$PYTHON" -c "import sys,json; print(json.load(sys.stdin)['updates'][$i]['service'])" <<< "$PRE_JSON" 2>/dev/null) || SVC_NAME="unknown"
                UPDATE_ARGV=()
                while IFS= read -r _update_arg; do
                    UPDATE_ARGV+=("$_update_arg")
                done < <("$PYTHON" -c "
import sys, json
for a in json.load(sys.stdin)['updates'][$i].get('argv', []):
    print(a)
" <<< "$PRE_JSON" 2>/dev/null)
                if [[ ${#UPDATE_ARGV[@]} -gt 0 ]]; then
                    _UPDATE_ENV=()
                    _UPDATE_ENV_TEXT=$("$PYTHON" -c "
import sys, json
update = json.load(sys.stdin)['updates'][$i]
unset = update.get('unset_environment', [])
environment = update.get('environment', {})
if (
    not isinstance(unset, list)
    or not all(isinstance(key, str) and key for key in unset)
    or not isinstance(environment, dict)
    or not all(
        isinstance(key, str) and key and isinstance(value, str)
        for key, value in environment.items()
    )
):
    raise SystemExit(2)
for key in unset:
    print('-u')
    print(key)
for key, value in environment.items():
    print(f'{key}={value}')
" <<< "$PRE_JSON" 2>/dev/null) || {
                        setup_log WARN "Update environment invalid for $SVC_NAME; skipping"
                        continue
                    }
                    while IFS= read -r _update_env; do
                        [[ -n "$_update_env" ]] && _UPDATE_ENV+=("$_update_env")
                    done <<< "$_UPDATE_ENV_TEXT"
                    setup_status INFO "Updating $SVC_NAME..."
                    setup_log INFO "  command: ${UPDATE_ARGV[*]}"
                    env ${_UPDATE_ENV[@]+"${_UPDATE_ENV[@]}"} \
                        "${UPDATE_ARGV[@]}" \
                        || setup_log WARN "Update failed for $SVC_NAME (exit $?)"
                fi
            done
            setup_log INFO 'Re-checking staleness after update'
            PRE_JSON=$("$PYTHON" -m agent_worktrees pre-launch 2>/dev/null) || PRE_JSON='{"action":"continue"}'
            _log_prelaunch_diagnostics "$PRE_JSON"
            PRE_ACTION=$(echo "$PRE_JSON" | "$PYTHON" -c "import sys,json; print(json.load(sys.stdin).get('action','continue'))" 2>/dev/null) || PRE_ACTION="continue"
            if [[ "$PRE_ACTION" == "self-update" ]]; then
                setup_log WARN 'Still stale after update -- proceeding anyway'
            fi
        fi
    else
        setup_status INFO 'Skipping plugin update check (--no-update).'
    fi

    # (3) Plugin reconciliation (repo-configured payloads + gated runtimes).
    #     Independent of WORKTREE_NO_UPDATE; opt out with WORKTREE_NO_RECONCILE=1.
    #     Two passes: payload first, then runtime (readable only next pass).
    if [[ "$with_reconcile" == "1" && "${WORKTREE_NO_RECONCILE:-}" != "1" ]]; then
        for _rpass in 1 2; do
            REC_JSON=$("$PYTHON" -m agent_worktrees reconcile-plugins 2>/dev/null) \
                || REC_JSON='{"action":"continue"}'
            REC_ACTION=$(printf '%s' "$REC_JSON" \
                | "$PYTHON" -c "import sys,json; print(json.load(sys.stdin).get('action','continue'))" 2>/dev/null) \
                || REC_ACTION="continue"
            while IFS= read -r _rdiag; do
                [[ -n "$_rdiag" ]] || continue
                setup_log WARN "Plugin reconcile: $_rdiag"
            done < <(printf '%s' "$REC_JSON" | "$PYTHON" -c '
import sys, json
for d in json.load(sys.stdin).get("diagnostics", []):
    print(f"{d.get('"'"'service'"'"', '"'"'?'"'"')} [{d.get('"'"'reason'"'"', '"'"'diagnostic'"'"')}] {d.get('"'"'message'"'"', '"'"''"'"')}")
' 2>/dev/null)
            if [[ "$REC_ACTION" != "reconcile" ]]; then
                if [[ "$_rpass" == "1" ]]; then
                    setup_log INFO 'Plugin reconcile: no executable actions'
                fi
                break
            fi
            REC_COUNT=$("$PYTHON" -c "import sys,json; print(len(json.load(sys.stdin).get('updates',[])))" <<< "$REC_JSON" 2>/dev/null) || REC_COUNT=0
            setup_status INFO "Reconciling plugins (pass $_rpass): $REC_COUNT action(s)..."
            for (( _ri=0; _ri<REC_COUNT; _ri++ )); do
                _RSVC=$("$PYTHON" -c "import sys,json; print(json.load(sys.stdin)['updates'][$_ri].get('service','?'))" <<< "$REC_JSON" 2>/dev/null) || _RSVC="?"
                _RARGV=()
                while IFS= read -r _reconcile_arg; do
                    _RARGV+=("$_reconcile_arg")
                done < <("$PYTHON" -c "
import sys, json
for a in json.load(sys.stdin)['updates'][$_ri].get('argv', []):
    print(a)
" <<< "$REC_JSON" 2>/dev/null)
                [[ ${#_RARGV[@]} -gt 0 ]] || continue
                if [[ "${_RARGV[0]}" == "copilot" ]] && ! command -v copilot &>/dev/null; then
                    setup_log WARN "Plugin reconcile: skipping $_RSVC (copilot not on PATH)"
                    continue
                fi
                setup_log INFO "Plugin reconcile: $_RSVC -> ${_RARGV[*]}"
                _RENV=()
                _RENV_TEXT=$("$PYTHON" -c "
import sys, json
update = json.load(sys.stdin)['updates'][$_ri]
for key in update.get('unset_environment', []):
    print('-u')
    print(key)
for key, value in update.get('environment', {}).items():
    print(f'{key}={value}')
" <<< "$REC_JSON" 2>/dev/null) || {
                    setup_log WARN "Plugin reconcile: invalid environment metadata for $_RSVC; skipping"
                    continue
                }
                while IFS= read -r _reconcile_env; do
                    [[ -n "$_reconcile_env" ]] && _RENV+=("$_reconcile_env")
                done <<< "$_RENV_TEXT"
                env ${_RENV[@]+"${_RENV[@]}"} "${_RARGV[@]}" 2>&1 | while IFS= read -r _rl; do setup_log INFO "reconcile: $_rl"; done \
                    || setup_log WARN "Plugin reconcile: step failed for $_RSVC"
            done
        done
    fi
}

# ── Pre-launch self-update + reconcile now run via invoke_update_apply ────
# (moved into the stage-then-join functions defined above, #1430).


# ── Direct-dispatch commands (bypass resolve/picker) ─────────────────────
# Subcommands that agent_worktrees's main() handles directly — these
# must NOT fall through to the resolve→picker flow.  Keep in sync with
# COMMAND_MAP in __main__.py, plus "services" and "agent-worktrees".
_DIRECT_COMMANDS="services repos knowledge worktree agent-worktrees resolve session-backend post-exit finalize push-changes mark-complete status list create cleanup validate install register unregister uninstall update install-status deploy-instructions get pre-launch stage-update reconcile-plugins dev handoff-cutover register-session deregister-session backfill-sessions anchor-check activity activity-log"
_IS_DIRECT=""
if [[ $# -gt 0 ]]; then
    for _dc in $_DIRECT_COMMANDS; do
        if [[ "$1" == "$_dc" ]]; then _IS_DIRECT=1; break; fi
    done
fi
if [[ -n "$_IS_DIRECT" ]]; then
    setup_log INFO "Direct dispatch: $1 (bypassing resolve)"
    # No Picker window to hide behind: stage + apply synchronously (no
    # reconcile, matching historical direct-command behavior) before dispatch.
    start_update_stage
    invoke_update_apply 0
    direct_args=(-m agent_worktrees)
    [[ -n "$LAUNCH_PROJECT" ]] && direct_args+=(--project "$LAUNCH_PROJECT")
    direct_args+=("$@")
    exec "$PYTHON" "${direct_args[@]}"
fi

# ── Background update stage (#1430) ──────────────────────────────────────
# Spawn the marketplace download now so it runs WHILE the Picker is open. It is
# joined and applied (installer + pre-launch + reconcile) after resolve returns
# an exec plan, before the tmux handoff -- see invoke_update_apply below.
start_update_stage

# ── Resolve launch plan via Python ────────────────────────────────────────

setup_log INFO 'Calling agent_worktrees resolve'
launch_trace resolve_start
resolve_args=(-m agent_worktrees)
[[ -n "$LAUNCH_PROJECT" ]] && resolve_args+=(--project "$LAUNCH_PROJECT")
resolve_args+=(resolve "$@")
JSON=$("$PYTHON" "${resolve_args[@]}")
RC=$?
if [[ $RC -ne 0 ]]; then
    setup_log ERROR "agent_worktrees resolve failed (exit $RC)"
    exit $RC
fi

# Non-interactive resolves (`resolve --json --worktree-id` / `--json --new`,
# used by agent-bridge ACP launches) emit the bridge's nested plan shape:
#   {"worktree": {...}, "launch": {"action": "exec", ...}}
# The launcher below consumes the *flat* plan ({"action": "exec", ...}); the
# nested `launch` object carries the identical keys, so unwrap it when present.
# A flat plan (no top-level `launch`) passes through unchanged.
JSON=$(printf '%s' "$JSON" | "$PYTHON" -c "import sys, json
d = json.load(sys.stdin)
print(json.dumps(d['launch'] if isinstance(d, dict) and 'launch' in d else d))")
if [[ -z "$LAUNCH_PROJECT" ]]; then
    LAUNCH_PROJECT=$(printf '%s' "$JSON" | "$PYTHON" -c "import sys,json; print(json.load(sys.stdin).get('project',''))")
fi

ACTION=$(echo "$JSON" | "$PYTHON" -c "import sys,json; print(json.load(sys.stdin).get('action','none'))")
setup_log INFO "Plan resolved: action=$ACTION"

if [[ "$ACTION" == "none" ]]; then
    EXIT_CODE=$(echo "$JSON" | "$PYTHON" -c "import sys,json; print(json.load(sys.stdin).get('exit_code',0))")
    exit "$EXIT_CODE"
fi

# ── Remote machine handoff via SSH ───────────────────────────────────────
if [[ "$ACTION" == "remote" ]]; then
    SSH_ALIAS=$(echo "$JSON" | "$PYTHON" -c "import sys,json; print(json.load(sys.stdin).get('ssh_alias',''))")
    REMOTE_CMD=$(echo "$JSON" | "$PYTHON" -c "import sys,json; print(json.load(sys.stdin).get('remote_command',''))")
    DISPLAY_NAME=$(echo "$JSON" | "$PYTHON" -c "import sys,json; print(json.load(sys.stdin).get('display_name',''))")
    setup_log INFO "Handing off to remote machine: $DISPLAY_NAME via $SSH_ALIAS"
    echo "Connecting to $DISPLAY_NAME..."
    # exec ssh with TTY allocation; the remote binstub takes over
    exec ssh -t "$SSH_ALIAS" "$REMOTE_CMD"
fi

if [[ "$ACTION" == "refresh" ]]; then
    # ── Picker refresh: apply the staged update, then relaunch (#1430) ───────
    # The picker's refresh icon exits with action=refresh. It runs from the
    # runtime venv the update replaces, so apply here (venv now free), then
    # re-exec the (now-updated) launcher to reopen the picker on the new version.
    setup_log INFO 'Picker refresh -- running full update and relaunching'
    # The explicit "Update available" -> enter gesture runs the SAME
    # comprehensive update as the `update` command (every registered plugin
    # payload + sibling modules + the runtime installer), not just the
    # opportunistic staged apply, which only pulls agent-worktrees and gates
    # its installer on a fingerprint diff / venv-drift -- so an
    # already-pulled-but-not-yet-deployed payload or sibling could relaunch
    # stale (dotfiles#443). `update` is itself version-gated, so it stays quick
    # when everything is already current.
    if [[ "${WORKTREE_NO_UPDATE:-}" != "1" ]]; then
        update_args=(-m agent_worktrees)
        [[ -n "$LAUNCH_PROJECT" ]] && update_args+=(--project "$LAUNCH_PROJECT")
        update_args+=(update)
        "$PYTHON" "${update_args[@]}" \
            || setup_log WARN 'Full update returned non-zero -- continuing to reconcile/relaunch'
    fi
    invoke_update_apply 1 1
    _RELAUNCH="$HOME/.agent-worktrees/bin/launch-session.sh"
    if [[ -x "$_RELAUNCH" ]]; then
        relaunch_args=()
        [[ -n "$LAUNCH_PROJECT" ]] && relaunch_args+=(--project "$LAUNCH_PROJECT")
        relaunch_args+=("$@")
        if [[ ${#COPILOT_PASSTHROUGH[@]} -gt 0 ]]; then
            relaunch_args+=(-- "${COPILOT_PASSTHROUGH[@]}")
        fi
        exec "$_RELAUNCH" "${relaunch_args[@]}"
    fi
    setup_log WARN 'Relaunch launcher missing after refresh; exiting'
    exit 1
fi

# ── Fast re-attach: skip the update when JOINING an already-live session ──
# Opening a worktree whose tmux session is already running just re-attaches to
# the Copilot already executing inside it. The plugin/runtime update is
# irrelevant to that running process (it applies on the process's next fresh
# start), so paying for the staged download join + installer + pre-launch here
# only delays the re-attach. When a live `wt-<id>` session exists, skip the
# apply for a fast jump-back-in; a fresh create/resume (no live session) still
# updates normally. Self-contained probe (mirrors the Windows launcher's
# Test-AwJoiningLiveSession, dev329) so it stays a surgical gate without
# reordering the rest of the launcher. The staged background download started
# earlier is harmless if left running -- it primes the cache for the next fresh
# start and never blocks this re-attach.
aw_joining_live_session() {
    # No-mux launches always (re)start Copilot directly -- the update is relevant.
    local _nomux_env="${WORKTREE_NO_MUX:-}"
    local _nomux_plan
    _nomux_plan=$(echo "$JSON" | "$PYTHON" -c "import sys,json; d=json.load(sys.stdin); print('1' if d.get('no_mux') else '0')" 2>/dev/null) || _nomux_plan=0
    if [[ "$_nomux_env" == "1" || "$_nomux_plan" == "1" ]]; then
        return 1
    fi
    command -v tmux &>/dev/null || return 1
    local _wtid
    _wtid=$(echo "$JSON" | "$PYTHON" -c "import sys,json; d=json.load(sys.stdin); print(d.get('worktree_id') or 'base')" 2>/dev/null) || _wtid=base
    [[ -z "$_wtid" ]] && _wtid=base
    # `.` is the window/pane separator in a target spec -- keep in sync with
    # `sessions.mux_session_name` (and the session name built below).
    _wtid="${_wtid//./_}"
    # `=`-prefix forces an exact session-name match (mirrors the join probe below).
    tmux has-session -t "=wt-${_wtid}" 2>/dev/null
}

if [[ "$ACTION" == "exec" ]]; then
    # ── Join the background update + apply, before the tmux handoff (#1430) ──
    # The Picker has closed, so it is now safe to swap the runtime venv. This
    # waits for the staged marketplace download, runs the installer if it
    # changed the payload (no re-exec -- a launcher change applies next launch),
    # then the pre-launch self-update and plugin reconcile. Skipped entirely on
    # a fast re-attach to an already-live session (see above).
    _JOINING_LIVE=0
    if aw_joining_live_session; then
        _JOINING_LIVE=1
        setup_log INFO 'Joining an already-live mux session; skipping pre-launch update for a fast re-attach (update applies on the process next fresh start).'
    else
        invoke_update_apply 1 1
    fi

    WORK_DIR=$(echo "$JSON" | "$PYTHON" -c "import sys,json; d=json.load(sys.stdin); print(d.get('work_dir',''))")
    # Path the status-bar updater renders from. Normally the pane cwd (work_dir),
    # but for deprecated Bare resume work_dir is HOME while the bar must still
    # show the worktree's identity + git disposition -- so prefer status_path
    # (the real worktree) when present.
    STATUS_PATH=$(echo "$JSON" | "$PYTHON" -c "import sys,json; d=json.load(sys.stdin); print(d.get('status_path') or d.get('work_dir',''))")
    POST_EXIT=$(echo "$JSON" | "$PYTHON" -c "import sys,json; d=json.load(sys.stdin); print('1' if d.get('post_exit') else '0')")
    WORKTREE_ID=$(echo "$JSON" | "$PYTHON" -c "import sys,json; d=json.load(sys.stdin); print(d.get('worktree_id') or '')")
    NO_MUX=$(echo "$JSON" | "$PYTHON" -c "import sys,json; d=json.load(sys.stdin); print('1' if d.get('no_mux') else '0')")

    # Env var override takes precedence
    _NO_MUX="${WORKTREE_NO_MUX:-}"
    if [[ "$_NO_MUX" == "1" ]]; then
        NO_MUX="1"
    fi

    # Durable launcher-start mark (Tier-A). Fires once WORKTREE_ID + mux mode
    # are known, before the (possibly hanging) mux/handoff step, so a launcher
    # that dies mid-flow still leaves a persistent trace. Records the mux mode
    # and links to the verbose Tier-B setup log via setup_log=.
    activity_log launcher_started "$WORKTREE_ID" \
        "mux=$([[ "$NO_MUX" == "1" ]] && echo none || echo tmux)" \
        "setup_log=$SETUP_LOG"

    # Worktree ID stays a LOCAL (non-exported) shell var: the launcher uses it
    # for the tmux session name, activity log, handoff, and post-exit, but it is
    # NOT exported into the child Copilot session. In-session tools resolve the
    # worktree from CWD (git-like), so no identity env var is leaked. See the
    # env -u prefix on the child launches below.

    # Export profile env vars (BYOK, offline mode, token limits, etc.)
    # Uses shlex.quote for safe shell quoting; keys are validated alphanumeric.
    ENV_EXPORTS=$(echo "$JSON" | "$PYTHON" -c "
import sys, json, shlex
d = json.load(sys.stdin)
for k, v in d.get('env', {}).items():
    print(f'export {k}={shlex.quote(str(v))}')
" 2>/dev/null) || true
    if [[ -n "$ENV_EXPORTS" ]]; then
        eval "$ENV_EXPORTS"
    fi

    # Build command array from JSON
    eval "CMD_ARRAY=( $( echo "$JSON" | "$PYTHON" -c "
import sys, json, shlex
d = json.load(sys.stdin)
print(' '.join(shlex.quote(a) for a in d.get('cmd', [])))
") )"

    SESSION_BACKEND_JSON=""
    _BACKEND_ENABLED=0
    if [[ -n "$WORKTREE_ID" && "$_JOINING_LIVE" != "1" ]]; then
        _BACKEND_BASE_ARGS=(-m agent_worktrees)
        [[ -n "$LAUNCH_PROJECT" ]] \
            && _BACKEND_BASE_ARGS+=(--project "$LAUNCH_PROJECT")
        _BACKEND_STATUS_ARGS=(
            "${_BACKEND_BASE_ARGS[@]}" session-backend status
            --worktree-id "$WORKTREE_ID" --json
        )
        if SESSION_BACKEND_STATUS_JSON=$(
            "$PYTHON" "${_BACKEND_STATUS_ARGS[@]}" 2>&1
        ); then
            :
        else
            _BACKEND_RC=$?
            setup_log ERROR "Session backend status failed (exit $_BACKEND_RC): $SESSION_BACKEND_STATUS_JSON"
            printf 'ERROR: Session backend status failed: %s\n' \
                "$SESSION_BACKEND_STATUS_JSON" >&2
            exit "$_BACKEND_RC"
        fi
        _BACKEND_ENABLED=$(printf '%s' "$SESSION_BACKEND_STATUS_JSON" | "$PYTHON" -c "
import json, sys
d = json.load(sys.stdin)
print('1' if d.get('enabled') and d.get('kind') == 'ahp' else '0')
")
        if [[ "$_BACKEND_ENABLED" == "1" ]]; then
            _AHP_ACCOUNT=$(printf '%s' "$SESSION_BACKEND_STATUS_JSON" | "$PYTHON" -c \
                "import json,sys; print(json.load(sys.stdin)['auth_account'])")
            if ! GH_TOKEN="$(gh auth token --user "$_AHP_ACCOUNT" 2>/dev/null)" \
                || [[ -z "$GH_TOKEN" ]]; then
                setup_log ERROR "Could not mint the AHP client token for account $_AHP_ACCOUNT."
                printf 'ERROR: Could not mint the AHP client token for account %s.\n' \
                    "$_AHP_ACCOUNT" >&2
                exit 3
            fi
            _BACKEND_ENSURE_ARGS=(
                "${_BACKEND_BASE_ARGS[@]}" session-backend ensure
                --worktree-id "$WORKTREE_ID" --json
            )
            if SESSION_BACKEND_JSON=$(
                AGENT_WORKTREES_AHP_AUTH_TOKEN="$GH_TOKEN" \
                    "$PYTHON" "${_BACKEND_ENSURE_ARGS[@]}" 2>&1
            ); then
                :
            else
                _BACKEND_RC=$?
                setup_log ERROR "Session backend ensure failed (exit $_BACKEND_RC): $SESSION_BACKEND_JSON"
                printf 'ERROR: Session backend ensure failed: %s\n' \
                    "$SESSION_BACKEND_JSON" >&2
                exit "$_BACKEND_RC"
            fi
            _AHP_ENDPOINT=$(printf '%s' "$SESSION_BACKEND_JSON" | "$PYTHON" -c \
                "import json,sys; print(json.load(sys.stdin)['endpoint_url'])")
            _AHP_SESSION=$(printf '%s' "$SESSION_BACKEND_JSON" | "$PYTHON" -c \
                "import json,sys; print(json.load(sys.stdin)['session_id'])")
            export -n GH_TOKEN 2>/dev/null || true
            if [[ ",${COPILOT_CLI_ENABLED_FEATURE_FLAGS:-}," != *",AHP_CLIENT,"* ]]; then
                if [[ -n "${COPILOT_CLI_ENABLED_FEATURE_FLAGS:-}" ]]; then
                    export COPILOT_CLI_ENABLED_FEATURE_FLAGS="${COPILOT_CLI_ENABLED_FEATURE_FLAGS},AHP_CLIENT"
                else
                    export COPILOT_CLI_ENABLED_FEATURE_FLAGS="AHP_CLIENT"
                fi
            fi
            POST_EXIT=0
            setup_log INFO "AHP client bound to session $_AHP_SESSION at $_AHP_ENDPOINT"
        fi
    fi

    # Append copilot passthrough args (from after -- separator)
    if [[ ${#COPILOT_PASSTHROUGH[@]} -gt 0 ]]; then
        CMD_ARRAY+=("${COPILOT_PASSTHROUGH[@]}")
    fi

    if [[ "$_BACKEND_ENABLED" == "1" ]]; then
        _FILTERED_CMD=()
        _SKIP_NEXT=0
        _HAS_EXPERIMENTAL=0
        for _ARG in "${CMD_ARRAY[@]}"; do
            if [[ "$_SKIP_NEXT" == "1" ]]; then
                _SKIP_NEXT=0
                continue
            fi
            case "$_ARG" in
                --ahp)
                    _SKIP_NEXT=1
                    ;;
                --ahp=*|--resume=*)
                    ;;
                --experimental)
                    _HAS_EXPERIMENTAL=1
                    _FILTERED_CMD+=("$_ARG")
                    ;;
                *)
                    _FILTERED_CMD+=("$_ARG")
                    ;;
            esac
        done
        CMD_ARRAY=("${_FILTERED_CMD[@]}")
        [[ "$_HAS_EXPERIMENTAL" == "1" ]] \
            || CMD_ARRAY+=(--experimental)
        CMD_ARRAY+=(--ahp "$_AHP_ENDPOINT" "--resume=$_AHP_SESSION")
    fi

    # Identity vars are stripped from the CHILD Copilot process so the session
    # env carries no ambient project/worktree identity -- in-session tools
    # resolve context from CWD (git-like). `env -u` runs inside the pane, so it
    # is robust to tmux-server-env inheritance. The launcher's own logic keeps
    # its local WORKTREE_ID / WORKTREE_PROJECT shell vars.
    CLEAN_ENV=(env -u WORKTREE_PROJECT -u WORKTREE_ID)

    if [[ -n "$WORK_DIR" ]]; then
        cd "$WORK_DIR"
    fi

    # Compose the paired private knowledge repo's plugin settings into the
    # harness worktree before Copilot starts and performs plugin discovery.
    # status_path is the real worktree during deprecated Bare resume.
    if ! _REFRESHED_PYTHON="$(resolve_runtime_python)"; then
        setup_log ERROR "Agent-worktrees runtime is unavailable after update apply; expected a complete slot under $RUNTIME_DIR"
        printf 'ERROR: Agent-worktrees runtime is unavailable after update apply; expected a complete slot under %s\n' \
            "$RUNTIME_DIR" >&2
        exit 1
    fi
    PYTHON="$_REFRESHED_PYTHON"
    setup_log INFO "Runtime refreshed before knowledge preflight: $PYTHON"

    _KNOWLEDGE_CWD="${STATUS_PATH:-${WORK_DIR:-$PWD}}"
    _KNOWLEDGE_ARGS=(-m agent_worktrees)
    [[ -n "$LAUNCH_PROJECT" ]] \
        && _KNOWLEDGE_ARGS+=(--project "$LAUNCH_PROJECT")
    _KNOWLEDGE_ARGS+=(knowledge compose-plugins --cwd "$_KNOWLEDGE_CWD" --json)
    if _KNOWLEDGE_JSON=$("$PYTHON" "${_KNOWLEDGE_ARGS[@]}" 2>&1); then
        setup_log INFO "Knowledge plugin preflight completed: $_KNOWLEDGE_JSON"
    else
        _KNOWLEDGE_RC=$?
        setup_log ERROR "Knowledge plugin preflight failed (exit $_KNOWLEDGE_RC): ${_KNOWLEDGE_JSON:-no details}"
        printf 'ERROR: Knowledge plugin preflight failed: %s\n' \
            "${_KNOWLEDGE_JSON:-no details}" >&2
        exit "$_KNOWLEDGE_RC"
    fi

    _MARKETPLACE_ARGS=(-m agent_worktrees reconcile-marketplaces
        --cwd "$_KNOWLEDGE_CWD" --ensure-ignored --json)
    if _MARKETPLACE_JSON=$("$PYTHON" "${_MARKETPLACE_ARGS[@]}" 2>&1); then
        setup_log INFO "Marketplace override preflight completed: $_MARKETPLACE_JSON"
    else
        _MARKETPLACE_RC=$?
        setup_log ERROR "Marketplace override preflight failed (exit $_MARKETPLACE_RC): ${_MARKETPLACE_JSON:-no details}"
        printf 'ERROR: Marketplace override preflight failed: %s\n' \
            "${_MARKETPLACE_JSON:-no details}" >&2
        exit "$_MARKETPLACE_RC"
    fi

    if [[ "$NO_MUX" == "1" ]]; then
        setup_log INFO "Mux disabled; launching directly"
    fi

    # ── tmux session-per-worktree (exec actions only) ─────────────────
    # Each worktree gets a single shared tmux session. Multiple terminal
    # connections (local, SSH) all land in the same session. The tmux
    # session ends when the launched process exits.
    #
    # WSL delegation (action=wsl) skips tmux — handled on the Linux side.
    # --no-mux / WORKTREE_NO_MUX=1 bypasses tmux for debugging.
    if [[ "$NO_MUX" != "1" && "$ACTION" == "exec" ]]; then
        if ! command -v tmux &>/dev/null; then
            setup_log ERROR "tmux is required for interactive sessions but was not found. Use --no-mux to request a direct session explicitly."
            activity_log mux_failed "$WORKTREE_ID" mux=tmux reason=not_found exit_code=1
            echo "ERROR: tmux is required for interactive sessions but was not found. Use --no-mux to request a direct session explicitly." >&2
            exit 1
        fi

        # tmux/psmux parse `.` in a target spec as the `window.pane` separator,
        # so a dotted WORKTREE_ID yields a session that can be created but never
        # addressed again ("can't find window: wt-<host>"). Worktree ids embed
        # the machine name, which is routinely dotted (every default macOS box
        # is `<name>.local`). Keep this in sync with `sessions.mux_session_name`.
        TMUX_SESS="wt-${WORKTREE_ID:-base}"
        TMUX_SESS="${TMUX_SESS//./_}"
        setup_log INFO "tmux: looking for session $TMUX_SESS"

        _aw_owned_tmux_session_id() {
            local sess="$1"
            [[ -n "${LAUNCH_ID:-}" ]] || return 1
            local session_id owner
            session_id="$(tmux display-message -p -t "=$sess" '#{session_id}' 2>/dev/null)" || return 1
            [[ -n "$session_id" ]] || return 1
            owner="$(tmux show-environment -t "$session_id" WORKTREE_LAUNCH_ID 2>/dev/null)" || return 1
            [[ "$owner" == "WORKTREE_LAUNCH_ID=$LAUNCH_ID" ]] || return 1
            printf '%s\n' "$session_id"
        }
        _aw_cleanup_owned_tmux_session() {
            local sess="$1"
            local session_id
            if session_id="$(_aw_owned_tmux_session_id "$sess")"; then
                tmux kill-session -t "$session_id" >/dev/null 2>&1 || true
                setup_log WARN "tmux: removed session owned by this launch: $sess"
            else
                setup_log WARN "tmux: cannot prove ownership of failed session $sess; leaving it intact"
            fi
        }

        # Per-session status bar + behaviors. agent-worktrees does NOT own your
        # global ~/.tmux.conf; instead we stamp these onto each session we
        # create/join, scoped to that session (`tmux set -t`, no -g), leaving
        # your personal config and any ad-hoc tmux sessions untouched.
        _AW_SESSION_OPTS="$HOME/.agent-worktrees/bin/session-options.sh"
        if [[ -r "$_AW_SESSION_OPTS" ]]; then
            # shellcheck source=/dev/null
            source "$_AW_SESSION_OPTS"
        fi
        _aw_apply_session_opts() {
            if declare -F aw_apply_tmux_session_options >/dev/null 2>&1; then
                aw_apply_tmux_session_options "$1" "${WORKTREE_ID:-}" || true
            fi
        }
        # Spawn the common, in-process Python status-updater (detached). It
        # keeps this session's @aw_ctx/@aw_seg vars fresh OFF the render path,
        # so the bar reads #{@aw_ctx}/#{@aw_seg} with zero spawn per repaint.
        # Safe to call on every create/join/handoff: an @aw_updater token
        # elects a single live updater and older ones self-retire. The updater
        # self-terminates within one interval of the session ending.
        _aw_spawn_status_updater() {
            local sess="$1"
            local aw; aw="$(command -v agent-worktrees 2>/dev/null || true)"
            [[ -x "$aw" ]] || aw="$HOME/.local/bin/agent-worktrees"
            [[ -x "$aw" ]] || return 0
            # Capture the worktree path BEFORE the subshell cd's away.
            local spath="${STATUS_PATH:-${WORK_DIR:-$PWD}}"
            # Root the detached loop at $HOME, never the caller's cwd: under the
            # sessionStart reseed hook the cwd is the plugin payload dir, and a
            # child holding it as its cwd blocks `copilot plugin update` from
            # replacing the payload on Windows (os error 32). Uniform on POSIX
            # (harmless: it locates its worktree via --path).
            ( cd "$HOME" 2>/dev/null || cd / ;
              env -u GH_TOKEN -u GITHUB_TOKEN \
                  -u AGENT_WORKTREES_AHP_AUTH_TOKEN \
                  setsid "$aw" status-updater --session "$sess" --mux tmux \
                  --path "$spath" >/dev/null 2>&1 < /dev/null & )
            disown 2>/dev/null || true
        }

        # If a tmux session already exists for this worktree, join it.
        # The attacher gets the shared view; no post-exit responsibility.
        if tmux has-session -t "=$TMUX_SESS" 2>/dev/null; then
            echo "Joining existing session: $TMUX_SESS"
            activity_log mux_attached "$WORKTREE_ID" mux=join
            # Refresh per-session options on (re)connect so a long-lived
            # session picks up the current bar without us owning the global.
            _aw_apply_session_opts "$TMUX_SESS"
            _aw_spawn_status_updater "$TMUX_SESS"
            set +e
            if [[ -n "${TMUX:-}" ]]; then
                tmux switch-client -t "=$TMUX_SESS"
            else
                tmux attach-session -t "=$TMUX_SESS"
            fi
            TMUX_ATTACH_EXIT=$?
            set -e
            if [[ "$TMUX_ATTACH_EXIT" -ne 0 ]]; then
                setup_log ERROR "Failed to attach to existing tmux session $TMUX_SESS (exit $TMUX_ATTACH_EXIT)."
                activity_log mux_failed "$WORKTREE_ID" mux=tmux reason=attach_failed "exit_code=$TMUX_ATTACH_EXIT"
                echo "ERROR: Failed to attach to existing tmux session '$TMUX_SESS' (exit code $TMUX_ATTACH_EXIT)." >&2
            fi
            exit "$TMUX_ATTACH_EXIT"
        fi

        # Create a new tmux session for this worktree.
        # The command is passed directly to new-session so the pane
        # (and session) exits when the process finishes — no lingering
        # shell.
        #
        # Disable errexit while creating the session so we can capture the
        # literal exit code, clean up any partial session, and emit diagnostics
        # before failing the launch.
        set +e
        setup_log INFO "tmux: creating session $TMUX_SESS"
        echo "Creating tmux session: $TMUX_SESS"
        echo ""

        # Propagate profile env vars into the tmux session.
        # The tmux server may predate this shell, so exported vars aren't
        # automatically inherited by new sessions. Identity vars
        # (WORKTREE_PROJECT/WORKTREE_ID) are deliberately NOT injected -- the
        # child resolves context from CWD, and CLEAN_ENV strips any inherited
        # copies inside the pane.
        TMUX_ENV_FLAGS=()
        if [[ -n "${SETUP_LOG:-}" ]]; then
            TMUX_ENV_FLAGS+=(-e "WORKTREE_SETUP_LOG=$SETUP_LOG")
        fi
        if [[ -n "${LAUNCH_ID:-}" ]]; then
            TMUX_ENV_FLAGS+=(-e "WORKTREE_LAUNCH_ID=$LAUNCH_ID")
        fi
        if [[ -n "$ENV_EXPORTS" ]]; then
            while IFS= read -r line; do
                # Strip 'export ' prefix → KEY=VALUE
                local_kv="${line#export }"
                if [[ "$_BACKEND_ENABLED" == "1" ]]; then
                    case "$local_kv" in
                        GH_TOKEN=*|GITHUB_TOKEN=*|AGENT_WORKTREES_AHP_AUTH_TOKEN=*)
                            continue
                            ;;
                    esac
                fi
                TMUX_ENV_FLAGS+=(-e "$local_kv")
            done <<< "$ENV_EXPORTS"
        fi
        if [[ "$_BACKEND_ENABLED" == "1" ]]; then
            TMUX_ENV_FLAGS+=(
                -e "COPILOT_CLI_ENABLED_FEATURE_FLAGS=$COPILOT_CLI_ENABLED_FEATURE_FLAGS"
            )
        fi

        # Pane wrapper — catches exit codes, records the pane_exited activity
        # mark, shows diagnostics on crash, and always exits 0 so
        # remain-on-exit doesn't trap the pane. `--aw-wt` carries the worktree
        # id for the mark and is consumed by the wrapper (not forwarded).
        PANE_WRAPPER="$HOME/.agent-worktrees/bin/pane-wrapper.sh"
        AHP_TOKEN_FILE=""
        if [[ "$_BACKEND_ENABLED" == "1" ]]; then
            if [[ ! -r "$PANE_WRAPPER" ]]; then
                setup_log ERROR "AHP tmux launch requires the pane wrapper at $PANE_WRAPPER"
                echo "ERROR: AHP tmux launch requires the agent-worktrees pane wrapper." >&2
                exit 3
            fi
            AHP_TOKEN_DIR=$(mktemp -d \
                "${TMPDIR:-/tmp}/agent-worktrees-ahp.XXXXXX") || exit 3
            if ! chmod 700 "$AHP_TOKEN_DIR"; then
                rmdir "$AHP_TOKEN_DIR" 2>/dev/null || true
                exit 3
            fi
            AHP_TOKEN_FILE="$AHP_TOKEN_DIR/token"
            if ! (umask 077 && printf '%s' "$GH_TOKEN" > "$AHP_TOKEN_FILE"); then
                rm -f -- "$AHP_TOKEN_FILE"
                rmdir "$AHP_TOKEN_DIR" 2>/dev/null || true
                exit 3
            fi
            trap '
                [[ -z "${AHP_TOKEN_FILE:-}" ]] || rm -f -- "$AHP_TOKEN_FILE"
                [[ -z "${AHP_TOKEN_DIR:-}" ]] || rmdir "$AHP_TOKEN_DIR" 2>/dev/null || true
            ' EXIT
        fi
        if [[ -r "$PANE_WRAPPER" ]]; then
            PANE_CONTROL=(--aw-wt "${WORKTREE_ID:-}")
            if [[ -n "$AHP_TOKEN_FILE" ]]; then
                PANE_CONTROL+=(--aw-ahp-token-file "$AHP_TOKEN_FILE")
            fi
            PANE_CMD=(
                "${CLEAN_ENV[@]}" bash "$PANE_WRAPPER"
                "${PANE_CONTROL[@]}" "${CMD_ARRAY[@]}"
            )
        else
            setup_log WARN "pane wrapper missing at $PANE_WRAPPER; using direct command"
            PANE_CMD=("${CLEAN_ENV[@]}" "${CMD_ARRAY[@]}")
        fi
        TMUX_COMMAND=(tmux)
        if [[ "$_BACKEND_ENABLED" == "1" ]]; then
            TMUX_COMMAND=(
                env -u GH_TOKEN -u GITHUB_TOKEN
                -u AGENT_WORKTREES_AHP_AUTH_TOKEN
                -u COPILOT_CLI_ENABLED_FEATURE_FLAGS tmux
            )
        fi

        TMUX_CREATE_MAX_ATTEMPTS=3
        TMUX_CREATE_TOTAL_ATTEMPTS=0
        TMUX_CREATE_EXIT=1
        TMUX_RETRY_CYCLE=1
        TMUX_RETRY_PROMPT="$_SHOW_LAUNCH_STATUS"
        for _copilot_arg in "${COPILOT_PASSTHROUGH[@]+"${COPILOT_PASSTHROUGH[@]}"}"; do
            [[ "$_copilot_arg" == "--stdio" ]] && TMUX_RETRY_PROMPT=0
        done
        while [[ "$TMUX_RETRY_CYCLE" == "1" ]]; do
            TMUX_RETRY_CYCLE=0
            for ((TMUX_CREATE_ATTEMPT=1;
                  TMUX_CREATE_ATTEMPT<=TMUX_CREATE_MAX_ATTEMPTS;
                  TMUX_CREATE_ATTEMPT++)); do
                TMUX_CREATE_TOTAL_ATTEMPTS=$((TMUX_CREATE_TOTAL_ATTEMPTS + 1))
                "${TMUX_COMMAND[@]}" new-session -d -s "$TMUX_SESS" \
                    -c "${WORK_DIR:-.}" \
                    "${TMUX_ENV_FLAGS[@]+"${TMUX_ENV_FLAGS[@]}"}" \
                    "${PANE_CMD[@]}"
                TMUX_CREATE_EXIT=$?
                [[ "$TMUX_CREATE_EXIT" -eq 0 ]] && break

                _aw_cleanup_owned_tmux_session "$TMUX_SESS"
                setup_log WARN \
                    "tmux: create attempt $TMUX_CREATE_ATTEMPT/$TMUX_CREATE_MAX_ATTEMPTS failed (exit $TMUX_CREATE_EXIT)"
                if [[ "$TMUX_CREATE_ATTEMPT" -lt "$TMUX_CREATE_MAX_ATTEMPTS" ]]; then
                    echo "WARNING: tmux startup attempt $TMUX_CREATE_ATTEMPT/$TMUX_CREATE_MAX_ATTEMPTS failed; retrying in 1 second..." >&2
                    sleep 1
                fi
            done

            if [[ "$TMUX_CREATE_EXIT" -ne 0 &&
                  "$TMUX_RETRY_PROMPT" == "1" && -t 0 && -t 1 ]]; then
                read -r -p "tmux could not create '$TMUX_SESS'. Retry? [y/N] " TMUX_RETRY_CHOICE
                case "$TMUX_RETRY_CHOICE" in
                    y|Y|yes|YES|Yes)
                        echo "Retrying tmux startup..." >&2
                        TMUX_RETRY_CYCLE=1
                        ;;
                esac
            fi
        done
        if [[ "$TMUX_CREATE_EXIT" -ne 0 ]]; then
            set -e
            RECOVERY_PROJECT="${LAUNCH_PROJECT:-agent-worktrees}"
            RECOVERY_COMMAND="$RECOVERY_PROJECT --worktree-id $WORKTREE_ID"
            PRESERVED_PATH="${STATUS_PATH:-${WORK_DIR:-.}}"
            setup_log ERROR "Failed to create tmux session $TMUX_SESS after $TMUX_CREATE_TOTAL_ATTEMPTS attempts (exit $TMUX_CREATE_EXIT). Worktree preserved at $PRESERVED_PATH; retry with: $RECOVERY_COMMAND"
            activity_log mux_failed "$WORKTREE_ID" mux=tmux reason=create_failed \
                "exit_code=$TMUX_CREATE_EXIT" "attempts=$TMUX_CREATE_TOTAL_ATTEMPTS" \
                recoverable=true
            echo "ERROR: Failed to create tmux session '$TMUX_SESS' after $TMUX_CREATE_TOTAL_ATTEMPTS attempts (exit code $TMUX_CREATE_EXIT). The worktree remains at '$PRESERVED_PATH'. Run '$RECOVERY_COMMAND' to retry, or use --no-mux to request a direct session explicitly." >&2
            exit "$TMUX_CREATE_EXIT"
        else

            activity_log mux_attached "$WORKTREE_ID" mux=create \
                "attempts=$TMUX_CREATE_TOTAL_ATTEMPTS"
            _aw_apply_session_opts "$TMUX_SESS"
            _aw_spawn_status_updater "$TMUX_SESS"
            if [[ -n "${TMUX:-}" ]]; then
                tmux switch-client -t "=$TMUX_SESS"
            else
                tmux attach-session -t "=$TMUX_SESS"
            fi
            TMUX_ATTACH_EXIT=$?
            set -e
            if [[ "$TMUX_ATTACH_EXIT" -ne 0 ]]; then
                setup_log ERROR "Failed to attach to new tmux session $TMUX_SESS (exit $TMUX_ATTACH_EXIT)."
                activity_log mux_failed "$WORKTREE_ID" mux=tmux reason=attach_failed "exit_code=$TMUX_ATTACH_EXIT"
                echo "ERROR: Failed to attach to new tmux session '$TMUX_SESS' (exit code $TMUX_ATTACH_EXIT)." >&2
                exit "$TMUX_ATTACH_EXIT"
            fi

            # We're back — either the user detached or the session ended.
            # Only run post-exit if the session is truly gone (user exited
            # the shell, not just detached).
            activity_log mux_detached "$WORKTREE_ID"
            if ! tmux has-session -t "=$TMUX_SESS" 2>/dev/null; then
                activity_log copilot_exited "$WORKTREE_ID" mux=tmux
                # Post-exit finalization
                if ! tmux has-session -t "=$TMUX_SESS" 2>/dev/null; then
                    if [[ "$POST_EXIT" == "1" && -n "$WORKTREE_ID" ]]; then
                        run_post_exit "$WORKTREE_ID" || \
                            echo "WARNING: Post-exit finalization failed. Run 'agent-worktrees finalize' to retry." >&2
                    fi
                fi
            fi

            exit 0
        fi
    fi

    # ── Direct launch (explicit --no-mux only) ────────────────────────

    if [[ "$NO_MUX" != "1" ]]; then
        setup_log ERROR "Internal launcher error: reached direct launch without --no-mux."
        activity_log mux_failed "$WORKTREE_ID" mux=tmux reason=unexpected_fallthrough exit_code=1
        echo "ERROR: Internal launcher error: reached direct launch without --no-mux." >&2
        exit 1
    fi

    setup_log INFO "Handing off to setup script"
    echo "Launching Copilot..."
    echo ""

    set +e
    if [[ "$_BACKEND_ENABLED" == "1" ]]; then
        GH_TOKEN="$GH_TOKEN" \
        COPILOT_CLI_ENABLED_FEATURE_FLAGS="$COPILOT_CLI_ENABLED_FEATURE_FLAGS" \
            env -u GITHUB_TOKEN -u AGENT_WORKTREES_AHP_AUTH_TOKEN \
            "${CLEAN_ENV[@]}" "${CMD_ARRAY[@]}"
    else
        "${CLEAN_ENV[@]}" "${CMD_ARRAY[@]}"
    fi
    COPILOT_EXIT=$?
    set -e
    activity_log copilot_exited "$WORKTREE_ID" mux=none "exit_code=$COPILOT_EXIT"

    # Post-exit finalization
    if [[ "$POST_EXIT" == "1" && -n "$WORKTREE_ID" ]]; then
        run_post_exit "$WORKTREE_ID" || \
            echo "WARNING: Post-exit finalization failed. Run 'agent-worktrees finalize' to retry." >&2
    fi

    exit $COPILOT_EXIT
fi

echo "ERROR: Unknown action: $ACTION" >&2
exit 1
