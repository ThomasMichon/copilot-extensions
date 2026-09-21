---
name: context-handoff
description: >
  Context handoff — generate continuation prompts for seamless session
  transitions, store them safely, and resume from handoffs left by prior
  sessions. Use this skill when preparing to hand off work to a new session or
  when resuming from a prior session's handoff. Trigger phrases include:
  - 'handoff'
  - '/handoff'
  - '/handoff-continue'
  - '/consume-handoff'
  - '/resume-handoff'
  - 'resume handoff'
  - 'resume from handoff'
  - 'resume from a handoff'
  - 'consume handoff'
  - 'continuation prompt'
  - 'hand off and continue'
  - 'next session'
  - 'context is getting large'
  - 'pick up where we left off'
  - 'pick up from last session'
  - 'resume from last session'
  - 'generate a handoff'
  - 'session transition'
---

# Context Handoff

Generate structured continuation prompts so a new Copilot CLI session can
resume work from the current one **without treating the current context window
as the boundary of the objective**.

## The core rule

Context-handoff is **process-manager agnostic**.

- It tracks context pressure.
- It teaches the continuation rules.
- It stores the baton.
- It can signal that a pickup is requested.

It does **not** spawn a successor, inspect mux state, retire panes, or perform
cutover choreography. If a worktree manager, agent-bridge, or another control
system wants to act on the pending handoff, it can. Otherwise a human can use
the short seed manually.

For a worktree-level cutover mismatch between the head-session ledger and a
control plane's current sweep target, use the dedicated
`diagnosing-handoff-cutover` skill.

## Every session knows this exists

