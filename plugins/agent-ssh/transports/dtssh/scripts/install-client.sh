#!/usr/bin/env bash
set -euo pipefail

skip_login=0
skip_discover=0
prune=0
clean=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-login) skip_login=1 ;;
    --skip-discover) skip_discover=1 ;;
    --prune) prune=1 ;;
    --clean) clean=1 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

if ! command -v dtssh >/dev/null 2>&1; then
  echo "Installing dtssh..." >&2
  curl -fsSL https://raw.githubusercontent.com/bmiddha/devtunnel-ssh/main/scripts/install-release.sh | sh
  export PATH="$HOME/.local/bin:$HOME/.dtssh/bin:$PATH"
fi

is_logged_in() {
  # dtssh has no `login --status` subcommand; query the bundled devtunnel CLI.
  local dt
  dt="$(command -v devtunnel || true)"
  [[ -z "$dt" ]] && return 1
  "$dt" user show --json 2>/dev/null | grep -q '"status"[[:space:]]*:[[:space:]]*"Logged in"'
}

if [[ $skip_login -eq 0 ]]; then
  if ! is_logged_in; then
    echo "Starting dtssh login. Complete the Entra prompt, then return here." >&2
    dtssh login
  fi
fi

if [[ $skip_discover -eq 0 ]]; then
  args=(discover)
  [[ $prune -eq 1 ]] && args+=(--prune)
  [[ $clean -eq 1 ]] && args+=(--clean)
  dtssh "${args[@]}"
fi

dtssh list
