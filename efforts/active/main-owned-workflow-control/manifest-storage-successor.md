# Manifest Storage Successor - Reader-First Source Migration

Parent and completion gate: [Main-Owned Workflow Control](README.md).
Coordination: #6237; existing CI/release and operational owners remain.
Status: compatible reader merged; exact format-only migration prepared for separate review.

## Goal

Continue the original-unit census past the fully minified v2 storage frontier
by implementing a source-only successor representation reader-first and then
migrating source data through ordinary review. Preserve logical witnesses,
caps, eligibility, guards and deadlines. This phase does not complete the
parent consolidation campaign or its historical/native/release acceptance.

## Scope approval

The following public-safe exchange is the original approval evidence, not an
agent inference.

**Question presented on November 25:**

> Five reviewed PRs are merged, adding 111 original cases; the admitted packet now passes all 10,375 cases. The fully minified v2 manifest has only 29 bytes left under its 1 MiB cap; even one new source binding needs at least 73. The parent effort remains open. Which direction should I take next?

**Selected answer, verbatim:**

`reader-first-successor-and-reviewed-source-migration`

**Selected option text:**

> Implement a successor representation reader-first, then migrate through normal review; preserve caps, logical witnesses, guards and deadlines

**Presented scope exclusion:**

> Representation or CPU work stays source-only. No native run, operational writer/controller change, runtime update, rollout, cleanup or guard exception is authorized by these choices.

The selected representation option, not the unselected CPU/hold alternatives,
defines this phase. Existing attempt limits, failed-test/input/native refusals,
resource custody, publication gates and owner boundaries remain.
No prior one-shot grant is renewed.

## Requirements

- Keep the 1 MiB stored-manifest envelope, existing parser/evidence bounds,
  300-second packet deadline and all proof/effect/eligibility checks.
- Preserve every current node identity and its order, original parameter rows,
  types, marks and IDs, source/target order, helper decisions and normalized
  admission semantics. Do not remove tests or inputs to make room.
- The successor-capable reader must retain identical v1/v2 normalization and
  rejection behavior. Older readers remain explicitly incapable of successor
  data; do not send them that data before support is established.
  Unknown or malformed data must refuse, not fall back to broader input.
- Distinguish honest source-code/test binding updates during reader development
  from representation migration: freeze the post-reader baseline and prove
  exact ordered normalized values and bindings across the data-only conversion.
- Preserve fresh evidence and cache invalidation. Changed raw bytes or recipes
  do not relabel an old artifact as current or create a reuse hit.
- Do not replay the installed-inventory `.git/config` refusal, application-
  adapter native path, or any other retained refusal. A new representation
  does not make an excluded effect eligible.
- Leave workflows, controllers, operational writers, installed runtimes,
  native execution, rollout, resource cleanup and ownership unchanged.

## Design reconciliation

_Agent-recommended realization, not additional operator intent._

This closes a storage bottleneck under the
[test-portfolio vision](../../../visions/test-portfolio/README.md)'s bounded
feedback loops, portfolio budget and evidence-bearing families. It does not
make test count an objective, relax actual-effect eligibility, or substitute
format savings for measured CI/review-throughput savings.

