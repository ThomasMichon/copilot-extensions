#!/usr/bin/env bash
set -euo pipefail

action="${1:-status}"
if [[ $# -gt 0 ]]; then shift; fi
alias_name="$(hostname | tr '[:upper:]' '[:lower:]')"
port="2222"
tunnel=""
user_name=""
skip_login=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --alias) alias_name="$2"; shift ;;
    --port) port="$2"; shift ;;
    --tunnel) tunnel="$2"; shift ;;
    --user) user_name="$2"; shift ;;
    --skip-login) skip_login=1 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

ensure_dtssh() {
  if ! command -v dtssh >/dev/null 2>&1; then
    curl -fsSL https://raw.githubusercontent.com/bmiddha/devtunnel-ssh/main/scripts/install-release.sh | sh
    export PATH="$HOME/.local/bin:$HOME/.dtssh/bin:$PATH"
  fi
}

is_logged_in() {
  # dtssh has no `login --status` subcommand; query the bundled devtunnel CLI.
  local dt
  dt="$(command -v devtunnel || true)"
  [[ -z "$dt" ]] && return 1
  "$dt" user show --json 2>/dev/null | grep -q '"status"[[:space:]]*:[[:space:]]*"Logged in"'
}

ensure_login() {
  [[ $skip_login -eq 1 ]] && return 0
  if ! is_logged_in; then
    dtssh login
  fi
}

host_args=(--alias "$alias_name" --port "$port")
[[ -n "$tunnel" ]] && host_args+=(--tunnel "$tunnel")
[[ -n "$user_name" ]] && host_args+=(--user "$user_name")

case "$action" in
  install|update)
    ensure_dtssh
    ensure_login
    dtssh service install "${host_args[@]}"
    ;;
  uninstall)
    dtssh service uninstall
    ;;
  start|stop|restart|status|logs)
    dtssh service "$action"
    ;;
  *)
    echo "usage: install-host.sh [install|update|uninstall|start|stop|restart|status|logs] [--alias NAME] [--port N] [--tunnel ID] [--user USER] [--skip-login]" >&2
    exit 2
    ;;
esac
