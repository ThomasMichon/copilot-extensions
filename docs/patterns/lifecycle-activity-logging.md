# Pattern: lifecycle-activity-logging

**Serves:** *Vision plugins/agent-worktrees* — the ground layer "owns the truth
about what worktrees exist, what each agent is doing, and whether a session is
live," including **the event log** and the **lifecycle hooks** that produce it.
**Exemplars:** `activity.py` (the persistent activity log), `launch-session.sh`
and `launch-session.ps1` (the launcher writers), the session register/deregister
hooks.

## Problem

A worktree session moves through many processes — binstub, picker, launcher,
multiplexer server, the Copilot process, session hooks, post-exit finalization,
and the background reaper. When something goes wrong for one worktree ("Ctrl+C
killed the pane," "the session never registered," "the launcher was reaped out
from under a live session"), an operator must **reconstruct that one launch flow
after the fact** across every process that touched it.

That is only possible if the lifecycle emits a **consistent, durable, joinable**
trail. Ad-hoc logging that differs per platform, per process, or per author does
not compose into a trace — the exact failure mode this pattern prevents (a real
regression once reaped live sessions on Windows, where the launcher wrote nothing
durable and the timeline had to be inferred by hand).

## The two tiers

Logging is split into two deliberately different tiers. Know which you are
writing to.

| Tier | File | Format | Scope | Retention | Survives reboot |
|------|------|--------|-------|-----------|-----------------|
| **A — activity log** | `~/.agent-worktrees/logs/activity.jsonl` (`cfg.install_dir()/logs/`) | one JSON object per line | machine-global, all worktrees | age-based, 7-day rolling window | **yes** |
| **B — setup log** | `<tmp>/worktree-setup-logs/setup-<pid>.log` | line-oriented text | one launcher process | count-based, newest 10 kept; also cleared on reboot | no |

- **Tier A is the durable record of record.** It carries **high-level** lifecycle
  events only (a worktree was created, a mux session attached, Copilot exited).
  It is the trail an operator queries days later with `agent-worktrees activity`.
- **Tier B is the verbose per-launch trace.** It carries the fine-grained
  step-by-step detail of a single launcher run (mux resolution, update-stage
  join, attach/detach reasons). It is intentionally ephemeral scratch, not a
  record of record.

Do not put verbose step spam in Tier A, and do not rely on Tier B for anything
after ~10 subsequent launches or a reboot.

## Standard approach

### 1. One writer API, arbitrary context

All Tier-A writes go through **`activity.log_event(event, *, worktree_id,
session_id, source, launch_id, **fields)`** (Python) or, from a shell launcher,
the **`agent-worktrees activity-log <event> --field k=v`** binstub that calls it.
Never hand-format a JSONL line. Extra context is passed as keyword fields (Python)
or repeatable `--field key=value` (shell); `None`-valued fields are dropped.

### 2. Logging never breaks the lifecycle (fail-silent)

A diagnostic write **must never** raise into the flow it observes. `log_event`
swallows every exception; shell writers run **detached and best-effort**
(`( … & )` / `Start-Process`), redirect their own output, and never gate the
launch on the log succeeding. A launcher that kills the terminal because its
logging call threw is a contradiction of this pattern.

### 3. Cross-platform parity (binding)

The Windows (`launch-session.ps1`) and POSIX (`launch-session.sh`) launchers emit
**the same Tier-A events at the same lifecycle points**. A mark that exists on one
platform but not the other is a parity defect — the durable trail must look the
same on every machine (per the [cross-platform-parity](cross-platform-parity.md)
invariant). New marks are added to **both** launchers in the same change.

### 4. Symmetric start/end marks

Every flow stage that can fail emits a **start** mark and a terminal
**end/result** mark, so a missing end mark is itself the signal that a stage hung
or the process died mid-stage. A stage that only logs its happy-path completion
cannot be distinguished from one that never ran.

### 5. Correlation by `launch_id`

`pid` is **not** a flow key — each stage runs in a different process. One launch
flow is tied together by a **`launch_id`**: a short unique token minted **once**
at launcher entry, exported (`WORKTREE_LAUNCH_ID`), threaded into the multiplexer
server's environment (so the pane and its hooks inherit it), and stamped on every
Tier-A record the flow produces (launcher marks, `session_started` /
`session_ended`, post-exit). `agent-worktrees activity --launch-id <id>` then
returns the whole flow deterministically, instead of guessing by
`worktree_id` + timestamp.

The **`launcher_started`** event additionally records the **Tier-B setup-log
path** (`setup_log=`), so a Tier-A anomaly links straight to its verbose Tier-B
trace — the one bridge between the two tiers.

### 6. The Tier-A event schema

Every record carries a fixed spine plus event-specific fields:

| Field | Meaning |
|-------|---------|
| `ts` | UTC ISO-8601, seconds precision |
| `event` | one of the documented event names |
| `worktree_id` | the worktree the event concerns |
| `session_id` | the Copilot session id, when known |
| `launch_id` | the launch-flow correlation id, when known |
| `pid` | the **logging** process's pid (diagnostic, not a flow key) |
| `host` | machine hostname (fleet disambiguation) |
| `source` | originating component: `python` or `launcher` |
| *extra* | event-specific: `branch`, `exit_code`, `mux`, `reason`, … |

Event names are **stable snake_case identifiers** and machine-parsed — keep them
ASCII (no glyphs). Add a new event name to the `activity.py` module docstring
when you introduce one.

### 7. Retention/cleanup is owned by the tier, not the caller

A caller never prunes. **Tier A** self-prunes inside `log_event`: a rewrite that
keeps only lines within the retention window, attempted only once the file grows
past a size threshold (so the common append stays cheap), best-effort, unparseable
lines preserved. **Tier B** is pruned at launcher start (keep newest N by mtime)
and is additionally reboot-volatile by living under the OS temp dir. Changing a
retention policy is a change to the owning tier's module/launcher, never a
per-call concern.

## Rationale

Splitting durable-coarse from ephemeral-verbose keeps the always-queryable record
small and bounded while still capturing deep per-launch detail when it is fresh.
A single writer API with fail-silent semantics guarantees the observer can never
harm the observed flow. Parity + symmetric marks + a `launch_id` are what turn a
pile of events into a **reconstructable trace** — the whole point of lifecycle
logging is answering "what happened to *this* launch," and that requires the
events of one flow to be durable, uniform across platforms, and joinable by a key
that survives the process boundaries the flow crosses.

This pattern is consistent with the ground-layer vision's **bounded, incremental**
reading discipline: the activity log is a growing dataset read by
cursor/watermark (`--since`, `--launch-id`, `--lines`), never a continuous full
sweep.

## See Also

- Intent: [`visions/plugins/agent-worktrees/`](../../visions/plugins/agent-worktrees/README.md)
  — the ground-layer authority for session state, the event log, and lifecycle hooks.
- Hub: [`docs/patterns/`](README.md) · Parity: [cross-platform-parity](cross-platform-parity.md)
- Reality: the agent-worktrees plugin `docs/` and `activity.py` module docstring
  (the authoritative event-name list).
