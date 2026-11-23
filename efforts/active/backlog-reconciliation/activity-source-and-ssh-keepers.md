*The following contribution was assisted using Copilot.*

# Activity source fulfillment and SSH-first owner keepers

Canonical campaign: [Backlog Reconciliation](README.md).

## Proposed disposition and scope

This is a research/tracker plan, not implementation or a completed disposition.
Propose acknowledging #14018 as fulfilled and closing it `COMPLETED` only after
the normal review/merge gate and the separate final qualification below.
#14018 remains OPEN at this proposal's observation. Retain #14047, #14048 and
#14049 OPEN as distinct, explicitly owner-held implementation contracts.
Do not fold them into each other or create another umbrella.

The #14018 original defines ACTIVE as a live native Copilot session PID lock
whose session CWD matches this worktree. Named mux membership is independent;
a mux-only worktree is not ACTIVE and may be logically finalized. Its five
acceptance bullets name six regression scenarios and explicitly exclude live
process termination, registry rewrites, service restart and runtime installation.
Do not add installed-runtime adoption or a live native run to that original
source-correction scope, and do not claim either happened.

## Complete #14018 acceptance correspondence

The bounded source study is complete. These are source-read findings, not new
execution results. [#14029](https://github.com/ThomasMichon/copilot-extensions/pull/14029)
merged to Dev as `a022b936f52ab89ecbcabcaf2a99290d19930ea4`, from head
`91f7783da3534a14b9048c9e8e4d753307c6f482`.
The observed Main snapshot is
`7f073dcd28f2f1c60b671f2322211128926c201e`.
Selected current local source matched that pinned Main source.

| Original condition | Source correspondence and retained boundary |
|---|---|
| Independent Copilot-active and mux-active observations in authoritative and cached status | `sessions.verify_worktree_active`, `picker_support/data_local.py` and production Picker derivation keep the matching native lock independent of named mux membership; legacy hints do not manufacture activity |
| Named-session cross-reference does not require unrelated mux-server coverage | Current `psmux_observation` delegates to the canonical Driver adapter; an existing positive-target regression raises the unrelated native-identity census yet retains the healthy named answer. Missing-target/absence evidence still requires adequate coverage and may remain unknown |
| Session CWD must match; foreign registration and stale locks are not activity | `reclaim_binding.py` checks registered session identity, native PID and matching CWD; missing CWD remains unknown, and dead or foreign sessions do not become this checkout's activity |
| Truthful unknown/error outcomes; physical safeguards separate from logical finalization | Authoritative probe flags and cached true/false/null preserve uncertainty. Current finalization retains independent physical checkout, mux, pair, data, process, hydration, source and obligation guards rather than redefining mux-only occupancy as ACTIVE |
| Bare Copilot, mux plus Copilot, mux-only, inactive, wrong-CWD and failed-listing controls | Existing `test_verify_worktree_active.py` covers the four signal combinations, wrong CWD and failed probes; `test_psmux_observation.py` preserves the named-positive versus incomplete-absence distinction |
| Explicit excluded effects, not a sixth acceptance bullet | No campaign process termination, registry rewrite, restart or runtime installation; no transfer of the source owner's resources |

The source owner's first discussion correctly reported that an older Main pin
lacked #14029's Darwin native-identity correction. The current observed Main
contains that correction and the Manager derivation. Preserve the earlier
observation as history, not as evidence that the present pin still lacks them.
Later exact source publishers #14060 and #14070 explain changes to the callers
and adapter; #13214 and #13621 supply the selected shared Driver boundary.
Merely associated later PRs are not substituted for those actual publishers.
This correspondence is not acceptance of the whole Driver program.

The source author's latest report of 176 passed, one skipped and 35-file strict
checks on both targets is **attributed**, not independently rerun by this
campaign. Earlier overlapping test counts are not added together. The bounded
source and existing controls support the proposed source correction; no new
native, fixture, installer or runtime experiment is authorized or represented
as passed. Final actual PR review/check verification remains a separate gate.

## SSH-first contracts retained OPEN

The complete current originals for all three issues have no discussions or
edit histories at the qualified observation. Each explicitly claims its bounded
implementation under the SSH-first terminal-launch mini-effort. That is an
existing owner's claim, not a campaign appointment or permission to inspect an
unqualified effort home.

| Issue | Distinct unfinished contract and validation exit |
|---|---|
| #14047 - parent | Review the vision/architecture amendment and canonical mini-effort before code. Package the selected project, destination and options; exit Manager and hand its existing terminal to TTY-enabled SSH. The normal target worktree command owns creation, anchor selection, mux, prompt staging, policy and Copilot. No originating preliminary creation, admission or policy round-trips. Preserve actual machine/environment versus execution-alias authority, initial connection bounds, real errors, cancellation, ownership, prompt provenance and old uncertain requests without replay. Validate local/remote New, anchor, cold Resume/Open, mux/no-mux, argument integrity, ordering and slow completion |
| #14048 - accepted-foreground transport child | Replace arbitrary elapsed completion expiry, including the reported eight-second server/nine-second socket and post-connection SSH wall clocks, with a creation-appropriate wait. Retain initial connection bounds, cancellation, definitive errors, locking, target-local policy and genuine disconnect uncertainty. Larger constants or global removal of background/status deadlines do not fulfill it. Slow success, definitive target error and real disconnect controls must avoid duplicate allocation |
| #14049 - Manager/terminal ordering child | Exit the UI before TTY SSH executes the normal target command. Originating plan resolution must not query target launch policy, create target worktrees, stage target prompts or wait for target receipts. Preserve every named option, shell-safe arbitrary prompts and existing transport authority rather than suffix guesses. Process/parser and exit-before-effects controls include supported older targets and explicit unsupported options without mutation replay |

The typed relations are `#14048 part-of #14047` and
`#14049 part-of #14047`. Transport completion and terminal effect ordering are
complementary interfaces, not duplicate outcomes. The preliminary-handshake
mechanism is the bounded supersession in #6057; its broader parity and #8375's
audit/native obligations retain their existing owners and acceptance. These
three originals neither transfer that broader work nor establish its completion.
No new hard whole-program sequencing edge is inferred from thematic alignment.

## Plan

1. Review and merge this complete cohort plan, its campaign Plan/global
   Validation pointers and dated journal through the normal current-head
   Copilot review and all actual required checks before dependent tracker
   effects. Preserve Goal, Request and every incumbent checked/unchecked row.
2. Apply the existing standing completion-check approval to #14018: retain
   the actual metadata-only issue recheck, complete collaborator roster and
   metadata-only delivering-PR review/check verification. Reconcile current
   original/editor/edit/discussion provenance, keeper/claim conditions and
   actual source/release correspondence within the original scope. Reuse
   unchanged admitted text; qualify genuinely changed material before reading.
   Do not reset prior consumption or release an actual source/copy hold.
3. If all original acceptance, fulfillment and provenance gates still pass,
   post an assisted acknowledgment linking #14029 and the actual source
   evidence, then close #14018 `COMPLETED` through supported operations.
   Otherwise keep it OPEN with the concrete remaining gap. A `Fixes` phrase,
   an old review, a partial source report or this plan alone is not completion.
   Reconcile unknown write outcomes without replaying them.
4. Audit the actual public explanation and provider disposition, reconcile the
   complete whole-queue census, and return to the parent Goal/Plan/actual global
   Validation Plan. Record no issue reduction until the actual effect is
   independently verified. Retain the three SSH-first keepers and all owners.

## Validation Plan

- [ ] The normal reviewed plan is merged, all five #14018 acceptance bullets
  and the explicit exclusion are mapped without adding a live-install bar,
  and all six named regression scenarios remain represented.
- [ ] Actual final issue, complete roster, delivering-PR review/check and
  source-correspondence results satisfy the standing approval and current
  eligibility/custody conditions, or a specific gap retains #14018 OPEN.
- [ ] Any acknowledgment and `COMPLETED` disposition are independently read
  back and audited; unknown effects are reconciled, not replayed.
- [ ] The complete census and parent return preserve every campaign check
  state, three distinct SSH-first owner keepers, all actual holds and resource
  obligations, and the fixed whole-queue target.

These are tracker-validation gates, not product-test execution permission.
The three SSH-first product exits remain the implementation owner's work.
No implementation, native run, installation, deployment, owner reassignment,
resource release or parked-effort resumption is included.

## Observation and next discriminating evidence

The latest complete census at 2026-11-23 00:00:10 UTC
(2026-11-22 16:00:10 PST) is 913 open: 462 creator-eligible and 451 excluded
metadata-only, 408 original-cohort and 505 inflow. It remains 184 above fixed
729 from the original 1,458 baseline. This proposal supplies no closure credit.
The five other arrivals #14053, #14075, #14077, #14115 and #14133 remain
metadata-only; do not infer their content or priority.

For #14018, the next evidence is the qualified final completion-check unit
after plan review, not another unchanged activity/Driver trace. For the three
keepers, it is the existing owner's reviewed mini-effort and eventual distinct
delivered acceptance, not repeated polling or a forced tracker fold.
The campaign's 43 incumbent check states, including 21 open items, remain.
