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
  **exact token counts** and, on the next idle, sends you an **agent-facing
  nudge that tells you to invoke this skill** when context utilization crosses
  a threshold:
  - **55%** — gentle reminder ("hand off soon")
  - **70%** — urgent reminder ("hand off now, compaction at ~80%")
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
context), a **live cutover** that spins up the successor *in place* (the
**primary** path — no copy/paste), and a **short reply prompt** as the fallback
when a cutover isn't possible.

**Live cutover is the default.** Facility sessions run inside a `wt-<id>` mux
wrapper (picker-launched or `embody`-spawned), so a handoff should **take over
in place**: store the handoff task, then spin up a successor Copilot in the same
mux and retire this session — the operator is **never handed a paste prompt** to
copy. Only when the session is *not* under a mux (cutover impossible) do you fall
back to the paste reply.

**Where the handoff is stored** — `save_handoff_prompt` decides:

- **agent-dispatch present →** a **`proposed`, `handoff`-labeled task** pinned to
  this worktree (payload = the full markdown; **no** session file). It returns a
  baton paste-seed *and* a **deferred cutover seed** on the `HANDOFF_SEED:` line.
- **agent-dispatch absent →** a **file** in the session state folder; the reply
  is `Read the handoff at <path> and continue: <topic>.`

You don't choose the storage — the tool does; it sits *on top of* agent-dispatch
when it exists and falls back to the file otherwise. **Prefer the task flow:** if
a coordinator is reachable, the handoff is a task, not a file — do not hand-write
a `*-prompt.md` file when `agent-dispatch health` answers.

### Steps (default = live cutover)

This is the **same autonomous sequence** whichever entry point triggered it —
the automated context-pressure nudge, a plain `/handoff`, `/handoff-continue`,
or the operator asking to "hand off and continue." Under a mux session you run
all of it hands-free and **end your turn**; the operator is not asked to paste
anything.

1. **Call `generate_handoff_prompt`** (registered by the context-handoff
   extension). It returns structured facts: session ID, cwd, branch, files
   modified, git status, turn count, key tool invocations.

2. **Compose the full handoff markdown** using the template below — lead with
   the original request, then direction/motivation, next steps, target goals,
   and gotchas.

3. **Call `save_handoff_prompt`** with that markdown as **`prompt_text`** (plus
   an optional short `title`). It stores the handoff and returns the paste reply
   **and** a `HANDOFF_SEED:` line (the deferred cutover seed).

4. **Perform the cutover (primary):** call **`continue_handoff`** with `seed` =
   exactly that `HANDOFF_SEED` string. It spawns the successor in a new window of
   this worktree's mux, seeded to take over the handoff, and cuts the operator
   over; this session retires itself on the next idle. **End your turn** — do not
   reply with a paste prompt.

5. **Fallback only if cutover can't run:** if `continue_handoff` reports it is
   **not under a mux session** (graceful, non-destructive), *then* reply with
   ONLY the short paste prompt (the user pastes it into `/clear`/`/new`, or runs
   `/resume-handoff`). Never paste the handoff contents.

> **Two completion models.** The **cutover** successor is a dispatched autopilot
> CLI: its seed uses `agent-dispatch consume <id> --defer-complete` and it
> **completes the task explicitly** only when it reaches the handoff's goal —
> so `completed` means *the work is done*. The **paste / `/resume-handoff`**
> path is a human quick-resume: its seed uses plain `agent-dispatch consume <id>`
> (baton — completed on pickup; the *work* is tracked by its effort/issue).

If `generate_handoff_prompt` / `save_handoff_prompt` are unavailable (extension
not loaded), compose the handoff manually: if `agent-dispatch health` answers,
create the task yourself (see "Where the handoff is stored" below); otherwise
write the file with the `create` tool to
`~/.copilot/session-state/<sessionId>/files/<sessionId>-prompt.md`.

**Do not** commit the handoff, write a file anywhere outside the session folder,
hide the reply prompt inside a tool call, or tell the user the handoff will be
picked up automatically on restart — **it will not**. A cutover successor picks
itself up; a fallback paste reply is resumed by the user (paste, or
`/resume-handoff`).

### Where the handoff is stored (agent-dispatch vs file)

`save_handoff_prompt` handles this automatically — **prefer the task, fall back
to the file** — so you normally just call it and reply with what it returns.
The mechanics, for when you must do it by hand (extension not loaded):

