# Bridge custody specialization

Back to the [effort](README.md). **Prospective, agent-recommended design.**
The [parent contract](../acknowledged-execution-handoff/contract.md) owns generic
admission, acknowledgement and handback semantics. This document defines the
Bridge participant obligations needed by #8063, not another execution service.

## Authority and evidence

Bridge owns its durable sessions, acquisition/creation attempts, prompt and
inbox admission, native transports and host attachments. It does not own
Dispatch's database or an adopter's semantic effects. Extend Bridge's existing
Database, SessionManager, acquisition, extension and host owners. Do not add a
second queue, global process registry, scheduler or remote database reader.

The following are different facts:

| Fact | Meaning | Does not establish |
|------|---------|--------------------|
| Admission | Bridge retained one original operation and its accepted facts | SDK/native send |
| Handoff outstanding | A particular consumer/transport may perform the effect | Non-delivery or safe retry |
| SDK/native acceptance | The receiving boundary accepted the original operation | Consumption or semantic completion |
| Consumption | Attributed native evidence observed the original input | Completed task or adopter effect |
| Custody transfer | The existing owner accepted responsibility for the same work | A new conversation or global acknowledgement |

Use the parent contract's distinction between recorded transition and
acknowledged destination. A Bridge receipt contributes only its expressly
qualified coverage. Empty inventory alone never acknowledges a transition.

## Proposed owner interfaces

Names here are proposed internal interfaces, not installed commands or accepted
wire schemas. Review them with their implementation and contract fixtures.

- `RestoreTarget` describes the verified retained assembly and its target-owned
  state contract; constructing or decoding it grants no authority.
- `RestoreQualification` reports versioned coverage, compatibility,
  installation/source identity, participant identities/revisions and blockers.
  It is an observation, not a transferable permission token.
- `RestoreFence` is live lifecycle custody: existing native owner references,
  installation transition identity, admission epoch and retained-owner state.
  Its effect checks run at the actual owning boundaries. Serialized JSON,
  caller labels and a boolean `complete` cannot manufacture one.
- Closure/inspection/settlement extend user-scoped Bridge administration and
  existing owner methods. They must not be exposed as unauthenticated health
  authorization or conflated with ordinary `/drain`.

Preflight verifies supported owners and target contracts without changing
admission, files, routing or native processes. Unsupported preflight refuses
before closure or installer effects. Admission may still change while preflight
runs: closure and final compatibility must revalidate the observed identities.
Failure after closure reports the resulting closed/blocked state honestly.

## Admission epoch and producer enrollment

Extend the current Bridge database with one installation-scoped lifecycle
transition/epoch record, not a parallel operation ledger. Bind it to canonical
installation identity, original transition ID, expected predecessor native
birth, current runtime incarnation and requested retained assembly. Persist
epochs monotonically; selecting older code never rewinds authority.

Each mutation-producing cohort enrolls through its actual owner inside the
established user-owned access boundary before its first effect.
Enrollment binds role, native/runtime incarnation,
supported custody contracts and existing operation associations. It is neither
an arbitrary out-of-boundary client declaration nor a heartbeat lease.
The [consumer refinement](consumer-recovery.md) defines the local trust and
cross-container boundary without per-agent authentication ceremonies.
Include daemon/background producers, acquisition and CLI launch callers, extension inbox consumers,
native prompt transports, local hosts and supported remote relationship owners.
Independently owned or unknown participants require their own compatible
attestation, or block; a local process label does not enroll a remote owner.

Closing admission CASes the existing open epoch under `Database`'s write lock
and one short SQLite transaction. Final admissions check that same epoch and
their current enrolled incarnation in the transaction recording the original
operation. An admission first retains an outstanding participant; closure
first refuses it before its effect. No remote call or awaited work holds the
SQLite write lock.

