# Thread register

Back to [Monitor agent fleet](../SKILL.md).

A sustained monitor watches many agents and also receives asynchronous operator
messages: one-shot questions, follow-ups to earlier steers, charter extensions
and tangential side-quests. Without a durable organization each message pulls
the monitor in a new direction and the main analysis is lost. The thread
register keeps every line of work visible, owned and bounded.

The **main thread** is the ongoing analysis of how to keep agents on track and
how to carry learnings into later rounds. The **self-improvement thread**
collects the monitor's own weak points (goal G4). Everything else either closes
the turn it arrives in, attaches to an existing thread, or is delegated.

## The register

Keep the register in a monitor-owned notes file in the supervisory record (for
example the session's private files folder), and reference it from the
monitor's checkpoint so a successor inherits it. It is private working state,
not repository content. One row per thread:

| Column | Content |
|---|---|
| ID | Short stable identifier, unique within the register (e.g. `T1`) |
| Kind | `main`, `self-improvement`, `charter`, `steer-followup`, `side-quest` or `one-shot` |
| Origin | The operator message that opened it (exact reference or quoted wording) and its time |
| Owner | The monitor, or the delegated agent / sub-agent and how to read its state |
| Status | `open`, `waiting` (with on what), `delegated`, `closed` |
| Next check | Time or event at which the monitor next looks at it |
| Exit criterion | The observable condition that closes it, written when the thread opens |

Seed every register with the `main` and `self-improvement` threads. Closed rows
stay until the next sweep report names them, then may be pruned.

## Classify every operator message on arrival

Classify before acting, and record the classification in the register:

| Kind | Handling |
|---|---|
| One-shot | A question answerable now. Answer it and close it in the same turn as one minimal closed row (origin, answer pointer, `closed`) |
| Charter | Extends or narrows the main thread's scope. Update the charter through its existing owner record (preserving the original source and any holds) and note the extension on the `main` row |
| Steer-followup | Concerns an existing thread or an earlier steer. Attach the message to that thread's row and update its next check or exit criterion; never open a duplicate |
| Side-quest | Tangential work the operator wants done. Delegate it to a bridge agent or sub-agent within existing authority, record that owner and the exit criterion, and only monitor it; the monitor does not do the side-quest itself |

When a message is ambiguous, pick the least disruptive kind (attach before
open, delegate before doing) and say which kind you chose in the reply. A
side-quest that needs authority the monitor lacks is reported with a
recommendation, not started.

## Sweep and reply discipline

- **Walk due threads first.** Each sweep begins by walking every thread whose
  next check is due, before any ad hoc work, and updates its status and next
  check.
- **Name the threads that moved.** Every reply to the operator lists which
  threads changed (ID and new status), so the operator can follow progress
  without rereading history.
- **Close only on the exit criterion.** A thread closes only when its
  recorded exit criterion is observed. A delegated owner reporting done, a
  delivery receipt or elapsed time is not closure; reobserve the criterion.
  Changing an exit criterion is a charter change and is noted on the row.
- **Weak points go to the self-improvement thread.** A monitor weak point found
  during a sweep (a failed reader, a wrong credential reference, a blind spot)
  is recorded against the `self-improvement` thread and handled through
  [self-improvement](self-improvement.md), not fixed in passing.

The register organizes the monitor's own work. It grants no authority: steering,
answer, delegation and source-change scopes still come from the charter.
