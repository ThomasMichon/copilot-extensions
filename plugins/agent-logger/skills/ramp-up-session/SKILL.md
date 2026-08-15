---
name: ramp-up-session
description: >
  Take over a dormant Copilot session from its on-disk state. Use this skill
  when a session cannot be resumed (a wedged CLI, a machine restart, an
  abandoned worktree) but its work should continue. It discovers the worktree's
  most recent session, collates its events.jsonl into a digest, and reconstructs
  where it left off so a fresh session can pick up the torch. By default it
  **delegates the context-expensive ramp-in to the `session-rampup` sub-agent**,
  which returns a compact takeover briefing so the dormant session's transcript
  never floods the main session.
  Trigger phrases include: - 'ramp up on <worktree>' - 'take over that session'
  - 'resume the dormant session' - 'pick up where the old session left off'
  - 'session takeover' - 'I can''t resume the session, continue its work'
---

# Session Ramp-Up

Resume the *work* of a session that can no longer be resumed the normal way.
Every Copilot session records its raw event stream at
`~/.copilot/session-state/<id>/events.jsonl`; this skill turns that stream into
a compact takeover brief and hands your current session the torch.

It reuses the segmenter engine wholesale — `ramp-up-session` is a thin front
end over the same `collate-session` machinery, adding only worktree-scoped
session discovery. The collated digest is written **ephemerally** (to
`$TEMP/session-digest/<id>/`), never to the persistent digest store.

## When to use

- The CLI is wedged / never reaches "ready", so `--resume` won't work.
- A machine restart killed a session mid-task.
- You want to continue a different worktree's abandoned session from a fresh
  one.

## Preferred: delegate the ramp-in to a sub-agent

**Ramping in reads a potentially huge transcript. Do it in a sub-agent, not in
your own context.** A dormant session can carry hundreds of turns; pulling that
into the main session defeats the purpose (you'd flood the very context you're
trying to preserve). Delegate the expensive reading to the neutral
**`session-rampup`** agent, which absorbs the bulk and returns a **compact
takeover briefing** (target ≤ ~6k tokens). It is the context firewall.

### 1. Identify the dormant worktree

You just need the worktree's short **suffix** — the last segment of its name
(e.g. `fbc5` from `anomalous-potato-win-20260724-120542-fbc5`). A full path or `.`
(current directory) also work. If the worktree lives on **another machine**,
note that machine's name. If the operator hasn't said which worktree, ask.
Optionally note a **focus** (what the operator wants to resume).

### 2. Spawn the `session-rampup` agent (sync)

Delegate with the `task` tool, `agent_type: "session-rampup"`, `mode: "sync"`.
Put the worktree suffix (and optional machine / session id / focus) in the
prompt, e.g.:

```
Ramp into the dormant session for worktree <SUFFIX> [on machine <NAME>].
[Optionally: session <UUID>.]
Focus: <what to resume, if the operator said>.
Return the bounded Ramp-Up Briefing.
```

The agent runs `ramp-up-session <suffix> [--machine <name>]`, reads the digest
surgically (`read-session-digest`), inspects the worktree's git state,
reconciles intent vs. reality, and returns only the briefing — no raw
transcript.

### 3. Take over

Read the returned briefing (it's small by design). Present a few-line situation
summary to the operator — what the session was doing, what landed, what remains
— then **continue the work** from where it stopped. For any detail the briefing
flags, call `read-session-digest <id> ...` yourself (see below) rather than
re-reading the whole session. If the takeover needs a decision only the operator
can make, surface it; otherwise proceed.

## Doing it inline (small sessions, or no delegation)

Run the tool in **this** session only when the dormant session is small, or a
sub-agent isn't available. Otherwise prefer delegation above — a large
transcript read inline will flood this session's context.

### 1. Identify the dormant worktree

You need the worktree's short **suffix** (e.g. `fbc5`), a full path, or `.` for
the current directory. If it lives on another machine, note that machine's name.
If the operator hasn't said which worktree, ask.

### 2. List candidates (optional but recommended)

```
ramp-up-session <suffix> --list
ramp-up-session <suffix> --machine <name> --list     # a worktree on another host
```

`ramp-up-session` is deployed as a binstub in `~/.local/bin` by the
agent-logger installer. If it is not on PATH (payload installed but the runtime
installer hasn't run, or `~/.local/bin` isn't on PATH), invoke it via the
deployed venv interpreter instead:

```
# POSIX
~/.agent-logger/.venv/bin/python -m agent_logger.segmenter.ramp_up <suffix> --list
# Windows
~/.agent-logger/.venv/Scripts/python.exe -m agent_logger.segmenter.ramp_up <suffix> --list
```

A bare suffix is hunted down in the local session store by matching worktree
directory names ending in `-<suffix>`. With `--machine <name>` naming another
host, the hunt is delegated over `ssh <name>` (a session's raw data lives on the
machine that produced it). This enumerates the matching sessions, most recent
first. Pick the one to take over (usually the most recent).

### 3. Produce the takeover brief

Ramp up the most recent session (omit `--list`), or a specific one with
`--session <id>`:

```
ramp-up-session <suffix>
ramp-up-session <suffix> --machine <name>            # a worktree on another host
ramp-up-session <suffix> --session <id>              # a specific session
ramp-up-session <suffix> --tail-turns 10             # surface more trailing turns
```

The brief contains:

- **Metadata** — session id, branch, working dir, head commit.
- **Checkpoints** — the CLI's own pre-compaction summaries (the strongest
  signal of accumulated work), when present.
- **Session stats** — turns, tool calls, failures, checkpoints.
- **Where it left off** — the last few turns verbatim (last user ask, the
  assistant's trailing actions, and any in-flight or failed tool calls).

Read the whole brief. It is your situational handoff.

### 4. Go deeper if needed

The full transcript was collated ephemerally. Read more with the existing
digest reader (no multi-machine system paths, temp-store aware):

```
read-session-digest <id> context
read-session-digest <id> list
read-session-digest <id> segment <N>
read-session-digest <id> grep --pattern <regex>
```

Use `grep` to find the last decision, an error, a file path, or the task the
session was on.

### 5. Reconcile against the worktree's real state

The brief tells you what the session *intended*; the worktree tells you what
actually **landed**. Before continuing, inspect the worktree itself:

```
git -C <worktree-path> status
git -C <worktree-path> log --oneline -n 15
git -C <worktree-path> diff            # uncommitted work in flight
```

Match the tail's in-flight actions against committed vs. uncommitted state so
you don't redo finished work or drop unfinished work.

### 6. Pick up the torch

Summarize the situation back to the operator in a few lines — what the session
was doing, what has landed, and what remains — then **continue the work** from
where it stopped. If the takeover requires a decision only the operator can
make, surface it; otherwise proceed.

## Notes

- **Discovery matches the worktree root**, not a subdirectory. If no sessions
  are found, confirm you passed the worktree root (the path in the session's
  `workspace.yaml`).
- **Quip/sub-agent temp sessions are excluded** automatically.
- **Nothing is persisted** to the digest store; the digest under
  `$TEMP/session-digest/<id>/` is scratch and safe to delete.
- `--json` emits a machine-readable summary (session id, digest dir, counts)
  instead of the Markdown brief, for programmatic callers.
