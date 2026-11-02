---
name: delegating-work
description: |
  Authors a bounded assignment prompt or contract when explicitly requested.
  Ordinary requests to delegate or execute tasks do not select this skill.
  Trigger phrases include:
  - 'prepare a delegate assignment'
  - 'write a bounded sub-agent prompt'
---

# Delegating Work

This skill authors an assignment prompt or contract only when that artifact is
explicitly requested. Ordinary requests to delegate or execute tasks do not
select it. It does
not decide whether arbitrary work should be delegated, prescribe a coordinator
or worker strategy, select a model, or require a preliminary budget lookup.
Domain-tool access remains governed by its owning capability.

## Assignment

State the concrete goal, minimum inputs, exact scope, exclusions, expected
result and validation, applicable authority, and any operator-supplied limits.
Choose enough detail for the actual assignment without imposing a universal
checklist or check-in cadence.

Retain the main objective and integration owner. Delegation narrows authority;
it does not grant access, publication, deployment, or permission to change a
different owner's resources. Execute the assigned scope directly; do not
create child agents unless the assignment explicitly authorizes nested
delegation. Never delegate back to the same agent type to continue its own work.

Assign non-overlapping edit ownership. A shared git worktree is a coupled
surface even when files differ: commits, stashes, rebases, and pushes affect the
whole checkout. Give a delegate doing git/PR work its own managed worktree,
not a worktree another owner is concurrently editing.

## Results and readiness

Distinguish progress from completion, verify the returned artifact and
validation before accepting it, and integrate it against the original goal.
Follow [worker evidence and readiness](references/worker-evidence-and-readiness.md)
for result acceptance or a prepared venue's task-specific readiness.

Transport availability is not resource authority or proof that the requested
work succeeded. Preserve literal failures and unresolved effects through their
existing owners; do not repeat an unknown-effect mutation as a fresh attempt.

This skill is not an agent authoring surface, a named-agent chooser, or a
replacement for repository, safety, authentication, and publication policy.