- **A coordinator answers (`agent-dispatch health` exits 0):** store the handoff
  as a **`proposed`, `handoff`-labeled task** pinned to this worktree, payload =
  the full markdown. This is the **whole** handoff — there is **no** session
  file in this mode.

  ```bash
  # write the markdown to a temp file, then:
  agent-dispatch create "Continue: <short title>" --proposed \
    --label handoff \
    --payload-file "<temp markdown path>" \
    --affinity worktree=<worktree_id> \
    --target-worktree <worktree_id> --target-machine <machine> \
    --source context-handoff \
    --dedup-key "handoff-<sessionId>"
  # then reply with the resume seed: "You are resuming a handoff (agent-dispatch
  # task <id>) … run: agent-dispatch consume <id> ; then continue: <topic>."
  ```

  - **`proposed`** (not `queued`): a handoff is a draft the operator resumes
    deliberately; `proposed` tasks are never auto-claimed by another agent.
  - **`--label handoff`** + **`--target-worktree`** pin it to *this* worktree —
    the marker `/resume-handoff` (and the Worktree Picker's handoff views) filter
    on. Resolve `<worktree_id>`/`<machine>` from the CWD (`agent-worktrees get
    worktree-dir` basename / `... get machine`). If you can't resolve a worktree,
    use the file flow instead — never file an unpinned, anyone-can-claim handoff.
  - **`--dedup-key handoff-<sessionId>`** makes re-running `/handoff` in the same
    session idempotent.

- **No coordinator:** write the file to
  `~/.copilot/session-state/<sessionId>/files/<sessionId>-prompt.md` and reply
  with `Read the handoff at <path> and continue: <topic>.`

---

## Live-Cutover Handoff — the primary path (hand off *and continue* in place)

A handoff is a **takeover in place**: the session **spins up its own successor
in the same mux** and retires itself, hands-free — no paste prompt for the
operator to copy. This is the **default** handoff flow whenever the session runs
under a `wt-<id>` mux (the norm: picker-launched or `embody`-spawned sessions).

**Primary, not opt-in.** A plain `/handoff` performs a cutover when it can; the
`/handoff-continue` command and phrases like "hand off and continue" are just
explicit ways to ask for the same thing. It kills the live session once the
successor is up, so it degrades gracefully (never destructively) when no mux is
present — *then* it falls back to the paste reply.

**What happens** — two explicit tool calls: `save_handoff_prompt` **stores** the
handoff, then `continue_handoff` **kicks the cutover** (the mux choreography
lives in `agent-worktrees handoff-cutover`; you just make the calls):

1. You compose the handoff markdown as usual, then call **`save_handoff_prompt`**
   (`prompt_text` + a short `title`). It stores the handoff (agent-dispatch task,
   else file) and returns the paste reply **plus a `HANDOFF_SEED:` line** — the
   **deferred** cutover seed.
2. You call **`continue_handoff` with `seed` = that exact HANDOFF_SEED string**.
   It spawns a **successor Copilot** in a **new window of this worktree's
   `wt-<id>` mux session**, booted the same way the picker boots one but seeded
   with `-i "<seed>"` (interactive — never `-p`), and **cuts the operator over**
   to it.
3. This (old) session **retires itself on the next `session.idle`** (agent-stop
   of the turn) via a **double-Ctrl-C ~600 ms apart** to its own pane — Copilot's
   native clean quit (a single Ctrl-C does little). Only the successor remains.

**The cutover successor completes explicitly (deferred).** Its seed uses
`agent-dispatch consume <id> --defer-complete` (load brief + take ownership, but
NOT complete); it works the handoff and runs `agent-dispatch complete <id>`
itself only when it reaches the goal — so the handoff task's `completed` state
means *the work is done*, not that a baton was handed over.

**Your job for a live cutover:** `save_handoff_prompt` → `continue_handoff(seed=
<HANDOFF_SEED>)` → read the confirmation → **end your turn**. Do not start new
work; this session quits itself. The successor picks up the handoff on its seeded
first turn.

**Graceful fallback.** If the session is **not running under a mux session** (or
the cutover fails), `continue_handoff` does nothing destructive and says so — the
handoff is still safely stored by `save_handoff_prompt` and resumable the
ordinary way: reply with the short paste prompt (into `/clear`, or
`/resume-handoff`). That paste path is the human quick-baton (completed on
pickup).

---

## Resuming From a Handoff

A handoff is **not** auto-loaded. How you resume depends on which form the
previous session produced — but **both run in the same, foreground Copilot CLI
session** you're sitting in (a handoff is continued by *you*, in a fresh context
window, **never** by a spawned background ACP agent unless the operator
explicitly asks):

