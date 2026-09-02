#!/usr/bin/env bash
set -uo pipefail

_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${CR_LIB:-$_SELF_DIR/../../lib/clean-room-lib.sh}"

: "${CR_SCENARIO_NAME:=context-handoff-eval}"
export CR_SCENARIO_NAME
ROOT="/home/operator/context-handoff-eval"
SOURCE="${CR_HARNESS_MOUNT:-/harness}"
MARKETPLACE="copilot-extensions"
INSTALLED="$HOME/.copilot/installed-plugins/$MARKETPLACE"

cr_init
cr_meta "role" "starting-state-and-tier-p"
mkdir -p "$ROOT" "$HOME/.copilot"

phase 0 "install unpublished handoff pair"
cat >"$HOME/.copilot/settings.json" <<JSON
{
  "extraKnownMarketplaces": {
    "$MARKETPLACE": { "source": { "source": "directory", "path": "$SOURCE" } }
  },
  "enabledPlugins": {
    "agent-worktrees@$MARKETPLACE": true,
    "context-handoff@$MARKETPLACE": true
  },
  "experimental": true
}
JSON
capture "marketplace-add" -- copilot plugin marketplace add "$SOURCE" || true
capture "install-agent-worktrees" -- \
  copilot plugin install "agent-worktrees@$MARKETPLACE" || true
capture "install-context-handoff" -- \
  copilot plugin install "context-handoff@$MARKETPLACE" || true
if [ -d "$INSTALLED/agent-worktrees" ] &&
   [ -d "$INSTALLED/context-handoff" ]; then
    pass "unpublished handoff pair installed through the supported marketplace"
else
    jam "plugin-load" "handoff pair did not install" \
      "run with -HarnessMount pointing at the source checkout"
fi

phase 1 "provision agent-worktrees and synthetic worktree"
export PATH="$HOME/.local/bin:$PATH"
capture "provision-agent-worktrees" -- \
  bash "$INSTALLED/agent-worktrees/scripts/install.sh" provision || true
mkdir -p "$ROOT/repo"
(
  cd "$ROOT/repo" &&
  git init -q &&
  git config user.email test@example.com &&
  git config user.name "Clean Room" &&
  printf '# context-handoff eval\n' >README.md &&
  git add README.md &&
  git commit -qm init
)
capture "register-repo" -- bash -lc \
  "cd '$ROOT/repo' && agent-worktrees register context-handoff-eval" || true
( cd "$ROOT/repo" && agent-worktrees create --json ) >"$ROOT/create.json" 2>"$CR_LOGDIR/create.stderr" || true
WT_ID="$(python3 "$_SELF_DIR/fixture.py" field --path "$ROOT/create.json" --name worktree_id)"
WT_PATH="$(python3 "$_SELF_DIR/fixture.py" field --path "$ROOT/create.json" --name worktree_path)"
if [ -n "$WT_ID" ] && [ -d "$WT_PATH" ]; then
    pass "synthetic worktree created"
else
    jam "repo-config" "could not create synthetic worktree" \
      "see context-handoff-eval/create.json and cr-logs/create.stderr"
fi
printf '%s' "$WT_ID" >"$ROOT/worktree-id"
printf '%s' "$WT_PATH" >"$ROOT/acp-cwd"

phase 2 "prepare pending high-fidelity handoff"
agent-worktrees register-session \
  --worktree-id "$WT_ID" --session-id eval-predecessor >/dev/null
HANDOFF_CLI="$INSTALLED/context-handoff/extensions/context-handoff/handoff-cli.mjs"
cat >"$ROOT/payload.md" <<'EOF'
## Session Continuation

### Objective
Measure prompt-first handoff takeover without sacrificing stored detail.

### Fidelity
Canary: HANDOFF_FIDELITY_7f1a9c2e

### Successor Work
Acknowledge the stored baton, become the worktree head, report the canary, and preserve an unverified predecessor.
EOF
(
  cd "$WT_PATH" &&
  node "$HANDOFF_CLI" save --no-task --json \
    --session-id eval-predecessor \
    --cwd "$WT_PATH" \
    --title "Measure handoff takeover" \
    --prompt-file "$ROOT/payload.md"
) >"$ROOT/save.json" 2>"$CR_LOGDIR/save.stderr" || true
STATE_DIR="$(cd "$WT_PATH" && agent-worktrees get worktree-state-dir --session-id eval-predecessor)"
printf '%s' "$STATE_DIR" >"$ROOT/state-dir"
SEED="$(python3 "$_SELF_DIR/fixture.py" field --path "$ROOT/save.json" --name seed)"
printf '%s' "$SEED" >"$ROOT/seed.txt"
EXPECTED_SEED="$(python3 "$_SELF_DIR/fixture.py" field --path "$_SELF_DIR/manifest.json" --name prompt)"
printf '\nexport AGENT_WORKTREES_HANDOFF_TOKEN=handoff-eval-predecessor\n' >>"$HOME/.profile"
if [ "$SEED" = "$EXPECTED_SEED" ] &&
   python3 "$_SELF_DIR/fixture.py" verify --root "$ROOT"; then
    pass "pending handoff, compact seed, and startup token are ready"
else
    jam "repo-config" "handoff eval fixture failed verification" \
      "see context-handoff-eval/save.json and payload.md"
fi

cr_finalize
