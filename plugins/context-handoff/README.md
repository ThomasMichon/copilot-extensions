# context-handoff

Context window monitoring and session handoff for GitHub Copilot CLI.

This plugin tracks context pressure, stores durable handoff briefs, and
preserves native `/goal` execution across an explicitly requested live handoff.
`continue_handoff` delegates process launch to Herdr or agent-worktrees and
admits the successor before allowing identity-checked predecessor retirement.
The legacy `trigger_handoff` signal-only route remains available for older
text-only records.

This plugin ships four cooperating payload pieces:

| Piece | Type | Role |
|-------|------|------|
| **continuity guidance hook** | Declarative `sessionStart` hook | Writes the full owner-marked continuity contract to the exact session folder and emits only `{}` |
| **context-handoff extension** | Copilot CLI session extension (`extension.mjs`) | Monitors `session.usage_info` for exact token counts; applies percentage-based soft/hard thresholds (55% / 70% by default) with optional repository overrides, delivered on the next idle; provides `generate_handoff_prompt`, `save_handoff_prompt`, `consume_handoff`, and `trigger_handoff` tools plus **`/handoff-continue`**, **`/consume-handoff`**, and the compatibility **`/resume-handoff`** alias |
| **context-handoff skill** | Skill | Owns the `/handoff` workflow: compose the continuation prompt from the extension's structured facts and the agent's live context, decide when to store it, and decide whether to ask or trigger |
| **payload-local fallback CLI** | Node script (`handoff-cli.mjs`) | Extension-free facts, save, trigger, and task/file consume. Invoked by exact verified plugin-root-relative path; it has no PATH binstub or install/runtime step and shares `handoff-core.mjs` with the extension |

## Native goal continuity

Requires Node.js and Copilot CLI **1.0.84-3** (the tested native API version).
The extension uses the public SDK plus the published
`session.autopilotObjective.getState` RPC, never patched CLI internals.

1. Save the brief and call `continue_handoff` with its exact `HANDOFF_SEED`.
   `/handoff-continue` requests both operations. Save alone does not launch.
2. The source stops automatic execution, finishes the current turn, and freezes
   final native usage at `session.idle`.
3. A fixed, named successor starts **without `-i` or a model admission turn**.
   Public workspace APIs carry the native-generated opaque objective snapshot;
   normal exit and cold resume hydrate the native objective registry.
4. The brief becomes `context-handoff.md` in the successor's session workspace.
   Shared task/file consumption, model/agent/permission checks, and mux
   bind/link/head acknowledgement precede activation.
5. A running objective gets at most one business continuation. A queued send ID
   alone is not admission: its exact public `user.message` must be observed
   before predecessor retirement. Herdr retirement verifies the recorded
   pane, terminal, and session identity.

| Source intent | Successor behavior |
|---|---|
| Running, finite remaining budget | Resume with exactly `max(0, cap - exact native usage)` remaining |
| Running, unlimited | Remain unlimited; an internal native ID may change |
| Paused or exhausted | Remain stopped; no automatic business message |
| Completed | Remain completed; never reopen automatically |
| No native goal | Preserve the profile and brief without creating a goal or sending a business message |

Credit limits are native **soft caps**, not hard billing limits. Preserve
native decimal/nano-AIU accounting; zero never means unlimited. Stopped goals
may retain the original positive cap and spent amount, including overshoot.
Running goals display the real native GoalPanel. Native interactive/paused/
completed modes intentionally hide it; do not add credits or enable autopilot
just to display a panel.

The runtime preserves the source model, reasoning effort, context tier, agent,
`COPILOT_HOME`, and permission mode. Native handoffs on both Herdr and mux
currently support only `allow-all`; manual/assisted sources are rejected
**before pausing the source or creating a pane**, not silently widened.
The direct native mux CLI and prepare/resume launcher enforce the same limit.
Ordinary non-native handoff behavior is unchanged.
First use in an empty profile can require native extension
trust confirmation for the plugin's existing capabilities.

Install context-handoff, agent-worktrees, and agent-dispatch as sibling plugin
payloads in the same installation root. The shared core resolves its runtime
peers relative to that root; installing only context-handoff in a different
root from its peers does not provide a working mux/task fallback. Use the
official plugin manager for all three payloads, not installed-cache copies.

