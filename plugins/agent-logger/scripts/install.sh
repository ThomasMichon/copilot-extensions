#!/usr/bin/env bash
# Agent Logger -- session-sync installer (Linux / WSL).
#
# Creates a venv at ~/.agent-logger, installs the agent-logger package, and
# registers a systemd *user* timer that runs `session-sync run --prune`
# every 4 hours. Idempotent.
#
# Usage:
#   bash scripts/install.sh install     # first time
#   bash scripts/install.sh update      # re-install package, keep timer
#   bash scripts/install.sh uninstall   # remove timer (keeps config)
#   bash scripts/install.sh status
set -euo pipefail

ACTION="${1:-status}"
INSTALL_DIR="${HOME}/.agent-logger"
VENV="${INSTALL_DIR}/.venv"
LOCAL_BIN="${HOME}/.local/bin"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# === install-contract:v4 self-stage -- keep byte-identical across plugins ===
# dotfiles #935: a plugin installer reads its own payload (src/, libs/,
# pyproject.toml) to build the venv, so while it runs -- especially if it wedges
# or times out -- it holds the SINGLETON installed-plugins/<mkt>/<plugin> payload
# dir busy (cwd/open handles). A concurrent `copilot plugin update <plugin>` then
# fights it (os error 32 on Windows; POSIX is more forgiving, but the design must
# be uniform): the payload freezes at the old version and reconcile keeps
# reverting the runtime toward it (the version-drift saga). Fix: when running
# from the marketplace payload, copy the WHOLE payload into a UNIQUE
# per-invocation staging dir OUTSIDE the payload and re-exec from there, so the
# singleton is touched only for the fast copy. A stalled run then holds only its
# own throwaway stage dir, never blocking the next invocation or a `copilot
# plugin update`. COPILOT_PLUGIN_STAGED_FROM tells _source_kind the payload was
# really the marketplace (see below). Env-guarded against re-exec loops; the
# stage-dir path (not under installed-plugins) is a second guard. The staging
# parent doubles as a WATCHDOG: it launches the staged child in its OWN session/
# process group and, on a deadline, kills the WHOLE group (POSIX process-group
# kill -- the twin of Windows `taskkill /T`), so a stalled install (the
# session-start-hook failure class) self-terminates instead of leaking forever.
# Best-effort, pid-guarded reap of dead-owner stage dirs (a concurrent or wedged
# installer's dir is never touched -- it uses its own unique dir).
if [[ -z "${COPILOT_PLUGIN_INSTALL_STAGED:-}" ]]; then
    __ss_self_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    __ss_payload="$(cd "$__ss_self_dir/.." && pwd)"
    case "$(printf '%s' "$__ss_payload" | tr '\\' '/')" in
        */.copilot/installed-plugins/*)
            __ss_name="$(sed -n 's/.*"name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$__ss_payload/plugin.json" 2>/dev/null | head -1)"
            if [[ -n "$__ss_name" ]]; then
                __ss_root="$HOME/.$__ss_name/.install-stage"
                __ss_stage="$__ss_root/$(date -u +%Y%m%dT%H%M%S)-$$"
                if mkdir -p "$__ss_stage" && cp -a "$__ss_payload" "$__ss_stage/"; then
                    __ss_staged_payload="$__ss_stage/$(basename "$__ss_payload")"
                    __ss_entry="$__ss_staged_payload/scripts/$(basename "${BASH_SOURCE[0]}")"
                    # Reap prior stage dirs; NEVER touch a live one. Remove only a
                    # sibling whose owner pid (the -<pid> suffix) is DEAD, so a
                    # concurrent or wedged installer's dir is left alone.
                    if [[ -d "$__ss_root" ]]; then
                        for __ss_sib in "$__ss_root"/*; do
                            [[ -d "$__ss_sib" ]] || continue
                            if [[ "$__ss_sib" == "$__ss_stage" ]]; then continue; fi
                            __ss_owner="${__ss_sib##*-}"
                            if [[ "$__ss_owner" =~ ^[0-9]+$ ]] && kill -0 "$__ss_owner" 2>/dev/null; then continue; fi
                            rm -rf "$__ss_sib" 2>/dev/null || true
                        done
                    fi
                    # WATCHDOG deadline: <NAME>_INSTALL_DEADLINE_SEC, else
                    # COPILOT_PLUGIN_INSTALL_DEADLINE_SEC, else 480s; <=0 disables.
                    __ss_deadline=480
                    __ss_dl_var="$(printf '%s' "$__ss_name" | sed 's/[^A-Za-z0-9][^A-Za-z0-9]*/_/g' | tr '[:lower:]' '[:upper:]')_INSTALL_DEADLINE_SEC"
                    __ss_dl_raw="${!__ss_dl_var:-}"
                    if [[ -z "$__ss_dl_raw" ]]; then __ss_dl_raw="${COPILOT_PLUGIN_INSTALL_DEADLINE_SEC:-}"; fi
                    if [[ "$__ss_dl_raw" =~ ^-?[0-9]+$ ]]; then __ss_deadline="$__ss_dl_raw"; fi
                    export COPILOT_PLUGIN_INSTALL_STAGED=1
                    export COPILOT_PLUGIN_STAGED_FROM="$__ss_payload"
                    # Launch the staged child in its OWN process group (bash job
                    # control) so `wait` propagates its REAL exit code AND the
                    # watchdog can kill the WHOLE tree via a process-group signal
                    # (the POSIX twin of Windows `taskkill /T`). setsid -w is
                    # avoided: on some util-linux builds it swallows the child's
                    # exit code (returns 0), which would mask a failed install.
                    set -m
                    bash "$__ss_entry" "$@" &
                    __ss_child=$!
                    set +m
                    if [[ "$__ss_deadline" -gt 0 ]]; then
                        (
                            __ss_waited=0
                            while kill -0 "$__ss_child" 2>/dev/null; do
                                sleep 1
                                __ss_waited=$((__ss_waited + 1))
                                if [[ "$__ss_waited" -ge "$__ss_deadline" ]]; then
                                    : > "$__ss_stage/.watchdog-fired"
                                    kill -- -"$__ss_child" 2>/dev/null || kill "$__ss_child" 2>/dev/null || true
                                    printf '[%sZ] WATCHDOG-KILL %s: install exceeded %ss deadline (child pid %s); killed tree. Slot lacks a completion marker -> will be tossed + retried. Stage: %s\n' \
                                        "$(date -u +%Y-%m-%dT%H:%M:%S)" "$__ss_name" "$__ss_deadline" "$__ss_child" "$__ss_stage" \
                                        >> "$HOME/.$__ss_name/reconcile.err.log" 2>/dev/null || true
                                    break
                                fi
                            done
                        ) &
                        __ss_watcher=$!
                        if wait "$__ss_child"; then __ss_rc=0; else __ss_rc=$?; fi
                        kill "$__ss_watcher" 2>/dev/null || true
                        wait "$__ss_watcher" 2>/dev/null || true
                        if [[ -e "$__ss_stage/.watchdog-fired" ]]; then exit 124; fi
                        exit "$__ss_rc"
                    fi
                    if wait "$__ss_child"; then exit 0; else exit $?; fi
                else
                    printf '  [WARN] self-stage failed, running in place\n' >&2
                fi
            fi
            ;;
    esac
