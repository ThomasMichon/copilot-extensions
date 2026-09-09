# context-handoff

Context window monitoring and session handoff for GitHub Copilot CLI.

This plugin is intentionally **process-manager agnostic**. It tracks context
pressure, teaches the rules of engagement for continuation, stores durable
handoff batons, and can **signal that a handoff pickup is requested**. It does
**not** spawn successor sessions, inspect mux state, retire panes, or perform
any other cutover choreography itself. Those actions belong to external control
planes such as a worktree manager, agent-bridge, or a human operator.

This plugin ships four cooperating payload pieces:

| Piece | Type | Role |
|-------|------|------|
| **continuity guidance hook** | Declarative `sessionStart` hook | Writes the full owner-marked continuity contract to the exact session folder and emits only `{}` |
| **context-handoff extension** | Copilot CLI session extension (`extension.mjs`) | Monitors `session.usage_info` for exact token counts; applies percentage-based soft/hard thresholds (55% / 70% by default) with optional repository overrides, delivered on the next idle; provides `generate_handoff_prompt`, `save_handoff_prompt`, `consume_handoff`, and `trigger_handoff` tools plus **`/handoff-continue`**, **`/consume-handoff`**, and the compatibility **`/resume-handoff`** alias |
| **context-handoff skill** | Skill | Owns the `/handoff` workflow: compose the continuation prompt from the extension's structured facts and the agent's live context, decide when to store it, and decide whether to ask or trigger |
| **payload-local fallback CLI** | Node script (`handoff-cli.mjs`) | Extension-free facts, save, trigger, and task/file consume. Invoked by exact verified plugin-root-relative path; it has no PATH binstub or install/runtime step and shares `handoff-core.mjs` with the extension |

## The boundary

`context-handoff` owns **continuity policy and baton storage**:

1. detect context pressure,
2. help the agent compose the right brief,
3. store that brief durably,
4. expose a short recovery seed,
5. signal that pickup is requested,
6. report whether anything seems to have picked the request up.

It does **not** own:

- mux / tmux / psmux / Herdr orchestration,
- successor process creation,
- pane retirement,
- PID verification,
- in-process cutover choreography.

If a control plane is present, it can watch the pending handoff state this
plugin leaves behind and perform the actual cutover. If not, the plugin still
returns a short handoff seed a human can use manually.

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
`consume_handoff`, and `trigger_handoff`, plus `/handoff-continue` and
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

## `trigger_handoff`: the signal-only contract

`trigger_handoff` is the plugin's one "arm the continuation" tool. It never
performs process management.

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
| `agent-dispatch` reachable | proposed, handoff-labeled task pinned to the current worktree |
| no `agent-dispatch` | one-time JSON file under the machine-local worktree state directory |

In both cases the plugin also returns a bounded one-line seed. The seed is a
locator, not the handoff itself: it contains a task lead, a recommendation to
use `/consume-handoff`, and one opaque `task:<id>` or `file:<id>` recovery
locator.

## Resuming

A handoff is **never** auto-loaded.

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
