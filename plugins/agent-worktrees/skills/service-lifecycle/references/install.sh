#!/usr/bin/env bash
set -euo pipefail

# Installer skeleton for a deployed service (reference example). See the
# service-lifecycle SKILL.md for the full lifecycle contract (install/uninstall/
# start/stop/status/update-config/update) and the drift-confirmation rules.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/opt/my-service"
SERVICE_USER="${USER}"   # real user, not root

case "${1:-status}" in
    install)       do_install ;;
    uninstall)     do_uninstall "$@" ;;
    start)         do_start ;;
    stop)          do_stop ;;
    status)        do_status ;;
    update-config) do_update_config "$@" ;;
    update)        do_update "$@" ;;
esac
