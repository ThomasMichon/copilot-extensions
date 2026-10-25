# Internal original-acquisition launch custody

Back to the [DB custody foundation](runtime-custody-foundation.md) and
[Bridge restoration effort](../../../efforts/active/bridge-retained-runtime-restoration/README.md).
The original journal now has an explicit internal local ACP lifecycle adapter.
It is not default enrollment, an HTTP capability or restore qualification.

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

## Explicit local effect owners

An existing internal owner may capture `AcquisitionFrontend` with the actual
`AcquisitionRuntime`, its installation observations and its enrolled frontend
admission, then supply it as `acquire(request, custody=frontend)`. Construction
does not enroll a producer. The captured runtime, manager, Database, database
file and frontend PID/birth remain bound across awaited selection/preparation.
The original request and admission commit together before worktree preparation.

The selected path uses the existing keyed `start_session` or strict local
`resume_session`, not a parallel launcher. Creation associates its original
session insertion with the same admission. Resume requires an already-associated
stopped record, no live client, and a fresh strict host inventory proving no
retained host for that session; missing inventory is not absence. Live reuse,
remote/provider/elevated targets, legacy resume, reattachment, recreation and
background recovery are not selected. An associated pending acquisition or
session cannot downgrade to the ordinary recovery/resume path.
History resync also refuses associated sessions before client teardown and
rechecks their association under the lifecycle lock.
A retained turn owner also refuses selected resume before another Host or
native load; stopping and reaping its processes does not enroll owner transfer.
Session insertion, shutdown stop and recovery-enable writes preserve the
durable association's automatic-recovery exclusion. Hydration excludes
associated history even if an older recovery flag was enabled. Startup and
direct Host reattachment refuse that association before adopting another client.
Ordinary Host status and graceful detach also retain that custody: an idle
associated Host is not marked reapable. A captured native owner short-circuits
the check; otherwise it reads the original session association. Only ordinary
unassociated idle Hosts retain automatic reaping eligibility.
The idle-TTL sweep also excludes retained custody, rechecking under the turn
lock, after lifecycle scheduling and immediately before automatic Host retirement.
The sweep skips a lifecycle-busy candidate after taking the turn lock, avoiding
inversion with reattachment's lifecycle-locked resume-prompt submission.
Its internal guard does not change explicit operator stop/reap behavior.
New explicitly selected original local Hosts disable the existing unexpected
and active-disconnect timeout bounds at the actual launcher. Retained quiet
custody cannot expire after a graceful `detach(False)`; unselected configured
Hosts retain their ordinary timeouts and no existing Host is adopted or changed.

One original `AcquisitionLaunchOwner` crosses local preparation and the worker
thread scheduling boundary into `launch_session_host`. The launcher validates
that actual owner, then admits the original handoff immediately before its
`Popen`. A closure or owner change after the one-shot commit leaves it
outstanding, never eligible for another launch.

The concrete acquisition adapter retains its original request and journal.
`LocalLaunchOwner` owns the shared live Session/target/Host/child/ACP reference
checks, one-shot native invocation and configuration fences. After acceptance,
`LocalHostTransport` retains those actual references and the complete original
handoff document for the existing turn protocol; `AcquisitionHostTransport`
remains its compatibility name. The separately selected
[original keyed-creation adapter](creation-custody.md) uses the same live effect
checks with its own same-row handoff and concrete owner, not an acquisition
request or another attempt ledger. Neither adapter enrolls a cohort by default
or makes retained observations into effect authority.

The newly launched local Host receives a stripped, original-acquisition context,
not a transferable grant. Its actual child owner rechecks the original
frontend parent PID/birth, database file identity, epoch, producer, request
digest, handoff and workspace through a passive local database open immediately
before child creation. The prompt stays in the original DB row, not in that
environment context. Attested-shell launches check again before releasing
their final exec acknowledgement. Neither the context nor Host-only nonce
reaches the child. This does not add a remote database reader or negotiate
custody with an independently owned host.

