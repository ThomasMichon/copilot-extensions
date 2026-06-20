#!/usr/bin/env bash
# =============================================================================
# install.sh -- Worktree Session Manager -- standardized installer interface
# =============================================================================
# Manages the worktree session infrastructure lifecycle: install, uninstall,
# start, stop, status, update-config, update.
#
# Deploys launcher and finalizer scripts to ~/.agent-worktrees/bin/ and creates
# the project binstub in ~/.local/bin/.
# Shared runtime at ~/.agent-worktrees/; project config at ~/.{project}/.
#
# Usage:
#   bash plugins/agent-worktrees/scripts/install.sh install
#   bash plugins/agent-worktrees/scripts/install.sh install --project-name my-repo
#   bash plugins/agent-worktrees/scripts/install.sh status
#   bash plugins/agent-worktrees/scripts/install.sh update
#
# Options:
#   --project-name N Project name (auto-detected if omitted)
#   --force          Overwrite config without drift confirmation
#   --remove-config  On uninstall: also delete config and session metadata
#   --machine NAME   Machine name (auto-detected if omitted)
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Ensure ~/.local/bin is on PATH (uv, pip-installed tools live here;
# non-interactive SSH sessions often miss it)
if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
    export PATH="$HOME/.local/bin:$PATH"
fi

# ── Parse arguments ──────────────────────────────────────────────────────

ACTION="${1:-status}"
shift || true

FORCE=false
REMOVE_CONFIG=false
MACHINE=""
PROJECT_NAME_ARG=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --force)         FORCE=true; shift ;;
        --remove-config) REMOVE_CONFIG=true; shift ;;
        --machine)       MACHINE="$2"; shift 2 ;;
        --project-name)  PROJECT_NAME_ARG="$2"; shift 2 ;;
        *)               echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

# ── Infer project name ──────────────────────────────────────────────────
# Priority: --project-name arg > WORKTREE_PROJECT env > existing config
# CWD is NOT auto-adopted -- pass --project-name to adopt explicitly.

PROJECT_NAME=""

if [[ -n "$PROJECT_NAME_ARG" ]]; then
    PROJECT_NAME="$PROJECT_NAME_ARG"
elif [[ -n "${WORKTREE_PROJECT:-}" ]]; then
    PROJECT_NAME="$WORKTREE_PROJECT"
else
    # Try to infer from CWD basename matching an existing config dir
    _cwd_name="$(basename "$PWD")"
    if [[ -f "$HOME/.$_cwd_name/config.yaml" ]]; then
        PROJECT_NAME="$_cwd_name"
    fi
fi
# Don't auto-adopt CWD repo -- runtime installs fine without a project.
HAS_PROJECT=false
if [[ -n "$PROJECT_NAME" ]]; then
    HAS_PROJECT=true
    # Validate project name (safe for dotdirs, binstubs, YAML keys)
    if [[ ! "$PROJECT_NAME" =~ ^[A-Za-z0-9._-]+$ ]]; then
        echo "ERROR: Invalid project name '$PROJECT_NAME' -- must match [A-Za-z0-9._-]+" >&2
        exit 1
    fi
fi

# ── Detect REPO_DIR from project config, then CWD ───────────────────────

REPO_DIR=""
if $HAS_PROJECT; then
    _config_file="$HOME/.$PROJECT_NAME/config.yaml"
    if [[ -f "$_config_file" ]]; then
        _anchor=$(grep 'anchor:' "$_config_file" 2>/dev/null | head -1 | sed 's/.*anchor:\s*//')
        if [[ -n "$_anchor" ]] && git -C "$_anchor" rev-parse --show-toplevel >/dev/null 2>&1; then
            REPO_DIR="$_anchor"
        fi
    fi
fi
if [[ -z "$REPO_DIR" ]]; then
    _git_root="$(git -C "$PWD" rev-parse --show-toplevel 2>/dev/null || true)"
    if [[ -n "$_git_root" ]]; then
        REPO_DIR="$_git_root"
    fi
fi

# ── Metadata ─────────────────────────────────────────────────────────────

SERVICE_NAME="Worktree Session Manager"
INSTALL_DIR="$HOME/.agent-worktrees"
BIN_DIR="$INSTALL_DIR/bin"
LOCAL_BIN="$HOME/.local/bin"
SERVICE_YAML="$SCRIPT_DIR/service.yaml"

if $HAS_PROJECT; then
    PROJECT_DIR="$HOME/.$PROJECT_NAME"
    WORKTREES_DIR="$PROJECT_DIR/worktrees"
else
    PROJECT_DIR=""
    WORKTREES_DIR=""
fi

DEPLOY_SOURCE_PATHS=("plugins/agent-worktrees/")
INSTALLER_REL_PATH="plugins/agent-worktrees/scripts/install.sh"

# Legacy scripts (pre-Python) -- for cleanup during migration
LEGACY_SCRIPTS=(
    launch-session.ps1
    finalize-session.ps1
    finalize-session.sh
    worktree-status.ps1
    worktree-cleanup.ps1
)

# Legacy alias binstubs that earlier versions deployed into BIN_DIR and/or
# LOCAL_BIN. They were removed from source (commit 688d74e) because they
# collide with worktree-manager and duplicate `agent-worktrees <subcommand>`,
# but already-deployed copies linger and cause confusion (e.g. invoking the
# flag-only `mark-complete` alias instead of `push-changes`/`finalize`).
# Pruned on every install/update. Bare name + .cmd/.ps1 variants are removed.
LEGACY_BINSTUBS=(
    mark-worktree-complete
    cleanup-worktrees
    mark-session-complete
)

# Python runtime paths (shared across projects)
LIB_DIR="$INSTALL_DIR/lib"
VENV_DIR="$INSTALL_DIR/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"
VENV_BIN="$VENV_DIR/bin/agent-worktrees"

# ── Status output helpers ────────────────────────────────────────────────

ok()      { echo "  ✓ $*"; }
changed() { echo "  → $*"; }
skipped() { echo "  ○ $*"; }
warn()    { echo "  ! $*"; }
err()     { echo "  ✗ $*"; }
header()  { echo ""; echo "═══ $* $(printf '═%.0s' $(seq 1 $((56 - ${#1}))))"; }

