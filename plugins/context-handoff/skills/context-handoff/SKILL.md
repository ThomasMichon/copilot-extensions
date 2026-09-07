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

## Default behavior: compose, save, ask

The normal flow is:

1. **Call `generate_handoff_prompt`.**
2. **Compose the markdown brief** using the effort-backed shape when a valid
   open active effort exists, otherwise the full standalone shape.
3. **Call `save_handoff_prompt`.** This safely stores the baton and returns the
   short handoff seed.
4. **Ask the user** whether to continue via handoff, phrased so they can answer
   briefly (for example, "sure").
5. **Call `trigger_handoff` only after that yes.**

Only skip the ask-and-wait step when **autopilot** is active or the user
already explicitly pre-authorized automatic handoff triggering.

## Efforts + handoffs

When both capabilities are present, use them to keep one session scoped to one
slice of a larger effort:

- the **effort** remains the durable source of truth and completion gate,
- the **handoff** carries only the immediate relay delta,
- one session should stride forward confidently, reach a clean boundary, and
  hand the next slice forward rather than trying to finish the entire effort in
  one context window.

For an effort-backed baton, link the repository-relative effort README and avoid
duplicating its request, plan, or journal. Carry only the next slice, immediate
blockers, decisions, in-flight work, and required confirmations.

## `trigger_handoff`

`trigger_handoff` is the explicit "arm pickup" step. Use it only after user
approval, or in autopilot / pre-authorized mode.

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

`/consume-handoff` is the canonical resume surface.

- It prefers this worktree's newest pending agent-dispatch handoff task.
- Otherwise it falls back to the newest matching unconsumed worktree-state
  handoff file.
- `/resume-handoff` is a compatibility alias.

If the user says "resume from handoff" without pasting an exact id or prompt,
sweep the current worktree's state first rather than doing a global search.

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
