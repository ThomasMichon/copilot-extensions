*The following contribution was assisted using Copilot.*

# Monitor publication and roster readers - receiving proposal

Canonical campaign: [Backlog Reconciliation](README.md).

**Tracker status:** The reviewed proposal was accepted directly in OPEN #2619,
and #13789 separately retired as redundant `NOT_PLANNED` tracking. The
[actual receiving and disposition record](journal/2026/11.22-monitor-roster-accepted-receiving.md)
retains the complete readback, provenance and census evidence. All eleven
M/R product conditions remain unchecked; the historical proposal and its
technical obligations below are preserved, not declared implemented.

## Intent and current gap

This is a research/tracker proposal to retain #13789's complete reader contract
as an explicit lane in existing OPEN #2619, not a claim that either feature is
implemented. Event-driven, incremental monitor publication and monitor-owned
roster reads are consecutive parts of one pipeline:

`events -> monitor producer/publication -> roster and push readers -> at-use validation`

The existing parent is preferred over another umbrella. Its original request
and all eight discussions remain intact, including partial-delivery evidence.
Neither shared vocabulary nor the relationship graph establishes acceptance.
The source remains OPEN until the reviewed receiving plan is reflected in the
actual parent body, the complete result is read back, and the separate source
eligibility/custody judgment succeeds.

## Complete receiving contract

The following clauses are proposed as an append-only addition to #2619. They
preserve unfinished work; their unchecked state is intentional.

### Producer and scheduling lane - incumbent #2619

- **M1 - Event coverage:** Native mux-topology changes, tracking-directory
  filesystem changes, existing session-lifecycle hooks and direct-command
  completion are freshness signals. Use targeted record refresh where the
  affected worktree is known; retain mux hooks and filesystem coverage rather
  than treating existing lifecycle hooks or advisory wake flags as substitutes.
- **M2 - Demand and shared observations:** Preserve transitive controller/claim
  reachability from live controllers, not only a worktree's own mux liveness.
  This is scheduling demand, not a replacement for the separate actual-activity
  classifier. Share one observation bundle per producer cycle.
- **M3 - Incremental work:** After initial publication, changing one of 1,000
  records must not classify unrelated Git checkouts or enumerate unrelated
  session history. A wake followed by a full-project resweep is not this result.
- **M4 - Correctness fallback:** Retain a long periodic correctness backstop for
  unavailable, missed, lost, lagging or reordered events and restart. It is not
  the primary trigger and must not be removed merely to lower sampled CPU.
- **M5 - Evidence:** Preserve comparable before/after workload evidence bound
  to actual source, release, installed runtime and supported native platform.
  Historical CPU observations, a timer adjustment, an explicit interval
  experiment, warm coalescing or a merged optimization are partial evidence,
  not full event coverage, incremental publication or a borrowed numeric SLO.
  Successful warm reads must not be mischaracterized as unconditional producer
  wakes; preserve the existing measured follow-up's narrower conclusions.

### Reader and point-of-use lane - source #13789

- **R1 - Monitor-owned answer:** `agent-worktrees list`, including `--json`,
  ensures the canonical owned status-monitor is running, starts it if needed,
  and asks it for its view. The monitor computes the roster and writes it to
  the database. `list` neither answers by reading the database behind the
  monitor nor recomputes the roster itself.
- **R2 - Warm and cold paths:** A current warm project roster returns promptly
  from the monitor cache without a rescan or caller recomputation. A cold
  request may wait for the first build, with a bounded, reported wait.
  Concurrent cold callers cause one build, not one build per caller. A hung
  monitor neither hangs `list` indefinitely nor gets reported as current.
- **R3 - Event-to-reader freshness:** With the monitor running, rows reflect
  create, finalize, session open/close and mux activation within the normal
  notification latency. This composes with M1-M4; it does not invent a numeric
  latency target or replace their broader correctness obligations.
- **R4 - Repeated consumers:** Fleet monitors, dashboards, supervisors and
  other repeat callers use the monitor's HTTP push status connection instead
  of interval polling. Document that connection as the supported repeated
  observation path and state that polling `list` on a timer is unsupported.
  `list` help and output, including a JSON hint, point to the push connection.
- **R5 - Point-of-use validation:** Single-worktree operations re-derive live
  state before acting: finalize, cleanup, claims settlement, embody/open,
  delegate finalization, PR completion and similar commands. A disagreement
  with the roster view on which the caller acted produces a clear stale-view
  mismatch refusal, rather than acting on stale data. Regression coverage
  includes at least finalize, cleanup and delegate finalization.
- **R6 - Fail-closed compatibility:** Fleet and delegate readers keep refusing
  anything not provably current. Keep `--passive` and `--cache-only` accepted
  as compatibility aliases or deprecate them without breaking old callers;
  they do not retain a stale reader mode that bypasses R1-R5.

