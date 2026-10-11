# Local CI-repair dispatch lane — outstanding requirements

This slice belongs to the existing
[Promotion-Failure Reactive Fix Agent effort](README.md), coordinated by
[#6102](https://github.com/ThomasMichon/copilot-extensions/issues/6102).
It records requested portable container-template and dispatch-emitter work,
not an implemented or enabled repair agent. The cloud `gh-aw` workflow remains
a separate execution lane; this slice changes none of its credentials.

## Ownership and composition

- **Effort driver:** coordinates the slice and its review/acceptance evidence.
- **`agent-containers`:** owns the portable execution-venue template and its
  provisioning/lifecycle contract.
- **`agent-dispatch`:** owns emitter admission, task deduplication, worker
  selection and dispatch through its existing infrastructure.
- **Repository failure-source adapter:** supplies verified validation-run and
  signature/issue evidence to that emitter; repository bindings stay in the
  adopting repository, not downstream-specific upstream source.

Reuse those existing owners rather than adding a bespoke scheduler, daemon,
queue, or competing failure detector. A container is a **venue**, not a
hardcoded charter: select the reviewed repair charter and workspace after a
task has passed admission. Provisioning a fleet member alone never establishes
that the dispatcher actually runs tasks in it.

## Required boundaries

The emitter consumes verified failure evidence from the existing
[watchdog](../../../tools/ci_failure_watchdog.py), not arbitrary issue text as
authority. Preserve stable failure identity, deduplication/cooldown, trusted
source admission, and diagnostic provenance. Logs and issue bodies remain
untrusted data. Reject unauthorized or insufficiently evidenced requests.

Bound attempts and escalation across recurring failure identities, including
tracker closure during an active attempt. The unresolved recurrence/episode
policy stays with [#5890](https://github.com/ThomasMichon/copilot-extensions/issues/5890);
do not choose that policy implicitly here or substitute a per-run timeout for
a per-failure attempt budget. Autonomous activation remains gated on resolving
that decision and proving the chosen contract.

Inference authentication and repository-operation authentication are separate
capabilities, potentially supplied by different accounts. Resolve each
explicitly and verify it in the selected venue. Use repository-scoped
credentials for repository operations; never select inference by a global
`gh auth switch`, inherited ambient account, or a double-duty repository token.
Missing or mismatched authority must block admission rather than silently
falling back to another account. Credential transport and custody must follow
the existing container/authentication contracts, not invent a parallel relay.

Apply the existing [CI-remediation vision](../../../visions/ci-failure-remediation/README.md):
diagnose whether tests or implementation diverged from established intent,
never weaken checks just to obtain green CI, and escalate ambiguous judgments.
Preserve protected automation/release paths and ordinary contribution review,
checks and merge authority; the repair worker gains no self-merge or bypass
authority from this template.

## Acceptance

- [ ] A portable template provisions through `agent-containers` and accepts
  more than one charter without baking an agent, repository, account, or host
  into the image; task-selected overlays reach the real invocation.
- [ ] Synthetic verified/unverified failure, duplicate, cooldown and
  unauthorized-source cases exercise the real emitter admission boundary,
  proving that only an admitted failure creates the intended task.
- [ ] The chosen #5890 policy is reviewed and tested for recurrence, closure
  during an active attempt, attempt exhaustion and explicit rearming.
- [ ] A contained task proves actual container dispatch, workspace/charter
  selection and lifecycle completion; a provisioned idle container does not
  count as dispatch evidence.
- [ ] Separate inference and repository credentials are verified in that venue.
  Wrong/missing credentials fail closed; repository-scoped operations do not
  switch global `gh` state or change inference identity, and cloud auth remains
  unchanged.
- [ ] Contrasting test-defect and implementation-regression fixtures prove
  intent-preserving triage; ambiguous intent and protected-path fixes escalate.
  A proposed repair follows the repository's ordinary review/merge gate.

No live credential acquisition, login, worker activation, workflow execution,
deployment or restart is authorized by this requirements document.

## See also

- [Owning effort](README.md) — plan, remaining guardrails and journal.
- [Architecture patterns](../../../docs/patterns/README.md) — composition,
  attributable ownership, fail-closed provenance and no competing runtime.
