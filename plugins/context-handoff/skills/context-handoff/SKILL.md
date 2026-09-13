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

Context-handoff stores the brief and preserves native `/goal` continuity.
After an authorized save, call `continue_handoff` with the exact returned
`HANDOFF_SEED`; `/handoff-continue` requests this live path. End the source turn
so final usage can settle before launch. Herdr or agent-worktrees creates the
fixed successor; the extension restores and admits it before identity-checked
retirement. Save alone does not launch.

Requires Node.js and the tested Copilot CLI 1.0.84-3 native API. Do not use a
plain text pickup to bypass native restoration. Running goals continue once
with their exact remaining soft cap; paused, exhausted, completed, and no-goal
handoffs submit no automatic business message. Hidden stopped GoalPanel is
native behavior, not grounds to enable autopilot or grant credits.

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
  conversation gets tighter.

## Two triggers, two gates

### Attached observers: prepare before cutover

Native `session.idle` includes attached shells, not just the agent turn.
For a source-private read-only observer of an independently running job,
call `continue_handoff` with the exact seed and `observers` entries:
`{shell_id, job: {host, id, identity, artifact_path, terminal_path, reattach}}`.
`identity` is the original scheduler ID or PID/start identity; preserve
authorization and remaining budget in the brief. The job must write durable
terminal status independently of the observer.

The tool saves the job and runtime observer metadata, then rejects an active
attached shell without arming cutover or stopping any process. Verify the
declared observer is separate and read-only; use `stop_bash` for ONLY that
source shell ID, never the original job or its launcher. Retry the SAME saved
request using `retry_handoff_cutover`; do not save/spawn again. Undeclared
attached work must finish or be explicitly identified, never blindly killed.

The admitted successor reads the restored observation records and checks
durable terminal status FIRST, including a result written during the
observation gap. Validate terminal artifacts or attach a NEW event-driven
observer to the SAME job, recording its new session/shell/PID identity.
Historical source shell IDs are not restored observers. Do not automatically
execute metadata, restart work, enable autopilot, create a goal or add budget.
Stopped/no-goal handoffs still send no automatic business message.

### 1. Context-pressure-driven handoff: trigger directly

When context pressure is the reason for handing off and the objective still has
more work left to do:

1. **Call `generate_handoff_prompt`.**
2. **Compose the markdown brief** using the effort-backed shape when a valid
   open active effort exists, otherwise the full standalone shape.
3. **Call `save_handoff_prompt`.** This safely stores the baton and returns the
   short handoff seed.
4. **Call `continue_handoff` with the saved seed immediately.**

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
5. **Call `continue_handoff` only after the user says yes.**

Only this turn-end follow-up path is skippable via **autopilot** or prior
explicit pre-authorization.

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

`trigger_handoff` retains the older signal-only pickup path. New native-aware
records (including no-goal saves) are directed to `continue_handoff`.

- For a **context-pressure-driven** handoff with work still left to do, call it
  immediately after `save_handoff_prompt`.
- For a **turn-end / follow-ups** handoff, call it only after the user says yes,
  unless autopilot or prior authorization already covers that path.

It may either:

- reuse the current session's most recently saved baton, or
- accept fresh `prompt_text` / `prompt` and store it in the same call.

Its contract is:

1. drop the full markdown in the current session's session-state folder,
2. refresh worktree-visible pending-handoff state when `agent-worktrees` is
   available,
3. reuse the existing agent-dispatch task path when available,
4. best-effort ping `agent-bridge` if present,
5. wait up to 30 seconds,
6. check for any pickup signal,
7. print manual instructions if nothing picked it up,
8. always end with the short handoff prompt/seed.

## Resume flow

An explicitly launched successor restores the native snapshot by normal cold
resume, writes `context-handoff.md` through the public workspace API, and
consumes its assigned task/file without a model admission turn. Preserve the
source model, effort, context tier, agent, home and permissions. Herdr rejects
unsupported manual/assisted launch modes before creating a pane.

Use `retry_handoff_cutover` for the existing request, not a second save/spawn.
Unknown launches or sends with no reconcilable public receipt preserve the
source; never blindly replay. A queued send is not complete until its exact
native user event exists. Identical goal text does not authorize overwriting a
new user goal. Ordinary already-admitted deliveries never recreate goals.

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
node $ch save --title '<topic>' --prompt-file '<handoff.md>' --session-id $env:COPILOT_AGENT_SESSION_ID --cwd $PWD
node $ch trigger --title '<topic>' --prompt-file '<handoff.md>' --session-id $env:COPILOT_AGENT_SESSION_ID --cwd $PWD
node $ch trigger --handoff-token '<HANDOFF_TOKEN>' --session-id $env:COPILOT_AGENT_SESSION_ID --cwd $PWD
node $ch consume --locator 'task:<task-id>' --session-id $env:COPILOT_AGENT_SESSION_ID --cwd $PWD
node $ch consume --locator 'file:<handoff-id>' --session-id $env:COPILOT_AGENT_SESSION_ID --cwd $PWD
```

## Template

Compose the appropriate shape and pass it to `save_handoff_prompt` as
`prompt_text`. Use exactly one of:

```markdown
## Effort-Backed Session Continuation
### Active Effort
### Next Slice
### Immediate Session Delta
### Completion Gates
### Re-Handoff Instructions

## Standalone Session Continuation
### Original Request
### Continuing Objective
### Direction & Motivation
### Progress
### Successor Work Roster
### Completion Gates
### Re-Handoff Instructions
### Gotchas
```

## Rules

- The seed is a **locator**, not the handoff. Never inline the full markdown in
  it.
- The stored brief may be long. Preserve fidelity there; optimize the seed and
  the pickup exchange instead.
- Keep the original topic and parent objective visible.
- Separate the handoff leg's completion gate from the broader objective's
  completion gate.
- Never claim auto-pickup. A handoff is not loaded automatically on restart.
