# Full asserted-title source fidelity

[Canonical railroad effort](README.md#finite-full-title-source-lane-from-3581)

- **Slice status:** Draft (review precedes execution; parent scope is unchanged).

This is the bounded execution proposal for #3581's direct F1-F4/FV1-FV4
lane inherited from #4413. The original issue explicitly permits independent
source/consumer qualification of this lane. Stable effort anchoring, all eight
current-slice/nudge clauses, N/NV native hosting and #3582 journal work remain
open with their existing owners and acceptance. Completing this slice cannot
close #3581 or the parent effort.

The change closes existing [worktree title/naming intent](../../../visions/plugins/agent-worktrees/README.md#title-and-naming):
retain durable identity instead of destroying it to fit one display.
It follows [right-sized ownership](../../../docs/patterns/README.md) and
[version-skew-safe contracts](../../../docs/patterns/service-contract-evolution.md).
No vision amendment or second title authority is proposed.

## Current source

`tracking.set_disposition` calls `cap_title`, whose `TITLE_MAX` is 30.
The history writer snapshots that already-capped value, and machine-readable
status/list consumers cannot recover the lost text. The publication-only
`normalize_title` helper does not repair ordinary title assertions.
Three title-write sites in `finalize_cli` also call `cap_title` directly;
title-only paths must retain current and new-history fidelity too.

The existing status write crosses `status_disposition_write` version 2.
Its running receiver owns the actual semantics: a new caller cannot assume
an older daemon stopped truncating because the new payload is installed.
The existing per-verb advertisement/minimum-version selection already
avoids sending an unsupported request and permits only its logged,
same-code, lock-preserving pre-send fallback. Sent/unknown outcomes cannot
be replayed.

`patient-knob` owns only this finite source lane under the #3581 claim.
The current contributor PR #6009 changes `tracking.list_records` for anchor
visibility, not these title-writer hunks; that contribution remains untouched.
No actual title/binding, hosted pane, service or runtime is a test fixture.

## Recommended design

The following are agent-recommended implementation choices, subject to plan
review, not new operator requirements or live-effect authority.

- Use the existing `WorktreeRecord.title` as the full canonical value rather
  than introducing a companion whose authority and stale-write handling would
  need another reconciliation rule. Keep current JSON and history field names.
- Normalize controls/newlines/whitespace using the existing title rules and
  preserve allowed Unicode. Recommend a 512-character normalized assertion
  bound: long enough for an identifying headline, finite for records/history.
  Reject over-bound new assertions explicitly before record/history or other
  disposition changes; do not silently truncate accepted full text. The
  issue's suggested 200 characters is not a mandatory threshold.
- Retain empty-title clearing and asserted/unasserted behavior. Read existing
  records and history without migrating, reconstructing or rejecting their
  old values. Old capped history remains honestly capped. Non-title writes
  must not revalidate or rewrite an unrelated existing title.
- Apply presentation caps at actual short consumers, retaining their current
  compact behavior. Preserve the full value in ordinary machine-readable
  record/list/status/history paths and consumers with room. `cap_title`
  remains a display helper, not the canonical storage gate. Publication-title
  normalization is unchanged.
- Keep binding metadata and its current title source consistent. This slice
  neither implements nor bypasses the separately open anchoring/phase rules;
  no new slice derivation, binding rewrite or native prefix/suffix mechanism.
- Route each affected ordinary title assertion through the same normalization,
  bound and history semantics under its existing record lock. Preserve
  unrelated record fields, terminal/effort guards and once-per-session trace
  behavior; do not introduce a new writer or duplicate history entry.

## Contract and compatibility

The changed boundary is the existing status-disposition mutation, not a new
CLI command, preflight RPC, daemon or on-disk companion schema.

Expand its receiver with a version-3 full-retention request semantic and
advertisement before qualified callers request it. A new caller selects the
running receiver's advertised verb support, not package-version equality.
An older supported request retains its legacy short-title semantics; absence
of a new full-retention selector cannot silently change its contract.
Non-title operations retain their existing supported baseline.

A qualified full-retention caller must not send to a version-2 receiver that
would silently cap the value. Reuse the existing pre-send capability selection
and same-code locked fallback; unsupported/unknown sent outcomes preserve
existing explicit errors and no-replay behavior. Keep the legacy request form
through at least the existing prior/current generation upgrade and rollback
window; this slice removes no old form. Old writers are not claimed to provide
the new full-retention guarantee.

No automatic runtime update, restart, repository-config migration or deployment
is part of this source delivery. Existing installer and lifecycle owners retain
their authority.

## Plan

- [ ] I0: Review and merge this finite proposal before functional edits.
- [ ] I1: Capture failing controls on the real ordinary writer, stored
  readback, JSON/history and short-display consumer, not a publication helper.
- [ ] I2: Implement the selected canonical-or-companion design, sane bound,
  ordinary writers and full new-history retention without losing old data.
- [ ] I3: Qualify the actual current/prior receiver selection and legacy/new
  request behavior, including rollback and ambiguous-outcome no replay.
- [ ] I4: Pass the affected consumer packet, required source guards and
  exact-head review/CI, merge and reconcile only the direct F/FV source lane.

## Validation Plan

- [ ] IV1: Real status/title writer and persisted record round-trip representative
  long/Unicode assertions; exact-bound and over-bound cases; normalization,
  clearing and unchanged unrelated fields. A rejected multi-field assertion
  leaves current record/history unchanged.
- [ ] IV2: Real ordinary JSON/list/status/history export retains the full
  accepted title. Actual compact display consumers remain bounded without
  altering storage. Existing binding fields and source precedence remain
  unchanged; publication helpers are not a substitute.
- [ ] IV3: New title assertions retain full history exactly once. Existing
  capped history stays readable without fabricated reconstruction. Non-title
  updates and real legacy title-only entrypoints preserve their contracts.
- [ ] IV4: Exercise new/new, old/new, new/old and receiver rollback through
  the actual version-selection/transport seam with owned synthetic peers.
  Missing/invalid support never sends a full-retention request to a truncating
  receiver; an admitted but unknown write is not replayed.
- [ ] IV5: Use a real fresh-profile CLI child against owned synthetic tracking
  data for ordinary status/title write and JSON/history readback. Disable
  resident activation and assert no operator records, bindings or services
  are touched. This proves first-use source behavior, not installation.
- [ ] IV6: Run affected title/disposition/history/IPC and consumer regressions,
  existing lint/install-contract/module/documentation guards and required CI.
  No exhaustive native/every-host matrix is added to required unit CI.

## Completion boundary

Only direct F1-F4/FV1-FV4 may be accepted after the implementation and every
applicable control pass, actual review/CI/merge and canonical reconciliation.
The native N/NV lane, original title anchoring, all current-slice/nudge clauses,
journaling, release/installed acceptance and the wider effort remain open.
No native pane/session, real provider or productive title/binding operation is
authorized by this plan; unavailable native rollout is not replaced by a fixture.