Linux Herdr/file-backed two-CLI handoffs, repeat handoffs, and native retry/
conflict boundaries have real-runtime coverage. Task/mux ownership and psmux
spaced-argument transport have regression fixtures; this is not a claim of
real Windows acceptance.

### Recovery

- Retain the source checkpoint and fixed successor identity on failure.
  `retry_handoff_cutover` reuses the existing saved request; do not save a new
  baton while an existing launch is unresolved.
- Interrupted first trust resumes the already-created empty receiver UUID.
  A prepared receiver resumes instead of provisioning a second session.
- Receiver preparation waits for the source's host-launch receipt before
  writing its own checkpoint. Startup/trust can finish while this event-driven
  wait is pending. An unknown host launch requires inspection of that receiver,
  not another spawn.
- A nonzero mux CLI exit can still carry a retained `new_pane` receipt
  (for example, exit 4 while session association is pending). Publish that
  receipt and reuse the receiver; retry must not create another pane.
  Structured pre-spawn rejections (exit 1/2/3, including no live mux) clear the
  launch request so an explicit retry is possible. A mux timeout, missing
  receipt, or malformed response leaves the request unresolved and the source
  preserved; a missing pane ID alone is not permission to respawn.
- A known queued send waits for its exact native event without resending.
  A lost acknowledgement reconciles the unique continuation from public events;
  an unknown outcome with no matching event stops and preserves the source.
- Only an owned activation with an unchanged event watermark and no business
  submission can be reactivated after an unlimited cold resume. Its prior
  internal ID is recorded. Identical goal text alone never proves ownership.
  A user's replacement objective is a conflict, not permission to overwrite it.
- Ordinary admitted deliveries never rebuild the goal or replay the message.
  The CLI fallback shares the restoration gate and cannot bypass hydration.

## Why the monitor is an extension

The live monitor is **only** possible as a session extension. The Copilot CLI
hook surface a plugin normally uses cannot replicate it:

- **No hook input carries token counts.** `session.usage_info` (current /
  limit tokens) is delivered only to the extension SDK via
  `session.on("session.usage_info", ...)`. No `sessionStart` / `postToolUse`
  hook input exposes it.
- **Command hooks cannot inject a turn.** The extension's nudge works by
  queueing a `session.send()` message from the `session.usage_info` handler and
  delivering it on the next `session.idle` boundary. Command-hook output is
  discarded (only `preToolUse` can *deny* a tool call, not inject a message).

So token monitoring and idle-boundary nudges require the extension payload.
The ambient continuity contract does not: it is delivered independently through
the plugin's static instruction pointer plus a declarative `sessionStart` file
writer.

## How the extension is delivered

This is a **plugin-contributed extension**. The Copilot CLI discovers
extensions contributed by **enabled** installed plugins directly from the
plugin's `extensions/` directory. This plugin ships exactly one:

```text
plugins/context-handoff/extensions/context-handoff/extension.mjs
```

There is **no** installed runtime, venv, binstub, copy to
`~/.copilot/extensions/`, deploy manifest, or `scripts/install.*`. Enabling the
plugin is the whole setup; the extension activates on the **next** Copilot CLI
session.

## Verify

A session where the plugin hooks loaded receives the full owner-marked
continuity contract in
`instructions/context-handoff/session-guidance.instructions.md` beneath its
exact session folder. The checked-in static pointer instructs the agent to read
that file if present. The hook itself emits only `{}`.

A loaded extension exposes `generate_handoff_prompt`, `save_handoff_prompt`,
`consume_handoff`, `continue_handoff`, `retry_handoff_cutover`, and
`trigger_handoff`, plus `/handoff-continue` and
`/resume-handoff`; `/extensions` lists it with source **plugin**. It
intentionally does **not** emit a user-visible "Session started" breadcrumb.

## The intended workflow

### 1. Compose and store early

When the session reaches a natural stopping point, or when the monitor nudges
you because context pressure is rising:

1. call `generate_handoff_prompt`,
2. compose the full markdown brief,
3. call `save_handoff_prompt`.

That is the routine, safe, non-committal step. It preserves the baton before
context gets tighter, but it does **not** arm pickup or request that any
external system create a successor.

