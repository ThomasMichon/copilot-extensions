# Current footprint contracts and deliberate compatibility

Parent: [Source-Managed Runtime Installs](README.md), Phase 4b.
Intent: [recognizer manifests](../../../visions/installer/README.md#recognizer-manifests).
Coordination: #7028; existing installation, execution, source and native owners
retain their scopes. This proposal changes no installed runtime or cleanup policy.

## Operator direction

The [verbatim refinement](inception-transcript.md#intentional-footprint-schema-evolution)
asks for intentional schema changes, practical backward compatibility and the
ability to stop supporting obsolete contracts without carrying an unlimited
chain. The original disposable-inventory and incremental-recognition requirements
remain. Source history is not itself a runtime compatibility requirement.

## Problem and boundaries

The first provider in #9499 stores complete snapshots and exact deltas in an
append-only payload history. Its source guard hashes the complete scripts/src
producer trees; changing those bytes requires another snapshot even when the
installed artifact classes and compatibility rules are unchanged.

`installer_engine_footprint.parse` validates that history but exports its latest
profile, effects, recognizers and producer hashes for operational decisions.
Shared/native uninstall uses the latest profile. Construction recognizes the
latest declared layout ranges, not a historical snapshot selected from the
history. Historical snapshots therefore do not currently implement historical
cleanup or a sequence of migrations.

Replace the redundant runtime audit ledger, not the knowledge needed to handle
supported old installations. Git and reviewed contributions remain the source
change history. Preserve existing finite removal, protected state, native custody,
immutable slots, source admission and the independent incoming-startup contract.
No manifest entry or transition authorizes a host effect.

## Proposed contract - agent-recommended

Use one current declaration and an explicit, finite portfolio of supported
legacy layout/transition rules. Separate:

- **Serialization schema:** identifies the document form and reader semantics.
  Changing required fields or meanings is an intentional schema revision.
- **Footprint contract:** describes current disposable classes, exclusions,
  process/registration effects and compatibility rules. Revise it when those
  semantics change, not whenever implementation bytes change.
- **Source producers:** current hashes of complete producer scopes. Refresh them
  in place for source-only changes; retain omission detection without inventing
  another footprint version.
- **Supported legacy rules:** named predecessor layouts, their recognition
  requirements and any still-needed reset or migration descriptors. Keep these
  separate from source/package version ordering and from historical source hashes.

Introduce a version-3 current-document form at the existing co-packaged provider.
The implementation uses strict `version`, `schema`, `plugin`, `revision`,
`contract`, `producers` and `change` fields. `contract` holds the existing finite
v1 `profile`, effect descriptors and named `recognizers`. Those named layout
ranges are the explicit supported-legacy portfolio; the reset profile already
retains `.venv`, `venv` and other supported predecessor artifacts. There is no
automatic union of old inventories. `revision` remains 36 at the schema-only
transition and advances only for semantic or producer-scope changes.

`change` is null for the schema-only conversion. Subsequent semantic changes
carry `from_revision`, the exact introduced/changed/retired `delta`, and one
`dispositions` entry per retired identity. An entry either identifies a retained
equivalent current descriptor or explicitly preserves unknown remnants with a
reason. Effect entry retirements are assessed individually, not hidden inside
an edited whole effect. Source-only updates preserve the revision and assessment.
Only the current assessment ships; Git retains earlier change assessments.

The existing constructor selects its sibling provider and refuses a declaration
outside that same scripts directory. Native MCP removal loads its own scripts
inventory and sibling provider, including for bounded v1 decoding; it cannot
select a newer provider from another payload. Other canonical-source dependency
fallbacks retain byte-identical owner code and the exact source repository
pairing. Frozen old payloads retain their
old reader and old document. The adapter expands all newly shipped readers to
v2/v3 before the co-packaged MCP declaration emits v3; it does not direct an old
installed helper at the new declaration. A mismatched old-reader/v3 pair is an
explicit pre-effect refusal, not an invocation to retry.

A footprint change must include an explicit introduced/changed/retired assessment
and update every affected current or supported-legacy rule. The source guard
compares normalized before/after contracts and producer scopes against the real
Git baseline. Source-only changes do not append history. Reviewed deltas belong
with the substantive transition/change, not an unconditional runtime snapshot
for every commit.

Do not silently lose retired artifact knowledge: each retired descriptor must
have an explicit disposition, such as a supported legacy cleanup rule, a
transition that removes/replaces it under existing custody, or deliberate support
retirement that preserves unknown remnants. Do not union every historical
deletion list and then execute that union.

## Compatibility and support retirement - agent-recommended

The provider accepts existing version-2 histories and normalizes their effective
current contract without rewriting installed data. Existing version-1 finite
uninstall profiles retain their current supported path. Historical source hashes
are not independent installed layouts; the version-2 adapter need not expose an
event ledger to ordinary runtime consumers.

Establish reader support before emitting version 3 where a reader and producer
can update independently. A co-packaged reader/document pair may transition
together only after verifying that every actual caller selects that exact pair.
An older installed helper must not accidentally consume a newer payload's schema.
Do not assume synchronized fleet updates or package-version equality.

Exercise the supported old-reader/new-provider, new-reader/old-document and
rollback combinations at their real loading boundaries. If an old reader cannot
accept version 3, keep it paired with its old document or use a proven compatible
provider path; do not emit the new form and retry an effectful older invocation.

Compatibility is maintained for documented useful layouts, not every historical
source commit forever. A later breaking change may retire a legacy adapter or
transition after an explicit supported upgrade/rollback decision, documentation
and focused regression coverage. Do not invent a permanent support promise, an
arbitrary keep-last-N policy, or an obligatory V1-to-V2-to-V3 migration ladder.

Missing or unsupported **incumbent reuse** evidence selects independent fresh
construction with a truthful reason. Unsupported required semantics in the
**incoming trusted declaration** remain an invalid/unsupported incoming contract,
not guessed support or permission to ignore safety fields. Unknown state remains
preserved; removal of compatibility never grants broader cleanup authority.

## Bounded reading and delivery - agent-recommended

Keep installed deployment/selection/completion markers at their existing 64 KiB
read bound. Use a separate bounded trusted-manifest reader sufficient for the
already-produced version-2 histories during the compatibility window; 1 MiB is
the proposed initial bound. The new current document must not grow with the
number of source-only updates. Oversized, unreadable, redirected, duplicated or
malformed declarations remain explicit pre-effect failures.

Change canonical provider, constructor, uninstall consumers and source guards
together through their existing ownership seams. Regenerate ordinary
repository-owned helper copies and source archives through the existing sync
tools; never edit an installed runtime or relabel retained qualification packets.
Renew source admissions and tests for changed bytes. Neither a schema proposal
nor a source merge qualifies native installation, live replacement or rollout.

## Plan

- [x] Review this amendment before changing the append-only schema/guard contract;
  #9793 merged after exact-head approval and required checks.
- [x] Inventory actual readers, producer guards and supported payload/runtime
  pairings; specify the current-document schema and explicit compatibility window.
- [x] Implement the canonical normalizer/current declaration and meaningful
  footprint-change guard, preserving complete current producer coverage.
- [x] Normalize supported version-2 inputs and preserve version-1 finite profiles;
  remove unconditional source-change history growth without losing required
  legacy dispositions or inventing an automatic migration chain.
- [x] Wire construction, shared/native uninstall, source/materialization guards
  and canonical helper shipping to the same provider and document.
- [ ] Qualify the matrix below, journal actual source closure and return to the
  parent Plan/Validation Plan; do not declare installation/adoption complete.

## Validation Plan

- [x] A source-only edit changes producer hashes but not the footprint contract
  or number of runtime compatibility entries; repeated edits keep size bounded.
- [x] Adding/removing/changing a disposable artifact or mandatory semantic
  requires an intentional contract change and explicit affected dispositions.
- [x] New readers accept real version-2 history fixtures and existing finite
  version-1 profiles where supported; current documents use the new form.
- [x] Actual selected payload/provider pairings preserve old consumers and
  supported rollback without assuming lockstep deployment or probing effects.
- [x] Supported legacy rules retain required old artifact knowledge; deliberate
  support retirement is explicit and unknown remnants remain preserved.
- [x] Unsupported incumbent layouts choose fresh construction, not an old-owner
  approval or mandatory sequential-migration prerequisite.
- [x] Unknown incoming required semantics, schema confusion, duplicate fields,
  invalid scopes and malformed/oversized/unreadable/redirected files fail explicitly.
- [x] Exact read boundaries preserve 64 KiB installed markers and the bounded
  legacy-manifest reader; current-manifest size is independent of source history.
- [x] Shared/native removers and constructors consume the same current contract;
  protected data, locks, provenance and uncertain native custody remain unchanged.
- [ ] Canonical fan-out, packaging/materialization, installer-contract controls
  and the exact-source no-effect packet cover the changed implementation.
  Native and installed acceptance run only through their existing authorized lanes.

The focused source packet passed 225 controls, including the real predecessor
document/reader, copied constructor and inventory pairings, exact read boundaries,
materialization and eight positively admitted no-effect unit additions. The
install-contract guard passes for all 13 plugins. Full exact-source no-effect
execution, implementation review/merge and parent reconciliation remain required.

The optional native removal portfolio is not this source packet. A developer-host
run exposed the existing passwd-home isolation gap #9614 and non-isolated process
inspection refusals. Its incomplete payload fixtures now ship the actual inventory
helper and provider before ownership publication; that fixture correction is not
positive native qualification. Complete native removal remains in the existing
#7028 acceptance lane with an isolated account/process namespace and unchanged
production admission. No real account, process, installation or rollout changed.

## See also

- [Fresh-default installation](fresh-default-installation.md)
- [Version-skew-safe contracts](../../../docs/patterns/service-contract-evolution.md)
- [Current install-contract documentation](../../../docs/install-contract.md)