# === install-contract:v3 source-kind -- keep byte-identical across plugins ===
_source_kind() {
    case "$(printf '%s' "$1" | tr '\\' '/')" in
        */.copilot/installed-plugins/*) printf 'marketplace' ;;
        *) printf 'local' ;;
    esac
}
# === end install-contract:v3 source-kind ===

# ── Machine detection ────────────────────────────────────────────────────

resolve_machine() {
    if [[ -n "$MACHINE" ]]; then
        echo "$MACHINE"
        return
    fi
    local hn
    hn="$(hostname | tr '[:upper:]' '[:lower:]')"
    # Use lowercase hostname as-is. If hostname differs from the desired
    # machine key, set MACHINE explicitly before running the installer.
    echo "$hn"
}

detect_platform() {
    if grep -qi microsoft /proc/version 2>/dev/null; then
        echo "wsl"
    else
        echo "linux"
    fi
}

# ── Projects registry ────────────────────────────────────────────────────

PROJECTS_YAML="$INSTALL_DIR/projects.yaml"

register_project() {
    # Add or update this project in the projects registry.
    # Must be called after deploy_venv (requires Python + pyyaml).
    if [[ ! -x "$VENV_PYTHON" ]]; then
        skipped "Projects registry: venv not ready"
        return
    fi

    local default_branch="master"
    local cfg_path="$PROJECT_DIR/config.yaml"
    if [[ -f "$cfg_path" ]]; then
        local _db
        _db=$(grep 'default_branch:' "$cfg_path" 2>/dev/null | head -1 | sed 's/.*default_branch:\s*//')
        if [[ -n "$_db" ]]; then
            default_branch="$_db"
        fi
    fi

    local machines_yaml=""
    if [[ -n "$REPO_DIR" && -f "$REPO_DIR/machines.yaml" ]]; then
        machines_yaml="$REPO_DIR/machines.yaml"
    fi

    local platform
    platform="$(detect_platform)"
    local wsl_distro="${WSL_DISTRO_NAME:-}"

    "$VENV_PYTHON" -c "
import yaml, sys, os
from pathlib import Path
from datetime import datetime, timezone

projects_path = Path(sys.argv[1])
project_name = sys.argv[2]
anchor = sys.argv[3]
machines_yaml = sys.argv[4]
default_branch = sys.argv[5]
config_dir = sys.argv[6]
platform = sys.argv[7]
wsl_distro = sys.argv[8]

# Read existing registry
if projects_path.exists():
    try:
        data = yaml.safe_load(projects_path.read_text()) or {}
    except yaml.YAMLError:
        data = {}
else:
    data = {}

projects = data.setdefault('projects', {})

# Preserve existing entry fields we don't want to clobber
existing = projects.get(project_name, {})

entry = {
    'anchor': anchor or None,
    'machines_yaml': machines_yaml or None,
    'registered_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
    'default_branch': default_branch,
    'config_dir': config_dir,
}

# Set WSL state when running inside WSL
if platform in ('wsl', 'linux'):
    wsl_info = {'state': 'adopted'}
    if wsl_distro:
        wsl_info['distro'] = wsl_distro
    if anchor:
        wsl_info['path'] = anchor
    entry['wsl'] = wsl_info
elif isinstance(existing.get('wsl'), dict):
    # Preserve existing WSL state when re-registering from native Linux
    entry['wsl'] = existing['wsl']

projects[project_name] = entry

# Write back
projects_path.parent.mkdir(parents=True, exist_ok=True)
header = '# ~/.agent-worktrees/projects.yaml\n# Registry of adopted repos for terminal profile generation.\n\n'
projects_path.write_text(header + yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True))
" "$PROJECTS_YAML" "$PROJECT_NAME" "${REPO_DIR:-}" "${machines_yaml:-}" "$default_branch" "~/.$PROJECT_NAME" "$platform" "$wsl_distro"

    ok "Project '$PROJECT_NAME' registered in projects.yaml"
}

# ── Helpers ──────────────────────────────────────────────────────────────