An admitted operation rechecks its retained authority at the final native/SDK
boundary. Closure prevents fresh effects but permits identity-bound outcome
reporting, observation and necessary cleanup. Already authorized work may
settle, or transfer through the same owner. Cleanup cannot create a new prompt,
start/resume a conversation or silently reopen admission. Pending/unknown
operations survive process death and expiry without acquiring retry permission.

## Cohort coverage and source ownership

| Cohort and existing owner | Proposed extension and final fence |
|--------------------------|------------------------------------|
| `db_core.py`, `db_schema.py`, `db_sessions.py`, `session_core.py` | Durable lifecycle epoch and enrolled producer observations inside the existing DB owner; session/turn admission retains epoch and runtime association. No migration or writer startup during compatibility inspection. |
| `workspace_creation.py`, `provider_creation.py`, `acquisition_runtime.py`, `acquisition_journal.py`, `acquisition_store.py`, `acquisition_executor.py` | Extend current embodiment/attempt/effect rows and executor leases. Inventory allocating, preparing, launching, pending and completed-but-unsettled effects; fence again immediately before allocation, native launch, resume and delivery. |
| `db_prompts.py`, `db_sessions.py`, `session_prompts.py`, `native_keyed_admission.py` | Preserve atomic queued-row consumption plus turn creation. Extend the original turn's native-admission metadata with transport custody/outcome; persist before send and reconcile the same turn/invocation after interruption. |
| `db_live_sessions.py`, `db_live_message_receipts.py`, `db_live_controls.py`, `routes/live_sessions.py` | Extend existing `live_messages` rows and consumer registration; atomic per-row handoff before returning a poll result, epoch-bound control claims, retained acceptance/consumption/outcome distinctions. |
| `extensions/agent-bridge/extension.mjs`, `delivery.mjs` | Negotiate/enroll the real consumer incarnation, check its original handoff at final SDK admission, report acceptance and consumption separately, and preserve ambiguous outstanding work on reconnect. No new independent outbox. |
| `session_start.py`, `session_resume.py`, `session_lifecycle.py`, background recovery/handoff owners | Enroll current producers and fence final start/resume, wake/redrive, teardown and ownership transfer, including direct internal callers outside ASGI middleware. |
| `session_host/host_index.py`, native private receipt/commit owners, `remote_relationships.py` and `relationship_authority.py` | Strict fresh inventory under the existing index mutex; retain native references and negotiated host/relationship custody. Fence final attachment, launch and handoff in the actual host/transport owner. Unknown remote custody blocks rather than triggering remote inspection. |
| `app.py`, `app_routes.py`, `auth_custody.py`, `session_drain_gate.py`, `routes/admin.py` | Wire owner enrollment and lifecycle closure through ordinary startup, authenticated control and private restored activation. Request quiescence is only one cohort; no auto-undrain may override the retained epoch. |

New focused helpers may be introduced under `src/agent_bridge/` for the
qualification types, DB-owned epoch queries and cross-owner inventory
composition. They do not replace the owners above. Preserve module-size limits
by extracting coherent helpers rather than enlarging grandfathered modules.

Inventory the complete actual producer call graph before claiming the cohort
table is exhaustive. Every newly discovered effect path must be enrolled and
fenced or explicitly make qualification incomplete; do not silently omit it.
SDK methods lacking a fenceable/deduplicated boundary remain unsupported for
automatic replay. Ordinary unqualified operation can retain its existing
behavior without advertising restore qualification.

## Retained inbox handoffs and native reconciliation

For qualified consumers, polling atomically records an original row-bound
handoff and consumer incarnation before returning the body. Poll retries expose
the retained attempt, not another permission to send it. The consumer retains
that identity through final admission, SDK acceptance and attributed consumption.
Another native session, alias rollover or consumer incarnation cannot inherit
it by label. These are correctness and replay fences, not a cryptographic
identity requirement for equally privileged participants inside the user's
established boundary.

