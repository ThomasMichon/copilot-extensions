#!/usr/bin/env bash
# =============================================================================
# install.sh -- agent-index -- plugin installer for Linux / WSL / macOS
# =============================================================================
# Manages the agent-index service shell lifecycle: install, update, status,
# start, stop, uninstall. Runtime lives at ~/.agent-index/ and the binstub goes
# to ~/.local/bin/agent-index.
# =============================================================================

set -euo pipefail

_ok()   { printf '  [OK]   %s\n' "$1"; }
_skip() { printf '  [SKIP] %s\n' "$1"; }
_fail() { printf '  [FAIL] %s\n' "$1" >&2; }
_warn() { printf '  [WARN] %s\n' "$1" >&2; }
_step() { printf '  ...    %s\n' "$1"; }

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

PKG_SRC_DIR="$PLUGIN_DIR/src/agent_index"

ACTION="${1:-status}"
shift || true

NO_SERVICE=0
PURGE=0
INSTALL_DIR=""
FORCE="${AGENT_INDEX_ALLOW_DOWNGRADE:-0}"
[[ "$FORCE" == "1" ]] && FORCE=1 || FORCE=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-service) NO_SERVICE=1; shift ;;
        --purge) PURGE=1; shift ;;
        --force) FORCE=1; shift ;;
        --install-dir) INSTALL_DIR="$2"; shift 2 ;;
        *) shift ;;
    esac
done

INSTALL_DIR="${INSTALL_DIR:-$HOME/.agent-index}"
VENV_DIR="$INSTALL_DIR/.venv"
LOCAL_BIN="$HOME/.local/bin"
VENV_PYTHON="$VENV_DIR/bin/python"
STUB="$LOCAL_BIN/agent-index"
SYSTEMD_UNIT="agent-index.service"
UNIT_DIR="$HOME/.config/systemd/user"
ENV_FILE="$INSTALL_DIR/service.env"

# === engine-daemon: durable, persistent embedding-engine runtime =============
# The heavy embedding stack (torch + transformers + sentence-transformers) lives
# in a DURABLE venv OUTSIDE the versioned service runtime, at
# AGENT_INDEX_ENGINE_HOME (default ~/.agent-index/engine). It is provisioned ONCE
# and preserved across service updates -- a routine `update` swaps only the
# versioned service runtime + symlink and never rebuilds torch or restarts the
# warm engine daemon (effort agent-index-engine-daemon; vision §warm-durable-engine).
ENGINE_HOME="${AGENT_INDEX_ENGINE_HOME:-$HOME/.agent-index/engine}"
ENGINE_HOME="${ENGINE_HOME/#\~/$HOME}"
ENGINE_VENV="$ENGINE_HOME/.venv"
ENGINE_VENV_PYTHON="$ENGINE_VENV/bin/python"
ENGINE_ENV_FILE="$ENGINE_HOME/engine.env"
ENGINE_SYSTEMD_UNIT="agent-index-engine.service"
# === end engine-daemon ======================================================

