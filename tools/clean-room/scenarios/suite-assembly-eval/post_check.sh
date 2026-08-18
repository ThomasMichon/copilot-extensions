#!/usr/bin/env bash
# suite-assembly-eval/post_check.sh -- programmatic ground-truth AFTER the turn.
#
# Records the OBJECTIVE assembled state the judge anchors on: whether the suite
# self-provisioned (binstub versions), whether the repo actually got registered
# (projects.yaml), whether a worktree exists (agent-worktrees list), and whether
# agent-bridge answers. It also surfaces self-heals: a hand-edited projects.yaml
# or a raw `git worktree` outside the documented flow. It never substitutes for
# the literal-mode judgment. MUST be LF.
set -uo pipefail

_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${CR_LIB:-$_SELF_DIR/../../lib/clean-room-lib.sh}"
: "${CR_SCENARIO_NAME:=suite-assembly-eval}"
export CR_SCENARIO_NAME
cr_init 2>/dev/null || true

phase 9 "post-check: assembled-state ground-truth"

capture "pc-wt-version"     -- bash -lc 'agent-worktrees --version 2>&1' || true
capture "pc-bridge-version" -- bash -lc 'agent-bridge --version 2>&1' || true
capture "pc-projects"       -- bash -lc 'cat $HOME/.agent-worktrees/projects.yaml 2>&1 || echo "(no projects.yaml)"' || true
capture "pc-wt-list"        -- bash -lc 'cd $HOME/demo-repo 2>/dev/null; agent-worktrees list 2>&1; echo "---repos---"; agent-worktrees repos 2>&1' || true
capture "pc-bridge-agents"  -- bash -lc 'agent-bridge agents 2>&1 | head -20' || true

# Objective assembly outcome.
if [ -f "$HOME/.agent-worktrees/projects.yaml" ] && grep -qi 'demo-repo' "$HOME/.agent-worktrees/projects.yaml" 2>/dev/null; then
    info "assembly signal: demo-repo IS registered in projects.yaml"
    cr_meta "post_repo_registered" "yes"
else
    info "demo-repo NOT registered in projects.yaml"
    cr_meta "post_repo_registered" "no"
fi
# agent-worktrees' default layout puts worktrees at ~/<repo>.worktrees/<id>/, not
# ~/.worktrees -- scan the tool's real layout (and any *.worktrees sibling).
_wt_count="$(find "$HOME" -maxdepth 4 -type d -path '*.worktrees/*' -name '.git' 2>/dev/null | wc -l | tr -d ' ')"
if [ "${_wt_count:-0}" = 0 ]; then
    _wt_count="$(find "$HOME" -maxdepth 4 -path '*.worktrees/*' -name '.git' 2>/dev/null | wc -l | tr -d ' ')"
fi
if [ "${_wt_count:-0}" -gt 0 ]; then
    info "assembly signal: $_wt_count git worktree(s) present under ~/*.worktrees"
    cr_meta "post_worktree_created" "yes:$_wt_count"
else
    info "no worktree found under ~/*.worktrees"
    cr_meta "post_worktree_created" "no"
fi

# Self-heal tripwire: a raw `git worktree` outside the documented agent-worktrees
# flow would show up as a linked worktree the tool doesn't know about. Record the
# git-native view so the judge can compare against the tool's list.
capture "pc-git-worktrees" -- bash -lc 'cd $HOME/demo-repo 2>/dev/null && git worktree list 2>&1 || echo "(demo-repo unavailable)"' || true

cr_finalize
