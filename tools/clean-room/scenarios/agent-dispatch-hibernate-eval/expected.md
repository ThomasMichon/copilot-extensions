# agent-dispatch-hibernate-eval — expected outcome (judge rubric)

This is the rubric for `clean-room-judge` to score the driven-agent transcript
under **literal mode**. It elaborates `manifest.json`'s `expected_outcome`.

## The task the agent was given

> You are driving a goal to completion in the worktree `clean-room/hibernate-worker`.
> The next step cannot proceed until a slow external condition resolves. That wait
> is represented by this single blocking command, which returns ONLY once the
> condition is released:
>
> `/home/operator/.cr-signals/hibernate-signal-wait.sh`
>
> Following ONLY agent-dispatch's OWN documentation, hand this blocking wait to the
> layer in the cheapest way the plugin supports — do NOT sit on this live session
> holding the wait, and do NOT invent your own waiting mechanism. … Then report
> exactly what command you ran.

The starting state (from `setup.sh`): agent-dispatch is installed **solo** and
first-session-provisioned (binstub on PATH); a git-init'd worker worktree exists at
`~/hibernate-worker`; and a **caller-controlled signal** `hibernate-signal` is armed
— a truly-blocking wait (a FIFO read, no CPU spin) whose **only** release is the
harness (`post_check.sh` calls `cr_signal_fire`). The point is to audit whether
agent-dispatch's **own docs** carry a fresh agent to *hibernate-the-wait*: hand the
provided blocking wait to a **detached, tokenless** waiter and let the session be
torn down, rather than busy-waiting or improvising.

## Why a caller-controlled signal (the instrument)

An internally-timed wait (a plain `sleep N`) can be faked and masks whether the
agent truly hibernated. Because the **harness owns the wake edge** (`cr_signal_fire`
is called only by `post_check.sh`, after it has observed the suspend), hibernation
is objectively observable: a live waiter after the turn is a genuine hand-off, and
any resolution/progress the agent claims *without* a waiter is proof it did not
suspend.

## Intended literal path

1. **Discover the docs.** The agent finds agent-dispatch's own *hibernate-the-wait*
   documentation (SKILL "Hibernate the wait" and/or the README section) rather than
   guessing at a waiting strategy.
2. **Identify the command.** The documented form is
   `agent-dispatch run --detach --resume <machine/worktree> -- <blocking-cmd>`
   (the non-detached `run --resume … -- …` foreground form is also legitimate).
3. **Hand off the PROVIDED wait.** It passes exactly
   `/home/operator/.cr-signals/hibernate-signal-wait.sh` as the wait command,
   resume-affinitied to `clean-room/hibernate-worker`.
4. **Suspend.** It ends the turn without running/resolving the wait itself — the
   worker is "suspended," a detached waiter owns the wait.

## PASS

The run PASSES if the agent **discovered hibernate-the-wait in agent-dispatch's own
docs** and handed the provided blocking wait to
`agent-dispatch run --detach --resume clean-room/hibernate-worker -- /home/operator/.cr-signals/hibernate-signal-wait.sh`
(or the non-detached `run --resume … -- …` form), leaving a live detached waiter and
**not** running/resolving the wait in-session. The objective corroboration in
`cr-report.json` (from `post_check.sh`):

- `post_detached_waiter: present`
- `post_agent_dispatch_run: yes` (the waiter is owned by an `agent-dispatch run`
  process — the documented mechanism)
- `post_waiter_detached: yes` is *supporting* evidence of a true `--detach`
  (foreground `run --resume` legitimately shows `no` — weigh with the transcript).
- `post_waiter_released_on_fire: yes` confirms the caller-owned wake edge worked.

Stopping at the documented hand-off is a **success of the docs**.

## FALSE-PASS → FAIL (the tripwires)

The run is a **FALSE-PASS (→ FAIL)** if the agent reached a "waiting/handled" end
state by **improvising** instead of using the documented mechanism. Concretely, any
of:

- **Ran the wait script directly** (foreground) — sat on the live session holding
  the wait.
- **Busy-waited in-session** — a `sleep`/`while`/poll loop in the session itself.
- **Hand-rolled its own background waiter** — `nohup … &`, a bare `&`, `setsid`, or
  a self-authored waiter script — instead of `agent-dispatch run`.
- **Swapped in its own timer** — replaced the provided wait with a `sleep N` or a
  different condition.
- **Claimed the goal "done"** without ever suspending on the wait.

`post_check.sh` records the objective tripwires: `post_detached_waiter: absent`
combined with a transcript that shows in-session waiting, `post_agent_dispatch_run:
no` while a waiter nonetheless exists (hand-rolled), or `post_own_waiter_script`
naming an agent-authored waiter. Cross-check them against the transcript.

## Fix owner

A confirmed FALSE-PASS means agent-dispatch's docs did not make *hibernate-the-wait*
**discoverable/actionable** enough for a fresh agent — the finding flows back to
**agent-dispatch** (docs / SKILL discoverability), not to the clean room.

## Scope note (Slice 1 — suspend-only)

This scenario validates the **suspend** half end-to-end: the agent hands the wait to
a detached tokenless waiter, and the harness proves it owns the wake edge by firing
the signal and confirming release. Per the docs the resume nudge is **best-effort**,
so a full **resume-into-the-same-session round-trip** (the detached waiter's
agent-bridge nudge landing back in the driven worktree) is **Slice 2**, gated on a
live bridge — out of scope here. A failed/absent resume nudge is therefore **not** a
FAIL in this scenario.

## Inconclusive

If the transcript is truncated or the agent's stated command is missing, mark the
affected step `INCONCLUSIVE` and name the artifact that would settle it (usually
`eval/transcript.txt` for the full turn, or `cr-report.json` for the post-check
signals).
