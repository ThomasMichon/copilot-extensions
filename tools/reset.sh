#!/usr/bin/env bash
# =============================================================================
# reset.sh -- one-shot teardown / baseline reset for copilot-extensions.
#
# Stops the agent-bridge daemon + credential relay, removes all three plugin
# runtimes (~/.agent-worktrees, ~/.agent-bridge, ~/.agent-codespaces), their
# binstubs, project binstubs, and the systemd user unit -- so a machine returns
# to a clean baseline without manual process-killing or filesystem sweeps.
#
# Idempotent and dependency-free: works even when the binstubs/CLIs are broken.
# Does NOT touch your source repos or their .worktrees content.
#
# Usage:
#   bash tools/reset.sh [--remove-plugins] [--remove-project-configs] [--yes]
# =============================================================================

set -uo pipefail

REMOVE_PLUGINS=false
REMOVE_PROJECT_CONFIGS=false
ASSUME_YES=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --remove-plugins)         REMOVE_PLUGINS=true; shift ;;
        --remove-project-configs) REMOVE_PROJECT_CONFIGS=true; shift ;;
        --yes|-y)                 ASSUME_YES=true; shift ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

step() { echo "  ...    $*"; }
ok()   { echo "  [OK]   $*"; }
skip() { echo "  [SKIP] $*"; }
warn() { echo "  [WARN] $*"; }

RUNTIMES=("$HOME/.agent-worktrees" "$HOME/.agent-bridge" "$HOME/.agent-codespaces")
BINSTUBS=("agent-worktrees" "agent-bridge" "agent-codespaces")
PORTS=(9280 9281 9857)
LOCAL_BIN="$HOME/.local/bin"
SYSTEMD_UNIT="agent-bridge.service"

echo ""
echo "=== copilot-extensions reset ==="
echo ""

if ! $ASSUME_YES; then
    echo "This removes the agent-worktrees / agent-bridge / agent-codespaces"
    echo "runtimes, binstubs, service, and config. Source repos are untouched."
    $REMOVE_PLUGINS && echo "  + marketplace plugins will be uninstalled"
    $REMOVE_PROJECT_CONFIGS && echo "  + per-project ~/.<project> config dirs will be removed"
    read -r -p "Proceed? (y/N) " ans
    case "$ans" in y|Y|yes|YES) ;; *) echo "Aborted."; exit 0 ;; esac
    echo ""
fi

# -- 1. Stop the systemd user unit --------------------------------------------
if command -v systemctl &>/dev/null; then
    if systemctl --user list-unit-files 2>/dev/null | grep -q "$SYSTEMD_UNIT"; then
        systemctl --user stop "$SYSTEMD_UNIT" 2>/dev/null || true
        systemctl --user disable "$SYSTEMD_UNIT" 2>/dev/null || true
        rm -f "$HOME/.config/systemd/user/$SYSTEMD_UNIT"
        systemctl --user daemon-reload 2>/dev/null || true
        ok "Removed systemd unit $SYSTEMD_UNIT"
    else
        skip "systemd unit $SYSTEMD_UNIT not present"
    fi
fi

# -- 2. Kill anything still bound to the bridge / relay ports -----------------
for port in "${PORTS[@]}"; do
    pids=""
    if command -v ss &>/dev/null; then
        pids=$(ss -lptnH "sport = :$port" 2>/dev/null | grep -oP 'pid=\K[0-9]+' | sort -u)
    elif command -v lsof &>/dev/null; then
        pids=$(lsof -ti "tcp:$port" 2>/dev/null)
    fi
    for pid in $pids; do
        kill -TERM "$pid" 2>/dev/null && ok "Killed process on port $port (pid=$pid)"
    done
done

# -- 3. Best-effort: run each plugin's own uninstall --------------------------
plugin_root="$HOME/.copilot/installed-plugins"
find_plugin_script() {
    local name="$1" script="$2"
    [[ -d "$plugin_root" ]] || return 1
    local pj
    pj=$(grep -rl "\"$name\"" "$plugin_root" --include=plugin.json 2>/dev/null | head -1)
    [[ -n "$pj" ]] || return 1
    local p="$(dirname "$pj")/scripts/$script"
    [[ -f "$p" ]] && echo "$p"
}

if aw=$(find_plugin_script agent-worktrees install.sh); then
    step "Running agent-worktrees uninstall..."
    bash "$aw" uninstall --remove-config --force >/dev/null 2>&1 || true
fi
if ab=$(find_plugin_script agent-bridge install.sh); then
    step "Running agent-bridge uninstall..."
    bash "$ab" uninstall --purge >/dev/null 2>&1 || true
fi
if ac=$(find_plugin_script agent-codespaces install.sh); then
    step "Running agent-codespaces uninstall..."
    bash "$ac" uninstall >/dev/null 2>&1 || true
fi

# -- 4. Hard sweep (idempotent -- catches partial / init-only installs) -------
for rt in "${RUNTIMES[@]}"; do
    if [[ -d "$rt" ]]; then
        rm -rf "$rt" && ok "Removed $rt" || warn "Could not fully remove $rt"
    fi
done

for name in "${BINSTUBS[@]}"; do
    if [[ -f "$LOCAL_BIN/$name" ]]; then rm -f "$LOCAL_BIN/$name" && ok "Removed binstub $name"; fi
done

# Project binstubs: any ~/.local/bin file that launches the worktree runtime
if [[ -d "$LOCAL_BIN" ]]; then
    for f in "$LOCAL_BIN"/*; do
        [[ -f "$f" ]] || continue
        if grep -q '\.agent-worktrees/bin/launch-session' "$f" 2>/dev/null; then
            rm -f "$f" && ok "Removed project binstub $(basename "$f")"
        fi
    done
fi

# -- 5. Optional: per-project config dirs (~/.<project>) ----------------------
if $REMOVE_PROJECT_CONFIGS; then
    for d in "$HOME"/.*/; do
        cfg="$d/config.yaml"
        if [[ -f "$cfg" ]] && grep -qE 'worktree_root|anchor:' "$cfg" 2>/dev/null; then
            rm -rf "$d" && ok "Removed project config $(basename "$d")"
        fi
    done
fi

# -- 6. Optional: marketplace plugins -----------------------------------------
if $REMOVE_PLUGINS; then
    if command -v copilot &>/dev/null; then
        for name in "${BINSTUBS[@]}"; do
            step "copilot plugin uninstall $name"
            copilot plugin uninstall "$name@copilot-extensions" >/dev/null 2>&1 || true
        done
        copilot plugin marketplace remove ThomasMichon/copilot-extensions >/dev/null 2>&1 || true
        ok "Marketplace plugins uninstalled"
    else
        warn "copilot CLI not found -- skipping plugin uninstall"
    fi
fi

# -- 7. Report leftovers ------------------------------------------------------
echo ""
leftovers=()
for rt in "${RUNTIMES[@]}"; do [[ -d "$rt" ]] && leftovers+=("$rt"); done
if [[ ${#leftovers[@]} -eq 0 ]]; then
    ok "Baseline reset complete -- no copilot-extensions runtime artifacts remain"
else
    warn "Some artifacts remain:"
    for l in "${leftovers[@]}"; do echo "    - $l"; done
fi
echo ""
