*The following contribution was assisted using Copilot.*

# Direct Retained Registry and Deferral Acceptance

Canonical parent: [Dispatch Fleet Reliability and Recovery](README.md), Phase 3.

This tracking-only intake retains the complete original, refinements and
directly inherited acceptance of #8778/#8779. Existing checked source,
Participants, custodians and other parent Plan/Validation items remain.
Neither technical completion nor a live retry/release operation is declared.

## Coordination and compatible choices

Keep coordinator/supervisor, Bridge registry/acquisition, startup/native-
history and assigned terminal-capacity ownership. No new implementation
assignee, reservation store, cleanup actor or service is created.

Preserve max-attempts **OR** a separate finite deferral budget without
charging explicitly retryable observations as hard permanent failures.
Preserve exponential **OR** capped backoff and blocked **OR** visible
dead-letter exhaustion semantics without restoring the retired `DEAD_LETTER`
task enum. Preserve release **OR** flag: absent current positive release/
effect/absence authority, live or UNKNOWN allocations remain retained/flagged.

Typed transient/unreadable/transport/restart versus definitive-not-registered
classification and finite episode accounting remain open implementation
choices. Lossless intake does not need to preselect an algorithm; no choice
permits infinite retry, success-shaped empty registry or replay of uncertain
accepted work.

Original counts, cadence, configuration, output limits, capacity and lookup
timings remain attributed history, not new universal retry/native/performance
bounds. Related abandon CLI guards and scoped reservation reads are distinct
outcomes, not extra consolidation members or hard prerequisites.

## Plan

### #8778 - Bounded deferral episode

- [ ] `P.8778.retained-union`: Retain the complete original, refinements and
  inherited contract, including all original alternatives, finite limits,
  observation context, uncertainty and source/resource duties below.
- [ ] `P.8778.allocation-release-OR-flag`: Preserve release **OR** flag under
  existing positive authority. Count, age or status alone never frees a
  live/UNKNOWN original allocation.
- [ ] `P.8778.backoff-OR`: Replace indefinite no-progress rearming with
  exponential **OR** capped backoff. Original eleven-to-sixteen-minute
  cadence and 84/730 observations are history, not prescribed future bounds.
- [ ] `P.8778.custody-and-scope`: Preserve #7116 acquisition/native/retained-
  start, #7805 assigned terminal-capacity, Bridge key/history and supervision
  responsibilities. Do not create workspaces, replay unknown launches or
  sweep held rows.
- [ ] `P.8778.finite-budget-OR`: Preserve `max_attempts` **OR** a separate
  finite deferral budget; keep task failures, reservation attempts and lane
  configuration visibly distinct.
- [ ] `P.8778.historical-observation`: Retain all original observed counts,
  configuration, output-limit and capacity evidence as attributed history,
  not fresh liveness/configuration proof or an all-host performance target.
- [ ] `P.8778.visible-exhaustion-OR`: Expose blocked **OR** semantically
  dead-letter exhaustion with the last detail and retained handle across
  diagnostics, without reinstating a retired task enum.

### #8779 - Typed registry result

- [ ] `P.8779.retained-union`: Retain the complete original, refinements and
  inherited registry-to-reservation contract and original custody/limits.
- [ ] `P.8779.bounded-transient-retry`: Give only genuine transients a few
  finite backed-off reads and retryable deferral classification. Definitive
  missing registration may fail immediately; never retry every error forever
  or silently substitute another registry/source.
- [ ] `P.8779.caller-and-history`: Preserve original task/reservation/
  allocation and Bridge uncertainty custody. Later successful `agent-show`
  history is not a current registry result, cleanup grant or transfer of a
  downstream client's source responsibility.
- [ ] `P.8779.shared-episode-accounting`: Compose the finite #8778 episode
  without spending permanent-failure budget for explicitly retryable
  observations; preserve queued-task/failed-reservation distinctions and
  truthful exhaustion.
- [ ] `P.8779.typed-registry-result`: Distinguish timeout/transport/restart/
  unreadable uncertainty from definitive missing registration; a generic
  `None` cannot erase the actual observation.
- [ ] `P.8779.validation-and-choice`: Qualify actual classification/accounting
  with finite transient/not-found/unknown/restart controls. Sentinel,
  exception and retry design remain reviewed choices, not a new authority.

## Validation Plan

Every `V.<issue>.<clause>` checks its **complete matching Plan row**, including
all original alternatives, limits, evidence context and source/resource
custody. The short validation descriptions do not narrow those requirements.

### #8778

- [ ] `V.8778.retained-union`: Read back every original/refined/inherited
  clause, distinguishing historical observations from unfinished acceptance.
- [ ] `V.8778.allocation-release-OR-flag`: Qualify original release/flag
  alternatives and live/UNKNOWN refusal under existing effect authority.
- [ ] `V.8778.backoff-OR`: Qualify the selected finite backoff without
  promoting old incident timings into new limits.
- [ ] `V.8778.custody-and-scope`: Verify unchanged acquisition, retained
  start, capacity, Bridge/history and supervision custody; no new cleanup.
- [ ] `V.8778.finite-budget-OR`: Prove a finite episode under the selected
  original budget alternative with truthful distinct task/reservation/lane
  accounting.
- [ ] `V.8778.historical-observation`: Preserve full original historical
  evidence without claiming current configuration, liveness or new native
  qualification.
- [ ] `V.8778.visible-exhaustion-OR`: Verify visible exhaustion, last detail,
  retained handle and diagnostics without restoring the retired enum.

### #8779

- [ ] `V.8779.retained-union`: Compare the full registry original/refinements/
  inheritance against every direct row and retained modality.
- [ ] `V.8779.bounded-transient-retry`: Distinguish finite retryable
  uncertainty from definitive absence and ensure no silent source change.
- [ ] `V.8779.caller-and-history`: Verify original allocation/attempt/Bridge
  uncertainty and downstream source custody; old success is not a live result.
- [ ] `V.8779.shared-episode-accounting`: Qualify the real shared episode,
  non-permanent transient accounting and truthful exhaustion.
- [ ] `V.8779.typed-registry-result`: Exercise every original observation
  class without collapsing uncertainty into missing registration.
- [ ] `V.8779.validation-and-choice`: Complete the finite actual
  classification/accounting controls through the chosen reviewed design.

## Delivery and completion boundary

The parent directly retains this Plan **and** Validation under its same
Participants/Coordination. Normal reviewed delivery and whole-current-parent
readback precede supported administrative supersession. Tracker consolidation
is neither behavior completion nor a resource/claim release. Other parent
items, original technical/native/installed gates and uncertain allocations
remain; no whole-parent, every-host/native or generic acknowledgment gate is
introduced.
