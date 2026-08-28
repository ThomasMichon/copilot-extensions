# context-handoff

Context window monitoring and session handoff for GitHub Copilot CLI.

This plugin ships two cooperating pieces:

| Piece | Type | Role |
|-------|------|------|
| **context-handoff extension** | Copilot CLI session extension (`extension.mjs`) | Monitors `session.usage_info` for exact token counts; applies percentage-based soft/hard thresholds (55% / 70% by default) with optional repository overrides, delivered on the next idle; provides `generate_handoff_prompt`, `save_handoff_prompt`, `consume_handoff`, `continue_handoff`, and `retry_handoff_cutover` tools plus the **`/handoff-continue`** and **`/resume-handoff`** slash commands. `save_handoff_prompt` sits **on top of agent-dispatch**: when a coordinator is reachable **and a linked worktree can be resolved**, it stores the handoff as a `proposed`/`handoff` **task** (payload = metadata plus markdown, pinned to the worktree, no file handoff); otherwise it falls back to a one-time machine-local state file outside the repo checkout, including from an adopted anchor. `retry_handoff_cutover` re-attempts a live cutover from the **already-saved** handoff (no regeneration) — for when a spawned successor window came up empty because its first prompt never submitted, so no session was created. `/resume-handoff` digs up this checkout's pending handoff (task, else file), consumes it once, and **injects its continuation prompt into the current session** |
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

| Threshold | Behavior |
|-----------|----------|
| 55% of window | Soft reminder: hand off at the next clean boundary |
| 70% of window | Urgent reminder: hand off now; compaction remains at ~80% |

An owning repository may override either percentage in
`.context-handoff/config.yaml`:

```yaml
thresholds:
  soft_percent: 65
  hard_percent: 75
```

The extension discovers the nearest Git repository root from the session's
starting directory, so the same config applies from nested directories and Git
worktrees. Values must be integers from 1 through 79, and `soft_percent` must be
lower than `hard_percent`. Invalid config produces a visible warning and uses
the 55% / 70% defaults. If the runtime does not report a window size, the
extension reports utilization as unknown and does not invent an absolute
threshold.

Reminder state resets after a successful compaction.

In both cases the extension emits a user-visible `session.log` warning and an
agent-facing `session.send()` nudge. The nudge is queued by
`session.usage_info` and delivered only at the next `session.idle` boundary, so
it does not interrupt an in-flight turn. Failures to send the nudge are logged
as warnings; they do not block the session.

## Live cutover is successor-consume-driven

A live cutover (`continue_handoff`) spawns a successor Copilot in a new mux
window and passes the exact first prompt through Copilot's native `-i` argv
before the process starts; it never relies on terminal readiness parsing or
`send-keys`. The prompt begins with the task title (for useful successor title
inference), names the intended worktree id and cwd, names the dispatch task, and
gives the exact consume/bind/retire/complete commands. It does **not**
retire the predecessor on the predecessor's next idle.

The successor's first command loads the brief and binds the new session to the
handoff's exact durable token. That atomic bind links the numbered handoff,
records the outgoing session as **`handed-off`**, advances the replayable head
ledger, and then retires the predecessor pane through
`agent-worktrees handoff-cutover --retire-pane`.

This keeps recovery safe: if the successor never comes up or never consumes the
handoff, the predecessor pane remains available and the terminal is not closed.
The stored metadata also records the predecessor's mux session; retirement
verifies that the pane token still belongs to that session, so a token reused
after a mux restart is treated as an already-gone predecessor rather than an
unrelated pane to interrupt.

### Continuity is objective-driven, not phase-driven

The successor seed and `/resume-handoff` prompt treat the handoff as active
responsibility for the original objective. A completed predecessor phase is
history, not evidence that the broader work is done. After loading the brief,
the successor continues every actionable phase already permitted by the
original request without waiting for another user nudge. One session may consume
a handoff, drive many additional slices, and hand off again when its own context
fills.

The handoff template therefore separates the continuing parent objective and
successor work roster from completed progress, and distinguishes the current
handoff task's completion gate from the worktree's true completion gate. If no
actionable work remains and the parent objective is genuinely complete, the
session should finish instead of creating a live successor only to report
closure.

When agent-worktrees reports a valid open `active_effort`, the skill uses a
smaller effort-backed shape: repository-relative effort pointer, participant,
current/next slice, and only the predecessor's immediate blockers, decisions,
in-flight work, and required confirmations. The effort README remains the
single durable request/plan/journal; the baton does not copy it. Repositories
without effort adoption, stale/closed bindings, and objectives with no effort
continue to use the full standalone handoff.

The live `-i` seed reinforces that contract before the successor reads the
baton: an active effort is the source of truth and completion gate, so the
successor selects its next authorized Plan or Validation Plan item and drives
toward `Done` rather than finalizing after a handoff task, phase, or pull request.

**Failed-successor recovery (`retry_handoff_cutover`).** The native `-i`
transport is receipt-checked by the pane wrapper; a rejected flag or immediately
exiting successor is reaped without retiring the predecessor. Because the
handoff is already stored, `retry_handoff_cutover` re-attempts the cutover **from
that saved handoff without regenerating it**: it recovers the worktree's pending
task/file, rebuilds the *identical* cutover seed (via the same `buildCutoverSeed`
used by `save_handoff_prompt`), and spawns a fresh seeded successor. Run it from
the predecessor.

