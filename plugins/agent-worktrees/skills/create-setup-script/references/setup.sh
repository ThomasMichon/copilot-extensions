#!/usr/bin/env bash
# setup.sh -- worktree session setup script (reference example)
#
# Full Bash example for a `launch`/setup script invoked by agent-worktrees.
# Conventions (see the create-setup-script SKILL.md):
#   - Accept --machine, --recovery, and pass remaining args to Copilot.
#   - Detect ACP mode (`--acp` present) and skip banners / heavy deps for speed.
#   - Launching Copilot MUST be the last step (exec).
MACHINE="${HOSTNAME}"
IS_ACP=false
COPILOT_ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --machine)  MACHINE="$2"; shift 2 ;;
        --recovery) shift ;;
        --acp)      IS_ACP=true; COPILOT_ARGS+=("$1"); shift ;;
        *)          COPILOT_ARGS+=("$1"); shift ;;
    esac
done

# 1. Environment
export MY_API_KEY="..."

# 2. Dependencies (skip in ACP mode)
if [[ "$IS_ACP" != "true" ]]; then
    [[ -d node_modules ]] || npm ci --quiet
fi

# 3. Banner (skip in ACP mode)
if [[ "$IS_ACP" != "true" ]]; then
    echo "[>] Ready: ${WORKTREE_PROJECT:-unknown} on $MACHINE"
fi

# 4. Launch Copilot (REQUIRED -- must be last)
exec copilot "${COPILOT_ARGS[@]}"
