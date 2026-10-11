---
applyTo: "**"
---
<!-- copilot-extension-instruction-projection {"applyTo":"**","bodySha256":"163d7e8f69227eb940bdfc06b9328402bc4cbbe71e282d8bdb5ac332ba964496","customizationKind":"instructions","deliveryKind":"body","destination":".github/instructions/agent-dispatch/blocked-task-fallback.instructions.md","plugin":"agent-dispatch@copilot-extensions","pluginVersion":"0.16.2-dev1","renderedBytes":2742,"schema":"copilot-extensions.instruction-projection","sourceId":"blocked-task-fallback","template":"instructions/blocked-task-fallback.instructions.md","templateBytes":1958,"templateSha256":"f8521aeb78c58bf04ed5e6dd755dc8a0fcd2c0899e29698091e7e2ceb849cfed","version":1} -->


# Agent Dispatch -- a live task is structurally blocked

This file is reflected directly into the consuming repo's own source tree, so
it loads on every session regardless of whether `agent-dispatch` registered
correctly this session.

## If a dispatched task is live but not actually making progress

**Inspect first.** Don't guess at a task's state -- read it:

```bash
agent-dispatch show <task-id>
agent-dispatch card show <task-id>   # its glanceable brief + steer inbox
agent-dispatch events <task-id>      # the full audit trail
```

`card show` tells you whether the task is genuinely `awaiting_steer` (it
posted a form via `--request-input` and is waiting on an operator answer --
answer it with `agent-dispatch steer submit <task-id> --field k=v` or take it
as the worker with `steer take`) versus merely dormant.

**Then act, matching the actual state:**

- **Suspended, parked under its owner** -- `agent-dispatch release <task-id>`
  returns it to `queued` for a replacement embodiment to pick up (never
  hand-edit its row to force this).
- **Held but stuck with no card** -- `agent-dispatch yield <task-id> --note
  "<why>"` returns it to `queued` with a note; add `--exclude-self worktree`
  (or `machine`) so the same candidate isn't immediately re-offered it if the
  block was environmental.
- **Genuinely done, or a duplicate** -- `agent-dispatch abandon <task-id>
  --permit --reason "<why>"` (or `--duplicate-of <ref>`) terminally retires
  it; never silently drop a task by ignoring it.
- **Waiting on an external event** (a PR, a build, a review) -- this is a
  normal **suspend**, not a blockage; see `agent-dispatch recipes describe
  conflict-resolution` for the SUSPEND/RESUME contract most producer-opened
  PR work already uses.

Never hand-clear another loop's/agent's reservation or exclusive key to
"unstick" a task you don't own -- steer, release, or abandon it explicitly
through the commands above.

<!-- copilot-guidance-body-end:v1 {"bindingSha256":"e24cb773bb265d0b284ef0b044343133ba2bca817ca8d25081691a44fcafe43b"} -->