This is not a mechanism a session opts into only once it feels context
pressure or the user says a trigger phrase. Every session receives the
static, hookless session-start guidance
(`instructions/context-handoff/session-guidance.instructions.md`, written by
this plugin's `sessionStart` hook -- see `scripts/emit-guidance.*`), which
states plainly -- whether or not it began
from a handoff -- that the mechanism exists and that context pressure is
never a reason to rush, truncate diligence, or leave work unfinished. Treat
that awareness as standing permission to work as thoroughly as a task
deserves: you can always hand off instead of cutting corners.

## Continuity contract

A handoff transfers **active responsibility for the original objective**. It is
not a recap and it is not proof that the predecessor's latest phase completed
the work.

- Re-read the **Original Request**, **Continuing Objective**, ordered
  **Successor Work Roster**, and any cited effort or issue.
- Keep driving every actionable next phase the original request already allows.
  Do not wait for another user prompt merely because one phase, PR, or checklist
  slice finished.
- Consuming the handoff is setup, not completion. Begin substantive work after
  pickup. If the inherited plan is incomplete, finish the planning needed to
  act and then execute it, subject to any required safety, review, approval, or
  confirmation gate.
- If context pressure returns before the parent objective is done, hand off
  again with the same parent objective and the newly remaining roster.
- A handoff with no actionable successor work is usually malformed. If the
  original objective is genuinely complete, finish instead of creating a baton
  merely to announce closure.

## When to generate

- The extension nudges you when exact context utilization crosses the configured
  thresholds.
- The user explicitly asks for a handoff or continuation prompt.
- You reach a natural stopping point and want to preserve a baton before the
  conversation gets tighter. This is a reason to *hand off*, never a reason to
  simply end the turn with outstanding work unhandled -- see **Ending a turn
  with outstanding work** below.

## Ending a turn with outstanding work

If the assigned effort or task still has outstanding work, the final action
before ending a turn is always to save and trigger a handoff -- never to stop
and leave the remaining work implicit. "This is a suitable stopping point,"
"this session has run long," and "it's getting late" are not, by themselves,
reasons to end a turn short of that. Legitimate reasons to stop short of
driving further are a genuine crossroads (a design decision only the operator
can make), an error that needs diagnosis before continuing, a design
contradiction, or a step that requires confirmation before a potentially
destructive action. Even then, the correct close is still to save and trigger
a handoff naming the blocker -- not a silent stop.

## Two triggers, two gates

### 1. Context-pressure-driven handoff: trigger directly

When context pressure is the reason for handing off and the objective still has
more work left to do:

1. **Call `generate_handoff_prompt`.**
2. **Compose the markdown brief** using the effort-backed shape when a valid
   open active effort exists, otherwise the full standalone shape.
3. **Call `save_handoff_prompt`.** This safely stores the baton and returns the
   short handoff seed.
4. **Sync the worktree** -- see "Sync before triggering" below.
5. **Call `trigger_handoff` immediately.**

Do **not** ask the user for confirmation first on this path. Running low on
context while work remains is sufficient justification by itself.

### 2. Turn-end / follow-ups handoff: compose, save, ask

When you have completed the requested work and would otherwise end the turn by
listing a set of follow-up ideas or questions:

1. **Call `generate_handoff_prompt`.**
2. **Compose the markdown brief.**
3. **Call `save_handoff_prompt`.**
4. **Replace the usual follow-up list** with one short, low-friction offer to
   continue via handoff.
5. **Sync the worktree** -- see "Sync before triggering" below.
6. **Call `trigger_handoff` only after the user says yes.**

Only this turn-end follow-up path is skippable via **autopilot** or prior
explicit pre-authorization.

## Sync before triggering: give the successor the latest code

The successor inherits the **same on-disk worktree** the predecessor is
sitting in -- not a fresh checkout. If that worktree's branch is behind the
repo's default branch, the successor starts on stale plugin code and stale
instructions, including any bugs already fixed upstream since this session
began (a live example: a plugin-load reliability fix that shipped mid-session
would only reach the successor if the worktree's tip actually contains it).
A predecessor that hands off without syncing silently hands the same bug to
its own successor.

Before calling `trigger_handoff` (the final step of either flow above), when
the worktree is a git checkout with a remote default branch:

1. Commit any uncommitted local changes first (worktree-local WIP commits are
   normal and expected here -- this is not "finish the work," just "don't
   leave it uncommitted going into a rebase").
2. Sync onto the latest default branch -- prefer `agent-worktrees git sync`
   (fetch + rebase, conflict-safe: aborts and leaves the branch unchanged on
   a real conflict) when that tool is available; otherwise `git fetch` +
   `git rebase origin/<default-branch>` directly.
3. If the sync hits a real conflict, do **not** block the handoff on
   resolving it there -- note the conflict and the branch's un-synced state
   plainly in the handoff brief instead, so the successor knows to resolve it
   as its first action rather than silently inheriting stale code without
   realizing it.

This is a lightweight, mechanical step, not a reason to delay a
context-pressure-driven handoff that needs to trigger immediately -- skip
straight to noting the un-synced state in the brief if there is any doubt
about whether it is safe to rebase right now (e.g. genuinely conflicting
in-flight work you cannot lose).

## Efforts + handoffs

When both capabilities are present, use them to let one session own one slice
of a larger effort:

- the **effort** remains the durable source of truth and completion gate,
- the **handoff** carries only the immediate relay delta,
- one session should stride forward confidently, reach a clean boundary, and
  hand the next slice forward rather than trying to finish the entire effort in
  one context window.

For an effort-backed baton, link the repository-relative effort README and avoid
duplicating its request, plan, or journal. Carry only the next slice, immediate
blockers, decisions, in-flight work, and required confirmations.

## `trigger_handoff`

`trigger_handoff` is the explicit "arm pickup" step.

- For a **context-pressure-driven** handoff with work still left to do, call it
  immediately after `save_handoff_prompt`.
- For a **turn-end / follow-ups** handoff, call it only after the user says yes,
  unless autopilot or prior authorization already covers that path.

It may either:

- reuse the current session's most recently saved baton, or
- accept fresh `prompt_text` / `prompt` and store it in the same call.

Its contract is:

1. drop the full markdown in the current session's session-state folder,
2. **always:** durably store it (reusing the existing agent-dispatch task
   path when available, otherwise a worktree-state file),
3. **only when `.context-handoff/config.yaml`'s `mode` is `auto`** (the
   default is `manual-only` -- see "Mode gate" below): note it in the
   worktree's own record via `agent-worktrees note-handoff` <!-- marketplace-isolation: allow agent-worktrees-management --> (this creates a
   `pending_handoffs` entry agent-worktrees' resident monitor can discover
   and claim independently -- a live-cutover trigger point, not merely
   advisory, so it is gated the same as the two below), refresh
   worktree-visible PENDING-HANDOFF state when `agent-worktrees` is
   available, and best-effort ping `agent-bridge` if present,
4. wait up to 30 seconds for the cutover itself to start -- not for the
   successor to fully finish cold-starting and consume the handoff (a real
   Copilot cold-start routinely takes 40-90+ seconds, and isn't worth
   blocking on) -- **skipped entirely under `manual-only`**, since nothing
   will spawn automatically,
5. check for any pickup signal, including the earlier, cheaper "spawn
   acknowledged" marker,
6. print manual instructions only if nothing at all happened; print a
   distinct "already under way" note when a spawn is merely in flight, or a
   distinct "automatic cutover is disabled" note under `manual-only`,
7. always end with the short handoff prompt/seed.

## Mode gate

`.context-handoff/config.yaml`'s `mode` defaults to `manual-only`: the
automatic pressure nudges, the force-tier auto-trigger, and step 3 above
(the three live-cutover triggers -- the worktree-record note, worktree-
visible pending-handoff state, and the agent-bridge ping) are all opt-in,
requiring `mode: auto` in that file (repo-level) or
`~/.context-handoff/config.yaml` (user-level). Under the default,
`trigger_handoff` still fully composes, stores, and
seeds the handoff -- it just never wires up automatic pickup, so the
operator/agent must consume it manually. Do not assume live cutover
happens unless you have confirmed `mode: auto` is set.

## Resume flow

`/consume-handoff` is the canonical resume surface.

- It prefers this worktree's newest pending agent-dispatch handoff task.
- Otherwise it falls back to the newest matching unconsumed worktree-state
  handoff file.
- `/resume-handoff` is a compatibility alias.

If the user says "resume from handoff" without pasting an exact id or prompt,
sweep the current worktree's state first rather than doing a global search.

### When consume fails because the handoff is already claimed

`consume_handoff` always reports the claimant's session id when a handoff was
already consumed, or is currently being consumed, by another session
(`result.claimedBySession` / the message text). When this happens:

1. **State the claimant session id to the user.** Never silently treat this
   as "nothing to do" or reconstruct a different objective from session
   history.
2. **Offer to file a bug**, but do not file one automatically. A racing or
   duplicate consumption attempt is usually a sign of a real defect (e.g. a
   control system spawning more than one successor for the same handoff) --
   ask the user first, then file it if they say yes.

### Extension-host disconnected mid-call

A handoff tool call -- `consume_handoff` most commonly -- can fail with
something like "Extension disconnected before responding to tool call" when
the Copilot CLI's extension host restarts mid-call, for example while a
background plugin update or reconciliation pass is being applied. A following
notification that the available tool set changed (tools disappearing and
reappearing) is a strong corroborating signal that this is what happened.

This is a **transport-level failure, not a semantic answer**. Unlike an
already-claimed response (which always names a claimant session), a
disconnect carries no information about whether the underlying store was
read, mutated, or left untouched -- it is not evidence that no handoff is
pending.

1. Do not conclude "nothing is pending" or reconstruct a different objective
   from session history solely because the call errored this way.
2. Once the tool set stabilizes (previously-lost tools become available
   again), retry the identical call once.
3. If the tool remains unavailable, or the retry fails the same way, fall
   back to the payload-local CLI (`consume --locator`, `facts`,
   `check-heads`) -- it talks to the durable stores directly and does not
   depend on the extension host being up.
4. Only report "nothing pending" once that CLI-backed retry also finds none.

### Diagnosing a stuck cutover (predecessor not confirmed retired)

`trigger_handoff` is one stage (6 of 13) in a wider cutover lifecycle traced
across this plugin and `agent-worktrees`' resident status monitor -- see
[context-handoff's README § Handoff-lifecycle observability](../../README.md#handoff-lifecycle-observability)
for the full stage model and stores. For a predecessor whose retirement was
never confirmed after its successor was spawned, first run the safe retry when
you are still inside the superseded predecessor session:

```bash
node "$CH" retry-cutover --session-id "$COPILOT_AGENT_SESSION_ID" --cwd "$PWD"
```

That path refocuses an already-live successor instead of spawning a duplicate.
If no live successor exists, it falls back to a fresh spawn attempt. If the
problem is specifically an unretired predecessor after a spawn is recorded (a
successor associated as a candidate *or* already linked, plus a recorded spawn
event -- not only a fully confirmed cutover), then run
`agent-worktrees handoffs-check --worktree-id <id>` <!-- marketplace-isolation: allow diagnostic-tooling --> (or `--all`, `--execute`
to actually retire what it finds) before assuming manual intervention is
needed -- the read-only report does not itself confirm the pane is still
alive, only `--execute`'s live check does -- and do not manually kill a
predecessor pane yourself. It does **not** diagnose "acknowledged but
nothing appeared" (no successor was ever
recorded) -- that case has no dedicated diagnostic yet.

## CLI fallback

When the extension is absent, invoke the payload-local CLI by exact verified
plugin-root-relative path:

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
node "$CH" check-heads --json --cwd "$PWD"
node "$CH" retry-cutover --session-id "$COPILOT_AGENT_SESSION_ID" --cwd "$PWD"
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

PowerShell:

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
node $ch check-heads --json --cwd $PWD
node $ch retry-cutover --session-id $env:COPILOT_AGENT_SESSION_ID --cwd $PWD
node $ch save --title '<topic>' --prompt-file '<handoff.md>' --session-id $env:COPILOT_AGENT_SESSION_ID --cwd $PWD
node $ch trigger --title '<topic>' --prompt-file '<handoff.md>' --session-id $env:COPILOT_AGENT_SESSION_ID --cwd $PWD
node $ch trigger --handoff-token '<HANDOFF_TOKEN>' --session-id $env:COPILOT_AGENT_SESSION_ID --cwd $PWD
node $ch consume --locator 'task:<task-id>' --session-id $env:COPILOT_AGENT_SESSION_ID --cwd $PWD
node $ch consume --locator 'file:<handoff-id>' --session-id $env:COPILOT_AGENT_SESSION_ID --cwd $PWD
```

## Last-resort fallback: write the file yourself

The tool-backed path (`save_handoff_prompt` / `trigger_handoff` /
`consume_handoff`) and the payload-local CLI fallback above both assume
*something* still works -- the extension host, `node`, or a reachable store.
When even that assumption is unsafe (the extension is disconnected, the CLI
fails the same way, or storage reports "no safe task or file store was
available"), the **most durable** continuation path needs none of it: an
ordinary file write, plus a short prompt a human pastes into a fresh session
after `/clear`. This always works because it depends on nothing but the
agent's normal ability to write a file and print text.

1. Compose the same markdown brief the Template section describes.
2. Write it with an ordinary file write -- no MCP tool call, no extension, no
   `node` -- to a stable path under the current session's own state folder:
   the same `~/.copilot/session-state/<session-id>/` directory the extension
   itself already uses (for example
   `~/.copilot/session-state/<session-id>/files/handoff-<slug>.md`). Create the
   `files/` directory first if it does not already exist -- it is not
   guaranteed to be pre-created. This directory persists independent of the
   extension, the payload-local CLI, and any store selection.
3. State the exact absolute path to the user.
4. Give the user a short prompt to paste into a new session after `/clear`,
   naming that exact path and instructing the next session to read it and
   resume the objective it describes -- not merely recap it. For example:

   ```text
   /clear
   Read <absolute-path-to-file> and resume the objective it describes.
   ```

5. This path has no automatic pickup, no claim tracking, and no
   supersession -- it is a manual handoff between two humans/agents. Prefer
   the tool-backed and CLI-backed paths above whenever either is reachable;
   reserve this one for when both have failed.

## Template

Compose the appropriate shape and pass it to `save_handoff_prompt` as
`prompt_text`. Use exactly one of:

```markdown
## Effort-Backed Session Continuation
### Active Effort
### Next Slice
### Immediate Session Delta
### Outstanding Background Flows & External State
### Completion Gates
### Re-Handoff Instructions

## Standalone Session Continuation
### Original Request
### Continuing Objective
### Direction & Motivation
### Progress
### Successor Work Roster
### Outstanding Background Flows & External State
### Completion Gates
### Re-Handoff Instructions
### Gotchas
```

## Rules

- Every session has this mechanism available from turn one, whether or not it
  began from a handoff -- the extension delivers a one-time awareness message
  on the first turn so this is never gated behind a pressure threshold or an
  explicit trigger phrase. Context pressure is never a reason to truncate
  diligence; it is only a reason to hand off.
- The seed is a **locator**, not the handoff. Never inline the full markdown in
  it.
- The stored brief may be long. Preserve fidelity there; optimize the seed and
  the pickup exchange instead.
- Keep the original topic and parent objective visible.
- Separate the handoff leg's completion gate from the broader objective's
  completion gate.
- Never claim auto-pickup. A handoff is not loaded automatically on restart.
- Never end a turn with outstanding work and no handoff. "Suitable stopping
  point," "session ran long," and "getting late" do not excuse it; only a
  genuine crossroads, an error, a design contradiction, or a confirmation-gated
  destructive step does -- and even those close with a handoff naming the
  blocker, not a silent stop.
- Never silently drop outstanding background flows (watches, polls,
  `manage_schedule` entries, long-running commands) or external state this
  session owns (open PRs, held claims/leases, peer-agent coordination). Always
  carry each forward in the handoff's **Outstanding Background Flows &
  External State** section as either resumable (state how) or an explicit
  open item -- write "none" only when genuinely none exist.