deploy_package() {
    if [[ ! -f "$PLUGIN_DIR/pyproject.toml" ]]; then
        err "Plugin source not found: $PLUGIN_DIR"
        return 1
    fi
    if [[ ! -x "$VENV_PYTHON" ]]; then
        err "Venv Python missing -- create the venv first"
        return 1
    fi

    if ! uv pip install --python "$VENV_PYTHON" --reinstall-package agent-worktrees "$PLUGIN_DIR" --quiet; then
        err "Package install failed"
        return 1
    fi

    # Retire the legacy file-copy dir FIRST so a stale PYTHONPATH=.../lib cannot
    # make the probe resolve to the old copy (or shadow it at runtime).
    rm -rf "$LIB_DIR"

    # Stamp build info into the installed copy (PYTHONPATH cleared for the probe).
    local pkg_dir
    pkg_dir="$(PYTHONPATH= "$VENV_PYTHON" -c 'import agent_worktrees, os; print(os.path.dirname(agent_worktrees.__file__))' 2>/dev/null || true)"
    if [[ -n "$pkg_dir" ]]; then
        local _repo_root _commit _branch _ts _src_norm _ver
        _repo_root="$(cd "$PLUGIN_DIR/../.." && pwd)"
        _commit="$(git -C "$_repo_root" rev-parse HEAD 2>/dev/null || echo unknown)"
        _branch="$(git -C "$_repo_root" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
        _ts="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
        _src_norm="$(echo "$PLUGIN_DIR" | tr '\\' '/')"
        _ver="$(sed -n 's/^version *= *"\([^"]*\)".*/\1/p' "$PLUGIN_DIR/pyproject.toml" 2>/dev/null || echo 0.0.0)"
        cat > "$pkg_dir/_build_info.py" <<PYEOF
"""Build provenance -- auto-generated at deploy time. Do not edit."""

from __future__ import annotations

BUILD_INFO: dict[str, str] = {
    "version": "$_ver",
    "commit": "$_commit",
    "branch": "$_branch",
    "build_timestamp": "$_ts",
    "source": "$_src_norm",
}
PYEOF
    else
        warn "Could not locate installed agent_worktrees -- build info not stamped"
    fi

    ok "Package installed into venv"
}

deploy_venv() {
    # Create venv via uv (--allow-existing handles re-install). Deps come from
    # pyproject at package install time -- no ad-hoc pyyaml here.
    if ! uv venv "$VENV_DIR" --python 3.11 --allow-existing 2>/dev/null; then
        if ! uv venv "$VENV_DIR" --allow-existing 2>/dev/null; then
            err "Failed to create venv at $VENV_DIR"
            return 1
        fi
    fi
    ok "Venv created at $VENV_DIR"
}

deploy_wrappers() {
    mkdir -p "$BIN_DIR"
    local src="$PLUGIN_DIR/bin/launch-session.sh"
    if [[ ! -f "$src" ]]; then
        err "Wrapper source not found: $src"
        return 1
    fi
    # Atomic replace -- write to temp then mv, so a concurrent session
    # reading launch-session.sh isn't corrupted mid-write.
    local tmp
    tmp="$(mktemp "$BIN_DIR/launch-session.sh.XXXXXX")"
    cp "$src" "$tmp"
    chmod +x "$tmp"
    mv -f "$tmp" "$BIN_DIR/launch-session.sh"
    ok "Wrapper: launch-session.sh"

    # Deploy pane wrapper (handles exit codes inside tmux/psmux panes)
    local pane_src="$PLUGIN_DIR/bin/pane-wrapper.sh"
    if [[ -f "$pane_src" ]]; then
        tmp="$(mktemp "$BIN_DIR/pane-wrapper.sh.XXXXXX")"
        cp "$pane_src" "$tmp"
        chmod +x "$tmp"
        mv -f "$tmp" "$BIN_DIR/pane-wrapper.sh"
        ok "Wrapper: pane-wrapper.sh"
    fi

    # Deploy sessionStart hook scripts (bootstrap-check + project-hooks + register-session + anchor-hygiene-check)
    for script in bootstrap-check.ps1 bootstrap-check.sh project-hooks.ps1 project-hooks.sh register-session.ps1 register-session.sh deregister-session.ps1 deregister-session.sh anchor-hygiene-check.ps1 anchor-hygiene-check.sh; do
        local script_src="$SCRIPT_DIR/$script"
        if [[ -f "$script_src" ]]; then
            tmp="$(mktemp "$BIN_DIR/$script.XXXXXX")"
            cp "$script_src" "$tmp"
            chmod +x "$tmp"
            mv -f "$tmp" "$BIN_DIR/$script"
            ok "Hook: $script"
        fi
    done
}

remove_legacy_scripts() {
    local removed=0
    for script in "${LEGACY_SCRIPTS[@]}"; do
        if [[ -f "$BIN_DIR/$script" ]]; then
            rm -f "$BIN_DIR/$script"
            ((removed++)) || true
        fi
    done
    if [[ $removed -gt 0 ]]; then
        changed "Removed $removed legacy script(s) from $BIN_DIR"
    fi
}

remove_legacy_binstubs() {
    # Sweep legacy alias binstubs from both runtime BIN_DIR and user LOCAL_BIN,
    # covering bare (bash), .cmd (Windows) and .ps1 variants.
    local removed=0
    for name in "${LEGACY_BINSTUBS[@]}"; do
        for dir in "$BIN_DIR" "$LOCAL_BIN"; do
            for f in "$dir/$name" "$dir/$name.cmd" "$dir/$name.ps1"; do
                if [[ -f "$f" ]]; then
                    rm -f "$f"
                    ((removed++)) || true
                fi
            done
        done
    done
    if [[ $removed -gt 0 ]]; then
        changed "Removed $removed legacy binstub(s)"
    fi
}

deploy_binstub() {
    mkdir -p "$LOCAL_BIN"
    # Generate project-specific binstub that routes through the Python CLI.
    # The CLI dispatches: no args → launch session, known subcommand → handler.
    # Falls back to launch-session.sh if venv is missing (recovery path).
    local tmp
    tmp="$(mktemp "$LOCAL_BIN/$PROJECT_NAME.XXXXXX")"
    cat > "$tmp" <<'BINSTUB_HEAD'
#!/usr/bin/env bash
BINSTUB_HEAD
    cat >> "$tmp" <<BINSTUB_BODY
export WORKTREE_PROJECT="$PROJECT_NAME"
export PYTHONUTF8=1
# #25: a project binstub is a cross-project entry point --
# drop any inherited WORKTREE_ID so worktree resolution uses CWD.
unset WORKTREE_ID APERTURE_WORKTREE_ID
_AW="\$HOME/.agent-worktrees/.venv/bin/agent-worktrees"
if [[ -x "\$_AW" ]]; then
    exec "\$_AW" "\$@"
fi
# Fallback: launch session directly (venv missing / recovery)
exec "\$HOME/.agent-worktrees/bin/launch-session.sh" "\$@"
BINSTUB_BODY
    chmod +x "$tmp"
    mv -f "$tmp" "$LOCAL_BIN/$PROJECT_NAME"
    ok "Binstub: $LOCAL_BIN/$PROJECT_NAME"

    # Tool binstubs (parity with Windows .cmd stubs)
    for stub in agent-worktrees; do
        local stub_src="$PLUGIN_DIR/bin/$stub"
        if [[ -f "$stub_src" ]]; then
            tmp="$(mktemp "$LOCAL_BIN/$stub.XXXXXX")"
            cp "$stub_src" "$tmp"
            chmod +x "$tmp"
            mv -f "$tmp" "$LOCAL_BIN/$stub"
            ok "Binstub: $LOCAL_BIN/$stub"
        fi
    done
}

deploy_config() {
    local machine="$1"
    local platform="$2"
    local config_path="$PROJECT_DIR/config.yaml"

    if [[ -f "$config_path" ]] && ! $FORCE; then
        skipped "Config exists at $config_path (use --force to overwrite)"
        return 1
    fi

    if [[ -z "$REPO_DIR" ]]; then
        skipped "Config generation skipped (no repo detected -- create config.yaml manually)"
        return 1
    fi

    local src_root
    src_root="$(dirname "$REPO_DIR")"
    local worktree_root="$REPO_DIR.worktrees"

    cat > "$config_path" <<EOF
# ~/.$PROJECT_NAME/config.yaml
# Machine-local configuration for $PROJECT_NAME worktree management.

srcroot: $src_root
machine: $machine
platform: $platform
repo_name: $PROJECT_NAME

repos:
  $PROJECT_NAME:
    anchor: $REPO_DIR
    # worktree_root defaults to $worktree_root -- a sibling
    # <anchor>.worktrees dir, matching Copilot CLI's /worktree layout.
    # Uncomment and set an absolute path to override.
    default_branch: master
    remote: origin
EOF
    changed "Written config: $config_path"
    return 0
}

write_deploy_manifest() {
    local manifest_path="$INSTALL_DIR/deploy-manifest.json"
    local machine platform kind ver commit branch dirty
    machine="$(resolve_machine)"
    platform="$(detect_platform)"
    kind="$(_source_kind "$PLUGIN_DIR")"
    ver="$(sed -n 's/^version *= *"\([^"]*\)".*/\1/p' "$PLUGIN_DIR/pyproject.toml" 2>/dev/null || echo 0.0.0)"

    commit="null"; branch="null"; dirty="false"
    if [[ "$kind" == "local" ]]; then
        local repo_root c b
        repo_root="$(cd "$PLUGIN_DIR/../.." && pwd)"
        c="$(git -C "$repo_root" rev-parse --short HEAD 2>/dev/null || echo unknown)"
        b="$(git -C "$repo_root" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
        commit="\"$c\""; branch="\"$b\""
        [[ -n "$(git -C "$repo_root" status --porcelain -- plugins/agent-worktrees/ 2>/dev/null)" ]] && dirty="true"
    fi

    local tmp="$manifest_path.tmp"
    cat > "$tmp" <<EOF
{
  "schema_version": 3,
  "service": "agent-worktrees",
  "deployed_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "deployed_by": "${machine}-${platform}",
  "source": {
    "kind": "$kind",
    "path": "$PLUGIN_DIR",
    "repo": "copilot-extensions",
    "plugin": "agent-worktrees",
    "version": "$ver",
    "commit": $commit,
    "branch": $branch,
    "dirty": $dirty
  },
  "venv": "$VENV_DIR",
  "runtime": "python"
}
EOF
    mv -f "$tmp" "$manifest_path"
    ok "Deploy manifest written (source: $kind)"
}

show_deploy_status() {
    local manifest_path="$INSTALL_DIR/deploy-manifest.json"
    if [[ ! -f "$manifest_path" ]]; then
        skipped "No deploy manifest (deploy with updated installer to create one)"
        return
    fi

    local commit branch deployed_at is_dirty
    commit="$(python3 -c "import json; m=json.load(open('$manifest_path')); print(m.get('commit','unknown')[:10] if m.get('commit') else 'unknown')")"
    branch="$(python3 -c "import json; m=json.load(open('$manifest_path')); print(m.get('branch','unknown') or 'unknown')")"
    deployed_at="$(python3 -c "import json; m=json.load(open('$manifest_path')); print(m.get('deployed_at','unknown'))")"
    is_dirty="$(python3 -c "import json; m=json.load(open('$manifest_path')); print(str(m.get('dirty',False)).lower())")"

    if [[ "$is_dirty" == "true" ]]; then
        changed "Deployed from $branch @ $commit (DIRTY)"
    else
        ok "Deployed from $branch @ $commit"
    fi
    ok "Deployed at $deployed_at"

    # Staleness check
    local deployed_commit
    deployed_commit="$(python3 -c "import json; m=json.load(open('$manifest_path')); print(m.get('commit','') or '')")"
    if [[ -n "$deployed_commit" && -n "$REPO_DIR" ]]; then
        local stale_count
        stale_count="$(git -C "$REPO_DIR" log --oneline "$deployed_commit..HEAD" -- "${DEPLOY_SOURCE_PATHS[@]}" 2>/dev/null | wc -l)" || stale_count=0
        if [[ "$stale_count" -eq 0 ]]; then
            ok "Up to date (no source changes since deploy)"
        else
            changed "Stale -- $stale_count commit(s) behind HEAD"
        fi
    fi
}

deploy_tabby_profile() {
    local platform="$1"
    local machine="${2:-}"
    local tabby_template="$PLUGIN_DIR/terminal/tabby-template.yaml"
    local machines_yaml="${REPO_DIR:+$REPO_DIR/machines.yaml}"
    local tabby_config="$HOME/.config/tabby/config.yaml"

    # Skip on WSL -- Tabby is a native Linux desktop app
    if [[ "$platform" == "wsl" ]]; then
        skipped "Tabby profile: skipped on WSL"
        return
    fi

    # Skip if Tabby config dir doesn't exist (not installed / never launched)
    if [[ ! -d "$HOME/.config/tabby" ]]; then
        skipped "Tabby profile: ~/.config/tabby not found (Tabby not installed?)"
        return
    fi

    if [[ ! -f "$tabby_template" ]]; then
        err "Tabby template not found: $tabby_template"
        return 1
    fi

    # Warn if Tabby is running -- it overwrites config.yaml from memory on exit
    if pgrep -x tabby >/dev/null 2>&1; then
        echo "  ⚠ Tabby is running -- close it before updating, or changes will be overwritten"
    fi

    "$VENV_PYTHON" -c "
import sys, yaml, copy
from pathlib import Path

template_path = sys.argv[1]
config_path = sys.argv[2]
machines_path = sys.argv[3]
self_machine = sys.argv[4] if len(sys.argv) > 4 else ''
project_name = sys.argv[5] if len(sys.argv) > 5 else 'my-project'

template = yaml.safe_load(Path(template_path).read_text())
local_profile = template['profile']
scheme = template['colorScheme']

# Substitute project placeholders in the local profile
display_name = ' '.join(w.capitalize() for w in project_name.split('-'))
def _sub(obj):
    if isinstance(obj, str):
        return obj.replace('__PROJECT__', project_name).replace('__PROJECT_TITLE__', display_name)
    if isinstance(obj, dict):
        return {k: _sub(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sub(v) for v in obj]
    return obj
local_profile = _sub(local_profile)

# Load existing config or create minimal structure
if Path(config_path).exists():
    try:
        config = yaml.safe_load(Path(config_path).read_text()) or {}
    except yaml.YAMLError:
        print('  ⚠ Tabby config is malformed -- skipping profile merge', file=sys.stderr)
        sys.exit(0)
else:
    config = {}

changed = False
profiles = config.setdefault('profiles', [])

def upsert_profile(prof):
    \"\"\"Insert or update a profile by id, returning True if changed.\"\"\"
    idx = next((i for i, p in enumerate(profiles) if p.get('id') == prof['id']), None)
    if idx is not None:
        if profiles[idx] != prof:
            profiles[idx] = prof
            return True
    else:
        profiles.append(prof)
        return True
    return False

# Local project profile (insert at front)
existing_idx = next((i for i, p in enumerate(profiles) if p.get('id') == local_profile['id']), None)
if existing_idx is not None:
    if profiles[existing_idx] != local_profile:
        profiles[existing_idx] = local_profile
        changed = True
else:
    profiles.insert(0, local_profile)
    changed = True

# Generate remote SSH profiles from machines.yaml
env_labels = {'windows': 'Windows', 'wsl': 'WSL', 'linux': 'Linux'}

if Path(machines_path).exists():
    try:
        machines_data = yaml.safe_load(Path(machines_path).read_text()) or {}
    except yaml.YAMLError:
        machines_data = {}

    for key, entry in (machines_data.get('machines') or {}).items():
        if key == self_machine:
            continue
        ssh = entry.get('ssh') or {}
        if not ssh.get('ready', False):
            continue
        display_name = entry.get('display_name', key)

        for env in ssh.get('environments', []):
            alias = env.get('alias', '')
            env_name = env.get('name', '')
            env_label = env_labels.get(env_name, env_name)
            shell = env.get('shell', 'bash')

            # Plain SSH profile
            ssh_id = f'ssh:{key}-{env_name}'
            ssh_profile = {
                'id': ssh_id,
                'type': 'local',
                'name': f'{display_name} ({env_label})',
                'icon': 'fa-terminal',
                'color': '#F6A821',
                'isBuiltin': False,
                'options': {
                    'command': 'ssh',
                    'args': [alias],
                    'cwd': '',
                    'env': {},
                },
            }
            if upsert_profile(ssh_profile):
                changed = True

            # Project launcher profile -- SSH + run binstub
            binstub = f'{project_name}.cmd' if shell == 'pwsh' else project_name
            launcher_id = f'{project_name}:{key}-{env_name}'
            launcher_label = display_name if env_label == 'Linux' else f'{display_name} {env_label}'
            # Title-case the project name for display
            display_project = project_name.replace('-', ' ').title()
            launcher_profile = {
                'id': launcher_id,
                'type': 'local',
                'name': f'{display_project} ({launcher_label})',
                'icon': 'fa-flask',
                'color': '#F6A821',
                'isBuiltin': False,
                'options': {
                    'command': 'ssh',
                    'args': ['-t', alias, binstub],
                    'cwd': '',
                    'env': {},
                },
            }
            if upsert_profile(launcher_profile):
                changed = True

# Set global color scheme to Aperture Science
terminal = config.setdefault('terminal', {})
current_scheme = terminal.get('colorScheme', {})
if current_scheme.get('name') != scheme['name'] or current_scheme.get('foreground') != scheme['foreground']:
    terminal['colorScheme'] = scheme
    changed = True

if changed:
    Path(config_path).write_text(yaml.dump(config, default_flow_style=False, sort_keys=False, allow_unicode=True))
    print('changed')
else:
    print('ok')
" "$tabby_template" "$tabby_config" "$machines_yaml" "$machine" "$PROJECT_NAME"

    local result=$?
    if [[ $result -ne 0 ]]; then
        err "Tabby profile merge failed"
        return 1
    fi

    # Verify: check local profile + color scheme + SSH profiles
    local status
    status=$("$VENV_PYTHON" -c "
import sys, yaml
from pathlib import Path

project_name = sys.argv[2]
local_id = f'local:{project_name}'

config = yaml.safe_load(Path(sys.argv[1]).read_text()) or {}
profiles = config.get('profiles', [])
ids = {p.get('id', '') for p in profiles}
has_local = local_id in ids
has_ssh = any(pid.startswith('ssh:') for pid in ids)
scheme_name = config.get('terminal', {}).get('colorScheme', {}).get('name', '')
if has_local and scheme_name == 'Aperture Science':
    if has_ssh:
        print('ok_with_ssh')
    else:
        print('ok_local_only')
else:
    print('err')
" "$tabby_config" "$PROJECT_NAME")

    local display_project
    display_project="$(echo "$PROJECT_NAME" | tr '-' ' ' | sed 's/\b\(.\)/\u\1/g')"

    case "$status" in
        ok_with_ssh)
            ok "Tabby profile: $display_project + remote SSH profiles"
            ;;
        ok_local_only)
            ok "Tabby profile: $display_project (no remote SSH profiles generated)"
            ;;
        *)
            err "Tabby profile merge verification failed"
            ;;
    esac
}

remove_tabby_profile() {
    local tabby_config="$HOME/.config/tabby/config.yaml"

    if [[ ! -f "$tabby_config" ]]; then
        return
    fi

    "$VENV_PYTHON" -c "
import sys, yaml
from pathlib import Path

config_path = sys.argv[1]
project_name = sys.argv[2]
config = yaml.safe_load(Path(config_path).read_text()) or {}

profiles = config.get('profiles', [])
original_len = len(profiles)
local_id = f'local:{project_name}'
launcher_prefix = f'{project_name}:'
# Remove local profile, SSH profiles, and project launcher profiles
config['profiles'] = [
    p for p in profiles
    if p.get('id') != local_id
    and not (p.get('id', '').startswith('ssh:'))
    and not (p.get('id', '').startswith(launcher_prefix))
]

if len(config['profiles']) < original_len:
    Path(config_path).write_text(yaml.dump(config, default_flow_style=False, sort_keys=False, allow_unicode=True))
    print('removed')
else:
    print('absent')
" "$tabby_config" "$PROJECT_NAME"

    local result
    result=$?
    if [[ $result -eq 0 ]]; then
        changed "Removed Tabby $PROJECT_NAME profiles (local + SSH)"
    fi
}

check_tabby_profile() {
    local tabby_config="$HOME/.config/tabby/config.yaml"

    if [[ ! -f "$tabby_config" ]]; then
        skipped "Tabby: not installed or never launched"
        return
    fi

    local status
    status=$("$VENV_PYTHON" -c "
import sys, yaml
from pathlib import Path

project_name = sys.argv[2]
local_id = f'local:{project_name}'
launcher_prefix = f'{project_name}:'

config = yaml.safe_load(Path(sys.argv[1]).read_text()) or {}
profiles = config.get('profiles', [])
ids = {p.get('id', '') for p in profiles}
has_local = local_id in ids
has_ssh = any(pid.startswith('ssh:') for pid in ids)
has_launchers = any(pid.startswith(launcher_prefix) for pid in ids)
ssh_count = sum(1 for pid in ids if pid.startswith('ssh:'))
launcher_count = sum(1 for pid in ids if pid.startswith(launcher_prefix))
scheme_name = config.get('terminal', {}).get('colorScheme', {}).get('name', '')

if has_local and scheme_name == 'Aperture Science':
    if has_ssh and has_launchers:
        print(f'ok:{ssh_count}:{launcher_count}')
    elif has_ssh:
        print(f'ssh_only:{ssh_count}')
    else:
        print('local_only')
elif has_local:
    print('profile_only')
else:
    print('missing')
" "$tabby_config" "$PROJECT_NAME" 2>/dev/null)

    local display_project
    display_project="$(echo "$PROJECT_NAME" | tr '-' ' ' | sed 's/\b\(.\)/\u\1/g')"

    case "$status" in
        ok:*)
            local ssh_n launcher_n
            ssh_n="$(echo "$status" | cut -d: -f2)"
            launcher_n="$(echo "$status" | cut -d: -f3)"
            ok "Tabby: $display_project + ${ssh_n} SSH + ${launcher_n} launcher profiles"
            ;;
        ssh_only:*)
            local ssh_n
            ssh_n="$(echo "$status" | cut -d: -f2)"
            changed "Tabby: $display_project + ${ssh_n} SSH profiles (no launchers)"
            ;;
        local_only)
            changed "Tabby: $display_project local only (no remote SSH profiles)"
            ;;
        profile_only)
            changed "Tabby: $display_project present, but color scheme differs"
            ;;
        missing)
            err "Tabby profile: $display_project not found"
            ;;
        *)
            skipped "Tabby: could not check profile"
            ;;
    esac
}

