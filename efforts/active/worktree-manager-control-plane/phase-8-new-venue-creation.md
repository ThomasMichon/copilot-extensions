# New venue creation into the existing owned session

Parent: [Worktree Manager](README.md#5913---existing-generic-new-to-the-same-owned-session).
Canonical issue: [#5913](https://github.com/ThomasMichon/copilot-extensions/issues/5913).

**Status:** Proposed contract refinement; no implementation or live acceptance.
The bounded planning claimant is `patient-knob`. This document does not assign
the provider, session-host, supervision or native-validation owners' work.
The root's P5913-1 through P5913-3, V5913, G5913 and Coordination remain
unchecked and authoritative.

## Outcome and alignment

From an empty CodeSpaces or Containers pivot, collect explicit repository and
provider specification, provision through the provider, and carry the operator
into the same owned Copilot session contract that Open uses. No existing row
or second manual attach is required.

This is vision-closing against the stated venue-pivots-ux
[`new-venue-then-embody`](../../../visions/venue-pivots-ux/README.md#new-venue-then-embody)
delta and preserves Picker `render-derive-not-own`. Follow the existing
[CLI/service contract](../../../docs/patterns/service-contract-evolution.md)
and [progress/timeout](../../../docs/patterns/progress-and-timeouts.md)
patterns. Do not add a contribution schema, provider CLI, transport, allocator,
claim store or session lifecycle owner.

## Current contract and the missing composition

The generic foundation is shipped, not missing:
`pivot_create_action.py` parses static fields, command templates and confirmation;
`CreateActionScreen` collects them without a row. Neither current venue manifest
declares a `create_action`.

The production consumer is not a terminal-launch contract.
`engine_pivot_actions._run_create_action` invokes `run_resolved` off the render
flow; `tasks.RegisteredPivotRuntime` captures output with `ACTION_TIMEOUT=30`.
On success it refreshes rows and displays a short message. By contrast,
`engine_worktree_actions._open_venue` returns an `open-venue` decision; after
the Picker exits, `launcher.open_venue` runs the provider's `copilot` with the
real terminal. Adding a manifest that runs interactive `copilot` in the
captured path would not deliver this outcome.

Provider operations also have materially different results:

| Owner | Existing behavior to preserve |
|-------|--------------------------------|
| CodeSpaces `create`, allocation and reuse | Account-scoped admission, reuse-before-create and capacity refusal precede allocation. A reuse result is not proof of fresh provisioning. Creation can wait for availability and run longer post-create hooks; ordinary output is text, not a typed session receipt. |
| Containers `reconcile_up` | Tops a declared fleet up to a target size; it can do nothing or create several members. It is not an atomic "allocate one New target" operation. Existing drivers, profile/hosting checks and lifecycle admission remain the owners. |
| Provider `copilot` and shared `venue_copilot` | Own target admission, claims/leases, connection locks, preparation, reservation, connection, embodiment and release. A restricted or non-SSH-capable target must not gain interactive reach. |

Do not scrape a success line, choose the newest discovered row, compute an
unguarded `existing count + 1`, or infer an exact registered session from a
generic command exit. Do not use quota cleanup, forced claims, profile
relaxation or implicit fleet/configuration creation to make New succeed.

## Dependency-ordered delivery

### N0 - Qualify the composition before enabling either manifest

- [ ] Resolve the existing form-command result to the owning provider's exact
  selected or created target, then use the existing Open/session contract.
  Prove the handoff through the actual form, command boundary and post-Picker
  terminal path, not a second manual step or detached-session-only substitute.
- [ ] Establish how this composition retains the existing contribution schema
  and command owners. No production New declaration is admitted until that
  path is qualified. If it requires a changed independently deployed boundary,
  return a scoped reviewed contract amendment before enabling callers; this
  proposal does not preapprove new syntax or a result protocol.
- [ ] Preserve the current generic create behavior for non-venue contributions.
  Establish support from the exact executing runtime, with provider-first
  expansion and explicit old-Manager, old-provider and rollback outcomes.
  Unsupported composition refuses before allocation; never try an effectful
  new call and replay an old form on failure.
- [ ] Retain progress through long availability/provisioning work and the
  existing owning cancellation boundary. Neither the generic 30-second action
  deadline nor communication silence may destroy an admitted operation, release
  custody or license a duplicate create. Report partial and unknown outcomes
  distinctly and recover through the existing provider owner.

### N1 - Prove each provider's target and custody

- [ ] CodeSpaces: retain explicit repository/branch/specification, account,
  defaults, capacity and reuse semantics. Fresh and reused outcomes identify
  the actual target and retain its original claim/lease and preparation state.
  Do not force creation, reclaim quota or repeat allocation after uncertainty.
- [ ] Containers: select an explicit declared fleet/specification and reconcile
  its repository with operator input before effects. Reuse existing driver and
  allocation/admission owners for exactly the selected member. Do not rewrite
  fleet size/configuration, create an arbitrary fleet, borrow an unrelated
  member or convert top-up completion into target identity.
- [ ] Preserve target/profile/hosting restrictions, busy/unknown owners and
  ordinary refusal. Across success, failure and cancellation, distinguish
  confirmed release from retained or unknown custody; cleanup never deletes
  a provisioned venue or another owner's resource as incidental compensation.

### N2 - Enable the continuous user flow

- [ ] Only after N0/N1, contribute both provider New forms through the shipped
  `create_action` contract. Collect explicit target repository/specification
  without an existing row; validate missing/invalid prerequisites before
  allocation and retain explicit confirmation/cancellation.
- [ ] Carry each successful qualified provider result into the same Open path,
  including its ordinary admission, reservation, connection and embodiment.
  Preserve row refresh and complete actionable failure output without claiming
  an attach, session identity or clean release that the owner did not confirm.
- [ ] Record source, release and separately authorized installed/live evidence
  independently. Keep #5913 and the root's gates open until their full original
  acceptance passes, even if a prerequisite or one provider has merged.

## Validation Plan

- [ ] **NV0:** Real generic form/command/terminal-boundary fixtures prove N0:
  no row, confirmation and cancel, exact-target handoff, unchanged non-venue
  creation, long progress, timeout/unknown outcome and old/new/rollback peers.
  Assert no allocation on unsupported composition and no replay after ambiguity.
- [ ] **NV1:** Both provider fixtures exercise default/missing prerequisites,
  explicit repository/specification, fresh/reused or full-fleet outcomes,
  refusal and partial failure. Assert actual selected identity, restrictions,
  reservation/connect/release ordering and retained versus released leases.
  Include two competing requests so neither can claim the other's member.
- [ ] **NV2:** The actual New-to-Open consumer reaches the owning session path
  for each supported venue. Generic CLI success or reattachment is not proof
  of exact registered-session identity. Use the existing owner-qualified
  observation instead of a new passive mux or native-session probe.
- [ ] **NV3:** Retain the original applicable owned live lane for both venue
  shapes, including failure and lease handling. Run it only with its resource
  owner's bounded effect authority. Fixtures, source merge and plan review
  do not satisfy or waive this lane; #3507's native scope stays separate.
- [ ] **NV4:** Before every implementation publication, run focused owning
  source/consumer tests, required lint/contracts and exact-head CI/review.
  Keep the exhaustive/live matrix out of unconditional required PR CI.

## Coordination and planning result

The current finite claim covers research and this reviewed plan only.
#3657's supervision/actions, #3507's native Docker validation, provider
allocation/lease/connection and session-host custodians retain their scopes.
Before implementation, reconcile the actual selected hunks and current claims,
and record a bounded implementing owner through the existing issue/Journal
process. No takeover follows from missing assignees or an old observation.

This plan authorizes no live provisioning, enrollment, session launch, lease,
credential operation, service repair, rollout or destructive cleanup. An
unavailable live lane leaves NV3 open, not permission to create another venue.
After the planning PR merges, record that finite outcome separately and release
its planning reservation without closing #5913 or checking feature gates.
