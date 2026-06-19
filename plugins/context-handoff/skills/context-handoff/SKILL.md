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

### Two outputs: file + short prompt

A handoff produces **two things**:

1. **A handoff file** saved to the session state folder at
   `<session-folder>/files/handoff.md`. This contains the full
   structured continuation prompt with all the detail.

2. **A short copy-paste prompt** shown to the user inline. This is
   what they actually paste into the next session — it should be
   **3–5 lines max** and reference the handoff file for full context.

### Steps

1. **Call the `generate_handoff_prompt` tool** if available (registered
   by the context-handoff extension). It returns structured session
   facts: session ID, cwd, branch, files modified, git status, turn
   count, and key tool invocations.

2. **Write the full handoff file** to `<session-folder>/files/handoff.md`
   using the template below. Include everything the next session needs.

3. **Present a short prompt** to the user in a fenced code block they
   can copy-paste into a new session. Keep it **3–5 lines max**.

4. **Call `save_handoff_prompt`** with the composed text. The extension
   writes the prompt to the session's state folder.

If the `generate_handoff_prompt` tool is unavailable (extension not
loaded), compose the handoff manually from your own context.

---

## Resuming From a Handoff

The user copies the short prompt from the previous session and pastes
it as the first message in a new session. The prompt contains the path
to the full handoff file, which the new session reads to orient itself.

### If the user says "pick up from last session"

1. **Check your context** — the handoff may already be present (injected
   by `onSessionStart`). Look for `[Context Handoff]` markers. If found,
   use that context to orient and start working.

2. **If no handoff was injected** — the previous session may not have
   generated one. Fall back to `session_store_sql` to query the most
   recent session for this repo and summarize what was worked on.

---

## Handoff File Template

Write this to `<session-folder>/files/handoff.md`:

```markdown
## Session Continuation

### Original Request
<The user's original ask — preserve the session's core topic>

### Progress
- [x] Completed items (with key file paths)
- [ ] In-progress / remaining items

### Next Steps
1. <Immediate next action>
2. <Follow-up actions>

### Gotchas
<Approaches that failed, workarounds discovered, non-obvious context>
```

---

## Rules

- **Do not exceed 40 lines or ~250 words.** This goes into a new
  session's context window — prefer omission over completeness.
- **Lead with the original topic.** The "Original Request" section must
  reference the session's founding purpose, not just recent activity.
  Without this, the next session lacks orientation.
- **Be specific.** "Fix the auth bug" is useless. "JWT refresh in
  `src/auth/token.ts:142` has a race — mutex added but error handler
  uses old non-awaited path" is useful.
- **Include file paths.** The new session won't know what you touched.
- **Include what failed.** Approaches that didn't work save the next
  session from repeating them.
- **Include the why.** Decisions without rationale get re-litigated.
- **Include only details needed to resume work.** Git status, recent
  commits, and mechanical data can be re-derived by the new session.
- **The handoff file can be as long as needed.** It's read on demand by
  the next session, not injected into context automatically.
- **The inline prompt must be short.** 3–5 lines. The user will
  copy-paste it — keep it scannable.

---

## Integration Notes

- Handoff prompt files are stored in the session's state folder:
  `~/.copilot/session-state/<sessionId>/files/<sessionId>-prompt.md`