deploy_git_hooks_path() {
    if [[ -z "$REPO_DIR" ]]; then return; fi
    local current
    current="$(git -C "$REPO_DIR" config --local core.hooksPath 2>/dev/null)" || true
    if [[ "$current" == "tools/hooks" ]]; then
        ok "Git hooksPath = tools/hooks"
        return
    fi
    if [[ -n "$current" ]]; then
        echo "  ⚠ Git core.hooksPath already set to '$current' -- not overwriting" >&2
        echo "    To update manually: git -C $REPO_DIR config --local core.hooksPath tools/hooks" >&2
        return
    fi
    git -C "$REPO_DIR" config --local core.hooksPath tools/hooks
    changed "Set git core.hooksPath = tools/hooks"
}

deploy_tmux_config() {
    local src="$PLUGIN_DIR/terminal/tmux.conf"
    local dst="$HOME/.tmux.conf"

    if [[ ! -f "$src" ]]; then
        echo "  ⚠ tmux.conf template not found at $src" >&2
        return
    fi

    if [[ -f "$dst" ]] && ! $FORCE; then
        if diff -q "$src" "$dst" >/dev/null 2>&1; then
            skipped "tmux config up to date"
            return
        fi
        changed "tmux config drift detected -- updating"
    fi

    cp "$src" "$dst"
    changed "tmux config deployed to $dst"
}