Point-of-use validation happens **before** mutation. The incumbent
direct-command targeted advisory refresh happens **after** command completion.
These are complementary, not competing alternatives. A successful durable
mutation must not depend on successful notification delivery.

## Requirement crosswalk

| Source condition | Direct receiver | Remaining acceptance |
|---|---|---|
| #2619 original mux, filesystem and lifecycle triggers | M1 | Actual coverage, not hook names |
| #2619 controller/claim refinement | M2 | Transitive demand without activity redefinition |
| #2619 command-completion refinement | M1 and boundary paragraph | Targeted advisory refresh; durable writes independent |
| #2619 one-of-1,000 and shared-cycle refinements | M2-M3 | No unrelated Git/history reconstruction |
| #2619 long fallback and recovery | M4 | Missing/reordered/unavailable events and restart |
| #2619 native/performance and partial-delivery discussions | M5 | Comparable actual workload evidence; narrower credit retained |
| #13789 intended contract 1; acceptance 1 | R1-R2 | Stopped monitor starts; correct bounded first roster |
| #13789 intended contracts 2-3; acceptance 2-3 and 5 | R2 | Warm/no rescan, one cold build, hung refusal |
| #13789 acceptance 4 | R3 | Create/finalize/session/mux notification freshness |
| #13789 intended contract 4; acceptance 6 | R4 | HTTP push documentation, help/output hint, no timer polling |
| #13789 intended contract 5; acceptance 7 | R5 | All named operations; three named regression controls |
| #13789 acceptance 8 and legacy flags | R6 | Fleet/delegates fail closed; compatible aliases or deprecation |

## Plan and coordination

1. Review and merge this receiving proposal through the normal PR flow before
   dependent tracker publication. Do not change Goal, Request, historical
   acceptance or existing check states to obtain that approval.
2. Refresh the complete collaborator roster and source/target provenance,
   authored discussions, current claims and explicit keeper conditions.
   Reuse unchanged admitted evidence; acquire only genuinely changed,
   qualified material. Unknown custody is not proof of no owner.
3. Append the complete M1-M5/R1-R6 contract, boundary, plan, acceptance and
   coordination to the current #2619 body without replacing incumbent text.
   Verify the whole actual body and all discussions, not just the appended
   excerpt or a link to this proposal. A new conflict or keeper stops the fold.
4. Separately judge whether #13789 is redundant tracking of that accepted
   unfinished contract. If every clause and actual owner condition is retained,
   use the supported `NOT_PLANNED` tracking-retirement route with an explanation
   that the work remains planned in OPEN #2619. Do not use `COMPLETED`.
   Otherwise retain #13789 OPEN with its specific remaining receiving gap.
5. Audit each actual effect under the original identity, reconcile a complete
   whole-queue census, and return to the campaign Plan/Validation Plan.
   Unknown mutation results require reconciliation, never blind replay.

#2619 remains OPEN. This proposal assigns no implementation, native fixture,
installation, deployment or resource work. Preserve #3761's authoritative
writer/storage ownership, #6086's measured spawn/performance ownership,
#11298's freshness work, #11211's fleet reader and all incumbent producer,
reader, lifecycle and native-source custodians. Existing #12870 coordination
is not a takeover. Do not contact or reassign owners merely to manufacture
acceptance, and do not infer any claim release from tracker consolidation.

## Validation Plan

- [x] Preserve every incumbent original/refinement/discussion and every
  source intended contract, eight acceptance bullets and compatibility choice
  directly in the actual receiving body; all product acceptance stays open.
- [x] Verify current complete provenance, eligibility and actual keeper/claim
  conditions before effects; retain genuine new conflicts or unaccepted gaps.
- [x] Read back the complete accepted parent and discussions, audit the
  separate supported source disposition if justified, and verify #2619 OPEN.
- [x] Reconcile the complete census and campaign journal without attributing
  unrelated departures, partial delivery or documentary review to fulfillment.

These are tracker acceptance gates, not permission to execute product tests.
Future implementation owners retain the M/R technical validation obligations.
The whole campaign remains Active until its own fixed target and remaining
Plan/Validation obligations are resolved.

## Traceability and observation

Qualified complete source: [#13789](https://github.com/ThomasMichon/copilot-extensions/issues/13789).
Existing complete receiver and refinements:
[#2619](https://github.com/ThomasMichon/copilot-extensions/issues/2619),
including its
[command-reference clarification](https://github.com/ThomasMichon/copilot-extensions/issues/2619#issuecomment-7059551828).
This proposal makes no new implementation/source trace or measured-performance
claim.

The latest complete census at 2026-11-22 03:17:35 UTC (2026-11-21 19:17:35 PST)
contains 905 open issues: 458 creator-eligible and 447 excluded metadata-only,
408 original-cohort and 497 inflow. That is 176 above the fixed 729 target
from the 1,458 baseline. This proposal itself supplies zero reduction credit.
