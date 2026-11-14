# Parked questions (pending input)

Back to [Monitor agent fleet](../SKILL.md).

A worker parked on an interactive question (a form, elicitation or
ask-the-user prompt) is the most common fleet blocker. It stops all progress
until someone answers, and the operator is often away. Most such questions
are routine decisions the worker already had authority to make. Treat every
parked question as a finding; answer one only under an explicit answer scope.

## Detect

- Read the transport's pending-input or attention field in the no-wake result
  snapshot. A session can report a running or active turn state while it is
  parked on a question; **turn state alone does not rule out pending input**.
- Record the question text, offered options, any default, the exact request
  or tool-call identifier, and when it was asked.
- A question already answered or withdrawn by its owner is closed; do not
  report it again.

## Classify

Judge the question against the worker's own charter, not the monitor's
convenience.

| Class | Examples | Monitor action |
|-------|----------|----------------|
| Routine, within the worker's delegated authority | design or ordering choice, retry or validation depth, whether to continue the assigned plan, stopping its own helpers, picking a documented option that already names a default | Answer under the answer scope; otherwise report with the recommended answer |
| Operator-only | destructive effects, new access or spend, objective change, deployment, permission or approval prompts, raising its own caps, budgets or review-round limits, conflicting operator instructions | Never answer. Report once with the evidence, a recommended answer and its default |
| Unclear | the authority boundary or intent cannot be established from the record | Treat as operator-only |

A routine question with no default is also a guidance finding: the worker
should have decided, or asked with a default and a revisit time.

## Answer within an answer scope

Answering is an effect on another owner's session. It needs a recorded
**answer scope** in the charter (recipients, permitted question classes,
validity); a read-only or steering grant does not authorize answers.

1. **Recheck.** Re-read the session immediately before answering: the same
   request is still pending, the head is unchanged, and no later hold or
   operator reply applies.
2. **Persist first.** Record the intended answer, the request identifier, the
   cited authority and the time in the supervisory record. If persistence
   fails, do not answer.
3. **Use the supported answer path.** Answer through the transport's
   documented answer operation with the exact request identifier. Prefer an
   option the question offered; keep free text brief and cite the charter
   source.
4. **Record the result.** An accepted answer is delivery, not progress.
   Reobserve at the next checkpoint. An unknown result is reconciled through
   the transport owner, never by blind re-answer.

## When no supported answer path exists

**Never inject keystrokes, drive a terminal or impersonate the operator** to
clear a question. Record the missing answer path as a coverage gap. Report
the question to the operator once with a ready-to-paste answer and its
default, and include it in the sweep report until it is answered.

## Relay operator answers at class scope

When the operator answers a parked question, relay it at the scope of the
decision class, not the narrow invocation the worker asked about. Before
relaying a worker's question to the operator, rewrite options the worker
narrowed on its own (one attempt, a pin, an invented expiry) into a broad or
standing option beside any narrower one, and state the current state plainly.
An answer such as "yes until done" or "as much as it wants" is a standing grant
for that class until the task completes or the operator narrows it; relay it
that way and record it in the worker's charter, not only the monitor's note.

## Prevent repeat questions

When the same worker parks on routine questions repeatedly, steer it (under
the steering scope) to decide routine matters itself and to ask future
questions with a recommended default and a revisit time, then continue other
work. Record the pattern as a guidance weak point for its owner.
