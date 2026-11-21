# External Agenda Placement and Migration Doctor - Proposal

Back to the [canonical effort](README.md) and
[dehydration architecture](architecture.md). Tracking: #13790, prerequisite to
#13783 activation and #13784 automatic removal. This is a proposed contract,
not an available doctor command or a live migration instruction.

## Operator direction

> We need to move `.effort-agenda` out of the worktrees' actual footprints; it needs to be in ~/.<repo>/worktrees/<worktree-id>

> We'll need a "doctor" for that

## Placement

Use the Worktrees-owned per-repository data home:

```text
~\.<repo>\worktrees\<worktree-id>\
```

The proposed agenda layout beneath that home is
`.effort-agenda\<lane>\`, preserving the existing domain namespace. That nested
layout is an agent recommendation; the external worktree home is the operator's
requirement. Keep source checkout paths wholly separate.

Resolve the repository state root, worktree identity and admitted installation
scope through the existing Worktrees data-placement owner. The path expression
describes the intended per-repository home, not permission to synthesize a root
from an untrusted alias, write into another installation, or put mutable data in
a runtime slot. Owner-published locators remain the common discovery mechanism.
No consumer invents a second fallback or creates a new lane while an existing
lane still owns that identity.

New managed agendas use the external home. Existing lanes keep their current
writer and data until explicitly migrated. This proposal changes the managed
default; it does not silently move a live standalone agenda or require the
standalone agenda plugin to depend on Worktrees.

## Doctor report

Extend the existing report-first Worktrees doctor and its domain adapters, not
a second repair service. Exact CLI flags and report schema remain to be designed.
Default inspection performs no allocation, database creation, writer start,
locator change, content move or deletion.

Within explicitly selected repository/installation authority, join registered
worktree identities, current/retained placement descriptors and the known
legacy `.effort-agenda` location at each selected checkout. Use bounded
enumeration with honest continuation/incomplete results, not an arbitrary home
directory or transcript crawl. Do not initialize a missing agenda to inspect it.

Report the worktree and lane/store identity, current and proposed home,
authoritative locator, writer/busy evidence, preservation hold, interruption
stage and next supported action. Distinguish:

- Already external and correct, or genuinely absent.
- Legacy checkout-local and eligible for an explicit move.
- Busy writer, unresolved owner or unavailable inspection.
- Conflicting destination, duplicate source or locator disagreement.
- Interrupted migration with its retained operation identity.
- Unsupported, malformed, linked or incomplete data.

An empty or unreadable local directory is not proof of empty accepted history.
A matching-looking destination is not proof that a prior migration completed.
Read-only inspection cannot acquire live agenda custody.

## Explicit repair/migration

The following is an agent-recommended shape, to be implemented through the
existing placement and agenda domain owners:

1. Select the exact reported source, store/lane identity and expected revision.
   Acquire the existing writer/placement fencing and recheck source and target.
   A live or unknown writer refuses; the doctor does not stop agents itself.
2. Allocate or validate the external destination through the data owner. Retain
   an operation journal outside the disposable checkout before migration effects.
   A foreign or ambiguous destination refuses without overwrite or merge.
3. Use the agenda owner's supported lossless move or consistent export/import,
   preserving complete accepted history, identities and transactional state.
   Do not copy an open database or preserve only its rendered Markdown.
4. Verify the destination at the admitted source revision. Switch the
   authoritative locator once under the same ownership boundary; prevent a
   source and destination from both becoming live writers.
5. Explicitly retire the exact old source through its owner after verified
   preservation and locator cutover. A successful copy, archive receipt or
   directory-name match alone does not discharge the retained-data hold.
6. Verify ordinary agenda discovery/reading/writing through the existing
   consumer contract. Record completion so a repeated doctor run is a no-op.

Retain unknown outcomes. Retry/recovery uses the same operation identity and
reconciles source, destination and locator before taking another effect.
Interruption before or after locator cutover must never allocate a second lane,
delete the only accepted history, or silently pick the newer-looking copy.
Unsupported stored revisions receive a supported adapter or an actionable
refusal; retaining history must not require its original executable forever.

Doctor repair is an explicit action. Neither ordinary launch, roster population
nor dehydration performs this migration implicitly. Until verified source
retirement, the checkout-local preservation hold blocks dehydration. Afterwards,
normal dehydration leaves the external data home and locator untouched.

## Compatibility and qualification

Expand owner support before changing managed defaults or callers. Select actual
provider/agenda contract support, keep legacy reads truthful, and prevent an old
writer from recreating a checkout-local duplicate after locator cutover.
Rollback cannot silently reinitialize the old source or lose access to the
external data. Define and test that boundary before activation.

Use disposable native Windows and Linux/WSL fixtures. Prove report-mode
non-mutation, already-external no-op behavior, complete history/identity
preservation, active/unknown writer refusal, ignored legacy data, target
conflicts, malformed/linked homes, partial export/import, interrupted cutover
and source retirement, idempotent retry, old/new readers and safe rollback.
Then remove and recreate the checkout through real Git and prove that the same
agenda remains discoverable and usable from its external home.

No live operator agenda migration, source deletion, new repair daemon or rollout
is authorized by publishing this plan.
