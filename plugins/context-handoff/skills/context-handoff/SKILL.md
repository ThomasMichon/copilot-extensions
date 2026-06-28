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
  - 'continuation prompt'
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
seamlessly resume work from the current session.

## How This Differs From Related Skills

| Skill | Question | Scope | Primary Source |
|-------|----------|-------|----------------|
| **context-handoff** (this) | "What did last session queue up?" | Per-worktree | Dedicated handoff system |
| **recap** | "What did I last do?" | Facility-wide | Permanent Record logs |
| **backlog** | "What's next for ___?" | Per-service/tool | Gitea → plans → ROADMAP |

Handoff is a relay baton — it carries structured state from one session
to the next on the same worktree. Recap is a rearview mirror. Backlog
is a task list.

---

## When to Generate a Handoff

- The context-handoff extension monitors `session.usage_info` events for
  **exact token counts**. It injects reminders via `additionalContext`
  when context utilization crosses thresholds:
  - **55%** — gentle reminder ("consider generating a handoff soon")
  - **70%** — urgent reminder ("generate NOW, compaction at ~80%")
- The user explicitly asks for a handoff or continuation prompt.
- You sense the conversation is getting complex (even if token usage
  hasn't hit thresholds yet — they reset after compaction).

---

## How to Generate

A handoff produces **two things**:

1. **A handoff file** written to the current session's state folder at
   `~/.copilot/session-state/<sessionId>/files/<sessionId>-prompt.md`
   (via the `save_handoff_prompt` tool, passing the markdown as `prompt_text`).
   This holds the **full** handoff: the direction and **motivation** of the
   work, key **next action items**, and the **target goals** — plus enough file
   paths and gotchas to resume.

2. **A short wrapper prompt** you reply with inline. It is **addressed to the
   next session's agent** and the user copies it **verbatim** into `/clear` (or
   `/new`). It must (a) name the **full path** to the handoff file and (b)
   instruct that agent to **read** the file and continue. It does **not** repeat
   the handoff contents — the file holds those.

### Steps

1. **Call `generate_handoff_prompt`** (registered by the context-handoff
   extension). It returns structured facts: session ID, cwd, branch, files
   modified, git status, turn count, key tool invocations.

2. **Compose the full handoff markdown** using the template below — lead with
   the original request, then direction/motivation, next steps, target goals,
   and gotchas.

3. **Call `save_handoff_prompt`** with that full markdown **as the `prompt_text`
   argument** (the `prompt` alias is also accepted). It writes the file to the
   session state folder and **returns the absolute path**.

4. **Reply with the short wrapper prompt.** One or two sentences, **addressed to
   the next agent**, that name the returned path (a `~/` form is fine) and tell
   that agent to **read** the handoff file and continue. The user pastes it
   verbatim into `/clear` (or `/new`). For example:

   > Read the handoff at `~/.copilot/session-state/<id>/files/<id>-prompt.md`
   > and continue: config-reflect + system-worktrees efforts (HA drift tracking
   > → multi-system framework).

If the `generate_handoff_prompt` tool is unavailable (extension not loaded),
compose the handoff manually and write the file with the `create` tool to the
same session-folder path.

**Do not** commit the handoff file, write it anywhere outside the session
folder, hide the path inside a tool call, or tell the user it will be picked
up automatically on restart — **it will not**. The user must paste the wrapper
prompt themselves.

---

## Resuming From a Handoff

A handoff is **not** auto-loaded. The user resumes by pasting the wrapper prompt
(from the previous session) as the first message in a new session. That prompt
names the handoff file path and tells you to read it; read that file to orient
yourself, then continue.

### If the user says "pick up from last session" with no pasted prompt

The previous session's handoff was not pasted in. Fall back to
`session_store_sql` to find the most recent session for this repo/worktree and
summarize what was worked on, or look for a
`~/.copilot/session-state/<id>/files/<id>-prompt.md` file if you know the id.

---

## Handoff File Template

Write this (via `save_handoff_prompt`) to
`~/.copilot/session-state/<sessionId>/files/<sessionId>-prompt.md`. Full
template: [`references/handoff-template.md`](references/handoff-template.md).
Its sections:

```markdown
## Session Continuation
### Original Request
### Direction & Motivation
### Progress           (- [x] done / - [ ] remaining, with file paths)
### Next Action Items  (1. immediate next, 2. follow-ups)
### Target Goals       (done-ness criteria)
### Gotchas            (failed approaches, workarounds, non-obvious context)
```

---

## Rules

- **The handoff FILE can be as long as needed** — it's read on demand when the
  next agent opens it, not injected into any context automatically. Capture
  direction/motivation, next actions, and target goals in full.
- **The inline WRAPPER prompt must be short — one or two sentences.** It is
  addressed to the **next agent**: it names the full handoff file path (a `~/`
  form is fine) and tells that agent to **read** the file and continue. It is
  copy-pasted verbatim into `/clear` (or `/new`); keep it scannable and do
  **not** repeat the file's contents.
- **Lead with the original topic.** The "Original Request" must reference the
  session's founding purpose, not just recent activity.
- **Be specific.** "Fix the auth bug" is useless. "JWT refresh in
  `src/auth/token.ts:142` has a race — mutex added but error handler uses old
  non-awaited path" is useful. Include file paths, what failed, and the why.
- **Never claim auto-pickup.** A handoff is never loaded automatically on
  restart. Do not imply Copilot will resume on its own — the user must paste
  the wrapper prompt.
- **Keep it in the session folder.** Do not commit the handoff file or write it
  anywhere outside `~/.copilot/session-state/<sessionId>/files/`. Do not hide
  the path inside a tool call — show it to the user in your reply.

---

## Integration Notes

- Handoff files are stored in the session's state folder:
  `~/.copilot/session-state/<sessionId>/files/<sessionId>-prompt.md`
- `save_handoff_prompt` writes that file and returns its absolute path; surface
  that path to the user verbatim in the wrapper prompt. Pass the markdown as the
  **`prompt_text`** argument (the `prompt` alias is also accepted).
