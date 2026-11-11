# Full asserted-title source fidelity

[Canonical railroad effort](README.md#finite-full-title-source-lane-from-3581)

- **Slice status:** Active (finite plan reviewed in #11952; parent scope is unchanged).

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

## Source boundary

`tracking.set_disposition` owns normalized current-title and new-history
retention. Its qualified title callers use the full assertion, while legacy
status requests explicitly select the existing 30-character projection.
The ordinary title-only paths use that same full assertion/history helper.
The publication-only `normalize_title` contract remains separate.

The status mutation retains `status_disposition_write` version 2 as its
legacy baseline and advertises version 3 for qualified full-title requests.
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

## Selected design

The following implementation choices were agent recommendations in the finite
plan. They do not add operator requirements or live-effect authority.

- Use the existing `WorktreeRecord.title` as the full canonical value rather
  than introducing a companion whose authority and stale-write handling would
  need another reconciliation rule. Keep current JSON and history field names.
- Normalize controls/newlines/whitespace using the existing title rules and
  preserve allowed Unicode. Use a 512-character normalized assertion
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

- [x] I0: Review and merge this finite proposal before functional edits (#11952).
- [x] I1: Capture failing controls on the real ordinary writer, stored
  readback, JSON/history and short-display consumer, not a publication helper.
- [x] I2: Implement the selected canonical-or-companion design, sane bound,
  ordinary writers and full new-history retention without losing old data.
- [x] I3: Qualify the actual current/prior receiver selection and legacy/new
  request behavior, including rollback and ambiguous-outcome no replay.
- [ ] I4: Pass the affected consumer packet, required source guards and
  exact-head review/CI, merge and reconcile only the direct F/FV source lane.

## Validation Plan

- [x] IV1: Real status/title writer and persisted record round-trip representative
  long/Unicode assertions; exact-bound and over-bound cases; normalization,
  clearing and unchanged unrelated fields. A rejected multi-field assertion
  leaves current record/history unchanged.
- [x] IV2: Real ordinary JSON/list/status/history export retains the full
  accepted title. Actual compact display consumers remain bounded without
  altering storage. Existing binding fields and source precedence remain
  unchanged; publication helpers are not a substitute.
- [x] IV3: New title assertions retain full history exactly once. Existing
  capped history stays readable without fabricated reconstruction. Non-title
  updates and real legacy title-only entrypoints preserve their contracts.
- [x] IV4: Exercise new/new, old/new, new/old and receiver rollback through
  the actual version-selection/transport seam with owned synthetic peers.
  Missing/invalid support never sends a full-retention request to a truncating
  receiver; an admitted but unknown write is not replayed.
- [x] IV5: Use a real fresh-profile CLI child against owned synthetic tracking
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

## Journal

### 2026-11-11 - Native YAML Unicode scalars

The next review found that JSON surrogate-pair escapes are not portable YAML
scalars: the C loader rejects non-BMP titles, while the pure-Python loader
retains surrogate code units. Four fail-first controls reproduce cold-read
failure after both an asserted title write and an unrelated summary save.

Title serialization now uses PyYAML's double-quoted scalar emitter with
ASCII escaping, preserving numeric strings, Unicode separators and non-BMP
characters for both safe loaders. The JSON-only import is removed. All 343
cases in the five affected writer, tracking, history, publication-state and
receiver files pass, including the explicit fresh-profile CLI child.
The unchanged required packet readmits only the changed tracking import
dependency; no node, actor, coverage, effect or budget expansion is requested.
The new exact-head review and required CI remain mandatory before merge.

### 2026-11-10 - Scalar type and Unicode framing controls

Seven new controls reproduce two serialization defects: a long numeric
assertion is read back as a YAML integer, and U+0085/U+2028/U+2029 both break
unquoted record scalars and split JSON history entries. The cold record reader
is asserted explicitly so a warm record cache cannot hide damaged bytes.

Titles now serialize as quoted JSON-compatible YAML strings. New history
escapes Unicode for legacy-reader compatibility, while append/read use LF
delimiters to preserve existing literal Unicode rows. The complete affected
core writer/storage/history packet passes 335 cases, with 54 unrelated cases
deselected. It includes the explicit fresh-profile CLI and real transport
controls. An intermediate serializer edit omitted the JSON import; that
implementation error was fixed and lint plus the whole packet pass.

Only the two changed import-dependency bindings are readmitted for required
units. Their source/node inventory, coverage and effect budgets are unchanged;
the existing JSON standard-library import introduces no actor. Earlier
current-head CI success does not waive the two unresolved findings or the
next exact-head review/CI. No productive record, native host or runtime is
mutated.

### 2026-11-10 - Unit dependency readmission

The initial required unit job refused execution because the admitted
`tracking.py` import dependency had changed. Only that dependency digest is
readmitted after reviewing the scalar normalization/helper changes: no new
imports, actors, packet nodes, coverage targets, effect permissions or budgets
are introduced. The existing required Linux packet must qualify the corrected
head before merge; an earlier approval does not waive this gate.

The local prepared interpreters lack Coverage, so no local native-packet pass
is claimed and no package was installed to manufacture one. The 107/12 source
consumer results remain distinct from required packet execution.

### 2026-11-10 - Implementation candidate

The finite plan merged in #11952 at 17:36:56 UTC as
`916b1ccb057ea0fa70d2e23ecd6b980ccc9b9377` before functional edits.
Six actual writer/atomic-refusal controls failed first. The qualified ordinary
writer and title-only paths now retain full assertions and new history; the
legacy request without the full-retention selector still caps and clears as
before. Non-title writes keep their existing version-2 baseline. Input
validation precedes codename backfill and disposition effects.

The final affected six-file Worktrees packet passes 107 cases, including the
explicit T3 fresh-profile CLI child, actual TCP new/current and legacy/current
requests, missing/old/invalid receiver capability, declaration rollback and
post-admission unknown-outcome no replay. A prior unqualified selection skipped
the T3 case; its separate explicit run passed before the final packet.
Three added title-only controls initially had incomplete project/config test
fixtures; those fixtures were corrected without weakening the assertions.
The Manager's unmodified detail renderer/native Option consumer passes 12
focused cases, including new long-title inputs at 48, 118 and 600 columns:
only the displayed projection is shortened and the full source value survives.
No new row-budget policy or another owner's renderer implementation is included.

These are source/unit and simple real-consumer checks on Windows with Python
3.13 x64, not native ARM or installed-runtime timing. The fresh private profile
proves first-use CLI behavior, not installation: no installer changes.
N/NV native hosting and productive titles/bindings were not exercised and retain
their separate authority. Required source guards, implementation review/CI,
merge and finite canonical acceptance remain open.