deploy_copilot_plugin() {
    # Install agent-worktrees from the copilot-extensions marketplace.
    # Ensures the marketplace is registered, installs or updates the plugin,
    # then removes any stale _direct install.
    #
    # When running from inside the installed-plugins directory (i.e.
    # invoked by cmd_update after it already ran 'copilot plugin update'),
    # skip the update call to avoid replacing files under our own feet.

    if ! command -v copilot >/dev/null 2>&1; then
        warn "Copilot CLI not found - skipping plugin install"
        return
    fi

    # Detect if we are running from the installed plugin directory
    local installed_plugins_dir="$HOME/.copilot/installed-plugins"
    local running_from_installed=false
    case "$PLUGIN_DIR" in
        "$installed_plugins_dir"*) running_from_installed=true ;;
    esac

    # 1. Register marketplace if not present
    if ! copilot plugin marketplace list 2>/dev/null | grep -q 'copilot-extensions'; then
        local add_out
        add_out=$(copilot plugin marketplace add ThomasMichon/copilot-extensions 2>&1) || {
            warn "Failed to register marketplace: $add_out"
            return
        }
        changed "Registered copilot-extensions marketplace"
    fi

    # 2. Parse current plugin state
    local plugin_list has_marketplace=false has_direct=false
    plugin_list=$(copilot plugin list 2>/dev/null)
    if echo "$plugin_list" | grep -q 'agent-worktrees@copilot-extensions'; then
        has_marketplace=true
    fi
    if echo "$plugin_list" | grep 'agent-worktrees' | grep -qv '@'; then
        has_direct=true
    fi

    # 3. Install or update marketplace plugin
    local out
    if $running_from_installed; then
        ok "Copilot plugin updated (marketplace)"
    elif $has_marketplace; then
        out=$(copilot plugin update agent-worktrees@copilot-extensions 2>&1) || {
            warn "Plugin update failed: $out"
        }
        ok "Copilot plugin updated (marketplace)"
    else
        out=$(copilot plugin install agent-worktrees@copilot-extensions 2>&1) || {
            warn "Plugin install failed: $out"
            return
        }
        changed "Copilot plugin installed (agent-worktrees@copilot-extensions)"
    fi

    # 4. Remove stale _direct install if marketplace is now present
    if $has_direct; then
        if copilot plugin list 2>/dev/null | grep -q 'agent-worktrees@copilot-extensions'; then
            copilot plugin uninstall agent-worktrees >/dev/null 2>&1 || true
            changed "Removed stale _direct plugin install"
        fi
    fi
}

