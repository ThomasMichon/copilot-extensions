# context-handoff

Context window monitoring and session handoff for GitHub Copilot CLI.

This plugin ships four cooperating payload pieces:

| Piece | Type | Role |
|-------|------|------|
| **continuity guidance hook** | Declarative `sessionStart` hook | Injects a concise, owner-marked `additionalContext` kernel that tells objective-owning sessions to work thoroughly across context windows, treat handoff as a relay rather than completion, and move unfinished planning into execution without bypassing required gates |
| **context-handoff extension** | Copilot CLI session extension (`extension.mjs`) | Monitors `session.usage_info` for exact token counts; applies percentage-based soft/hard thresholds (55% / 70% by default) with optional repository overrides, delivered on the next idle; provides `generate_handoff_prompt`, `save_handoff_prompt`, `consume_handoff`, `continue_handoff`, and `retry_handoff_cutover` tools plus **`/handoff-continue`**, **`/consume-handoff`**, and the compatibility **`/resume-handoff`** alias. Storage prefers a worktree-pinned agent-dispatch task and falls back to a one-time worktree-state file. Both front ends use the same SDK-free `handoff-core.mjs` implementation. |
| **context-handoff skill** | Skill | The `/handoff` workflow -- composes the continuation prompt from the extension's structured facts and the agent's live context. (Resume is handled by the extension's `/resume-handoff` command, which injects the handoff; the skill documents both) |
| **payload-local fallback CLI** | Node script (`handoff-cli.mjs`) | Extension-free facts, save/cutover/continue, task/file consume with acknowledgement and takeover, retry, and manual fallback. Invoked by exact verified plugin-root-relative path; it has no PATH binstub or install/runtime step and shares `handoff-core.mjs` with the extension. |

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
the plugin's declarative `sessionStart` hook, following the repository's
context-injection pattern.

The hook intentionally treats plugin enablement as its applicability gate. Its
policy is capability-generic and source-neutral, so it does not inspect
`sessionStart` `cwd` or `source`, assume a mux or worktree is present, or exclude
resumed sessions. Detailed handoff mechanics select the available task, file,
live-cutover, or paste path on demand.

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

A session where the plugin hook loaded receives an owner marker beginning with
`[owner: context-handoff@...]` in `additionalContext`. Without an adopted
aggregate authority, an adjacent agent-worktrees payload also contributes that
payload's exact command in an honestly attributed `adjacent-compatibility`
catalog. Adjacency is a payload-presence check, not an assertion that
agent-worktrees is enabled in the current session; the catalog reports `ready`
only when both its command and installer are present, otherwise `unavailable`.
With the exact compatible `context-injection@copilot-extensions` authority
adopted, this plugin contributes only its compact continuity kernel and
agent-worktrees contributes its own catalog to the deterministic aggregate.
The POSIX compatibility catalog requires a system
`python3` or `python`; without one, the valid continuity kernel still emits by
itself. A standalone context-handoff installation also emits only its own
kernel. A loaded extension exposes the `generate_handoff_prompt`,
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
window and passes the exact first prompt through Copilot's native `-i` argv.
The single-line ASCII seed is at most 1024 characters and has exactly three
parts: a stable task-first title lead, one recommendation to invoke
`/consume-handoff` after startup to acknowledge and take over, and one raw
recovery command that retrieves the stored task or file payload. The full
handoff and lifecycle orchestration are never inlined in the seed.
Both task and file recovery commands re-enter payload-local
`handoff-cli.mjs consume`, so checkpointing, acknowledgement, takeover, and
verified retirement are not bypassed. The command is ASCII-safe: it derives the
canonical `context-handoff@copilot-extensions` payload beneath `os.homedir()` and
verifies manifest provenance at runtime, so a Unicode home/install path remains
lossless without entering the seed.

Copilot creates no successor session until that initial prompt is submitted.
The launch carries the pending handoff token in the successor environment; only
after the prompt creates a real session can agent-worktrees' `sessionStart` hook
associate `(successor session, handoff token, target worktree)`. This startup
association is deliberately **not takeover**: the predecessor remains the
worktree head and stays alive.

Storage records the predecessor through
`agent-worktrees session-binding --session-id <id> --json`, whose existing
session-lock/process-ancestry resolver remains the authority even when
`TMUX_PANE`/`PSMUX_PANE` is absent. Environment pane values are used only when
they can be validated against a live mux.

The successor explicitly acknowledges through `/consume-handoff`. Task-backed
consumption writes a durable checkpoint before the one-time task consume, then
uses one atomic `bind-session --handoff-token` result to link succession and
verify the new head, updates the worktree title, and only then retires the
recorded predecessor. The normal takeover therefore needs one agent turn, one
extension consume tool call, and three ground-layer lifecycle calls
(bind/acknowledge, title, retire). A same-successor retry resumes from the
checkpoint without replaying the task payload. Retirement requires the recorded
mux, PID, and process-creation identity; older metadata without those fields
remains consumable but leaves the predecessor for explicit manual cleanup.
Before mux shutdown, the recorded mux, PID, and creation identity are
revalidated. The mux then receives bounded graceful/hard pane shutdown. If the
Copilot process survives as an orphan, Windows verifies creation time and calls
`TerminateProcess` through the same open process handle; POSIX uses
`pidfd_open` plus `pidfd_send_signal`. A host without the required atomic reap
reports retirement failure and never signals the numeric PID unsafely, although
the pane may already have exited.
Copilot descendant creation tokens come from the original process-table
snapshot used to discover that child tree. Retirement never learns a child's
expected identity from a later PID lookup, which could mistake PID reuse for
the originally observed descendant.
When task consumption uses `defer_complete`, canonical `/consume-handoff` keeps
the task owned and injects the exact `agent-dispatch complete <id>` command.
Neither successful prompt injection nor takeover completes the task; the
successor runs that command only after the handoff objective's completion gate.
If delivery fails or the successor crashes, the owned task and checkpoint remain
the retry authority.
If the one-time task consume may have completed before its checkpoint update,
retry proceeds only when structured `agent-dispatch show` state proves the task
is started/suspended/completed, owned by the expected worktree owner, and carries
this exact successor `owner_session_id`. Free-form errors, coordinator outages,
and timeouts never imply consumption and leave the predecessor alive.

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

An effort or knowledge repository is therefore optional enrichment, not a
handoff dependency. Handoff storage remains the pinned dispatch task or
machine-local worktree-state file described below.

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

If `/consume-handoff` is not yet available during extension startup, the seed's
raw recovery command can retrieve the same stored payload. It is deliberately a
single command rather than a long shell orchestration chain.

This split follows the repo-wide
[`primitives below, orchestration above`](../../docs/patterns/README.md)
invariant: agent-worktrees provides the lower-level session/worktree mechanisms,
while context-handoff composes the handoff policy above them.

### Efficiency and fidelity validation

`tools/clean-room/scenarios/context-handoff-cutover` is the deterministic Tier-P
gate. It records initial seed characters and estimated tokens, asserts the
one-prompt/one-turn/one-consume-tool budget, and hashes an encoded/decoded payload
to prove byte fidelity.

`tools/clean-room/scenarios/context-handoff-eval` is the opt-in, identity-free
Tier-E witness. It launches one fresh successor with the real compact seed and
emits `context-handoff-eval-metrics.json`: seed size/token estimate, turns and
exact submitted-prompt/turn/consume-tool counts derived from agent-bridge's
structured turn detail, exact `turn.prompt` equality with the runner composite
plus exact seed containment, full byte-for-byte
payload presence in the structured consume result, time to takeover and
retire-or-preserve decision, payload hashes/canary visibility, startup-candidate
acknowledgement, head verification, and safe predecessor handling. Run it only
with the ordinary clean-room eval flow; its live Copilot target and credits are
never defaults or identities committed to the scenario.
Each drive clears prior transcript and structured-result/turn artifacts in both
the root eval directory and reused `run-*` directories before capture. Failed
capture therefore advertises no structured evidence, and scenario scoring fails
closed instead of accepting stale data from an earlier run.

```powershell
tools\clean-room\run.ps1 -Scenario context-handoff-eval -Mode eval `
  -HarnessMount <source-checkout>
```

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

### Deferred non-mux launcher contract (#1632)

This delivery does not spawn a new terminal when no mux is available. A future
launcher must preserve this contract:

1. Persist the task/file, note-handoff pointer, and predecessor PID plus process
   creation identity before launch.
2. Record the expected predecessor command shape and verify both command and
   creation identity before any termination; a PID match alone is insufficient.
   On Windows the creation token is the process creation `FILETIME` and command
   verification uses the OS process command line. On POSIX the creation token is
   `/proc/<pid>/stat` start time and command verification uses the executable/
   argv identity available from the process table.
3. Transport the successor prompt through a prompt file (or an equivalently
   lossless file-backed channel), never a lossy inline terminal command.
4. Let the successor consume and establish bind/succession/head/title state
   before it requests verified predecessor retirement.

Until #1632 implements that launcher, no-mux mode returns a clearly delimited
copyable seed while retaining all durable recovery records.

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
agent can drive the identical flow through a standalone Node CLI. Prefer the
exact plugin root supplied to hooks; otherwise locate an installed payload and
verify its `plugin.json` name before deriving the CLI path:

```bash
CH_ROOT="${COPILOT_PLUGIN_ROOT:-}"
if [ ! -f "$CH_ROOT/plugin.json" ]; then
  CH_ROOT="$HOME/.copilot/installed-plugins/copilot-extensions/context-handoff"
  provenance="$(node -e 'const fs=require("fs"),m=JSON.parse(fs.readFileSync(process.argv[1],"utf8")); console.log(`${m.name||""}|${m.repository||""}`)' "$CH_ROOT/plugin.json" 2>/dev/null)"
  [ "$provenance" = "context-handoff|https://github.com/ThomasMichon/copilot-extensions" ] || exit 1
fi
CH="$CH_ROOT/extensions/context-handoff/handoff-cli.mjs"
[ -f "$CH" ] || exit 1

node "$CH" facts --json
node "$CH" cutover  --title "<topic>" --prompt-file "<handoff.md>"
node "$CH" save     --title "<topic>" --prompt-file "<handoff.md>"
node "$CH" continue --seed "<HANDOFF_SEED>" --handoff-token "<HANDOFF_TOKEN>"
node "$CH" consume  --task-id "<task-id>" --defer-complete
node "$CH" consume  --handoff-id "<handoff-id>"
node "$CH" retry
```

The CLI is a thin wrapper over **`handoff-core.mjs`**. `facts` emits the
session/worktree/Git facts available without SDK state; the agent composes the
same effort-backed or standalone markdown. Store, seed, cutover, task/file
consume, acknowledgement/head takeover, verified retirement, retry, and manual
fallback all use the same core as the extension. Session id defaults to
`$COPILOT_AGENT_SESSION_ID`; `--session-id`/`--cwd` override; `--no-task` forces
the file store; `--json` emits machine-readable results.

This remains a payload-only plugin: the fallback is invoked as
`node <verified-plugin-root>/extensions/context-handoff/handoff-cli.mjs`. It
does not add a PATH command, installer, venv, service, or runtime deployment.
Shared-core calls to `agent-worktrees` and `agent-dispatch` resolve only the
provenance-checked sibling payload in the same marketplace installation cell;
they never use ambient `PATH` or a project-dispatch wrapper to select that
payload or its Python runtime. The sibling payload resolver locates or first-use
provisions its authoritative versioned runtime, then the core invokes that
Python directly with exact argv in isolated UTF-8 mode (`-I -X utf8`), with
inherited `PYTHONHOME` and `PYTHONPATH` removed. Prompt/title/payload text
therefore never becomes batch source or crosses a PowerShell-to-native argument
re-serialization boundary. Provisioning has its own installation-sized timeout
instead of inheriting the shorter health/query timeout.

> **Module layout.** `cutover-seed.mjs` owns the pure bounded seed contract and
> `handoff-core.mjs` owns storage, metadata, consumption, checkpoints, and
> cutover. `extension.mjs` injects SDK logging into that core; `handoff-cli.mjs`
> is the extension-free front end.

The reusable lifecycle and invocation invariants are documented in
[`docs/patterns/context-handoff-lifecycle.md`](../../docs/patterns/context-handoff-lifecycle.md).