# === install-contract:v3 versioned-venv (agent-index: .venv-as-symlink) ===
# Build each version into versions/<version> and make the historical `.venv`
# path a symlink into the active slot. Enabled by default (set AGENT_INDEX_VERSIONED=0
# or COPILOT_EXT_NO_VERSIONED=1 to opt out); COPILOT_EXT_NO_VERSIONED=1 force-disables.
LINK_DIR="$VENV_DIR"
LINK_PYTHON="$VENV_PYTHON"
VERSIONED_RUNTIME=0
SRC_VERSION=""
if [[ "${COPILOT_EXT_NO_VERSIONED:-}" != "1" && ! "${AGENT_INDEX_VERSIONED:-}" =~ ^(0|false|no|off)$ ]]; then
    if [[ -f "$PLUGIN_DIR/pyproject.toml" ]]; then
        SRC_VERSION="$(sed -n 's/^version *= *"\([^"]*\)".*/\1/p' "$PLUGIN_DIR/pyproject.toml" | head -n1)"
    fi
    if [[ -n "$SRC_VERSION" ]]; then
        VERSIONED_RUNTIME=1
        VENV_DIR="$INSTALL_DIR/versions/$SRC_VERSION"
        VENV_PYTHON="$VENV_DIR/bin/python"
    fi
fi

_versioned_activate() {
    [[ "$VERSIONED_RUNTIME" == 1 ]] || return 0
    local vr="$SCRIPT_DIR/versioned_runtime.py"
    local py="$VENV_DIR/bin/python"
    [[ -x "$py" ]] || py="$LINK_DIR/bin/python"
    if ! "$py" "$vr" --root "$INSTALL_DIR" --link-name ".venv" activate "$SRC_VERSION" --replace-nonlink; then
        _fail "Failed to activate versioned venv (.venv -> versions/$SRC_VERSION)"
        return 1
    fi
    _ok "Runtime version $SRC_VERSION active (.venv -> versions/$SRC_VERSION)"
}

_versioned_current() {
    [[ "$VERSIONED_RUNTIME" == 1 ]] || { echo ""; return 0; }
    local vr="$SCRIPT_DIR/versioned_runtime.py"
    local py="$LINK_DIR/bin/python"
    [[ -x "$py" ]] || py="$VENV_DIR/bin/python"
    [[ -x "$py" ]] || { echo ""; return 0; }
    "$py" "$vr" --root "$INSTALL_DIR" --link-name ".venv" current 2>/dev/null || echo ""
}

_versioned_gc() {
    local keep_prev="${1:-}"
    [[ "$VERSIONED_RUNTIME" == 1 ]] || return 0
    local vr="$SCRIPT_DIR/versioned_runtime.py"
    local py="$LINK_DIR/bin/python"
    [[ -x "$py" ]] || py="$VENV_DIR/bin/python"
    [[ -x "$py" ]] || return 0
    if [[ -n "$keep_prev" ]]; then
        "$py" "$vr" --root "$INSTALL_DIR" --link-name ".venv" gc --protect-pids --keep "$keep_prev" 2>&1 | sed 's/^/  ...    gc: /' || true
    else
        "$py" "$vr" --root "$INSTALL_DIR" --link-name ".venv" gc --protect-pids 2>&1 | sed 's/^/  ...    gc: /' || true
    fi
}
# === end install-contract:v3 versioned-venv ===

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

# Resolve a vendored library path (libs/<name>) across multiple layouts.
# Prints the resolved directory path to stdout (nothing else).
# Returns 0 if found, 1 if not.
_resolve_vendored_lib() {
    local lib_name="$1"
    local candidate

    # 1. Vendored inside agent-index (marketplace install layout)
    candidate="$PLUGIN_DIR/libs/$lib_name"
    if [[ -f "$candidate/pyproject.toml" ]]; then
        cd "$candidate" && pwd
        return 0
    fi

    # 2. Relative path (git checkout layout: plugins/agent-index/../../libs/<name>)
    candidate="$PLUGIN_DIR/../../libs/$lib_name"
    if [[ -f "$candidate/pyproject.toml" ]]; then
        cd "$candidate" && pwd
        return 0
    fi

    # 3. Git repo registry (~/.git-repos) -- use Python for safe YAML parsing
    if [[ -f "$HOME/.git-repos" ]]; then
        candidate="$(python3 -c "
import pathlib, os
try:
    import yaml
except ImportError:
    raise SystemExit(1)
reg = yaml.safe_load(pathlib.Path.home().joinpath('.git-repos').read_text())
repo = (reg or {}).get('repos', {}).get('copilot-extensions', {})
if repo:
    p = repo.get('path', os.path.join(reg.get('srcroot', ''), 'copilot-extensions'))
    p = os.path.expanduser(p)
    lib = os.path.join(p, 'libs', '$lib_name')
    if os.path.isfile(os.path.join(lib, 'pyproject.toml')):
        print(lib)
        raise SystemExit(0)
raise SystemExit(1)
" 2>/dev/null)" && {
            echo "$candidate"
            return 0
        }
    fi

    # 4. Common checkout path (repo exists but registry absent/stale)
    candidate="$HOME/src/copilot-extensions/libs/$lib_name"
    if [[ -f "$candidate/pyproject.toml" ]]; then
        cd "$candidate" && pwd
        return 0
    fi

    return 1
}

# zero-downtime cutover primitives (module ``zdd``), extracted from agent-bridge.
_resolve_zdd() { _resolve_vendored_lib zdd; }

# Check if the zdd cutover lib is already importable in the venv.
_zdd_installed() {
    [[ -x "$VENV_DIR/bin/python" ]] || return 1
    "$VENV_DIR/bin/python" -c 'from zdd.cutover import CutoverOrchestrator' 2>/dev/null
}

_installed_version() {
    [[ -x "$LINK_PYTHON" ]] || return 1
    local v
    v="$("$LINK_PYTHON" -c 'from importlib.metadata import version; print(version("agent-index"))' 2>/dev/null)" || return 1
    [[ -n "$v" ]] || return 1
    printf '%s\n' "$v"
}

_source_version() {
    local manifest="$PLUGIN_DIR/plugin.json"
    [[ -f "$manifest" ]] || return 1
    local v
    v="$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$manifest" | head -n1)"
    [[ -n "$v" ]] || return 1
    printf '%s\n' "$v"
}

_version_lt() {
    local a="${1//-/.}" b="${2//-/.}"
    [[ "$a" == "$b" ]] && return 1
    local lower
    lower="$(printf '%s\n%s\n' "$a" "$b" | sort -V | head -n1)"
    [[ "$lower" == "$a" ]]
}

_downgrade_guard() {
    local installed source
    installed="$(_installed_version)" || return 0
    source="$(_source_version)" || {
        _warn "Could not read source version from plugin.json -- skipping downgrade guard"
        return 0
    }
    if _version_lt "$source" "$installed"; then
        if [[ "$FORCE" -eq 1 ]]; then
            _warn "Downgrade $installed -> $source forced (--force / AGENT_INDEX_ALLOW_DOWNGRADE)"
            return 0
        fi
        _fail "Refusing to downgrade agent-index: installed $installed > source $source"
        exit 1
    fi
}

_find_python() {
    local candidate
    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1 \
           && "$candidate" --version 2>&1 | grep -qi python; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

_ensure_runtime() {
    if [[ ! -d "$PKG_SRC_DIR" ]]; then
        _fail "Package source not found at $PKG_SRC_DIR"
        exit 1
    fi
    local py
    py="$(_find_python)" || { _fail 'Python not found on PATH (need 3.10+)'; exit 1; }
    _ok "Python: $py"
    local have_uv=0
    command -v uv >/dev/null 2>&1 && have_uv=1

    mkdir -p "$INSTALL_DIR" "$LOCAL_BIN"
    _ok "Directories: $INSTALL_DIR"

    if [[ ! -x "$VENV_PYTHON" ]]; then
        if [[ "$have_uv" -eq 1 ]]; then
            _step 'Creating venv via uv...'
            uv venv "$VENV_DIR" --allow-existing >/dev/null 2>&1 \
                || "$py" -m venv "$VENV_DIR" >/dev/null 2>&1
        else
            _step 'Creating venv via python -m venv...'
            "$py" -m venv "$VENV_DIR" >/dev/null 2>&1
        fi
        [[ -x "$VENV_PYTHON" ]] || { _fail "Venv creation failed -- $VENV_PYTHON not found"; exit 1; }
        _ok 'Venv created'
    else
        _skip 'Venv already exists'
    fi


    # zdd (zero-downtime cutover primitives: routing table + orchestrator).
    local zdd_dir
    if zdd_dir="$(_resolve_zdd)"; then
        if [[ "$have_uv" -eq 1 ]]; then
            uv pip install --python "$VENV_PYTHON" "$zdd_dir" --reinstall-package agent-zdd --refresh-package agent-zdd --quiet
        else
            "$VENV_PYTHON" -m pip install "$zdd_dir" >/dev/null
        fi || {
            _fail "zdd install failed"
            exit 1
        }
    elif _zdd_installed; then
        _skip "zdd already installed in venv (marketplace layout)"
    else
        _fail "Cannot locate zdd library. Reinstall the agent-index plugin from the marketplace (copilot plugin install agent-index@copilot-extensions), then rerun this installer."
        exit 1
    fi

    _pip_install() {
        if [[ "$have_uv" -eq 1 ]]; then
            uv pip install --python "$VENV_PYTHON" "$PLUGIN_DIR"
        else
            "$VENV_PYTHON" -m pip install "$PLUGIN_DIR"
        fi
    }
    if ! pkg_out="$(_pip_install 2>&1)"; then
        _fail 'Failed to install agent-index package into venv'
        printf '%s\n' "$pkg_out" >&2
        exit 1
    fi
    _ok 'Package installed: agent-index'

    cat > "$STUB" << 'STUBEOF'
#!/usr/bin/env bash
export PYTHONUTF8=1
exec "$HOME/.agent-index/.venv/bin/python" -m agent_index "$@"
STUBEOF
    chmod +x "$STUB"
    _ok "Binstub: $STUB"

    local prev_version=""
    if [[ "$VERSIONED_RUNTIME" == 1 ]]; then
        prev_version="$(_versioned_current)"
        if ! "$VENV_PYTHON" -c 'import agent_index' 2>/dev/null; then
            _fail "Fresh runtime slot failed its health gate (versions/$SRC_VERSION) -- not activating"
            exit 1
        fi
        _versioned_activate || exit 1
    fi

    _write_manifest

    if "$LINK_PYTHON" -c 'import agent_index' 2>/dev/null; then
        _ok 'Verification: module imports successfully'
    else
        _fail 'Verification: module import failed'
        exit 1
    fi

    if [[ "$VERSIONED_RUNTIME" == 1 ]]; then
        _versioned_gc "$prev_version"
    fi

    case ":$PATH:" in
        *":$LOCAL_BIN:"*) _ok "PATH: $LOCAL_BIN is on PATH" ;;
        *) _step "Add $LOCAL_BIN to your PATH: export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
    esac
}