assert_path() {
    case ":$PATH:" in
        *":$LOCAL_BIN:"*) ok "$LOCAL_BIN is on PATH" ;;
        *)
            err "$LOCAL_BIN is not on PATH"
            echo "    Add to ~/.bashrc: export PATH=\"\$HOME/.local/bin:\$PATH\""
            ;;
    esac
}

ensure_copilot_experimental() {
    # Ensure experimental: true in Copilot CLI settings.json.
    # The CLI gates extension loading on this flag -- COPILOT_FEATURE_FLAGS
    # alone is not sufficient. Both are required.
    local settings_file="$HOME/.copilot/settings.json"
    [[ -f "$settings_file" ]] || return 0

    if command -v python3 >/dev/null 2>&1; then
        local result
        result=$(python3 -c "
import json, sys
try:
    with open('$settings_file') as f:
        d = json.load(f)
    if d.get('experimental', False):
        print('already_on')
        sys.exit(0)
    d['experimental'] = True
    with open('$settings_file', 'w') as f:
        json.dump(d, f, indent=2)
        f.write('\n')
    print('updated')
except Exception as e:
    print(f'error: {e}', file=sys.stderr)
    print('error')
" 2>/dev/null) || result="error"
        case "$result" in
            already_on) ok "Copilot experimental mode enabled" ;;
            updated)    changed "Copilot experimental mode enabled (required for extensions)" ;;
            *)          warn "Could not update $settings_file" ;;
        esac
    fi
}