Follow [right-sized composition](../../../docs/patterns/README.md#design-principles)
and [contract evolution](../../../docs/patterns/service-contract-evolution.md):
keep the codec in its existing source owner, expand readers first, prove the
actual executing consumers, migrate data conditionally and retain legacy
meaning. No service, RPC, dependency or operational negotiation is added.

A versioned prefix-group representation is the initial recommendation to
evaluate: retain explicit ordered suffixes while factoring repeated case-name
prefixes. Proposed storage identity:
`copilot-extensions.promotion-units/v3`, normalizing to the existing logical
unit contract, not a new effect-eligibility profile.

Retain the existing root fields and ordered file groups. Within a file group,
encode ordered pairs of a nonempty prefix and a nonempty explicit suffix list:

```json
["tools/promotion_unit_tests/test_example.py", [
  ["test_case", ["[first]", "[second]"]],
  ["TestGroup::test_plain", [""]]
]]
```

Expansion concatenates each prefix and suffix verbatim. Writers group only
contiguous runs, never sort cases or merge noncontiguous runs into a new order.
The reader still applies existing logical validation after bounded expansion.
No reference counts, executable transformation or inferred omission replaces
explicit cases. Existing candidate/native profiles remain separate and
unchanged. Confirm this concrete shape against actual consumers and bootstrap
constraints before implementation; materially different realization returns
through ordinary design review.

## Consumer and bootstrap closure

The November 26 read-only trace closes the following actual paths:

| Consumer | Executing reader and data selection | Consequence |
|---|---|---|
| PR CI | Checked-out head's `ci_pr_unit_reuse.py` and central contract | Reader/code and active manifest share the tested revision |
| Promotion unit execution | Frozen candidate's runner, paired with the admitted immutable Dev input | No mutable-latest lookup or new controller route is needed |
| Qualification and publication | `reviewed-helpers` and `validated-dev` both checkout `needs.gate.outputs.sha` | The helpers reading source data come from that same frozen Dev revision |
| Receipt and packet verification | `load_manifest` and `verify_packet` normalize through `admit_manifest` | Keep raw file digests separate from logical manifest identity |
| Release-scope guard | Reads sources, targets and each outer file group's path only | Nested case-prefix groups do not change its selection semantics |

Fetched and independently provider-confirmed Main
`07549ae5e5831f7c00263572d3375ddb276d4122` uses those frozen-Dev helper
selections. Its initial Main-selected lifecycle helper does not read the unit
manifest. Preserve this distinction rather than assuming every helper runs
from Main or adding a new release/rollout prerequisite.
Before migration, confirm that the actual selected readers contain the merged
compatibility code and that the migration's source descends from it.

The central contract and compact contract/packet/candidate test owners are
already admitted. Extend their existing admitted test flows, retaining old
assertions and all test/parameter identities. New reader branches will execute
under existing gates without adding a source binding or splitting a table.
Honest full-width code/test hash updates do not grow the v2 serialization.
The reader stage keeps the active representation in v2 and the existing
1 MiB cap; it adds no preload or eligibility exception.

## Plan

_Agent-recommended realization within the selected scope; not new authority._

- [x] Review and merge this phase's intent before implementing the codec or
  changing the active stored representation.
- [x] Trace actual manifest readers, writers/converters, proof/recipe consumers
  and independently selected control revisions. Close the 29-byte bootstrap:
  make new focused coverage execute without dropping old nodes/bindings,
  exceeding the cap, inventing a profile exception or preloading production.
- [x] Implement a strictly versioned compatible reader and focused controls in
  the owning source. Keep active source data in v2 during reader delivery.
  Prefer the existing bound owners while bootstrap space is constrained.
  Record the concrete representation and compatibility contract in the code
  review before using it.
  V3 prefix groups normalize through unchanged logical admission. Fifteen
  existing contract/packet/candidate controls and both real compatibility-CLI
  cases pass unchanged effect guarding. Names, parameter tables, old assertions
  and selection remain; only five existing code/test hashes are rebound in v2.
  Fresh-process old/new file consumers preserve ordered full-corpus values.
  Reader delivery and data migration remain unchecked below.
- [x] Merge the reader and prove the actual consumers can read the successor
  before migration. Use their real code/control selection, not package-version
  equality. If a separately owned consumer needs adoption, preserve that
  owner's seam; do not dispatch a release, edit a controller or assume uptake.
  Name the supported reader/data pairs and rollback window explicitly.
  An older reader's truthful pre-execution refusal is not compatibility, and
  rollback must never drop admitted witnesses to regain an older format.
  Reader #14658 merges as `7ef2eefee79e64d48e265daefab4ded622de8fbd`;
  full hosted CI runs all 10,375 cases before normal Maintainer merge.
  Reviewed/merged/working blobs and current bindings/helpers match.
  PR heads and frozen-Dev helpers select the capable source reader.
  Current Main `73554ecb6359f06ef8248fdf090c92686be719fe` leaves the traced
  workflow/helper selection unchanged. Matching v2 data is retained at the
  immutable reader merge; unsupported decoder-only rollback stays incompatible.
- [x] Prepare a separately reviewed source-data migration from the frozen
  post-reader v2 baseline. Independently compare every ordered normalized
  value, source binding, target, helper decision and budget before writing.
  Do not combine additional unit enrollment with the format-only conversion.
  Baseline is the reader merge above. Independent expansion and recursive
  type/order comparison preserve all 10,375 nodes, 1,499 bindings, 546 targets,
  92 helper decisions and the 300-second limit. Both fresh-process file
  consumers agree; only storage changes from 1,048,547 to 697,907 bytes.
  Separate data review/hosted qualification/merge remain pending below.
- [ ] Verify reviewed delivery, full current packet and persistent bytes,
  journal the phase outcome, then resume original-unit census through its
  unchanged metadata/guard/coverage/admission flow. Return to the parent
  Goal and unresolved Plan/Validation; do not finalize this worktree.

## Validation

_Agent-recommended checks preserving the established acceptance boundary._

- [ ] Existing v1/v2 inputs normalize identically; supported old/new reader
  directions, strict unknown-version refusal and rollback remain explicit.
- [ ] New stored data reconstructs exact original node strings and order,
  including class-qualified/parameterized names, Unicode, brackets, empty
  parameter suffixes where applicable, noncontiguous runs and repeated file
  groups. No case is truncated, relabeled, omitted or sorted into a new order.
- [ ] Malformed shapes/types, duplicate logical identities, invalid paths or
  source bindings, invalid UTF-8, invalid budgets and oversized expansion
  fail closed under the existing limits. Bound expansion before oversized
  materialization; no unbounded reference/repetition encoding is introduced.
- [ ] Focused new controls genuinely execute during reader qualification;
  retaining test files without running their new branches is not coverage.
  Preserve old assertions and identities while closing the bootstrap budget.
- [ ] A lightweight real consumer path exercises old/new input in a fresh
  process without production preload where feasible. Record the actual mode
  used and any unavailable tier rather than substituting a proxy.
- [ ] Natural required hosted CI runs the complete admitted packet at each
  relevant code/data head. Preserve source, context, counts and outcomes;
  never rerun merely to erase an unchanged failure or bypass required CI.
- [ ] Prove actual consumer compatibility before the data switch and exact
  post-reader-baseline logical equivalence before/after conversion.
- [ ] Review, configured required checks, normal role-scoped merge, independent
  merged-state/blob readback, supported pull-forward and current binding/helper
  verification complete both stages. Record unit, simple E2E, first-touch and
  hosted tiers and concrete reasons for any inapplicable tier.
- [ ] The data-only migration stays within 1 MiB and preserves the exact
  frozen post-reader counts and values. The present pre-reader inventory is
  10,375 nodes, 1,499 source bindings, 546 targets and every existing helper
  decision. Any reader-stage binding update or new focused control must be
  explicit and normally qualified; no census addition is hidden in migration.

## Decision and progress record

The [approval Journal](journal/2026/11.25-original-unit-delivery.md) and
[implementation Journal](journal/2026/11.26-manifest-successor.md) own the
decision and subsequent review/delivery history. This phase's Plan/Validation,
not an opened PR or a reader-only milestone, owns its bounded completion.
The parent remains the root completion owner.
