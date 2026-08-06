#!/usr/bin/env bash
# Bootstrap the agent-containers runtime (Linux / WSL / macOS).
#
# Creates the shared runtime at ~/.agent-containers/ -- a venv with the
# agent_containers package installed (via uv pip install) -- and deploys the
# `agent-containers` binstub into ~/.local/bin.
#
# Run once per machine. Idempotent -- safe to re-run for repairs or upgrades.
#
# Usage:
#   ./init.sh [--force] [--install-dir DIR]

set -euo pipefail

_ok()   { printf '  [OK]   %s\n' "$1"; }
_skip() { printf '  [SKIP] %s\n' "$1"; }
_fail() { printf '  [FAIL] %s\n' "$1" >&2; }
_step() { printf '  ...    %s\n' "$1"; }

FORCE=0
INSTALL_DIR=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --force) FORCE=1; shift ;;
        --install-dir) INSTALL_DIR="$2"; shift 2 ;;
        *) shift ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

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

PKG_SRC_DIR="$PLUGIN_DIR/src/agent_containers"

INSTALL_DIR="${INSTALL_DIR:-$HOME/.agent-containers}"
VENV_DIR="$INSTALL_DIR/.venv"
LOCAL_BIN="$HOME/.local/bin"
VENV_PYTHON="$VENV_DIR/bin/python"