# ── Actions ──────────────────────────────────────────────────────────────

case "$ACTION" in
    install)
        header "Installing $SERVICE_NAME"

        machine="$(resolve_machine)"
        platform="$(detect_platform)"
        echo "  Machine:  $machine"
        echo "  Platform: $platform"
        if $HAS_PROJECT; then
            echo "  Project:  $PROJECT_NAME"
            if [[ -n "$REPO_DIR" ]]; then
                echo "  Repo:     $REPO_DIR"
            fi
        else
            echo "  Project:  (none - runtime only; pass --project-name to adopt a repo)"
        fi

        # Prereq checks
        missing_prereqs=()
        command -v git >/dev/null 2>&1 || missing_prereqs+=("git")
        command -v uv >/dev/null 2>&1 || missing_prereqs+=("uv")
        if [[ ${#missing_prereqs[@]} -gt 0 ]]; then
            err "Missing prerequisites: ${missing_prereqs[*]}"
            exit 1
        fi

        # Create directories (runtime always; project only if adopting)
        mkdir -p "$INSTALL_DIR" "$BIN_DIR" "$LOCAL_BIN"
        if $HAS_PROJECT; then
            mkdir -p "$PROJECT_DIR" "$WORKTREES_DIR"
        fi

        # -- Shared runtime (venv first: package install targets the venv) --
        deploy_venv || exit 1
        deploy_package || exit 1
        deploy_wrappers || exit 1
        remove_legacy_scripts
        remove_legacy_binstubs
        deploy_copilot_plugin
        ensure_copilot_experimental
        assert_path

        # -- Project-specific (only when adopting) --
        if $HAS_PROJECT; then
            deploy_config "$machine" "$platform" || true
            deploy_binstub
            register_project
            deploy_tabby_profile "$platform" "$machine"
            deploy_tmux_config
            deploy_git_hooks_path

            if [[ -n "$REPO_DIR" ]]; then
                WORKTREE_PROJECT="$PROJECT_NAME" PYTHONUTF8=1 \
                    "$VENV_PYTHON" -m agent_worktrees deploy-instructions --machine "$machine" 2>&1 \
                    | sed 's/^/  /' || warn "Instruction file deployment skipped"
            fi
        fi

        write_deploy_manifest

        echo ""
        ok "Installation complete"
        echo "  Runtime dir: $INSTALL_DIR"
        if $HAS_PROJECT; then
            echo "  Project dir: $PROJECT_DIR"
            echo "  Usage:       $PROJECT_NAME"
        fi
        echo "  Runtime:     Python ($VENV_PYTHON)"
        ;;

    uninstall)
        header "Uninstalling $SERVICE_NAME"

        # Remove Tabby profile (before venv removal -- needs Python)
        if $HAS_PROJECT; then
            remove_tabby_profile
        fi

        # Remove project binstub
        if $HAS_PROJECT; then
            local_binstub="$LOCAL_BIN/$PROJECT_NAME"
            if [[ -f "$local_binstub" ]]; then
                rm -f "$local_binstub"
                changed "Removed binstub: $local_binstub"
            fi
        fi

        # Remove tool binstubs
        for stub in mark-session-complete agent-worktrees; do
            local stub_path="$LOCAL_BIN/$stub"
            if [[ -f "$stub_path" ]]; then
                rm -f "$stub_path"
                changed "Removed binstub: $stub_path"
            fi
        done

        # Sweep any lingering legacy alias binstubs
        remove_legacy_binstubs

        # Remove Python runtime (venv + package)
        if [[ -d "$VENV_DIR" ]]; then
            rm -rf "$VENV_DIR"
            changed "Removed venv: $VENV_DIR"
        fi
        if [[ -d "$LIB_DIR" ]]; then
            rm -rf "$LIB_DIR"
            changed "Removed package: $LIB_DIR"
        fi

        # Remove wrappers
        for wrapper in launch-session.cmd launch-session.sh; do
            rm -f "$BIN_DIR/$wrapper"
        done
        remove_legacy_scripts
        changed "Removed wrappers from $BIN_DIR"

        # Remove tmux config
        if [[ -f "$HOME/.tmux.conf" ]]; then
            rm -f "$HOME/.tmux.conf"
            changed "Removed tmux config (~/.tmux.conf)"
        fi

        if $REMOVE_CONFIG; then
            if $HAS_PROJECT; then
                rm -rf "$PROJECT_DIR"
                changed "Removed project dir $PROJECT_DIR (config + session metadata)"
            fi
            rm -rf "$INSTALL_DIR"
            changed "Removed runtime dir $INSTALL_DIR"
        else
            rm -f "$INSTALL_DIR/deploy-manifest.json"
            if $HAS_PROJECT; then
                skipped "Config and session metadata preserved at $PROJECT_DIR"
            fi
            echo "    Use --remove-config to delete everything"
        fi

        ok "Uninstall complete"
        ;;

    start)
        header "Starting $SERVICE_NAME"
        skipped "Not a daemon -- invoke with: agent-worktrees"
        ;;

    stop)
        header "Stopping $SERVICE_NAME"
        skipped "Not a daemon -- Ctrl+C or close the terminal to end a session"
        ;;

    status)
        header "$SERVICE_NAME Status"

        # Venv
        if [[ -x "$VENV_PYTHON" ]]; then
            ok "Venv Python: $VENV_PYTHON"
        else
            err "Venv Python missing: $VENV_PYTHON"
        fi

        # Package (installed in the venv)
        if PYTHONPATH= "$VENV_PYTHON" -c 'import agent_worktrees' 2>/dev/null; then
            ok "Package importable in venv"
        else
            err "Package not importable in venv"
        fi

        # Wrapper
        if [[ -f "$BIN_DIR/launch-session.sh" ]]; then
            ok "launch-session.sh deployed"
        else
            err "launch-session.sh missing"
        fi

        # Tool binstubs
        for stub in agent-worktrees; do
            if [[ -f "$LOCAL_BIN/$stub" ]]; then
                ok "Binstub installed at $LOCAL_BIN/$stub"
            else
                err "Binstub missing at $LOCAL_BIN/$stub"
            fi
        done

        if $HAS_PROJECT; then
            # Project binstub
            if [[ -f "$LOCAL_BIN/$PROJECT_NAME" ]]; then
                ok "Binstub installed at $LOCAL_BIN/$PROJECT_NAME"
            else
                err "Binstub missing at $LOCAL_BIN/$PROJECT_NAME"
            fi

            # Config (project dir)
            if [[ -f "$PROJECT_DIR/config.yaml" ]]; then
                ok "Config at $PROJECT_DIR/config.yaml"
            else
                err "Config missing at $PROJECT_DIR/config.yaml"
            fi

            # Tabby terminal profile
            check_tabby_profile

            # Active sessions
            if [[ -d "$WORKTREES_DIR" ]]; then
                total=$(find "$WORKTREES_DIR" -name '*.yaml' 2>/dev/null | wc -l)
                active=$(grep -l 'status: active' "$WORKTREES_DIR"/*.yaml 2>/dev/null | wc -l)
                ok "$active active session(s), $total total"
            fi
        else
            skipped "Project status skipped (no project specified)"
        fi

        # tmux config
        if [[ -f "$HOME/.tmux.conf" ]]; then
            ok "tmux config at ~/.tmux.conf"
        else
            echo "  ! tmux config missing -- run 'update' to deploy" >&2
        fi

        assert_path

        # Git hooks
        if [[ -n "$REPO_DIR" ]] && $HAS_PROJECT; then
            hooks_path="$(git -C "$REPO_DIR" config --local core.hooksPath 2>/dev/null)" || true
            if [[ "$hooks_path" == "tools/hooks" ]]; then
                ok "Git hooksPath = tools/hooks"
            elif [[ -n "$hooks_path" ]]; then
                echo "  ! Git hooksPath = $hooks_path (expected tools/hooks)"
            else
                err "Git core.hooksPath not set -- run 'update' to configure"
            fi
        else
            skipped "Git hooks check skipped (no repo detected)"
        fi

        show_deploy_status
        ;;

    update-config)
        header "Updating $SERVICE_NAME Config"

        if ! $HAS_PROJECT; then
            err "No project specified -- pass --project-name"
            exit 1
        fi

        if [[ ! -f "$PROJECT_DIR/config.yaml" ]]; then
            err "Config not found -- run 'install' first"
            exit 1
        fi

        if $FORCE; then
            machine="$(resolve_machine)"
            platform="$(detect_platform)"
            deploy_config "$machine" "$platform"
        else
            skipped "Config is machine-generated -- use --force to regenerate"
            echo "    Current: $PROJECT_DIR/config.yaml"
        fi
        ;;

    update)
        header "Updating $SERVICE_NAME"

        if [[ ! -d "$BIN_DIR" ]]; then
            err "Not installed -- run 'install' first"
            exit 1
        fi

        # -- Shared runtime (venv first: package install targets the venv) --
        deploy_venv || exit 1
        deploy_package || exit 1
        deploy_wrappers || exit 1
        remove_legacy_scripts
        remove_legacy_binstubs
        deploy_copilot_plugin
        ensure_copilot_experimental

        # -- Project-specific (only when a project is known) --
        if $HAS_PROJECT; then
            deploy_binstub
            register_project
            deploy_tabby_profile "$(detect_platform)" "$(resolve_machine)"
            deploy_tmux_config
            deploy_git_hooks_path

            if [[ -n "$REPO_DIR" ]]; then
                update_machine="$(resolve_machine)"
                WORKTREE_PROJECT="$PROJECT_NAME" PYTHONUTF8=1 \
                    "$VENV_PYTHON" -m agent_worktrees deploy-instructions --machine "$update_machine" 2>&1 \
                    | sed 's/^/  /' || warn "Instruction file deployment skipped"
            fi
        fi

        write_deploy_manifest

        ok "Update complete"
        ;;

    *)
        echo "Usage: $0 {install|uninstall|start|stop|status|update-config|update} [--project-name NAME] [--force] [--remove-config] [--machine NAME]" >&2
        exit 1
        ;;
esac