### 2. Context-pressure-driven handoff: trigger directly

If the reason for the handoff is **context pressure** and the objective still
has more work left to do, the agent should call `trigger_handoff` directly
after saving the baton. This path does **not** ask for confirmation first.

### 3. Turn-end follow-ups ask before triggering

If the requested work is done and the agent would otherwise end the turn by
listing follow-up ideas or questions, the flow is different:

- **compose + save** the baton,
- **ask the user** whether to continue via handoff,
- only after a brief yes (for example, "sure") call `trigger_handoff`.

Only this turn-end follow-up path is skipped by autopilot mode or prior user
pre-authorization.

### 3. Let one session own one slice

When a repository uses both **efforts** and **handoffs**, the combination is
meant to keep sessions well scoped. A single session should not "bite off" an
entire long-running effort just because the overall objective is still active.
Instead, one session advances one natural slice confidently, reaches a clean
boundary, writes the relay delta, and hands the next slice to the successor.
The effort remains the durable source of truth; the handoff carries only the
immediate baton.

## `trigger_handoff`: legacy signal-only contract

New extension saves include a native-presence checkpoint (including explicit
absence). `trigger_handoff` directs these records to `continue_handoff` so that
a text pickup cannot bypass native restoration. For older text-only records,
the signal-only contract below applies.

- For **context-pressure-driven** handoffs with remaining work, call it
  immediately after `save_handoff_prompt`.
- For **turn-end / follow-up** handoffs, call it only after the user says yes,
  unless autopilot or prior pre-authorization applies.

Its contract is:

1. drop the composed handoff markdown in the current session's session-state
   folder,
2. refresh worktree-visible pending-handoff state when `agent-worktrees` is
   available,
3. reuse the existing `agent-dispatch` task-backed storage path when available,
4. best-effort ping `agent-bridge` if present,
5. wait up to 30 seconds for pickup,
6. check whether the session-state marker was consumed, the worktree recorded a
   successor, or the dispatch task moved out of `proposed` / `queued`,
7. if nothing picked it up, print manual continuation instructions,
8. always end by printing the final short handoff prompt/seed.

That final seed is the "if your download doesn't start, click here" fallback:
it gives a human or control system enough to continue even if none of the
signaling paths responded during the grace window.

## Storage

`save_handoff_prompt` and `trigger_handoff` use the same durable store
selection:

| Coordinator availability | Storage |
|---|---|
| Active Herdr pane | checkout-scoped one-time file, independent of agent-worktrees |
| `agent-dispatch` reachable | proposed, handoff-labeled task pinned to the current worktree |
| no `agent-dispatch` | one-time JSON file under the machine-local worktree state directory |

In both cases the plugin also returns a bounded one-line seed. The seed is a
locator, not the handoff itself: it contains a task lead, a recommendation to
use `/consume-handoff`, and one opaque `task:<id>` or `file:<id>` recovery
locator.

## Resuming

A saved handoff is **never** auto-loaded merely by opening another session.
An explicitly launched native successor deterministically consumes its assigned
brief during bootstrap, without an initial model prompt.

- `/consume-handoff` is the canonical slash command. It prefers a pending
  worktree-pinned agent-dispatch handoff task and otherwise falls back to the
  newest matching unconsumed worktree-state handoff file.
- `/resume-handoff` is a compatibility alias for `/consume-handoff`.
- If the extension is unavailable, use the payload-local CLI and pass the
  recovery locator to `consume --locator`.

For a natural-language "resume from handoff" request, sweep the current
worktree's own state first rather than doing a global search.

### Already-claimed handoffs

A consume attempt that fails because the handoff was already consumed (or is
currently being consumed elsewhere) always reports the claimant's session id,
via `result.claimedBySession` and inline in the message text -- for both the
file-backed and agent-dispatch task-backed stores. When this happens, state
the claimant session id to the user and offer to file a bug (do not file one
automatically): repeated or racing consumption of the same handoff is
typically a sign of a real defect upstream, not routine behavior.

## Payload-local CLI fallback

When the extension does not resolve or fails to load, the plugin's payload
files are still on disk. Resolve the verified `handoff-cli.mjs` relative to the
installed plugin root and invoke it with `node`.