The window between the final owner check and an SDK call is itself outstanding
custody. Closure must wait for a known outcome or retain it as unknown; it must
not acknowledge merely because a later check would reject a new attempt.
SDK acceptance without consumption continues to appear in inventory.
Consumption does not certify downstream semantic completion. Claimed controls
also remain outstanding until their actual outcome is known.
Accepted payloads and original handoffs remain durable across missing replies,
disconnects and timeouts; unknown outcome is retained work, not a discard state.
Recovery follows the [lossless consumer contract](consumer-recovery.md#delivery-and-recovery).

Preserve the native keyed-invocation and turn journals. A crash after native
acceptance but before DB settlement leaves the same invocation unresolved.
Reconcile using the actual native owner/receipt and existing idempotency rules;
where the native API cannot prove absence, return blocked/unknown, never resend.
The extension's bounded in-memory tracking is not complete durable inventory.
Interrupt/requeue logic must not reconstruct new send authority from it.

Legacy poll consumers can already hold returned bodies without a handoff row.
They are unsupported until their real owners establish closure and reconcile
that uncertainty. Missing rows, terminal status, expired registration or a
missing ACK never establish absence. No transcript inspection or protected
session intervention is authorized to fill the gap.

## Strict inventory and lock discipline

Use the existing installation lifecycle serialization outside short owner
critical sections. The lifecycle owner first pins its installation/transition
identity, closes the Bridge epoch, then gathers qualified cohort evidence.
Database inventory reads all relevant rows in one transaction without status
filters or result limits hiding old unsettled operations.

Host inventory holds the existing cross-process index mutex, reloads fresh
bytes and validates the whole format, versions, identities and native bindings.
Malformed records, duplicate keys, links/reparse points, permission errors or
unsupported fields refuse; do not use cached `all()` or skip undecodable rows.
Missing legacy evidence is unknown unless an authoritative enrolled baseline
establishes legitimate absence. Observation must not create/flush an empty index.

Do not hold a DB transaction across host/native/remote calls or nest DB and host
mutexes in opposing orders. Instead, closure prevents new admissions, each
owner returns a monotonic membership/operation revision, and a final DB/epoch
check verifies the exact enrolled cohort and corresponding revisions. Settlement
can advance those revisions, requiring another bounded read or a visible blocker.
A native operation accepted before closure remains in the inventory even if
membership or task status subsequently changes.

The resulting certificate binds the exact transition, epoch, installation,
assembly and predecessor native reference. Recheck it before passive launch,
publication, predecessor retirement and cleanup. A changed route/native birth,
restarted participant or changed membership invalidates it. None of this is
global Dispatch/adopter acknowledgement.

## Compatibility and custody handback

The retained target declares which durable state contracts and operation
variants it can read, preserve, reconcile and write, including closure epochs,
native admission, inbox handoffs, acquisition histories and host protocols.
Inspect the actual verified target through a side-effect-free, read-only entry;
do not derive this declaration from HTTP generation or a DB version number.
The current owner compares it with the actual closed inventory and migration
history. Missing/unknown contracts, lossy downgrade or unqualified legacy
records refuse. No migration, history deletion or credential creation is allowed
to make a target appear compatible.

The controller also verifies actual passive preparation under that contract:
read-only state hydration, no discovery-driven mutation, no autonomous claims,
no queued sends and no shared relay activation. Existing private reconstruction
is a component to extend, not proof of a full target or installation restore.

Custody may move only through existing host/native owner protocols with explicit
acceptance tied to the original work, attachment generation and native birth.
Retain live references through acknowledgement. If transfer is unavailable,
known work must settle naturally or remain blocked; do not stop it to obtain
qualification. Reverse handback closes the destination epoch and applies the
same barriers without reviving an old capability.

After any crash, recover the existing transition and operation identities.
Unknown settlement remains unknown. A new controller must earn existing-owner
custody rather than minting a new transition, sender or native conversation.
Protected-owner and adopter-effect restrictions apply in both directions.