1. **agent-dispatch form** (the default when a coordinator is running). The
   previous session's reply was the resume seed `You are resuming a handoff
   (agent-dispatch task <id>) … run: agent-dispatch consume <id>`. Resume either by:
   - running **`/resume-handoff`** (no argument) after `/clear`/`/new` in the
     same worktree — the extension consumes this worktree's pending handoff task
     and **injects its continuation prompt** as your next turn (see below); or
   - pasting that resume seed, which loads the full brief AND marks the task
     completed with one command (`agent-dispatch consume <id>`) and tells you to
     continue in place.
2. **File form** (the fallback when no coordinator was running). The reply was
   `Read the handoff at <path> and continue: …`; pasted into a new session, it
   names the file and tells you to read it and continue.

> **A handoff is in-place: same worktree, new session.** The point of a handoff
> is to continue *this* work with a fresh context window, so the new session
> runs in the **same worktree** as the one that wrote the handoff. **Never write
> (or follow) a handoff that says "create / build on a fresh worktree"** -- the
> operator owns local worktrees and an agent does not spin one up as a
> continuation of its own work (see the **`worktree`** skill). If a PR merged in
> the previous session, the next session simply syncs the worktree forward
> (`agent-worktrees git sync`) and keeps going.

### `/resume-handoff` — an injected slash command (extension-provided)

**`/resume-handoff` is a real slash command registered by the context-handoff
extension — not a skill gesture.** When the operator runs it (no argument) in the
target worktree, the *extension* does the work and **injects the handoff's
continuation prompt into this session as your next turn**. You don't run any
commands — you simply receive the injected handoff and continue the work in this
foreground session (never a background agent).

What the extension does under the hood, in order:

1. **agent-dispatch task (preferred).** If a coordinator answers, it finds this
   worktree's newest `proposed`, `handoff`-labeled task (matching
   `target_worktree`), **consumes** it (approve → claim → start → complete — a
   handoff is a baton, delivered once picked up), reads its payload, and injects
   that payload framed as "you are resuming a handoff; continue in place."
2. **Session file (fallback).** If no coordinator (or no matching task), it finds
   the newest session-folder `*-prompt.md` whose recorded CWD matches this
   worktree and injects its contents the same way.
3. **Nothing found.** It tells the operator (a log line); paste a handoff prompt
   directly instead.

So your job on resume is just: **read the injected handoff and keep going.** The
task is already marked complete by the time you see it — don't re-claim or
re-complete it; the continuation *work* is tracked by its effort/issue, not the
handoff task.

> The command needs the context-handoff **extension** loaded (it registers the
> slash command). If the extension isn't loaded, `/resume-handoff` won't exist —
> resume by pasting the previous session's reply prompt instead (the resume seed
> `You are resuming a handoff (agent-dispatch task <id>) … run: agent-dispatch
> consume <id>` or `Read the handoff at <path> …`). When you *do* get a pasted
> agent-dispatch resume seed, act on it directly: `agent-dispatch consume <id>`
> loads the brief **and** marks the handoff completed in one shot (idempotent —
> safe to run even if a live cutover already completed it). If you only want to
> read without consuming, `agent-dispatch payload <id> --raw`.

### If the user says "pick up from last session" with no pasted prompt

The previous session's handoff was not pasted in. Fall back to
`session_store_sql` to find the most recent session for this repo/worktree and
summarize what was worked on, or look for a
`~/.copilot/session-state/<id>/files/<id>-prompt.md` file if you know the id.

---

## Handoff Template

Compose this markdown and pass it to `save_handoff_prompt` as `prompt_text`
(it becomes the agent-dispatch **task payload**, or the file contents in
fallback mode). Full template:
[`references/handoff-template.md`](references/handoff-template.md). Its sections:

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

- **The handoff CONTENT can be as long as needed** — whether it lands in the
  task payload or a file, it's read on demand when the next agent resumes, not
  injected into any context automatically. Capture direction/motivation, next
  actions, and target goals in full.
- **The inline REPLY prompt must be short — one or two sentences.** It is
  addressed to the **next agent** and is whichever form `save_handoff_prompt`
  returned: the agent-dispatch resume seed `You are resuming a handoff
  (agent-dispatch task <id>) … run: agent-dispatch consume <id> ; then
  continue: <topic>.` (task) or `Read the handoff at <path> and continue:
  <topic>.` (file). It is copy-pasted verbatim into `/clear` (or `/new`); keep
  it scannable and do **not** repeat the handoff contents.
- **Lead with the original topic.** The "Original Request" must reference the
  session's founding purpose, not just recent activity.
- **Be specific.** "Fix the auth bug" is useless. "JWT refresh in
  `src/auth/token.ts:142` has a race — mutex added but error handler uses old
  non-awaited path" is useful. Include file paths, what failed, and the why.
- **Never claim auto-pickup.** A handoff is never loaded automatically on
  restart. Do not imply Copilot will resume on its own — the user resumes it
  (`/resume-handoff`, or paste the reply prompt).
- **One home, not two.** A handoff lives in **one** place — the agent-dispatch
  task when a coordinator is running, else a session file. Don't write a file
  *and* a task. In file mode, keep the file in
  `~/.copilot/session-state/<sessionId>/files/` (never commit it or write it
  elsewhere); in task mode there is no file. Show the reply prompt to the user;
  don't hide it in a tool call.

---

## Integration Notes

- **Storage is mode-dependent.** `save_handoff_prompt` prefers an agent-dispatch
  task (payload = the markdown, no file) and falls back to a session file at
  `~/.copilot/session-state/<sessionId>/files/<sessionId>-prompt.md` only when no
  coordinator is reachable. It returns the exact short prompt to reply with in
  either case. Pass the markdown as the **`prompt_text`** argument (the `prompt`
  alias is also accepted) plus an optional short **`title`** (task mode only).