```bash
CH_ROOT="${COPILOT_PLUGIN_ROOT:-}"
if [ ! -f "$CH_ROOT/plugin.json" ]; then
  CH_ROOT="$HOME/.copilot/installed-plugins/copilot-extensions/context-handoff"
  provenance="$(node -e 'const fs=require("fs"),m=JSON.parse(fs.readFileSync(process.argv[1],"utf8")); console.log(`${m.name||""}|${m.repository||""}`)' "$CH_ROOT/plugin.json" 2>/dev/null)"
  [ "$provenance" = "context-handoff|https://github.com/ThomasMichon/copilot-extensions" ] ||
    { echo "canonical context-handoff@copilot-extensions payload not found" >&2; exit 1; }
fi
CH="$CH_ROOT/extensions/context-handoff/handoff-cli.mjs"
[ -f "$CH" ] || { echo "context-handoff payload-local CLI not found" >&2; exit 1; }

node "$CH" facts --json --session-id "$COPILOT_AGENT_SESSION_ID" --cwd "$PWD"
node "$CH" save --title "<topic>" --prompt-file "<handoff.md>" \
  --session-id "$COPILOT_AGENT_SESSION_ID" --cwd "$PWD"
node "$CH" trigger --title "<topic>" --prompt-file "<handoff.md>" \
  --session-id "$COPILOT_AGENT_SESSION_ID" --cwd "$PWD"
node "$CH" trigger --handoff-token "<HANDOFF_TOKEN>" \
  --session-id "$COPILOT_AGENT_SESSION_ID" --cwd "$PWD"
node "$CH" consume --locator "task:<task-id>" \
  --session-id "$COPILOT_AGENT_SESSION_ID" --cwd "$PWD"
node "$CH" consume --locator "file:<handoff-id>" \
  --session-id "$COPILOT_AGENT_SESSION_ID" --cwd "$PWD"
```

PowerShell uses the same verified, plugin-folder-relative invocation:

```powershell
$chRoot = $env:COPILOT_PLUGIN_ROOT
if (-not (Test-Path -LiteralPath "$chRoot\plugin.json")) {
  $chRoot = Join-Path $HOME '.copilot\installed-plugins\copilot-extensions\context-handoff'
  try { $manifest = Get-Content -Raw "$chRoot\plugin.json" | ConvertFrom-Json } catch { $manifest = $null }
  if ($manifest.name -ne 'context-handoff' -or
      $manifest.repository -ne 'https://github.com/ThomasMichon/copilot-extensions') {
    throw 'canonical context-handoff@copilot-extensions payload not found'
  }
}
$ch = Join-Path $chRoot 'extensions\context-handoff\handoff-cli.mjs'
if (-not (Test-Path -LiteralPath $ch -PathType Leaf)) { throw 'context-handoff payload-local CLI not found' }
node $ch facts --json --session-id $env:COPILOT_AGENT_SESSION_ID --cwd $PWD
node $ch save --title '<topic>' --prompt-file '<handoff.md>' --session-id $env:COPILOT_AGENT_SESSION_ID --cwd $PWD
node $ch trigger --title '<topic>' --prompt-file '<handoff.md>' --session-id $env:COPILOT_AGENT_SESSION_ID --cwd $PWD
node $ch trigger --handoff-token '<HANDOFF_TOKEN>' --session-id $env:COPILOT_AGENT_SESSION_ID --cwd $PWD
node $ch consume --locator 'task:<task-id>' --session-id $env:COPILOT_AGENT_SESSION_ID --cwd $PWD
node $ch consume --locator 'file:<handoff-id>' --session-id $env:COPILOT_AGENT_SESSION_ID --cwd $PWD
```

## Thresholds

| Threshold | Behavior |
|-----------|----------|
| 55% of window | Soft reminder: compose/store a baton at the next clean boundary and trigger directly if work still remains |
| 70% of window | Urgent reminder: preserve the baton now and trigger directly; compaction remains at ~80% |

An owning repository may override either percentage in
`.context-handoff/config.yaml`:

```yaml
thresholds:
  soft_percent: 65
  hard_percent: 75
```

Invalid config produces a visible warning and uses the 55% / 70% defaults. If
the runtime does not report a window size, the extension reports utilization as
unknown and does not invent an absolute threshold.
