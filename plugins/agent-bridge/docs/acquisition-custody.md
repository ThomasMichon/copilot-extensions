# Internal original-acquisition launch custody

Back to the [DB custody foundation](runtime-custody-foundation.md) and
[Bridge restoration effort](../../../efforts/active/bridge-retained-runtime-restoration/README.md).
This is DB groundwork, not a native start/resume adapter or restore qualification.

## Original journal and admission

Schema 35 prepares the existing `acquisition_attempts` and `acquisition_effects`
tables through the writable schema owner, retaining their existing keys, request
digests, tokens, stages and pending-target exclusion. Old rows keep null custody
fields. Neither migration nor ordinary startup enrolls an acquisition producer.
Already-current damaged layouts refuse; passive opening and existing-only
writable adoption validate without migration, bootstrap or writer startup.

`AcquisitionJournal.claim(request, runtime_admission=...)` can retain the
installation/epoch/producer association with the original request and claim in
one `synchronous=FULL` transaction. The same insertion guard orders closure and
new acquisition admission, including unassociated legacy calls. Existing
completed receipts remain recoverable after closure without another insertion.
A retry cannot replace the association or upgrade a legacy attempt.

The existing writable journal still ensures its historical query indexes.
Passive journal construction only validates them. Ordinary unassociated claim,
stage and outcome writes preserve their prior synchronous policy; associated
writes restore that policy after their FULL commit or refusal.

## Prospective launch and observations

The journal's internal `prepare_launch` retains one prospective ACP creation or
resume handoff on the original attempt. It binds the request digest, original
admission, bridge session, selected native conversation (null only for creation)
and consumer incarnation. Repeated preparation observes the same handoff;
another incarnation cannot replace it. Legacy, foreign, terminal or already
delivered history cannot acquire prospective custody.
An explicitly retired creation can keep both prior IDs in its original request
as historical facts while selecting a new bridge/native identity. It cannot
revive either retired ID. Continuation/resume still requires the carried IDs.
Newly created allocations cannot retain resume handoffs: their completion must
record creation. Incompatible allocation/intent combinations refuse before
retaining a handoff or admitting a launch, leaving the original attempt unstarted.

`begin_launch` rechecks the original open epoch, enrolled producer, request,
pending stage and handoff in a short FULL transaction. It admits that handoff
once. A lost reply or process exit leaves the original `starting` observation,
not permission to try another native call. No DB lock spans external work.

`read_launch` recovers the original handoff even when a preparation reply was
lost. `report_launch` records the original native acceptance after closure,
owner loss or journal completion. A resume must report the selected native ID;
creation records its first accepted native ID immutably. Acceptance is not
consumption, semantic completion or a new send opportunity.
An attempt whose launch began cannot be turned into a pre-effect refusal.
Acquisition completion requires the retained launch result and matching
creation/resume identity; unknown launch outcome stays pending.

Original association, input, handoff and recorded launch observations are
retained. Stage updates preserve request facts and participate in the same
inventory revision. Full-row metadata inventory includes terminal and
unassociated acquisition history and validates retained launch observations.

## Deliberate remaining boundary

No HTTP capability, route, option, default enrollment or installer behavior is
added. `AcquisitionRuntime`, keyed workspace/provider creation, `start_session`,
`resume_session`, native ACP/host launch, CLI embodiment and background recovery
do **not** yet select these primitives. The consumer descriptor is an internal
owner observation, not evidence that the actual native owner was checked.
Calling a DB method alone does not qualify a native effect.

Final-owner integration must carry the original attempt across awaited
preparation and recheck it at each actual process/host/session effect. Unsupported
or ambiguous cohorts must remain blocked rather than replayed. Inventory retains
the acquisition/start/resume, host/remote, compatibility and installer blockers;
`restore_qualified` and `acknowledged` remain false.
