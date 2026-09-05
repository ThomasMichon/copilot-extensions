# Generic Reviewer Contract Boundary

Companion design note for
[`review-automation-reliability`](README.md). The archived
[`turnkey-reviewer-loops`](../../2026/09/03%20turnkey-reviewer-loops/README.md)
effort proved that the stock reviewer recipe can be composed with the existing
registrar, producer, recipe-driver, supervised-lane, and worktree primitives.
This note records the reusable ownership seam that the active reliability
effort still needs to make explicit. It does not create another declaration
schema or reviewer lifecycle.

## Generic and consumer ownership

| Concern | Owner |
|---|---|
| Queueing, claim, bounded retries, suspension, and terminal outcome accounting | Generic reviewer runtime |
| One live review per target and revision-driven resume of the same lineage | Generic reviewer recipe and driver |
| Isolated worktree/session allocation, retention, and terminal reclamation | Generic runtime through agent-worktrees |
| Render-or-report result boundary | Generic reviewer contract |
| Declaration schema, discovery, and profile validation | Existing agent-dispatch registrar |
| Target eligibility, ACLs, and acting forge identity | Consumer |
| Review rubric and guidance content | Consumer |
| Forge writeback, merge authority, and landing policy | Consumer |
| Scheduling policy and organizational telemetry | Consumer |

The generic layer must not import one consumer's ACL, identity, rubric, merge
policy, scheduler, or telemetry. A consumer supplies those concerns behind the
same reusable contract.

## Contract seams

| Seam | Responsibility |
|---|---|
| Producer/scheduler | Discovers or side-loads a target and binds the consumer's eligibility and cadence to an existing registrar declaration. |
| Recipe/driver | Decides work, suspend, resume, or settle from the current recipe signal while preserving one target lineage. |
| Reviewer agent | Inspects the authorized delta and returns review evidence plus a rendered verdict or a classified no-verdict outcome. |
| Result applicator | Applies the generic result through the consumer's forge adapter and landing policy. |

The reviewer-agent/result-applicator boundary is **render or report**: every
attempt produces either a rendered verdict or a classified reason that no
verdict could be rendered. Transport success, worker completion, retry
exhaustion, or permission failure must never be shaped as approval.

## Registrar boundary

`agent_dispatch.registrar` remains the sole owner of declaration schema,
discovery convention, and profile validation. Reviewer reliability work may
consume and cite that substrate, but must not define a competing declaration
format inside the reviewer recipe or a consumer adapter. Any missing
declaration field belongs as a registrar follow-up before the reviewer contract
depends on it.

## Existing implementation anchors

- `plugins/agent-dispatch/src/agent_dispatch/registrar.py` - declaration,
  discovery, and validation.
- `plugins/agent-dispatch/src/agent_dispatch/recipes/driver.py` - generic
  recipe/signal decision rhythm.
- `plugins/agent-dispatch/src/agent_dispatch/recipes/registry.py` - recipe
  archetype registration and rendering.
- `plugins/agent-dispatch/src/agent_dispatch/reviewer_loops.py` - current
  repository-reviewer composition and consumer integration.
- `plugins/agent-dispatch/docs/spawn-supervisor.md` - supervised task,
  session, and worktree lifecycle.
- `visions/plugins/agent-dispatch/reviewer/README.md` - standing reviewer
  intent and render-or-report boundary.

If an anchor does not yet satisfy a statement above, the active effort records
an implementation gap rather than treating this note as proof that the behavior
already exists.
