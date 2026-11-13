# Interventions that preserve the goal

Back to [Monitor agent fleet](../SKILL.md).

## Repeated validation or native qualification

First name the exact unmet criterion and the stage that prevented a result.
Then propose one discriminating change:

| Observation | Useful next action |
|-------------|-------------------|
| Tool wait elapsed, process still running | Observe the same owned command; do not start another copy |
| Environment preparation consumes the deadline | Measure preparation separately and use the runner's supported prepared environment |
| Broad suite repeatedly times out without a failing case | Select the smallest affected behavioral contract; retain broader acceptance as outstanding |
| Same native boundary fails without a changed hypothesis | Use one real minimal control at that boundary and compare candidate with a known baseline |
| Environment lease is busy | Use its documented bounded wait or independent admitted environment; do not remove the lock |
| Shared desktop activity contaminates observation | Request an explicitly agreed quiet window or already admitted dedicated host |
| Functional run passes only under a diagnostic profile | Record functional evidence and retain the original performance failure |
| Review repeats a fixed finding | Cite the current implementation/test and its fix on the original thread; do not edit correct code to trigger another pass |

Keep the repository's actual mandatory unit, integration, native and publication
gates. "Required CI is unit-only" does not establish a changed process boundary
works; conversely, a catalog of optional integration cases is not a demand to run
every one before every PR.

Before a costly packet, check that its platform, fixture inputs and launch mode
can exercise the reported real consumer. If repeated measurements show the
chosen architecture cannot plausibly meet the budget, compare another viable
design before paying for another broad qualification. Do not change the
required outcome without its owner's decision.

Use the existing runner's exact file/node selectors and incremental dependency
preparation. Inspect collection when needed to prove a new test is included.
Use immutable prepared fixtures or supported caches only where they do not
erase the cold-start, concurrency or lifetime behavior being tested.

Separate three questions:

1. Does the feature work?
2. Does it meet the declared performance/resource limits?
3. Is this environment capable of measuring those properties?

Do not silently relax an acceptance timeout, mock away the failing boundary,
skip cases or claim a container result as native desktop/host-lifetime proof.
A separately selected longer functional diagnostic can answer question 1
without passing question 2. Preserve both results and the remaining gate.

## Isolation and quiet time

Prefer the repository's existing isolated container lane for portable
integration. Verify selected source/new tests, offline or credential boundaries,
resource limits and cleanup. A generic development container is not necessarily
equivalent, and Linux does not prove Windows or macOS behavior.

For native desktop, installer or process-lifetime work, use an already
authorized compatible lane. If none is available, prepare an acceptance packet:
exact criterion, source, minimal command/selector, platform requirements,
expected duration, affected shared resources, cancellation/cleanup and the
required owner approval. Do not create capacity, stop other agents, reserve a
host, change power/security settings or weaken observability to get a pass.

A quiet window is an agreement with affected owners and a finite end, not a
new global lock or proof of zero interference. During an agreed window, observe
the real noise source; do not merely repeat the same test until chance yields
a green result. Return borrowed resources through their existing owner.
Raw executable counts do not identify independent worker lanes or available
capacity. Correlate owners and actual resource evidence before recommending an
admission change; an existing operator-backed capacity hold still requires its
owner to release it.

## Prerequisites and scope

A necessary detour names the blocked parent criterion, bounded exit and exact
return action before starting. Once its result is available, apply it to that
parent; do not automatically adopt every newly discovered improvement.
Record nonblocking findings in the existing appropriate backlog when authorized.

Before collecting more context, identify the decision the missing fact could
change. Reuse still-applicable operation/schema knowledge; refresh the specific
source, target and authority facts that can change. Supporting-tool publication
should block consumer work only when a real dependency makes it necessary.

If a branch is blocked on an operator decision, preserve its original approval
evidence and prepare one concrete decision packet. Work on another already
assigned independent item only when allowed; do not generate busywork or
expand the charter to avoid waiting. An all-work hold still means hold.

## Review and delivery

Classify the remaining gate precisely: code finding, current-head evidence,
required reviewer approval, optional automated review, CI, conflict, release
or deployment. Read the target repository's actual stopping rule instead of
inferring it from a generic "not approved" field or a prior repository.

Fix a valid finding's recurring class within the changed scope, then rerun
affected checks. Explain an obsolete or already-resolved finding with current
evidence. Do not chase zero comments after the documented bar is met, add dummy
commits to provoke review, or dismiss a real blocking finding as a nit.
Required approvals and thread-resolution gates remain independent.

Batch related documentation and effort updates at meaningful boundaries.
Publish the first complete functional slice when policy permits and steward
it to actual acceptance. A merged child PR does not close its parent; a
released source change does not prove rollout to running agents.

## Drive to resolution

For a completion charter, a steer names the next unmet acceptance criterion,
asks the agent to verify met criteria and finalize through its own flow, or
asks it to rework its own charter and plan when they have drifted. Follow
[per-agent charters](agent-charters.md#drive-completion-charters-to-resolution).
Standing charters get only an on-course nudge, and exploratory agents get
none. When the blocker is the agent's infrastructure rather than its plan,
use [graduated repair](repair-escalation.md) instead of a steer.

## A bounded nudge

```text
Root criterion: <source and remaining outcome>.
Observed: <comparable attempts and concrete missing delta>.
Recommended next action: <one smaller/different experiment or parent return>.
Exit: <observable result and where it feeds the root>.
Preserve: <review, approval, resource and ownership constraints>.
Please retain this result and return in your existing plan/checkpoint.
```

Send only under the skill's explicit authorization and deduplication procedure.
Do not request another full plan, status essay, review or audit unless that
specific missing information is the blocker.
