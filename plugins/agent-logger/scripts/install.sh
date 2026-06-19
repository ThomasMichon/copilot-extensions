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

install_package() {
  mkdir -p "${INSTALL_DIR}" "${LOCAL_BIN}"
  if [ ! -d "${VENV}" ]; then
    python3 -m venv "${VENV}"
    chg "created venv at ${VENV}"
  fi
  "${VENV}/bin/python" -m pip install --quiet --upgrade pip
  "${VENV}/bin/python" -m pip install --quiet "${PLUGIN_DIR}"
  ok "installed agent-logger package"

  # Binstub on PATH -> venv console script.
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
    ok "timer enabled (every 4h)"
    ;;
  update)
    install_package
    write_units
    systemctl --user daemon-reload || true
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
