---
name: context-handoff
description: >
  Context handoff — generate continuation prompts for seamless session
  transitions, and resume from handoffs left by previous sessions.
  Use this skill when preparing to hand off work to a new session or
  when resuming from a prior session's handoff.
  Trigger phrases include:
  - 'handoff'
  - '/handoff'
  - '/handoff-continue'
  - '/resume-handoff'
  - 'resume handoff'
  - 'resume from handoff'
  - 'resume from a handoff'
  - 'consume handoff'
  - 'continuation prompt'
  - 'hand off and continue'
  - 'live cutover'
  - 'continue automatically'
  - 'next session'
  - 'context is getting large'
  - 'pick up where we left off'
  - 'pick up from last session'
  - 'resume from last session'
  - 'generate a handoff'
  - 'session transition'
---

# Context Handoff

Use the exact `argv[0]` from the agent-worktrees session command catalog for
worktree-state and cutover operations executed in the current session. Replace
`<agent-worktrees catalog argv[0]>` with the raw path and quote it at the shell
call site on POSIX; in PowerShell use
`& "<agent-worktrees catalog argv[0]>" <args>`. Generated successor
first-action commands remain literal global
wrappers because they run before the successor receives session catalog
context.

Generate structured continuation prompts so a new Copilot CLI session can
seamlessly resume work from the current session.

## How This Differs From Related Skills

| Skill | Question | Scope | Primary Source |
|-------|----------|-------|----------------|
| **context-handoff** (this) | "What did last session queue up?" | Per-worktree | Dedicated handoff system |
| **recap** | "What did I last do?" | System-wide | Permanent Record logs |
| **backlog** | "What's next for ___?" | Per-service/tool | Gitea → plans → ROADMAP |

Handoff is a relay baton — it carries structured state from one session
to the next on the same worktree. Recap is a rearview mirror. Backlog
is a task list.

## Continuity Contract

A handoff transfers **active responsibility for the original objective**. It is
not a request to confirm that the predecessor's latest phase finished, and it is
not a session-closing recap. Consuming the baton starts the successor's relay
leg.

- Re-read the **Original Request**, **Continuing Objective**, ordered
  **Successor Work Roster**, and their cited effort/issue/source of truth.
- Keep driving every actionable next phase the original request permits, as far
  as the current context and available work allow. Do not wait for another user
  prompt merely because one phase, PR, or checklist slice completed.
- A single session may consume one handoff, implement and land many additional
  slices or phases, and produce another handoff when context pressure returns.
  Session boundaries do not define effort boundaries.
- Stop only when the parent objective's completion gate is met, an explicit user
  scope boundary or required safety confirmation stops progress, or a real
  blocker needs input. A handoff never waives those boundaries or confirmations.
- If context pressure returns before the parent objective is done, preserve the
  same objective and the newly remaining roster in the next handoff. Complete
  the current deferred handoff task only after that successor baton is durably
  stored, or after the parent objective itself is complete.

A handoff with no actionable successor work is usually malformed. If the
original objective is genuinely complete, finish the session and worktree
lifecycle instead of spawning a successor merely to announce completion, unless
the user explicitly requested an archival continuation prompt.

---

## When to Generate a Handoff

- The context-handoff extension monitors `session.usage_info` events for
  **exact token counts** and, on the next idle, sends you an **agent-facing
  nudge that tells you to invoke this skill** when context utilization crosses
  a threshold:
  - **55% of the window by default** — hand off at the next clean boundary
  - **70% of the window by default** — hand off now; compaction remains at ~80%
  An owning repository may override these percentages in
  `.context-handoff/config.yaml`.
  The nudge deliberately **does not prescribe individual tool calls** or a
  "write a file" outcome — it hands you to this skill, which owns the
  sequencing. When you receive it **under a mux session**, the correct response
  is the **autonomous live cutover** described below (store the handoff, spin up
  the successor Copilot in place, end your turn) — *not* a paste prompt back to
  the operator. The nudge fires once per threshold and resets after compaction.