_write_manifest() {
    _git_info() {
        local path="$1" commit branch dirty
        commit=$(git -C "$path" rev-parse --short HEAD 2>/dev/null || echo "unknown")
        branch=$(git -C "$path" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
        dirty="false"
        [[ -n "$(git -C "$path" status --porcelain 2>/dev/null)" ]] && dirty="true"
        echo "$commit $branch $dirty"
    }
    local manifest="$INSTALL_DIR/deploy-manifest.json"
    local kind ver commit branch dirty
    kind="$(_source_kind "$PLUGIN_DIR")"
    ver="$(sed -n 's/^version *= *"\([^"]*\)".*/\1/p' "$PLUGIN_DIR/pyproject.toml" 2>/dev/null || echo 0.0.0)"
    commit="null"; branch="null"; dirty="false"
    if [[ "$kind" == "local" ]]; then
        local repo_root _c _b _d
        repo_root="$(cd "$PLUGIN_DIR/../.." && pwd)"
        read -r _c _b _d <<< "$(_git_info "$repo_root")"
        commit="\"$_c\""; branch="\"$_b\""; dirty="$_d"
    fi
    local tmp="$manifest.tmp"
    cat > "$tmp" << EOF
{
  "schema_version": 3,
  "service": "agent-index",
  "deployed_at": "$(date -u '+%Y-%m-%dT%H:%M:%SZ')",
  "deployed_by": "$(hostname)-$(uname -s | tr '[:upper:]' '[:lower:]')",
  "source": {
    "kind": "$kind",
    "path": "$PLUGIN_DIR",
    "repo": "copilot-extensions",
    "plugin": "agent-index",
    "version": "$ver",
    "commit": $commit,
    "branch": $branch,
    "dirty": $dirty
  },
  "venv": "$LINK_DIR",
  "runtime": "python"
}
EOF
    mv -f "$tmp" "$manifest"
    _ok "Deploy manifest written (source: $kind)"
}

_install_role() {
    # Resolve this machine's role (host runs the engine; client is service-only).
    # Precedence: AGENT_INDEX_ROLE env, then the freshly-installed CLI's resolver
    # (config.yaml role:/engine:), else client. No machine names live here.
    if [[ -n "${AGENT_INDEX_ROLE:-}" ]]; then
        local r
        r="$(printf '%s' "$AGENT_INDEX_ROLE" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"
        if [[ "$r" == "host" || "$r" == "client" ]]; then printf '%s' "$r"; return 0; fi
    fi
    if [[ -x "$LINK_PYTHON" ]]; then
        local out
        out="$("$LINK_PYTHON" -m agent_index role 2>/dev/null | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')"
        if [[ "$out" == "host" || "$out" == "client" ]]; then printf '%s' "$out"; return 0; fi
    fi
    printf 'client'
}

_install_engine() {
    # Provision the DURABLE engine venv (agent-index[engine], the torch stack) at
    # AGENT_INDEX_ENGINE_HOME. Built ONCE and skipped if present (idempotent);
    # never rebuilt by a service `update`. Non-fatal -- a failure here leaves the
    # light, torch-free service fully functional. With arg "upgrade", an existing
    # venv is upgraded in place (the explicit engine-runtime update path) instead
    # of skipped.
    local upgrade=0
    [[ "${1:-}" == "upgrade" ]] && upgrade=1
    if [[ "${AGENT_INDEX_NO_ENGINE_DEPS:-}" == "1" ]]; then
        _skip "Engine runtime skipped (AGENT_INDEX_NO_ENGINE_DEPS=1)"
        return 1
    fi
    if [[ -x "$ENGINE_VENV_PYTHON" && "$upgrade" -eq 0 ]]; then
        _skip "Engine runtime already provisioned (durable venv preserved): $ENGINE_VENV"
        return 0
    fi
    local py
    py="$(_find_python)" || { _warn 'Python not found -- cannot provision engine runtime'; return 1; }
    if [[ "$upgrade" -eq 1 ]]; then
        _step 'Updating durable engine runtime (torch stack) -- may take a while'
    else
        _step 'Provisioning durable engine runtime (torch stack) -- one-time, may take a while'
    fi
    mkdir -p "$ENGINE_HOME"
    local have_uv=0
    command -v uv >/dev/null 2>&1 && have_uv=1
    if [[ "$have_uv" -eq 1 ]]; then
        uv venv "$ENGINE_VENV" --allow-existing >/dev/null 2>&1 || "$py" -m venv "$ENGINE_VENV" >/dev/null 2>&1
    else
        "$py" -m venv "$ENGINE_VENV" >/dev/null 2>&1
    fi
    [[ -x "$ENGINE_VENV_PYTHON" ]] || { _warn "Engine venv creation failed -- $ENGINE_VENV_PYTHON not found"; return 1; }

    # zdd is a declared dependency of agent-index but is not on PyPI -- install it
    # from the vendored lib first so pip can satisfy the requirement.
    local zdd_dir
    if zdd_dir="$(_resolve_zdd)"; then
        if [[ "$have_uv" -eq 1 ]]; then
            uv pip install --python "$ENGINE_VENV_PYTHON" "$zdd_dir" --reinstall-package agent-zdd --refresh-package agent-zdd --quiet >/dev/null 2>&1 || true
        else
            "$ENGINE_VENV_PYTHON" -m pip install "$zdd_dir" >/dev/null 2>&1 || true
        fi
    fi

    # agent-index[engine] -- the heavy embedding stack into the DURABLE venv only.
    # Default PyPI torch is the CPU wheel; set AGENT_INDEX_TORCH_INDEX to a CUDA
    # wheel index (e.g. https://download.pytorch.org/whl/cu121) for a GPU host.
    local rc=0
    if [[ "$have_uv" -eq 1 ]]; then
        local uv_args=(pip install --python "$ENGINE_VENV_PYTHON" "$PLUGIN_DIR[engine]")
        [[ "$upgrade" -eq 1 ]] && uv_args+=(--upgrade)
        [[ -n "${AGENT_INDEX_TORCH_INDEX:-}" ]] && uv_args+=(--extra-index-url "$AGENT_INDEX_TORCH_INDEX")
        uv "${uv_args[@]}" || rc=$?
    else
        local pip_args=(-m pip install "$PLUGIN_DIR[engine]")
        [[ "$upgrade" -eq 1 ]] && pip_args+=(--upgrade)
        [[ -n "${AGENT_INDEX_TORCH_INDEX:-}" ]] && pip_args+=(--extra-index-url "$AGENT_INDEX_TORCH_INDEX")
        "$ENGINE_VENV_PYTHON" "${pip_args[@]}" || rc=$?
    fi
    if [[ "$rc" -ne 0 ]]; then
        _warn 'Engine runtime install failed (torch stack) -- light service unaffected; provision later with the "engine" action'
        return 1
    fi
    if ! "$ENGINE_VENV_PYTHON" -c 'import torch' 2>/dev/null; then
        _warn 'Engine venv built but torch import failed'
        return 1
    fi
    if [[ "$upgrade" -eq 1 ]]; then
        _ok "Engine runtime updated (durable venv): $ENGINE_VENV"
    else
        _ok "Engine runtime provisioned (durable venv): $ENGINE_VENV"
    fi
    return 0
}

_restart_engine_daemon() {
    # Restart the engine daemon so a freshly-updated durable venv is loaded -- the
    # ONE place a restart is intended (the explicit engine-runtime update path),
    # decoupled from the service `update` (which must never bounce the engine).
    if [[ "$NO_SERVICE" -eq 1 ]]; then
        _skip "Engine daemon restart skipped (--no-service)"
        return 0
    fi
    if command -v systemctl >/dev/null 2>&1 && [[ -f "$UNIT_DIR/$ENGINE_SYSTEMD_UNIT" ]]; then
        systemctl --user restart "$ENGINE_SYSTEMD_UNIT" 2>/dev/null || true
        if systemctl --user is-active "$ENGINE_SYSTEMD_UNIT" >/dev/null 2>&1; then
            _ok "Engine daemon restarted (new engine runtime loaded) ($ENGINE_SYSTEMD_UNIT)"
        else
            _warn "Engine daemon restart failed -- check: systemctl --user status agent-index-engine"
        fi
    else
        _register_engine_daemon
    fi
}

_register_engine_daemon() {
    # Register the persistent systemd --user unit that runs the warm engine from
    # the durable venv. A warm engine is left untouched (never restarted) when it
    # is already active.
    if [[ "$NO_SERVICE" -eq 1 ]]; then
        _skip "Engine daemon skipped (--no-service)"
        return 0
    fi
    if [[ ! -x "$ENGINE_VENV_PYTHON" ]]; then
        _skip "Engine runtime not provisioned -- daemon not registered"
        return 0
    fi
    if [[ ! -f "$ENGINE_ENV_FILE" ]]; then
        cat > "$ENGINE_ENV_FILE" << 'ENVEOF'
# agent-index engine daemon environment
AGENT_INDEX_ENGINE_HOST=127.0.0.1
AGENT_INDEX_ENGINE_PORT=8421
ENVEOF
        _ok "Engine env: $ENGINE_ENV_FILE"
    else
        _skip "Engine env already exists: $ENGINE_ENV_FILE"
    fi
    if ! command -v systemctl >/dev/null 2>&1; then
        _skip "systemd not available -- run 'agent-index engine run' via your own supervisor on this host"
        return 0
    fi
    mkdir -p "$UNIT_DIR"
    cat > "$UNIT_DIR/$ENGINE_SYSTEMD_UNIT" << EOF
[Unit]
Description=agent-index -- durable, persistent embedding-engine daemon (warm, torch)
After=network.target

[Service]
Type=simple
Environment=PYTHONUTF8=1
Environment=AGENT_INDEX_ENGINE_HOME=$ENGINE_HOME
EnvironmentFile=-$ENGINE_ENV_FILE
ExecStart=$ENGINE_VENV_PYTHON -m agent_index engine run
Restart=on-failure
RestartSec=5
WorkingDirectory=$ENGINE_HOME

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload 2>/dev/null || true
    systemctl --user enable "$ENGINE_SYSTEMD_UNIT" 2>/dev/null || true
    # Keep a warm engine warm: only START if not already active (never restart on re-register).
    if systemctl --user is-active "$ENGINE_SYSTEMD_UNIT" >/dev/null 2>&1; then
        _skip "Engine daemon already running -- leaving the warm engine untouched ($ENGINE_SYSTEMD_UNIT)"
    else
        systemctl --user start "$ENGINE_SYSTEMD_UNIT" 2>/dev/null || true
        if systemctl --user is-active "$ENGINE_SYSTEMD_UNIT" >/dev/null 2>&1; then
            _ok "Engine daemon installed + started ($ENGINE_SYSTEMD_UNIT)"
        else
            _warn "Engine daemon installed but not active -- check: systemctl --user status agent-index-engine"
        fi
    fi
}

_install_service() {
    if [[ "$NO_SERVICE" -eq 1 ]]; then
        _skip "Service skipped (--no-service)"
        return 0
    fi
    if ! command -v systemctl >/dev/null 2>&1; then
        _skip "systemd not available -- run 'agent-index start' manually if this host runs the service"
        return 0
    fi
    mkdir -p "$UNIT_DIR"
    if [[ ! -f "$ENV_FILE" ]]; then
        cat > "$ENV_FILE" << 'ENVEOF'
# agent-index service environment
AGENT_INDEX_HOST=127.0.0.1
# AGENT_INDEX_PORT=0  # unset/0 = OS-assigned dynamic port advertised via rendezvous
ENVEOF
        _ok "Service env: $ENV_FILE"
    else
        _skip "Service env already exists: $ENV_FILE"
    fi
    cat > "$UNIT_DIR/$SYSTEMD_UNIT" << EOF
[Unit]
Description=agent-index -- portable indexing/search service shell
After=network.target

[Service]
Type=simple
EnvironmentFile=-$ENV_FILE
Environment=PYTHONUTF8=1
ExecStart=$LINK_PYTHON -m agent_index start
Restart=on-failure
RestartSec=5
WorkingDirectory=$INSTALL_DIR

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload 2>/dev/null || true
    systemctl --user enable "$SYSTEMD_UNIT" 2>/dev/null || true
    systemctl --user restart "$SYSTEMD_UNIT" 2>/dev/null || true
    if systemctl --user is-active "$SYSTEMD_UNIT" >/dev/null 2>&1; then
        _ok "Service installed + started ($SYSTEMD_UNIT)"
    else
        _warn "Service installed but not active -- check: systemctl --user status agent-index"
    fi
}

_status() {
    if [[ -x "$LINK_PYTHON" ]]; then
        "$LINK_PYTHON" -m agent_index status
    else
        _skip "Runtime not installed: $INSTALL_DIR"
    fi
}

_start() {
    if command -v systemctl >/dev/null 2>&1 && [[ -f "$UNIT_DIR/$SYSTEMD_UNIT" ]]; then
        systemctl --user start "$SYSTEMD_UNIT"
        _ok "Service started ($SYSTEMD_UNIT)"
    elif [[ -x "$LINK_PYTHON" ]]; then
        nohup "$LINK_PYTHON" -m agent_index start >> "$INSTALL_DIR/service.log" 2>&1 &
        _ok "Service process started"
    else
        _fail 'Runtime not installed'
        exit 1
    fi
}

_stop() {
    if [[ -x "$LINK_PYTHON" ]]; then
        "$LINK_PYTHON" -m agent_index stop || true
    fi
    if command -v systemctl >/dev/null 2>&1 && [[ -f "$UNIT_DIR/$SYSTEMD_UNIT" ]]; then
        systemctl --user stop "$SYSTEMD_UNIT" 2>/dev/null || true
        _ok "Service stopped ($SYSTEMD_UNIT)"
    fi
}

_uninstall() {
    _stop
    if command -v systemctl >/dev/null 2>&1 && [[ -f "$UNIT_DIR/$SYSTEMD_UNIT" ]]; then
        systemctl --user disable "$SYSTEMD_UNIT" 2>/dev/null || true
        rm -f "$UNIT_DIR/$SYSTEMD_UNIT"
        systemctl --user daemon-reload 2>/dev/null || true
    fi
    if command -v systemctl >/dev/null 2>&1 && [[ -f "$UNIT_DIR/$ENGINE_SYSTEMD_UNIT" ]]; then
        systemctl --user stop "$ENGINE_SYSTEMD_UNIT" 2>/dev/null || true
        systemctl --user disable "$ENGINE_SYSTEMD_UNIT" 2>/dev/null || true
        rm -f "$UNIT_DIR/$ENGINE_SYSTEMD_UNIT"
        systemctl --user daemon-reload 2>/dev/null || true
    fi
    rm -f "$STUB"
    if [[ "$PURGE" -eq 1 ]]; then
        rm -rf "$ENGINE_HOME"
        rm -rf "$INSTALL_DIR"
    fi
    _ok 'agent-index uninstalled'
}

case "$ACTION" in
    install)
        _ensure_runtime
        _install_service
        _role="$(_install_role)"
        if [[ "$_role" == "host" ]]; then
            _install_engine || true
            _register_engine_daemon
        else
            _skip "Engine runtime skipped (role: $_role) -- set 'role: host' in $INSTALL_DIR/config.yaml or AGENT_INDEX_ROLE=host to host the durable engine"
        fi
        ;;
    update) _downgrade_guard; _ensure_runtime; _install_service ;;  # engine venv + daemon left untouched by design
    engine) _install_engine || true; _register_engine_daemon ;;     # explicit host-side provisioning (role-independent)
    engine-update)                                                  # rebuild durable engine venv + restart daemon (decoupled from service update)
        if _install_engine upgrade; then _restart_engine_daemon; fi ;;
    status) _status ;;
    start) _start ;;
    stop) _stop ;;
    uninstall) _uninstall ;;
    *) _fail "Unknown action: $ACTION"; exit 2 ;;
esac