fi
# === end install-contract:v4 self-stage ===

# === install-contract:v4 smoke seam (test-only) -- keep byte-identical ===
# #935 install-flow test hook. When COPILOT_PLUGIN_INSTALL_SMOKE is set, prove
# the self-stage/lock/watchdog behavior WITHOUT a heavy venv build: this
# (post-stage) process records where it runs from + the recorded marketplace
# origin, optionally spawns a grandchild sleeper in the SAME process group (so a
# watchdog test can prove the WHOLE tree is killed), then sleeps to simulate a
# slow/wedged install. Never set in production.
if [[ -n "${COPILOT_PLUGIN_INSTALL_SMOKE:-}" ]]; then
    __sm_self_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    __sm_payload="$(cd "$__sm_self_dir/.." && pwd)"
    __sm_name="$(sed -n 's/.*"name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$__sm_payload/plugin.json" 2>/dev/null | head -1)"
    __sm_home="$HOME/.$__sm_name"
    mkdir -p "$__sm_home"
    __sm_sleep=6
    if [[ "${COPILOT_PLUGIN_INSTALL_SMOKE_SLEEP:-}" =~ ^[0-9]+$ ]]; then __sm_sleep="$COPILOT_PLUGIN_INSTALL_SMOKE_SLEEP"; fi
    __sm_grand_pid=0
    if [[ -n "${COPILOT_PLUGIN_INSTALL_SMOKE_GRANDCHILD:-}" ]]; then
        __sm_grand_sleep="$__sm_sleep"
        if [[ "$__sm_grand_sleep" -lt 3600 ]]; then __sm_grand_sleep=3600; fi
        sleep "$__sm_grand_sleep" &
        __sm_grand_pid=$!
    fi
    __sm_staged=false
    if [[ -n "${COPILOT_PLUGIN_INSTALL_STAGED:-}" ]]; then __sm_staged=true; fi
    printf '{"ran_from":"%s","staged_from":"%s","staged":%s,"child_pid":%s,"grandchild_pid":%s}\n' \
        "$__sm_self_dir" "${COPILOT_PLUGIN_STAGED_FROM:-}" "$__sm_staged" "$$" "$__sm_grand_pid" \
        > "$__sm_home/smoke.json"
    sleep "$__sm_sleep"
    exit 0