- The user explicitly asks for a handoff or continuation prompt.
- You sense the conversation is getting complex (even if token usage
  hasn't hit thresholds yet — they reset after compaction).

---

## How to Generate

A handoff has three parts: the **stored handoff** (the full continuation
context), an optional **live cutover** that spins up the successor *in place*,
and a **short paste prompt** for environments where cutover is not available.

**Live cutover is the default under mux.** A mux-backed session stores the
handoff, spawns a successor in the same `wt-<id>` mux session, and then stops
working. The predecessor does **not** retire itself on idle. The successor
retires the predecessor only after it consumes the stored handoff, so a
successor that never comes up leaves the predecessor and terminal available for
recovery.

### Storage and cutover matrix

| agent-dispatch coordinator | live mux session | Behavior |
|---|---|---|
| yes | yes | Store a `proposed`, `handoff`-labeled task pinned to this worktree. `continue_handoff` spawns the successor. The successor calls `consume_handoff` for that task, which runs the consume path and retires the recorded predecessor pane after pickup. |
| yes | no | Store the same task. Reply with the short paste prompt containing the full `agent-dispatch consume <id>` command. <!-- marketplace-isolation: allow handoff-seed-startup --> |
| no | yes | Store a one-time JSON handoff file under the worktree state directory reported by agent-worktrees, outside the repo checkout. `continue_handoff` spawns the successor. The successor calls `consume_handoff`, which marks the file consumed and retires the recorded predecessor pane. |
| no | no | Store the same one-time worktree-state file. Reply with the short paste prompt telling the next session to call `consume_handoff` with the handoff id. |

### Steps (default = live cutover)

1. **Call `generate_handoff_prompt`**. It returns structured facts: session ID,
   cwd, branch, files modified, git status, turn count, and key tool invocations.
2. **Compose the full handoff markdown** using the template below — lead with
   the original request, preserve the continuing parent objective, and provide
   an ordered successor work roster plus separate handoff/worktree completion
   gates. Completed progress must not erase later actionable phases.
3. **Call `save_handoff_prompt`** with that markdown as **`prompt_text`** (plus
   an optional short `title`). It stores the handoff and returns both the paste
   prompt and a `HANDOFF_SEED:` line.
4. **If under mux, call `continue_handoff`** with `seed` exactly equal to the
   `HANDOFF_SEED` string. Then end your turn. Do not start new work in the
   predecessor.
5. **If cutover cannot run**, reply with only the short paste prompt returned by
   `save_handoff_prompt`.

> **Two completion models.** A task-backed paste resume uses
> `agent-dispatch consume <id>` and completes the baton on pickup. <!-- marketplace-isolation: allow handoff-seed-startup -->
> A task-backed
> live cutover uses `consume_handoff` with `defer_complete: true`; the successor
> later runs `agent-dispatch complete <id>` only when it reaches the handoff
> completion gate. Finishing the predecessor's latest phase is not sufficient
> when the continuing objective still has actionable work. <!-- marketplace-isolation: allow handoff-seed-startup -->

### Fallback when the extension's tools are unavailable — the CLI

The tools above are provided by the context-handoff **extension**. When the
extension does not resolve or fails to load, they are simply absent — most
notably in a **Bare-resumed session**, where *no* extensions load at all
(`extensions list` shows nothing). The plugin's **payload files are still on
disk**, so an agent can drive the same store + live-cutover directly through a
standalone Node CLI — no extension runtime required:

```bash
CH="$HOME/.copilot/installed-plugins/copilot-extensions/context-handoff/extensions/context-handoff/handoff-cli.mjs"

# Store the handoff AND start the live cutover (the save_handoff_prompt +
# continue_handoff equivalent). Compose the markdown yourself (the extension's
# in-memory session facts are unavailable out-of-band) and pass it in:
node "$CH" cutover --title "<topic>" --prompt-file <handoff.md>

# Store only + print the paste prompt (no cutover):
node "$CH" save --title "<topic>" --prompt-file <handoff.md>
# Trigger a cutover for an existing seed:   node "$CH" continue --seed "<HANDOFF_SEED>"
# Consume a file-backed handoff:            node "$CH" consume --handoff-id <id>
```

It reuses the SDK-free `handoff-core.mjs` (same store format +
`agent-worktrees handoff-cutover` trigger <!-- marketplace-isolation: allow handoff-core-management -->
and the issue-#853 bash-first seed), so a handoff it stores is
byte-compatible with `consume_handoff` / `/resume-handoff`. Session id defaults
to `$COPILOT_AGENT_SESSION_ID`; pass `--session-id` / `--cwd` when running from
outside the worktree. `--no-task` forces the file store.

If even `node` is unavailable, compose the handoff manually and follow the same
storage rules: a task when a coordinator and worktree are resolvable, otherwise a
one-time file under the worktree state directory outside the checkout. Do not
write handoffs into the repo.

---

## Live-Cutover Handoff — the primary path (hand off *and continue* in place)

`save_handoff_prompt` stores the baton. `continue_handoff` only performs the mux
choreography: it opens a successor pane and seeds it. The stored baton includes
the predecessor pane id and session id. The successor's first action is to call
`consume_handoff`; that consume step loads the brief, marks file-backed handoffs
spent, records the predecessor as handed off, and retires the old pane through
`agent-worktrees handoff-cutover --retire-pane`. <!-- marketplace-isolation: allow handoff-core-management -->

This successor-consume-driven retire is the safety rule: **the predecessor never
self-retires on idle.** If the successor hangs before consuming, no retire is
attempted and the operator can switch back to the predecessor.

---

## Resuming From a Handoff

A handoff is **not** auto-loaded. How you resume depends on which form the
previous session produced:

1. **agent-dispatch form** (coordinator available). The paste prompt contains
   the complete `agent-dispatch consume <id>` command. <!-- marketplace-isolation: allow handoff-seed-startup -->
   `/resume-handoff` can also
   find and consume this worktree's newest proposed handoff task and inject the
   continuation prompt.
2. **File form** (no coordinator). The paste prompt tells the next session to
   call the extension's `consume_handoff` tool with the handoff id. The tool
   reads the worktree-state JSON file, marks it consumed, and injects the stored
   continuation. Re-running it on a consumed file returns a stop notice instead
   of replaying the brief.

### Natural-language resume requests: sweep this worktree's state first

When the user says "resume from handoff", "pick up from last session", or
similar **without pasting an exact handoff id/prompt**, do not begin with a
global agent-dispatch query or cross-session search. The current worktree's
the agent-worktrees state is the authoritative first stop:

1. Resolve the local state directory with
   `<agent-worktrees catalog argv[0]> get worktree-state-dir`. If the session resumed with a CWD
   outside its worktree, retry with the current session id:
   `<agent-worktrees catalog argv[0]> get worktree-state-dir --session-id <session-id>`.
2. Sweep `<worktree-state-dir>/handoff/*.json`, newest first. Select only a
   valid `kind: "context-handoff"` record for this worktree whose `consumed`
   value is not `true`. Call `consume_handoff` with its exact `path`; do not
   merely read or replay `promptText`, because the tool owns one-time
   consumption and predecessor retirement.
3. If no unconsumed file exists, read the worktree-local pointer history with
   `<agent-worktrees catalog argv[0]> status --history --json --limit 20` (pass
   `--worktree-id <id>` when CWD cannot resolve it). Walk newest-first for a
   `kind: "handoff"` entry. If it names `handoff task <id>`, call
   `consume_handoff` with that exact `task_id`.
4. Only when local state has neither a file baton nor an exact task pointer,
   query agent-dispatch for `proposed`, `handoff`-labeled tasks. Filter the
   result to `target_worktree == <current-worktree-id>` before choosing the
   newest task; never consume a task selected only by repo, title, or global
   recency.
5. Only after the complete worktree-local and worktree-filtered sweep finds
   nothing, fall back to `session_store_sql` for recent-session recovery. That
   fallback summarizes prior work; it is not a handoff consumption path.

If the user supplied an exact handoff id/path or the extension already injected
the handoff, skip discovery and consume/continue that exact baton.

### `/resume-handoff` — an injected slash command (extension-provided)

When the operator runs `/resume-handoff` in the target worktree, the extension:

1. Prefers this worktree's newest `proposed`, `handoff`-labeled agent-dispatch
   task and consumes it.
2. Otherwise finds the newest unconsumed worktree-state handoff file and consumes
   it with the same one-time semantics as `consume_handoff`.
3. Injects the continuation prompt into the foreground session, or logs that no
   pending handoff was found.

So your job on resume is: **read the injected handoff and keep going.** Do not
re-claim or re-complete a baton that `/resume-handoff` already consumed. Apply
the Continuity Contract above: the completed items are history; the continuing
objective and successor work roster are the authorization to keep driving.

### If the user says "pick up from last session" with no pasted prompt

The previous session's handoff was not pasted in. Perform the state-first sweep
above. If nothing is pending after its local-state, exact-pointer, and
worktree-filtered task checks, fall back to `session_store_sql` to identify the
most recent session for this repo/worktree and summarize what was worked on.

---

## Handoff Template

Compose this markdown and pass it to `save_handoff_prompt` as `prompt_text`
(it becomes the agent-dispatch **task payload**, or the prompt content inside
the one-time worktree-state handoff file). Full template:
[`references/handoff-template.md`](references/handoff-template.md). Its sections:

```markdown
## Session Continuation
### Original Request
### Continuing Objective
### Direction & Motivation
### Progress           (- [x] done / - [ ] remaining, with file paths)
### Successor Work Roster
### Completion Gates   (handoff leg vs. parent objective/worktree)
### Re-Handoff Instructions
### Gotchas            (failed approaches, workarounds, non-obvious context)
```

---

## Rules

- **The handoff CONTENT can be as long as needed** — whether it lands in the
  task payload or a file, it's read on demand when the next agent resumes, not
  injected into any context automatically. Capture direction/motivation, next
  actions, and target goals in full.
- **The inline REPLY prompt must be short — one or two sentences.** It is
  addressed to the **next agent** and is whichever form `save_handoff_prompt`
  returned: either the agent-dispatch resume seed with the full
  `agent-dispatch consume <id>` command, <!-- marketplace-isolation: allow handoff-seed-startup -->
  or the file-backed seed instructing
  the next agent to call `consume_handoff` with the handoff id. It is
  copy-pasted verbatim into `/clear` (or `/new`); keep it scannable and do
  **not** repeat the handoff contents.
- **Lead with the original topic.** The "Original Request" must reference the
  session's founding purpose, not just recent activity.
- **Preserve the parent objective.** A phase-complete milestone belongs under
  Progress. It must not replace the broader objective or become the handoff
  title when more of the original request remains.
- **Open forward, not backward.** The Successor Work Roster must contain the
  immediate next action and every already-known later slice the successor can
  pursue without another user decision. Do not write "no action remains" while
  the cited effort, issue, or original request still exposes actionable work.
- **Separate completion gates.** State both when the current deferred handoff
  task may be completed and when the parent objective/worktree is actually
  complete. A landed phase, consumed baton, or merged PR is not by itself the
  latter.
- **Plan the next relay.** Re-Handoff Instructions must tell a context-limited
  successor to carry the same parent objective and remaining roster into another
  handoff rather than stopping for operator intervention.
- **Be specific.** "Fix the auth bug" is useless. "JWT refresh in
  `src/auth/token.ts:142` has a race — mutex added but error handler uses old
  non-awaited path" is useful. Include file paths, what failed, and the why.
- **Never claim auto-pickup.** A handoff is never loaded automatically on
  restart. Do not imply Copilot will resume on its own — the user resumes it
  (`/resume-handoff`, or paste the reply prompt).
- **One home, not two.** A handoff lives in **one** place — the agent-dispatch
  task when a coordinator is running, else a worktree-state file outside the
  repo checkout. Don't write a file *and* a task. Show the reply prompt to the
  user; don't hide it in a tool call.

---

## Integration Notes

- **Storage is mode-dependent.** `save_handoff_prompt` prefers an agent-dispatch
  task (payload = metadata plus the markdown, no file handoff) and falls back to
  a one-time JSON file under the worktree state directory reported by
  the agent-worktrees state only when no coordinator is reachable. Pass the markdown as the
  **`prompt_text`** argument (the `prompt` alias is also accepted) plus an
  optional short **`title`**.