**Extension-load race (self-healing seed).** A live cutover seeds the
successor's **first turn**, which can run before the context-handoff extension
has finished (re)loading on the fresh launch — so `consume_handoff` is in the
tool catalog (tool search finds it) but invoking it fails with a transient
**400 / tool-not-found**. Because the session must exist (a prompt submitted)
before the extension can activate, the extension cannot pre-empt this from
inside. So the **cutover seed itself carries a retry-on-not-ready instruction**:
if the first `consume_handoff` call errors while the extension is still loading,
the successor waits briefly and retries the same call (up to 5 attempts) so the
launch self-heals. The human-facing *paste* prompt (resumed in an
already-loaded session, no race) stays short and omits the clause.

This split follows the repo-wide
[`primitives below, orchestration above`](../../docs/patterns/README.md)
invariant: agent-worktrees provides the lower-level session/worktree mechanisms,
while context-handoff composes the handoff policy above them.

## Standalone and degraded modes

The plugin remains useful in a plain Copilot CLI session with only
`context-handoff@copilot-extensions` enabled:

- `generate_handoff_prompt` and `save_handoff_prompt` work without
  agent-dispatch when agent-worktrees can resolve a worktree state directory.
- If `agent-dispatch` is absent or unhealthy, `save_handoff_prompt` writes a
  one-time file under the machine-local state directory reported by
  `agent-worktrees get worktree-state-dir`. Linked worktrees use their normal
  state namespace; an adopted anchor uses the stable `@anchor` namespace. Both
  are outside the repo checkout.
- File consumption uses an atomic claim. A dead consumer's claim is recoverable,
  and a retry from the same successor session can recover delivery after the
  durable spent mark; another session still receives the one-time stop notice.
- `continue_handoff` is best-effort. A linked worktree targets its `wt-<id>`
  mux; an adopted anchor targets the exact caller-owned mux containing the
  predecessor pane. Without a live mux it does nothing destructive and tells
  the agent to use the saved paste or `/resume-handoff` fallback.
- `/resume-handoff` first tries a pinned agent-dispatch handoff task when that
  stack is available; otherwise it falls back to the newest unconsumed
  worktree-state handoff file for the current CWD.

For a **natural-language** resume request handled by the skill (for example,
"resume from handoff" without an exact id), discovery is deliberately
checkout-local first: resolve `agent-worktrees get worktree-state-dir`, consume
the newest valid unconsumed file under its `handoff/` directory, then inspect
the worktree's local disposition history (when the checkout is a linked
worktree) for an exact task-backed handoff pointer. The same first step works
after restarting in an adopted anchor because its `@anchor` state namespace is
stable. A broader agent-dispatch list is only a fallback and must be filtered
to the current worktree before selecting a task; cross-session history is last.

### The `handoff-cli.mjs` fallback (extension didn't load at all)

Everything above assumes the **extension is loaded** so its tools exist. When it
is **not** — most importantly a **Bare-resumed session**, where the CLI loads no
extensions at all (`extensions list` shows nothing, so there are no
`*_handoff` tools) — the plugin's **payload files are still on disk**, so an
agent can drive the identical store + live-cutover through a standalone Node CLI:

```bash
CH="$HOME/.copilot/installed-plugins/copilot-extensions/context-handoff/extensions/context-handoff/handoff-cli.mjs"
node "$CH" cutover  --title "<topic>" --prompt-file <handoff.md>   # store + live cutover
node "$CH" save     --title "<topic>" --prompt-file <handoff.md>   # store + paste prompt (no cutover)
node "$CH" continue --seed "<HANDOFF_SEED>"                         # trigger a cutover for a seed
node "$CH" consume  --handoff-id <id>                               # load a file handoff + mark consumed
```

The CLI is a thin wrapper over **`handoff-core.mjs`** — the SDK-free store/trigger
core (same on-disk format, same `agent-worktrees handoff-cutover` trigger, same
issue-#853 bash-first seed shape from `cutover-seed.mjs`). A handoff it stores is
therefore byte-compatible with `consume_handoff` / `/resume-handoff`. Session id
defaults to `$COPILOT_AGENT_SESSION_ID`; `--session-id`/`--cwd` override; `--no-task`
forces the file store; `--json` emits a machine-readable result. The agent
composes the handoff markdown itself (the extension's in-memory session facts —
token counts, per-turn edits — are unavailable out-of-band).

> **Module layout.** `cutover-seed.mjs` (pure seed builders) and
> `handoff-core.mjs` (SDK-free store/trigger) are both importable without loading
> the session extension. `extension.mjs` and `handoff-cli.mjs` are the two
> front-ends; the extension is the live-monitor + tool surface, the CLI is the
> extension-free fallback. (`extension.mjs` still carries its own copies of the
> store helpers today; folding it onto `handoff-core.mjs` to retire the
> duplication is a test-guarded follow-up.)
