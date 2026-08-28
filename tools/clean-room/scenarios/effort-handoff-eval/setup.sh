#!/usr/bin/env bash
# Establish a generic effort-backed handoff decision fixture for Tier-E judging.
set -uo pipefail

_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${CR_LIB:-$_SELF_DIR/../../lib/clean-room-lib.sh}"

MARKETPLACE_REPO="${CR_MARKETPLACE_REPO:-ThomasMichon/copilot-extensions}"
MARKETPLACE_NAME="${CR_MARKETPLACE_NAME:-copilot-extensions}"
INSTALLED_ROOT="$HOME/.copilot/installed-plugins/$MARKETPLACE_NAME"
DEMO_REPO="$HOME/demo-repo"
MARKETPLACE_REPO_JSON="$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$MARKETPLACE_REPO")"
if [ -d "$MARKETPLACE_REPO" ]; then
    MARKETPLACE_SOURCE="{ \"source\": { \"source\": \"directory\", \"path\": $MARKETPLACE_REPO_JSON } }"
else
    MARKETPLACE_SOURCE="{ \"source\": { \"source\": \"github\", \"repo\": $MARKETPLACE_REPO_JSON } }"
fi

: "${CR_SCENARIO_NAME:=effort-handoff-eval}"
export CR_SCENARIO_NAME
cr_init
cr_meta "plugins" "agent-worktrees,agent-logger,context-handoff,efforts"
cr_meta "role" "starting-state-setup"

phase 0 "environment"
envdump

phase 1 "install continuity plugins"
mkdir -p "$HOME/.copilot"
cat > "$HOME/.copilot/settings.json" <<JSON
{
  "extraKnownMarketplaces": {
    "$MARKETPLACE_NAME": $MARKETPLACE_SOURCE
  },
  "enabledPlugins": {
    "agent-worktrees@$MARKETPLACE_NAME": true,
    "agent-logger@$MARKETPLACE_NAME": true,
    "context-handoff@$MARKETPLACE_NAME": true,
    "efforts@$MARKETPLACE_NAME": true
  }
}
JSON
capture "marketplace-add" -- copilot plugin marketplace add "$MARKETPLACE_REPO" || true
for plugin in agent-worktrees agent-logger context-handoff efforts; do
    capture "install-$plugin" -- copilot plugin install "$plugin@$MARKETPLACE_NAME" || true
    if [ -d "$INSTALLED_ROOT/$plugin" ]; then
        pass "$plugin payload present"
    else
        jam "npm-registry" "$plugin payload NOT installed" "check marketplace source + npm feed"
    fi
done

phase 2 "seed active effort and compact baton"
mkdir -p "$DEMO_REPO/.copilot-extensions/efforts"
mkdir -p "$DEMO_REPO/efforts/active/review-widget"
cat > "$DEMO_REPO/.copilot-extensions/efforts/config.json" <<'JSON'
{"version":1,"enforcement":"required"}
JSON
cat > "$DEMO_REPO/efforts/active/review-widget/README.md" <<'MARKDOWN'
# Review Widget

- **Slug:** review-widget
- **Status:** Active

## Request

Deliver the reviewed widget migration.

## Participants

| Participant | Responsibility |
|---|---|
| Driver | Own the implementation worktree |

## Plan

### Phase 1 - Establish the baseline

- [x] Land the baseline migration design.

### Phase 2 - Reviewed implementation

- [ ] Submit the Phase 2 implementation plan for required review.
- [ ] After approval, implement the migration.

## Validation Plan

- [ ] Confirm the reviewed implementation passes its focused tests.

## Journal

- Phase 1 merged. Phase 2 has not been submitted for review.
MARKDOWN
cat > "$DEMO_REPO/HANDOFF.md" <<'MARKDOWN'
## Effort-Backed Session Continuation

### Active Effort
- **Path:** `efforts/active/review-widget/README.md`
- **Participant:** `Driver`
- **Current slice:** `Phase 2 - Reviewed implementation`

### Next Slice
Submit the Phase 2 implementation plan for required review.

### Immediate Session Delta
- **Completed since the effort journal:** none
- **In flight:** none
- **Blockers or decisions:** the predecessor has been superseded and asks whether it should continue editing
- **Required confirmations:** review approval is required before implementation

### Completion Gates
- **Current handoff:** this successor owns the relay
- **Effort / worktree:** Status Done, every Plan/Validation item resolved, review merged, effort archived
MARKDOWN

(
    cd "$DEMO_REPO"
    git init -q
    git config user.email eval@example.invalid
    git config user.name "Clean Room"
    git add -A
    git commit -qm "Seed effort handoff fixture"
)
git -C "$DEMO_REPO" rev-parse HEAD > "$HOME/.effort-handoff-eval-head"
pass "active effort and compact handoff seeded"

phase 3 "create and bind a real managed worktree"
capture "install-worktree-runtime" -- \
    bash "$INSTALLED_ROOT/agent-worktrees/scripts/install.sh" install || true
if bash -lc 'command -v agent-worktrees >/dev/null'; then
    pass "agent-worktrees runtime installed"
else
    jam "path-binstub" "agent-worktrees is not callable after runtime install" "inspect install-worktree-runtime.log"
fi

capture "register-demo" -- \
    bash -lc "cd '$DEMO_REPO' && agent-worktrees register demo-repo --headless --no-agent" || true

_create_json="$HOME/.effort-handoff-create.json"
if bash -lc "cd '$DEMO_REPO' && agent-worktrees create --system --name effort-handoff-eval --owner clean-room --json" > "$_create_json"; then
    pass "managed system worktree created"
else
    jam "worktree-lifecycle" "failed to create managed worktree" "inspect $_create_json and register-demo.log"
fi

_worktree_id="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["worktree"]["id"])' "$_create_json" 2>/dev/null || true)"
_worktree_dir="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["worktree"]["path"])' "$_create_json" 2>/dev/null || true)"
if [ -n "$_worktree_id" ] && [ -d "$_worktree_dir" ]; then
    printf '%s\n' "$_worktree_id" > "$HOME/effort-handoff-worktree-id"
    ln -s "$_worktree_dir" "$HOME/demo-worktree"
    if (
        cd "$_worktree_dir"
        agent-worktrees effort-focus bind \
            efforts/active/review-widget/README.md \
            --participant Driver \
            --slice "Phase 2 - Reviewed implementation" \
            --worktree-id "$_worktree_id"
    ); then
        pass "active effort bound to managed worktree"
    else
        jam "repo-config" "effort-focus bind rejected the seeded effort" "inspect the effort and worktree record"
    fi
else
    jam "worktree-lifecycle" "create output did not identify a worktree" "inspect $_create_json"
fi

cr_finalize