The frontend retains the actual Host process handle, Host/child PID births,
Session, target, ACP client and SDK connection. The native new/load check runs
inside the actual RPC invocation, after task scheduling and metadata wrapping.
Direct `new_session` and `load_session` calls on that client require its retained
original owner; omitting or replacing the owner cannot select the raw SDK path.
The ordinary invalid-parameter metadata fallback cannot invoke a selected
new/load twice. Native acceptance is reported on the exact SDK reply, before
subsequent preference application; each configuration effect rechecks its owner
outside the preference fallback handler. A missing reply or configuration
failure does not authorize the resume ladder or a new conversation.

The actual Session retains its original launch owner before spawning. That
owner records the raw `Popen`, state-file path and nonce before readiness,
then the actual Host/child and existing HostIndex record before returning from
the spawn worker. It retains the socket, streams and client before ACP startup.
Readiness, initialization, native-reply and configuration failures do not run
ordinary launch rollback: no TERMINATE, stream close or Host retirement is
authorized by an unknown result, cancellation or a closed epoch. Failed startup
keeps the original objects and journal outstanding without claiming success or
granting replay. Ordinary unselected failed launches retain their cleanup.
An unknown readiness result may have only the original raw process/state-file
observations; it does not fabricate a ready Host or enroll reattachment.

After successful startup, only that original accepted local owner can select
its Host transport into the [existing original-turn protocol](native-turn-custody.md).
The acquisition's prompt and later selected turns use the same retained
turn handoff and final SDK gate, not an unassociated prompt after launch.
Acceptance is still not attributed consumption or semantic completion.
Until the accepted transport is captured, the original client refuses ordinary
prompt sends. Session prompt and handoff admission also refuse a durable
association without its turn owner, including after a failed capture or reload.
Capture failure preserves the accepted launch observation; it never authorizes
an unassociated turn.
Lifecycle handoff refuses the durable association before either authored-brief
or external-seed handling, so a failed brief cannot trigger an unfenced successor.

## Cost and source controls

Ordinary unselected launches add no custody database opens or process-birth
queries. Selected operations add bounded local identity/primary-key checks at
the actual effect boundaries and reuse the runtime's original journal; repeated
frontend checks do not re-ensure indexes. The existing FULL original-acquisition
and turn commits remain the durability boundaries. No new subprocess, service,
network request, tree/hash walk or full-history scan is added. Only the selected
local Host opens the same database passively at its child/exec boundaries.
Prompt and handoff admission without an installed turn owner add one local
primary-key lookup on the existing connection to refuse a retained association.
Startup performs one such lookup per indexed Host before planning or claiming
reattachment; direct reattachment checks before client teardown. Hydration uses
the session rows it already reads, without another history scan.

The focused source controls cover closure across worker scheduling, one-shot
commit, child creation, native RPC and consecutive configuration effects;
owner/reference changes; metadata retry refusal; and ordinary-path regression.
The isolated wire flow runs the actual local Host, child and ACP SDK with a
synthetic peer that independently reads original launch/turn rows before
replying. Create/load and lost-reply cases must each produce one Host and one
corresponding native request, with no replay and no consumption claim.
Live peers that withhold new/load replies remain alive after the bounded RPC
timeout, under the same owner/client/connection and indexed Host.

This is not a native performance budget or platform qualification. The separately
authorized qualification owner must measure ordinary versus selected cold/warm
create/load/first-prompt boundaries on each supported platform, preserving
per-sample errors, actual process/CPU coverage and insufficient-tail-sample
limits under the repository's performance-measurement pattern.

## Deliberate remaining boundary

No HTTP capability, route, option, default enrollment or installer behavior is
added. Keyed workspace/provider creation outside this selected acquisition,
CLI embodiment, background producers, host reattachment/transfer and remote
relationships remain separate cohorts. Directly calling a DB method or decoding
the child context does not establish live native custody.

Unsupported or ambiguous cohorts remain blocked rather than replayed. Inventory retains
the acquisition/start/resume, host/remote, compatibility and installer blockers;
`restore_qualified` and `acknowledged` remain false.
