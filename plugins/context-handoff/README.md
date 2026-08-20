# context-handoff

Context window monitoring and session handoff for GitHub Copilot CLI.

This plugin ships two cooperating pieces:

| Piece | Type | Role |
|-------|------|------|
| **context-handoff extension** | Copilot CLI session extension (`extension.mjs`) | Monitors `session.usage_info` for exact token counts; logs threshold warnings and queues agent-facing `session.send()` nudges at 55% / 70% utilization, delivered on the next idle; provides `generate_handoff_prompt`, `save_handoff_prompt`, `consume_handoff`, `continue_handoff`, and `retry_handoff_cutover` tools plus the **`/handoff-continue`** and **`/resume-handoff`** slash commands. `save_handoff_prompt` sits **on top of agent-dispatch**: when a coordinator is reachable **and this worktree can be resolved**, it stores the handoff as a `proposed`/`handoff` **task** (payload = metadata plus markdown, pinned to the worktree, no file handoff); otherwise it falls back to a one-time worktree-state file outside the repo checkout. `retry_handoff_cutover` re-attempts a live cutover from the **already-saved** handoff (no regeneration) — for when a spawned successor window came up empty because its first prompt never submitted, so no session was created. `/resume-handoff` digs up this worktree's pending handoff (task, else file), consumes it once, and **injects its continuation prompt into the current session** |
| **context-handoff skill** | Skill | The `/handoff` workflow -- composes the continuation prompt from the extension's structured facts and the agent's live context. (Resume is handled by the extension's `/resume-handoff` command, which injects the handoff; the skill documents both) |

## Why an extension (and not a pure plugin)

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

So the capability requires the extension payload.

## How the extension is delivered (no install step)

This is a **plugin-contributed extension**. The Copilot CLI discovers
extensions contributed by **enabled** installed plugins directly from the
plugin's `extensions/` directory (the `plugin` extension source) -- it scans
each `extensions/<name>/` subdir holding an `extension.{mjs,cjs,js}` file. This
plugin ships exactly one:

```
plugins/context-handoff/extensions/context-handoff/extension.mjs
```

There is **no** installed runtime, venv, binstub, copy to
`~/.copilot/extensions/`, deploy manifest, or `scripts/install.*`. Enabling the
plugin is the whole setup; the extension activates on the **next** Copilot CLI
session (extensions are scanned at startup).

## Requirements

Two conditions must hold for the extension to load -- both are handled outside
this plugin:

1. **The plugin must be enabled.** `context-handoff@copilot-extensions: true`
   in `enabledPlugins` (user `~/.copilot/settings.json`, or a repo's
   `.github/copilot/settings.json`). A marketplace plugin's `extensions/` dir is
   only scanned when the plugin is enabled.
2. **`experimental: true`** in `~/.copilot/settings.json` -- the CLI gates *all*
   extension loading behind it. Set it directly if your environment has not
   already done so. This plugin does not set it and does not require registering
   the repo with agent-worktrees.

## Verify

A loaded extension exposes the `generate_handoff_prompt`,
`save_handoff_prompt`, `consume_handoff`, `continue_handoff`, and
`retry_handoff_cutover` tools, plus
`/handoff-continue` and `/resume-handoff`; `/extensions` lists it with source **plugin**. It
intentionally does **not** emit a user-visible "Session started" breadcrumb. If
it does not load, confirm both requirements above and start a fresh session (the
`context-handoff-setup` troubleshooting skill walks through this).

## Thresholds

| Utilization | Behavior |
|-------------|----------|
| 55% | Gentle reminder: "consider generating a handoff soon" |
| 70% | Urgent reminder: "generate NOW, compaction at ~80%" |

Reminder state resets after a successful compaction.

In both cases the extension emits a user-visible `session.log` warning and an
agent-facing `session.send()` nudge. The nudge is queued by
`session.usage_info` and delivered only at the next `session.idle` boundary, so
it does not interrupt an in-flight turn. Failures to send the nudge are logged
as warnings; they do not block the session.

## Live cutover is successor-consume-driven

A live cutover (`continue_handoff`) spawns a seeded successor Copilot in a new
mux window and cuts the operator over. It does **not** retire the predecessor on
the predecessor's next idle. The stored handoff carries the predecessor pane id
and session id; the successor's seeded first action is to call `consume_handoff`.
That consume step loads the brief, marks file-backed handoffs spent, records the
outgoing session as **`handed-off`** via `agent-worktrees conclude-session`, and
retires the predecessor pane through `agent-worktrees handoff-cutover
--retire-pane`.

This keeps recovery safe: if the successor never comes up or never consumes the
handoff, the predecessor pane remains available and the terminal is not closed.

**Empty-successor recovery (`retry_handoff_cutover`).** A subtle failure mode:
the cutover spawns the successor window and *types* the seed, but Copilot only
creates a session (and fires `sessionStart`) once a first prompt is actually
**submitted**. If that submission never lands, the new window holds a live
Copilot at an empty prompt with **no session** — so no changeover is recorded and
the predecessor stays live (closing the empty window drops the operator back onto
it). Because the handoff is already stored, `retry_handoff_cutover` re-attempts
the cutover **from that saved handoff without regenerating it**: it recovers the
worktree's pending task/file, rebuilds the *identical* cutover seed (via the same
`buildCutoverSeed` used by `save_handoff_prompt`), and spawns a fresh seeded
successor. Run it from the predecessor.

This split follows the repo-wide
[`primitives below, orchestration above`](../../docs/patterns/README.md)
invariant: agent-worktrees provides the lower-level session/worktree mechanisms,
while context-handoff composes the handoff policy above them.

## Standalone and degraded modes

The plugin remains useful in a plain Copilot CLI session with only
`context-handoff@copilot-extensions` enabled:

- `generate_handoff_prompt` and `save_handoff_prompt` work without
  agent-dispatch when agent-worktrees can resolve a worktree state directory.
- If `agent-dispatch` is absent, unhealthy, or a worktree cannot be resolved,
  `save_handoff_prompt` writes a one-time file under the worktree state
  directory outside the repo checkout.
- `continue_handoff` is best-effort. Without a resolvable worktree + live mux it
  does nothing destructive and tells the agent to use the saved paste or
  `/resume-handoff` fallback.
- `/resume-handoff` first tries a pinned agent-dispatch handoff task when that
  stack is available; otherwise it falls back to the newest unconsumed
  worktree-state handoff file for the current CWD.
