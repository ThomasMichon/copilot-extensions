# Task Outputs and Review - Vision

- **Subject:** Durable, contract-bearing task conversations and outcomes.
- **Scope:** leaf (child of [agent-dispatch](../README.md)).
- **Status:** Draft
- **Last revised:** 2026-10-08
- **Reality docs:** `plugins/agent-dispatch/src/agent_dispatch/`
  (`queue_steering.py`, `queue_lifecycle.py`, `queue_completion_review.py`,
  `wake.py`) and the Tasks pivot in `worktree-manager/`.

## Purpose & Intent

A task is an asynchronous conversation with a durable outcome, not merely a
prompt that ends when a worker stops. An operator can ask a simple question,
receive a formatted answer, review it at leisure, and later start a follow-up
from its outcome and history. The same contract supports unattended emitters
and deterministic evaluators without making a live agent wait for a person.

## Concepts & Components

- **Output:** formatted content for people and optional structured data for
  machines, usable both in a submission and beside an input request.
- **Output contract:** a task-owned, versioned runtime validation contract
  agreed before work, separate from the prose criteria for a correct outcome.
- **Submission:** a worker's proposed final answer, not a demand for input.
- **Confirmation authority:** the operator or evaluator designated by the
  task's policy, independent of the submitting agent.
- **Conversation history:** durable revisions of outputs and input requests,
  their answers, and the review decisions that connect them.
- **Follow-up:** a new task explicitly derived from a completed task, preserving
  lineage without changing the completed task's accepted outcome.

## Features

### formatted-and-structured-output

Agents can publish standard Markdown and optional structured data as part of
either a submission or a steering request. People see the formatted answer;
evaluators consume the structured output without extracting meaning from
display prose. An input request retains its structured answer form alongside
the agent's output; output data and requested input are not confused.

### task-owned-output-contract

A caller can declare a JSON Schema output contract before the task is worked.
The coordinator enforces it at runtime across every write surface. A schema
version remains fixed for the work it governs; changing it is explicit and
cannot retroactively invalidate or reinterpret earlier output. Static client
types may derive from the same authority but cannot replace runtime validation.

### uniform-submit-independent-confirm

Workers uniformly submit, never confirm their own tasks. A submission releases
its execution slot and remains reviewable without retaining a live worker.
Emitters explicitly declare whether their default evaluator auto-confirms;
manual tasks default to operator approval, with an explicit per-task opt-out.
The effective confirmation policy is captured at creation. Automatic
confirmation still requires contract-valid output and remains a distinct,
attributable evaluator decision.

### review-exact-submission

Operators and evaluators accept or reject the exact submission they inspected.
Acceptance makes the task completed. Rejection returns the same task to
eligible work with the feedback and prior progress available to its next
worker. Neither rejection nor resubmission erases a previous answer. A stale
review cannot approve a newer output by accident.

### durable-conversation-history

Every published input request, agent output, operator answer, and review
decision is durably inspectable in order, linked to the revision it concerns.
The current card is a convenient projection, never the only surviving record.
Submitted and completed tasks retain their final formatted output and permit
browsing earlier exchanges after processes, ownership, and services change.

### follow-up-without-uncomplete

Completed tasks do not reopen. An operator can explicitly create a new task
using the accepted outcome, relevant history, and new instructions as context.
The old task remains completed and unchanged; the new task has its own goal,
contract, review policy, and visible connection to its predecessor.

### durable-fast-steer-acceptance

Confirming an input card records the answer and pending delivery durably, then
acknowledges acceptance without waiting for worker startup or a reply. A
confirmed answer makes its task eligible for queued or claimed continuation,
not falsely started merely because feedback was saved. Existing assignment
and worktree context survive; another worker cannot overlap a retiring one.
Retries resolve to the same accepted operation rather than duplicate answers.

### affirmative-asynchronous-delivery

Dispatch ensures accepted steering reaches its intended task incarnation,
whether by waking a stopped worker or nudging a live one. Transport acceptance
and worker consumption are distinguishable; unresolved delivery is visible
and recoverable rather than mistaken for successful receipt. A resume without
new work, an answered request, or a resolved monitored condition is not useful
delivery and must not churn agents back into sleep.

## Behaviors

### validation-is-not-verification

Contract validation proves the declared output shape, not that the answer
meets the goal. Invalid output fails explicitly before submission or card
publication, and never reaches automatic confirmation. An evaluator outage
leaves a submission pending, not accepted.

### diagnostics-not-conversation-noise

Normal diagnostic logs correlate acceptance and each delivery attempt with
timing, outcome, and failure cause, including worker acknowledgment. Transport
retries do not become new conversation entries. Logs do not disclose answer
bodies, credentials, or private payloads. The system distinguishes persistence
failure, accepted-but-undelivered work, and ambiguous client acknowledgment.

### one-authoritative-conversation

History, current output, confirmation, and follow-up lineage belong to dispatch's
existing single-writer task authority. CLI, MCP, HTTP, evaluators, and operator
UIs consume the same contracts; no UI-owned result store or second queue exists.

## Non-Goals / Boundaries

- No self-confirmation by a worker or confidence-based review bypass.
- No reopening a completed task to carry a new conversation.
- No reliance on TypeScript compilation to validate runtime agent output.
- No untrusted network fetching or execution during schema validation.
- No live agent retained solely to wait for human review.

## See Also

- [Parent dispatch vision](../README.md).
- [Tasks-pane UX](../tasks-pane-ux/README.md).
- [Backend lifecycle effort](../../../../efforts/active/agent-dispatch-monitor-and-confirmed-state/README.md).
- [Tasks-pane effort](../../../../efforts/active/agent-dispatch-tasks-pane-ux-overhaul/README.md).
