#!/usr/bin/env bash
# Project hooks runner -- runs on session start via hooks.json
# Discovers and executes per-project session-start hooks from the
# project config directory (~/.{project}/hooks/session-start.sh).

set -euo pipefail

project="${WORKTREE_PROJECT:-}"
if [[ -z "$project" ]]; then exit 0; fi

hook="$HOME/.$project/hooks/session-start.sh"
if [[ ! -f "$hook" ]]; then exit 0; fi

bash "$hook" || true

exit 0