fi
# === end install-contract:v4 smoke seam ===

# #935: bound uv's per-request network wait so a hung index/download degrades to
# "failed + retryable" rather than wedging the install; the self-stage watchdog
# is the authoritative TOTAL bound, this just shortens single-request stalls.
if [[ -z "${UV_HTTP_TIMEOUT:-}" ]]; then export UV_HTTP_TIMEOUT=60; fi

UNIT_DIR="${HOME}/.config/systemd/user"
TIMER_NAME="agent-logger-sync"

log()  { printf '  [%s] %s\n' "$1" "$2"; }
ok()   { log "OK" "$1"; }
chg()  { log "->" "$1"; }
warn() { log "WARN" "$1"; }

# === install-contract:v3 versioned-venv (agent-logger: .venv-as-symlink) ===
# Immutable per-version runtime (#581): build the venv into versions/<version> and
# make the `.venv` path a symlink into it, so the binstub symlinks, the systemd
# timer unit, and the deploy-manifest resolve through the link. LINK_DIR is the
# stable `.venv` path; VENV is redirected to the versions/<v> slot (build +
# health-gate). ALWAYS versioned -- the env opt-out (COPILOT_EXT_NO_VERSIONED /
# AGENT_LOGGER_VERSIONED) and the legacy in-place fork are retired;
# scripts/versioned_runtime.py owns the swap + migration + gc.
LINK_DIR="$VENV"
VERSIONED_RUNTIME=1
SRC_VERSION=""
if [[ -f "$PLUGIN_DIR/pyproject.toml" ]]; then
    SRC_VERSION="$(sed -n 's/^version *= *"\([^"]*\)".*/\1/p' "$PLUGIN_DIR/pyproject.toml" | head -n1)"
fi
if [[ -z "$SRC_VERSION" ]]; then
    echo "[FAIL] Cannot determine plugin version from pyproject.toml (required for the versioned runtime)." >&2
    exit 1
fi
VENV="$INSTALL_DIR/versions/$SRC_VERSION"

_versioned_activate() {
    # Health-gate the slot, swap the `.venv` symlink onto it (first migration moves
    # a legacy real `.venv` aside), gc keeping current + previous-good. POSIX rename
    # tolerates the timer's open files, and systemd restarts on the new slot.
    # Returns non-zero on failure. No-op in legacy mode.
    [[ "$VERSIONED_RUNTIME" == 1 ]] || return 0
    local vr="$SCRIPT_DIR/versioned_runtime.py"
    local py="$VENV/bin/python"  # runtime-resolution: allow install-time slot health-gate (VENV is the versioned slot)
    [[ -x "$py" ]] || py="$LINK_DIR/bin/python"
    [[ -x "$py" ]] || return 0
    if ! "$VENV/bin/python" -c 'import agent_logger' 2>/dev/null; then  # runtime-resolution: allow install-time slot health-gate
        warn "Fresh runtime slot failed its health gate (versions/$SRC_VERSION) -- not activating"
        return 1
    fi
    _versioned_mark_complete
    local prev
    prev="$("$py" "$vr" --root "$INSTALL_DIR" --link-name ".venv" current 2>/dev/null || echo "")"
    if ! "$py" "$vr" --root "$INSTALL_DIR" --link-name ".venv" activate "$SRC_VERSION" --replace-nonlink --no-link; then
        warn "Failed to activate versioned runtime slot (versions/$SRC_VERSION; marker-only, no .venv link)"
        return 1
    fi
    ok "Runtime version $SRC_VERSION active (marker-only; versions/$SRC_VERSION)"
    if [[ -n "$prev" ]]; then
        "$py" "$vr" --root "$INSTALL_DIR" --link-name ".venv" gc --protect-pids --keep "$prev" 2>&1 | sed 's/^/  gc: /' || true
    else
        "$py" "$vr" --root "$INSTALL_DIR" --link-name ".venv" gc --protect-pids 2>&1 | sed 's/^/  gc: /' || true
    fi
    return 0
}
# === end install-contract:v3 versioned-venv ===

_bootstrap_python() {
    # A python to run the stdlib-only versioned_runtime.py helper BEFORE the slot
    # venv exists (e.g. the pre-build toss). Prefers the current `venv` link's
    # python, then python3/python on PATH. Prints nothing + returns 1 if none
    # found (#935).
    if [[ -x "$LINK_DIR/bin/python" ]]; then echo "$LINK_DIR/bin/python"; return 0; fi
    local __c
    for __c in python3 python; do
        if command -v "$__c" >/dev/null 2>&1; then command -v "$__c"; return 0; fi
    done
    return 1
}

_payload_hash() {
    # Cheap payload fingerprint for the completion marker (#935): sha256 of
    # pyproject.toml + the vendored-lib version set. Detects a dev-checkout that
    # changed the payload WITHOUT bumping the version. Empty on any error.
    local __parts=""
    if [[ -f "$PLUGIN_DIR/pyproject.toml" ]]; then __parts="$(cat "$PLUGIN_DIR/pyproject.toml")"; fi
    if [[ -d "$PLUGIN_DIR/libs" ]]; then
        local __f
        while IFS= read -r __f; do
            __parts="$__parts"$'\n'"$(cat "$__f")"
        done < <(find "$PLUGIN_DIR/libs" -name pyproject.toml 2>/dev/null | sort)
    fi
    printf '%s' "$__parts" | sha256sum 2>/dev/null | awk '{print $1}' || true
}

_versioned_slot_clean() {
    # #935: ensure the target slot exists, tossing it first if a prior build left
    # it INCOMPLETE (no completion marker) so we never `uv venv --allow-existing`
    # over a corpse. The current/active slot is never tossed (the link-name is
    # derived from LINK_DIR so the current-slot guard works per plugin). No-op in
    # legacy mode.
    [[ "$VERSIONED_RUNTIME" == 1 ]] || return 0
    local vr="$SCRIPT_DIR/versioned_runtime.py"
    local py
    py="$(_bootstrap_python)" || return 0
    [[ -n "$py" ]] || return 0
    "$py" "$vr" --root "$INSTALL_DIR" --link-name "$(basename "$LINK_DIR")" slot "$SRC_VERSION" --clean-incomplete 2>&1 | sed 's/^/  ...    /' || true
}

_versioned_mark_complete() {
    # #935: write the slot's completion marker AFTER its isolated health gate
    # passed, so "marker present" == "healthy, complete build". A crashed /
    # watchdog-killed install never reaches here, leaving its slot markerless and
    # thus tossable + retryable. No-op in legacy mode. Runs the stdlib-only
    # versioned_runtime.py via any bootstrap python (the marker is slot-scoped, so
    # this helper is portable byte-identically across plugins).
    [[ "$VERSIONED_RUNTIME" == 1 ]] || return 0
    local vr="$SCRIPT_DIR/versioned_runtime.py"
    local py
    py="$(_bootstrap_python)" || return 0
    [[ -n "$py" ]] || return 0
    local ph
    ph="$(_payload_hash)"
    local args=("$vr" --root "$INSTALL_DIR" --link-name "$(basename "$LINK_DIR")" mark-complete "$SRC_VERSION")
    if [[ -n "$ph" ]]; then args+=(--payload-hash "$ph"); fi
    "$py" "${args[@]}" 2>&1 | sed 's/^/  ...    /' || true
}

# === install-contract:v4 source-kind -- keep byte-identical across plugins ===
# A runtime footprint's source is inferred from where the installer runs.
# Vendored under the Copilot CLI installed-plugins dir => marketplace;
# anything else (a git checkout) => local. #935: when the installer self-staged
# out of the marketplace payload, its live path is a throwaway stage dir, so
# infer the kind from the ORIGINAL payload path the self-stage prologue recorded
# in COPILOT_PLUGIN_STAGED_FROM (else the current path).
_source_kind() {
    case "$(printf '%s' "${COPILOT_PLUGIN_STAGED_FROM:-$1}" | tr '\\' '/')" in
        */.copilot/installed-plugins/*) printf 'marketplace' ;;
        *) printf 'local' ;;
    esac
}
# === end install-contract:v4 source-kind ===

_git_info() {
    local path="$1"
    local commit branch dirty
    commit=$(git -C "$path" rev-parse --short HEAD 2>/dev/null || echo "unknown")
    branch=$(git -C "$path" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
    dirty="false"
    if [[ -n "$(git -C "$path" status --porcelain 2>/dev/null)" ]]; then
        dirty="true"
    fi
    echo "$commit $branch $dirty"
}

# Unified schema_version 3 manifest writer. Self-contained per plugin (no shared
# module -- plugins are pulled independently from the marketplace). Records the
# source footprint (local vs marketplace) and is written atomically (temp+move).
_write_deploy_manifest() {
    local service="agent-logger" plugin="agent-logger"
    local manifest="${INSTALL_DIR}/deploy-manifest.json"
    local kind
    kind="$(_source_kind "$PLUGIN_DIR")"

    local ver="0.0.0"
    if [[ -f "$PLUGIN_DIR/pyproject.toml" ]]; then
        ver=$(grep -m1 '^version' "$PLUGIN_DIR/pyproject.toml" | sed 's/.*"\(.*\)".*/\1/' || echo "0.0.0")
    fi

    # Git provenance only applies to a local checkout.
    local commit="null" branch="null" dirty="false"
    if [[ "$kind" == "local" ]]; then
        local repo_root c b d
        repo_root="$(cd "$PLUGIN_DIR/.." && pwd)"
        read -r c b d <<< "$(_git_info "$repo_root")"
        commit="\"$c\""; branch="\"$b\""; dirty="$d"
    fi

    local tmp="$manifest.tmp"
    cat > "$tmp" << EOF
{
  "schema_version": 3,
  "service": "$service",
  "deployed_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "deployed_by": "$(hostname)-$(uname -s | tr '[:upper:]' '[:lower:]')",
  "source": {
    "kind": "$kind",
    "path": "$PLUGIN_DIR",
    "repo": "copilot-extensions",
    "plugin": "$plugin",
    "version": "$ver",
    "commit": $commit,
    "branch": $branch,
    "dirty": $dirty
  },
  "venv": "$VENV",
  "runtime": "python"
}
EOF
    mv -f "$tmp" "$manifest"
    ok "deploy manifest written (source: $kind)"
}

# --- self-provisioning helpers (runtime-self-provisioning pattern) -----------
# Vendor a standalone uv into the runtime tool dir when uv is absent (pristine or
# governed box) instead of dead-ending; add it to PATH for this run.
_ensure_uv() {
    command -v uv >/dev/null 2>&1 && return 0
    local tooldir="${INSTALL_DIR}/tool"
    if [ -x "${tooldir}/uv" ]; then export PATH="${tooldir}:$PATH"; return 0; fi
    chg "uv not found -- vendoring a standalone uv into ${tooldir}"
    mkdir -p "${tooldir}"
    local url="https://astral.sh/uv/install.sh" script="${tooldir}/uv-install.sh" got=""
    if command -v curl >/dev/null 2>&1; then curl -LsSf "$url" -o "$script" 2>/dev/null && got=1; fi
    if [ -z "$got" ] && command -v wget >/dev/null 2>&1; then wget -qO "$script" "$url" 2>/dev/null && got=1; fi
    if [ -z "$got" ] && command -v python3 >/dev/null 2>&1; then
        python3 - "$url" "$script" <<'PY' 2>/dev/null && got=1
import sys, urllib.request
urllib.request.urlretrieve(sys.argv[1], sys.argv[2])
PY
    fi
    if [ -n "$got" ] && [ -s "$script" ]; then
        env UV_INSTALL_DIR="${tooldir}" UV_UNMANAGED_INSTALL="${tooldir}" INSTALLER_NO_MODIFY_PATH=1 sh "$script" >/dev/null 2>&1 || true
    fi
    [ -x "${tooldir}/bin/uv" ] && [ ! -x "${tooldir}/uv" ] && ln -sf "${tooldir}/bin/uv" "${tooldir}/uv" 2>/dev/null || true
    if [ -x "${tooldir}/uv" ]; then export PATH="${tooldir}:$PATH"; ok "vendored uv into ${tooldir}"; return 0; fi
    warn "uv is required but not found, and vendoring failed (no reachable uv installer). Install uv, then retry."
    return 1
}

# Mirror pip's configured index to uv on a governed box (public PyPI TLS-blocked):
# uv does not read pip.conf, so derive index-url from pip config / the pip.conf
# files and export it. No-op where pip has no index (e.g. pristine -- the index
# then arrives via env / the clean-room fixture).
_ensure_uv_index() {
    [ -n "${UV_INDEX_URL:-}${UV_DEFAULT_INDEX:-}" ] && return 0
    local idx=""
    if command -v pip >/dev/null 2>&1; then idx="$(pip config get global.index-url 2>/dev/null | tr -d '[:space:]' || true)"; fi
    if [ -z "$idx" ] && command -v pip3 >/dev/null 2>&1; then idx="$(pip3 config get global.index-url 2>/dev/null | tr -d '[:space:]' || true)"; fi
    if [ -z "$idx" ]; then
        local f
        for f in "${PIP_CONFIG_FILE:-}" "$HOME/.config/pip/pip.conf" "$HOME/.pip/pip.conf" /etc/pip.conf /etc/xdg/pip/pip.conf; do
            [ -n "$f" ] && [ -f "$f" ] || continue
            idx="$(sed -n 's/^[[:space:]]*index-url[[:space:]]*=[[:space:]]*//p' "$f" | head -n1 | tr -d '[:space:]')"
            [ -n "$idx" ] && break
        done
    fi
    if [ -n "$idx" ]; then export UV_DEFAULT_INDEX="$idx"; chg "uv index derived from pip config (governed-feed bridge)"; fi
}

# Deploy the self-provisioning `agent-logger` binstub (install-on-first-use). Fast
# path execs the venv's `agent-logger` console script; otherwise it provisions on
# first use -- announcing (a human line + a machine-readable ::agent-provisioning::
# signal so a caller can extend its timeout), lock-serialized, fail-fast. The 5
# auxiliary console-script binstubs (session-sync, collate-session, ...) are plain
# symlinks created only by a full provision.
deploy_binstub() {
    mkdir -p "${LOCAL_BIN}" "${INSTALL_DIR}/bin"
    # Co-deploy the canonical marker-only resolver (uniform-runtime-resolution, #765).
    for r in resolve-runtime.sh resolve-runtime.ps1; do
        [ -f "${SCRIPT_DIR}/$r" ] && cp -f "${SCRIPT_DIR}/$r" "${INSTALL_DIR}/bin/$r"
    done
    cat > "${LOCAL_BIN}/agent-logger" << 'STUBEOF'
#!/usr/bin/env bash
# agent-logger binstub -- self-provisioning (install-on-first-use).
# Resolves the interpreter SOLELY via the junction-free versioned-runtime marker
# (the deployed resolve-runtime.sh; uniform-runtime-resolution, #765): current-
# version -> last-known-good -> newest complete slot. NEVER a `.venv` link, NEVER
# a PATH python -- when no slot is installed AGENT_RT_PY is empty and we self-
# provision on first use rather than silently binding the system interpreter.
export PYTHONUTF8=1
_name="agent-logger"
_root="$HOME/.$_name"
_resolver="$_root/bin/resolve-runtime.sh"
_resolve() {
    AGENT_RT_PY=""
    if [ -f "$_resolver" ]; then
        AGENT_RT_ROOT="$_root"
        . "$_resolver"
    fi
}
_resolve
[ -n "$AGENT_RT_PY" ] && exec "$AGENT_RT_PY" -m agent_logger "$@"
mkdir -p "$_root"
_status="$_root/.provision-status"
printf '%s\n' "[$_name] runtime not provisioned -- provisioning on first use (may take ~30-120s: acquires uv + builds a venv). Do not kill; extend your timeout." >&2
printf '::agent-provisioning:: plugin=%s eta_seconds=120 reason=first-use status=%s\n' "$_name" "$_status" >&2
_install="$(cat "$_root/payload-dir" 2>/dev/null)/scripts/install.sh"
[ -f "$_install" ] || _install="$(ls "$HOME"/.copilot/installed-plugins/*/"$_name"/scripts/install.sh 2>/dev/null | head -n1)"
if [ ! -f "$_install" ]; then
    printf '%s\n' "[$_name] cannot self-provision: installer not found in plugin payload. Ensure the plugin is enabled, then retry." >&2
    exit 127
fi
_lock="$_root/.provision.lock"
exec 9>"$_lock"
command -v flock >/dev/null 2>&1 && flock 9 2>/dev/null
_resolve
[ -n "$AGENT_RT_PY" ] && exec "$AGENT_RT_PY" -m agent_logger "$@"
printf 'provisioning %s\n' "$(date -u +%FT%TZ 2>/dev/null)" > "$_status" 2>/dev/null || true
bash "$_install" provision >&2
_rc=$?
_resolve
if [ "$_rc" -eq 0 ] && [ -n "$AGENT_RT_PY" ]; then
    printf 'ready %s\n' "$(date -u +%FT%TZ 2>/dev/null)" > "$_status" 2>/dev/null || true
    exec "$AGENT_RT_PY" -m agent_logger "$@"
fi
printf 'failed rc=%s %s\n' "$_rc" "$(date -u +%FT%TZ 2>/dev/null)" > "$_status" 2>/dev/null || true
if [ "$_rc" -eq 0 ]; then
    printf '%s\n' "[$_name] provisioning reported success but no runtime slot resolved." >&2
    _rc=1
else
    printf '%s\n' "[$_name] provisioning FAILED (rc=$_rc). See the log above; retry, or run: bash \"$_install\" provision" >&2
fi
exit "$_rc"
STUBEOF
    chmod +x "${LOCAL_BIN}/agent-logger"
    ok "binstub: ${LOCAL_BIN}/agent-logger (self-provisioning)"
}

# Cheap 'stamp': splat the agent-logger binstub + payload marker, defer the venv
# build to first use (fits a sessionStart hook's grace window). No venv, no uv.
do_stamp() {
    mkdir -p "${INSTALL_DIR}" "${LOCAL_BIN}"
    printf '%s\n' "${COPILOT_PLUGIN_STAGED_FROM:-$PLUGIN_DIR}" > "${INSTALL_DIR}/payload-dir"
    deploy_binstub
    ok "stamped: binstub on PATH; runtime provisions on first use."
}

install_package() {
  mkdir -p "${INSTALL_DIR}" "${LOCAL_BIN}"

  # Prerequisite: uv (venv + package management per the install contract).
  # Self-acquire uv (vendored if absent) + mirror the governed pip index to uv so
  # a solo/standalone install works on a pristine or governed box.
  _ensure_uv_index
  _ensure_uv || exit 1

  if [ ! -x "${VENV}/bin/python" ]; then
    _versioned_slot_clean
    if ! uv venv "${VENV}" --python 3.10 --allow-existing; then
      uv venv "${VENV}" --allow-existing
    fi
    chg "created venv at ${VENV}"
  fi
  # Vendored config-schema-migration lib (agent-config-migrate / module
  # config_migrate): plugin-vendored (marketplace) or repo-root (git checkout).
  local cfg_migrate_dir="${PLUGIN_DIR}/libs/config-migrate"
  if [ ! -f "${cfg_migrate_dir}/pyproject.toml" ]; then
    cfg_migrate_dir="$(cd "${PLUGIN_DIR}/../.." && pwd)/libs/config-migrate"
  fi
  if [ -f "${cfg_migrate_dir}/pyproject.toml" ]; then
    uv pip install --python "${VENV}/bin/python" --reinstall-package agent-config-migrate "${cfg_migrate_dir}" --quiet
  fi
  uv pip install --python "${VENV}/bin/python" "${PLUGIN_DIR}" --quiet
  ok "installed agent-logger package"

  # Versioned layout (#581): health-gate the slot + swap the `.venv` symlink.
  _versioned_activate || exit 1

  # Binstubs on PATH -> venv console scripts (the sanctioned POSIX launch path).
  # Both the service CLIs and the segmenter tools the log-session skill and
  # session-log-writer agent invoke, so they resolve on PATH rather than assuming
  # a bare command that was never deployed. Point at the stable `.venv` link
  # ($LINK_DIR), never a versions/<v> absolute a `gc` could remove.
  for name in session-sync collate-session read-session-digest prepare-session-log ramp-up-session; do
    ln -sf "${LINK_DIR}/bin/${name}" "${LOCAL_BIN}/${name}"
  done
  # The primary `agent-logger` entrypoint is a self-provisioning binstub (not a
  # plain symlink) so it can rebuild the runtime on first use in a confined host.
  deploy_binstub
  ok "linked binstubs into ${LOCAL_BIN}"

  # Machine-local config schema migration (idempotent + atomic). Non-fatal.
  if PYTHONUTF8=1 "${VENV}/bin/agent-logger" config-migrate 2>/dev/null; then
    :
  else
    warn "config migration skipped"
  fi
}

write_units() {
  mkdir -p "${UNIT_DIR}"
  cat > "${UNIT_DIR}/${TIMER_NAME}.service" <<EOF
[Unit]
Description=Agent Logger session-sync -- push Copilot session data to the configured target
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=${VENV}/bin/session-sync run --prune
# Generous start timeout: the FIRST sync cold-copies the entire session
# history (potentially thousands of sessions over a CIFS mount) and can take
# 10+ minutes; 120s killed it mid-copy. Incremental runs finish in seconds.
# (RuntimeMaxSec has no effect on Type=oneshot -- systemd ignores it -- so the
# run is bounded by TimeoutStartSec instead.)
TimeoutStartSec=1800
SyslogIdentifier=${TIMER_NAME}
EOF

  cat > "${UNIT_DIR}/${TIMER_NAME}.timer" <<EOF
[Unit]
Description=Agent Logger session-sync catch-up (periodic)

[Timer]
OnBootSec=5min
OnUnitActiveSec=4h
RandomizedDelaySec=10min
Persistent=true

[Install]
WantedBy=timers.target
EOF
  chg "wrote systemd user units to ${UNIT_DIR}"
}

case "${ACTION}" in
  install)
    install_package
    write_units
    systemctl --user daemon-reload
    systemctl --user enable --now "${TIMER_NAME}.timer"
    _write_deploy_manifest
    ok "timer enabled (every 4h)"
    ;;
  stamp)
    do_stamp
    ;;
  provision)
    install_package
    _write_deploy_manifest
    ok "runtime provisioned"
    ;;
  update)
    install_package
    write_units
    systemctl --user daemon-reload || true
    _write_deploy_manifest
    ok "package + units updated"
    ;;
  uninstall)
    systemctl --user disable --now "${TIMER_NAME}.timer" 2>/dev/null || true
    rm -f "${UNIT_DIR}/${TIMER_NAME}.service" "${UNIT_DIR}/${TIMER_NAME}.timer"
    systemctl --user daemon-reload || true
    chg "timer removed (config at ${INSTALL_DIR} kept)"
    for name in session-sync agent-logger collate-session read-session-digest prepare-session-log ramp-up-session; do
      rm -f "${LOCAL_BIN}/${name}"
    done
    chg "binstubs removed from ${LOCAL_BIN}"
    ;;
  status)
    if [ -x "${LINK_DIR}/bin/session-sync" ]; then
      ok "installed: $("${LINK_DIR}/bin/agent-logger" version 2>/dev/null || echo unknown)"
      "${LINK_DIR}/bin/session-sync" status || true
    else
      warn "not installed (run: bash scripts/install.sh install)"
    fi
    systemctl --user is-active "${TIMER_NAME}.timer" 2>/dev/null \
      && ok "timer active" || warn "timer not active"
    ;;
  *)
    echo "usage: install.sh {install|stamp|provision|update|uninstall|status}" >&2
    exit 2
    ;;
esac