# === install-contract:v3 versioned-venv -- keep byte-identical across plugins ===
# Immutable per-version runtime (#581): build the venv into versions/<version>
# and make the historical .venv path a symlink (POSIX) / junction (Windows) into
# it, so the binstub + deploy-manifest (which reference .venv) resolve through the
# link unchanged. .venv stays the stable reference; a version bump builds a new
# slot beside the old one and atomically swaps the link (never mutates a live
# venv). Opt out with COPILOT_EXT_NO_VERSIONED=1. scripts/versioned_runtime.py
# owns the swap + migration.
LINK_DIR="$VENV_DIR"                        # stable path the binstub/manifest reference
VERSIONED_RUNTIME=1
[[ "${COPILOT_EXT_NO_VERSIONED:-}" == "1" ]] && VERSIONED_RUNTIME=0
SRC_VERSION="$(sed -n 's/^version *= *"\([^"]*\)".*/\1/p' "$PLUGIN_DIR/pyproject.toml" 2>/dev/null || true)"
if [[ "$VERSIONED_RUNTIME" -eq 1 && -n "$SRC_VERSION" ]]; then
    VENV_DIR="$INSTALL_DIR/versions/$SRC_VERSION"
    VENV_PYTHON="$VENV_DIR/bin/python"
else
    VERSIONED_RUNTIME=0
fi
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
# credential-relay dir (vendored): plugin-vendored or repo-root. Force-reinstalled
# below so a local code change propagates even without a version bump.
CRED_RELAY_DIR="$PLUGIN_DIR/libs/credential-relay"
if [[ ! -f "$CRED_RELAY_DIR/pyproject.toml" ]]; then
    CRED_RELAY_DIR="$(cd "$PLUGIN_DIR/../.." && pwd)/libs/credential-relay"
fi
# config-migrate dir (vendored like credential-relay): plugin-vendored or repo-root.
CFG_MIGRATE_DIR="$PLUGIN_DIR/libs/config-migrate"
if [[ ! -f "$CFG_MIGRATE_DIR/pyproject.toml" ]]; then
    CFG_MIGRATE_DIR="$(cd "$PLUGIN_DIR/../.." && pwd)/libs/config-migrate"
fi

echo ''
echo '=== agent-containers init ==='
echo ''

if [[ ! -d "$PKG_SRC_DIR" ]]; then
    _fail "Package source not found at $PKG_SRC_DIR"
    exit 1
fi

# Find a Python interpreter
PYTHON_CMD=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        if "$candidate" --version 2>&1 | grep -qi python; then
            PYTHON_CMD="$candidate"
            break
        fi
    fi
done
if [[ -z "$PYTHON_CMD" ]]; then
    _fail 'Python not found on PATH (need 3.10+)'
    exit 1
fi
_ok "Python: $PYTHON_CMD"

if command -v docker >/dev/null 2>&1; then
    _ok "Docker: $(docker --version 2>/dev/null)"
else
    _step 'docker CLI not found -- agent-containers requires Docker for fleet operations'
fi

HAVE_UV=0
if command -v uv >/dev/null 2>&1; then HAVE_UV=1; fi

# -- 1. Directories ----------------------------------------------------
mkdir -p "$INSTALL_DIR" "$LOCAL_BIN"
_ok "Directories: $INSTALL_DIR"

# -- 2. Venv -----------------------------------------------------------
if [[ "$FORCE" -eq 1 || ! -x "$VENV_PYTHON" ]]; then
    if [[ "$HAVE_UV" -eq 1 ]]; then
        _step 'Creating venv via uv...'
        _versioned_slot_clean
        uv venv "$VENV_DIR" --allow-existing >/dev/null 2>&1 || {
            _step 'uv venv failed -- falling back to python -m venv'
            "$PYTHON_CMD" -m venv "$VENV_DIR" >/dev/null 2>&1
        }
    else
        _step 'Creating venv via python -m venv...'
        "$PYTHON_CMD" -m venv "$VENV_DIR" >/dev/null 2>&1
    fi
    if [[ ! -x "$VENV_PYTHON" ]]; then
        _fail "Venv creation failed -- $VENV_PYTHON not found"
        exit 1
    fi
    _ok 'Venv created'
else
    _skip 'Venv already exists'
fi

# -- 3. Install the package into the venv ------------------------------
if [[ "$HAVE_UV" -eq 1 ]]; then
    # credential-relay first (vendored lib), force-reinstalled so local code
    # changes propagate even without a version bump; then agent-containers.
    if [[ ! -f "$CRED_RELAY_DIR/pyproject.toml" ]]; then
        _fail "credential-relay source not found at $CRED_RELAY_DIR"
        exit 1
    fi
    if ! uv pip install --python "$VENV_PYTHON" --reinstall-package agent-credential-relay "$CRED_RELAY_DIR" --quiet 2>/dev/null; then
        _fail 'credential-relay install failed'
        exit 1
    fi
    if [[ ! -f "$CFG_MIGRATE_DIR/pyproject.toml" ]]; then
        _fail "config-migrate source not found at $CFG_MIGRATE_DIR"
        exit 1
    fi
    if ! uv pip install --python "$VENV_PYTHON" --reinstall-package agent-config-migrate "$CFG_MIGRATE_DIR" --quiet 2>/dev/null; then
        _fail 'config-migrate install failed'
        exit 1
    fi
    if ! uv pip install --python "$VENV_PYTHON" "$PLUGIN_DIR" --quiet 2>/dev/null; then
        _fail 'Failed to install agent-containers package into venv'
        exit 1
    fi
else
    if ! "$VENV_PYTHON" -m pip install --quiet "$PLUGIN_DIR" 2>/dev/null; then
        _fail 'Failed to install agent-containers package into venv'
        exit 1
    fi
fi
_ok 'Package installed: agent-containers'

# === install-contract:v3 versioned-venv activate -- keep byte-identical across plugins ===
if [[ "$VERSIONED_RUNTIME" -eq 1 ]]; then
    # Point the stable .venv link at this version's freshly-built slot, moving a
    # legacy real .venv aside on the first migration. Run via the slot's own
    # python (stdlib-only helper); a CLI plugin has no daemon holding the link.
    VR_SCRIPT="$SCRIPT_DIR/versioned_runtime.py"
    # Health-gate (#935): never swap the stable .venv link onto a slot whose
    # package does not import -- a broken build must not become the live runtime.
    # The marker is written only after this gate passes (so "marked" == healthy).
    if ! "$VENV_PYTHON" -c 'import agent_containers' 2>/dev/null; then
        _fail "Fresh runtime slot failed its health gate (versions/$SRC_VERSION) -- not activating"
        exit 1
    fi
    _versioned_mark_complete
    if ! "$VENV_PYTHON" "$VR_SCRIPT" --root "$INSTALL_DIR" --link-name '.venv' \
            activate "$SRC_VERSION" --replace-nonlink >/dev/null 2>&1; then
        _fail "Failed to activate versioned venv (.venv -> versions/$SRC_VERSION)"
        exit 1
    fi
    _ok "Runtime version $SRC_VERSION active (.venv -> versions/$SRC_VERSION)"
fi
# === end install-contract:v3 versioned-venv activate ===

# -- 4. Binstub --------------------------------------------------------
STUB="$LOCAL_BIN/agent-containers"
cat > "$STUB" << 'STUBEOF'
#!/usr/bin/env bash
export PYTHONUTF8=1
exec "$HOME/.agent-containers/.venv/bin/python" -m agent_containers "$@"
STUBEOF
chmod +x "$STUB"
_ok "Binstub: $STUB"

# -- 5. Deploy manifest ------------------------------------------------

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
    local path="$1" commit branch dirty
    commit=$(git -C "$path" rev-parse --short HEAD 2>/dev/null || echo "unknown")
    branch=$(git -C "$path" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
    dirty="false"
    [[ -n "$(git -C "$path" status --porcelain 2>/dev/null)" ]] && dirty="true"
    echo "$commit $branch $dirty"
}

# Unified schema_version 3 manifest (install-contract): records the source
# footprint (marketplace vs local) so deploys are auditable like the siblings.
MANIFEST_PATH="$INSTALL_DIR/deploy-manifest.json"
KIND="$(_source_kind "$PLUGIN_DIR")"
VER="$(sed -n 's/^version *= *"\([^"]*\)".*/\1/p' "$PLUGIN_DIR/pyproject.toml" 2>/dev/null || echo 0.0.0)"
COMMIT="null"; BRANCH="null"; DIRTY="false"
if [[ "$KIND" == "local" ]]; then
    REPO_ROOT="$(cd "$PLUGIN_DIR/../.." && pwd)"
    read -r _c _b _d <<< "$(_git_info "$REPO_ROOT")"
    COMMIT="\"$_c\""; BRANCH="\"$_b\""; DIRTY="$_d"
fi
TMP="$MANIFEST_PATH.tmp"
cat > "$TMP" << EOF
{
  "schema_version": 3,
  "service": "agent-containers",
  "deployed_at": "$(date -u '+%Y-%m-%dT%H:%M:%SZ')",
  "deployed_by": "$(hostname)-$(uname -s | tr '[:upper:]' '[:lower:]')",
  "source": {
    "kind": "$KIND",
    "path": "$PLUGIN_DIR",
    "repo": "copilot-extensions",
    "plugin": "agent-containers",
    "version": "$VER",
    "commit": $COMMIT,
    "branch": $BRANCH,
    "dirty": $DIRTY
  },
  "venv": "$LINK_DIR",
  "runtime": "python"
}
EOF
mv -f "$TMP" "$MANIFEST_PATH"
_ok "Deploy manifest written (source: $KIND)"

# -- Machine-local config schema migration (idempotent + atomic; never touches
# a repo/cwd containers.yaml -- that is an adopt concern). Non-fatal. --
if PYTHONUTF8=1 "$VENV_PYTHON" -m agent_containers config-migrate 2>/dev/null; then
    :
else
    _step 'Config migration skipped'
fi

# -- 6. Verify ---------------------------------------------------------
echo ''
if "$VENV_PYTHON" -c 'import agent_containers' 2>/dev/null; then
    _ok 'Verification: module imports successfully'
else
    _fail 'Verification: module import failed'
    exit 1
fi

if "$VENV_PYTHON" -c 'import credential_relay' 2>/dev/null; then
    _ok 'credential-relay: importable in venv'
else
    _fail 'credential-relay not importable in venv'
    exit 1
fi

case ":$PATH:" in
    *":$LOCAL_BIN:"*) _ok "PATH: $LOCAL_BIN is on PATH" ;;
    *) _step "Add $LOCAL_BIN to your PATH (e.g. in ~/.bashrc): export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac

echo ''
echo '=== agent-containers init complete ==='
echo '  Try: agent-containers version'
exit 0
