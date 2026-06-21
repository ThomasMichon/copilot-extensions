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
UNIT_DIR="${HOME}/.config/systemd/user"
TIMER_NAME="agent-logger-sync"

log()  { printf '  [%s] %s\n' "$1" "$2"; }
ok()   { log "OK" "$1"; }
chg()  { log "->" "$1"; }
warn() { log "WARN" "$1"; }

# === install-contract:v3 source-kind -- keep byte-identical across plugins ===
# A runtime footprint's source is inferred from where the installer runs.
# Vendored under the Copilot CLI installed-plugins dir => marketplace;
# anything else (a git checkout) => local.
_source_kind() {
    case "$(printf '%s' "$1" | tr '\\' '/')" in
        */.copilot/installed-plugins/*) printf 'marketplace' ;;
        *) printf 'local' ;;
    esac
}
# === end install-contract:v3 source-kind ===

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

install_package() {
  mkdir -p "${INSTALL_DIR}" "${LOCAL_BIN}"

  # Prerequisite: uv (venv + package management per the install contract).
  if ! command -v uv >/dev/null 2>&1; then
    warn "uv not found on PATH (required for venv + package management)"
    warn "Install: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
  fi

  if [ ! -x "${VENV}/bin/python" ]; then
    if ! uv venv "${VENV}" --python 3.10 --allow-existing; then
      uv venv "${VENV}" --allow-existing
    fi
    chg "created venv at ${VENV}"
  fi
  uv pip install --python "${VENV}/bin/python" "${PLUGIN_DIR}" --quiet
  ok "installed agent-logger package"

  # Binstub on PATH -> venv console script (the sanctioned POSIX launch path).
  ln -sf "${VENV}/bin/session-sync" "${LOCAL_BIN}/session-sync"
  ln -sf "${VENV}/bin/agent-logger" "${LOCAL_BIN}/agent-logger"
  ok "linked binstubs into ${LOCAL_BIN}"
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
TimeoutStartSec=120
RuntimeMaxSec=300
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
    ;;
  status)
    if [ -x "${VENV}/bin/session-sync" ]; then
      ok "installed: $("${VENV}/bin/agent-logger" version 2>/dev/null || echo unknown)"
      "${VENV}/bin/session-sync" status || true
    else
      warn "not installed (run: bash scripts/install.sh install)"
    fi
    systemctl --user is-active "${TIMER_NAME}.timer" 2>/dev/null \
      && ok "timer active" || warn "timer not active"
    ;;
  *)
    echo "usage: install.sh {install|update|uninstall|status}" >&2
    exit 2
    ;;
esac
