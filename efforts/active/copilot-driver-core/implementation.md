# Copilot Driver implementation map

Parent and completion gate: [main effort](README.md).
This is the reviewed-before-code implementation proposal, not runtime support.

## Extend the existing owners

| Current source seam | Refactor responsibility |
|---------------------|-------------------------|
| `plugins/agent-bridge/src/agent_bridge/session_host/` | Move the real Host/protocol/local lifecycle into the core. Keep Bridge's venue transport, relay and coordination adapters outside it. The spike is not the production implementation. |
| `session_host/host.py`, `launcher.py`, `private_attachment.py`, `private_commit.py`, `acp_boundary.py` | Preserve frame/ack identity, custody, bounded bootstrap, private acquisition and native process containment rather than rewriting the proven Host. |
| `session_host/bundle.py`, `receiver_policy.py`, portable receiving support | Use the new core's selected receiving closure, canonical repository-policy reader, resources and notices. No target-suite command becomes a bootstrap requirement. |
| `worktree-manager/bin/launch-session.{ps1,sh,cmd}`, `pane-launch.ps1`, `relocated_launch.py` | Move Copilot mux/pane execution to the core; Manager retains UI/window selection and calls the core. |
| `agent_worktrees/sessions.py`, `mux_creation.py`, pane/seed composition | Worktrees resolves workspace/agency and delegates execution. Preserve nesting cleanup, create/reuse gates, seed ownership and receipt semantics. |
| `agent-remote-driver` extension/control/registry modules | Preserve standalone use and mutation fencing; add launch-bound broker integration without copying another SDK driver. |
| `tools/runtime_application_project.py` and existing artifact/installer inventory | Declare the independent app and its reusable receiving export through these owners, not a bespoke installer or service. |

## App and receiving package

Agent recommendation: retain the `agent-remote-driver` namespace for this
refactor, with an independently buildable Python core alongside the existing
JavaScript extension. The Python implementation is the canonical
`libs/copilot-driver-core` distribution, consumed by Bridge and the Driver
application connector through ordinary first-party library references. This
preserves the existing materializer and build owner's library attribution,
rather than introducing sibling-plugin source dependencies.
Reuse the repository's wheel/application-project model
for ordinary installation and its compact receiving-archive mechanism for
hoisting the same canonical code and assets. The plugin remains a connector;
the app must run without plugin enablement.

The core must not import Bridge, Worktrees or Manager to launch or survive.
Small client/compatibility surfaces may compose the app, but its process must
run from the selected core environment/artifact, not a caller-owned venv or
checkout that disappears on caller update. Retain the necessary app generation
and extension/pane assets until their execution leg retires.

Use the existing first-party dependency/resource closure and admission rules.
Do not introduce a second resolver, mutable source install, third-party vendor
copy, whole-suite receiver or global startup daemon. Copilot and mux/native
prerequisites remain explicit, with their existing setup owners. Measure the
actual cold/repeat delivery-to-ready flow, not only archive creation.

## One broker, two backends

Headless mode preserves direct ownership of Copilot and its native ACP
connection. Headed mode owns the canonical mux execution and receives a
launch-bound connection from its selected extension. Both reach one Session
Host broker; SDK adaptation is a backend edge, not another conversation.

Preserve the existing TCP/frame contract for supported retained participants
while expanding the core's ACP-compatible WebSocket/TCP support provider-first.
Use the existing public `acp.v1` WebSocket JSON-RPC convention. HTTP/SSE alone
is not ACP interoperability; do not introduce a parallel envelope protocol.
The ordinary locked application carries the WS dependency and its metadata;
the compact dependency-free receiving archive remains TCP-only and advertises
that limitation. Neither artifact may claim the other's transport support.
Expose actual support from the executing app and running broker, separately
from package versions. New consumers select support before launch effects.
Strict unknown-argument and required-policy refusal remain.

The result relates a reserved endpoint, host/child or mux incarnation, native
session and current controller. A listener, PID or mux alone is not native
binding, input readiness, prompt acceptance or completed restoration. Credential
material stays private and out of argv, public descriptors and diagnostic output.
Reuse existing nonce/control and OS-user trust boundaries; no new universal
identity ceremony is needed.

The extension is supplied at process creation through the existing
`--plugin-dir` mechanism and scoped trust. Do not rewrite global settings,
broaden native tool permission, or assume warm join has creation-time
permission/input parity. Unsupported ACP/SDK interactions report honest
capability limits rather than silently accepting a weaker operation.

## Consumer convergence and compatibility

Workspace allocation, repository eligibility, staged prompts, original attempt
identity, preferences, auth/relay and durable agency remain with their current
owners. The core receives their selected execution request and owns its process
and broker result. It must not create another worktree, launch journal, task
queue or credential store.

Expand core providers and declare support before migrating callers. Keep
supported legacy imports/wire behavior through thin adapters where needed,
without retaining parallel launch implementations. Existing old hosts keep
their selected custody and recovery; this source refactor does not migrate or
terminate them live. Failure after possible admission never selects an old
launcher or replays a seed.

Migration is complete only after Manager launch-session, Worktrees
embody/copilot and Bridge create/resume, including their ordinary local and
remote paths, consume the same core. Native AHP/SDK/application peers remain
provider-honest; the refactor does not silently change mode defaults.

Recordless normalized setup now enters the retained core after setup hooks
through #12186, without a fabricated worktree record. Explicit runtime-root
resolution, policy-checked directory pinning and private execution storage are
covered by source/synthetic consumer controls. This does not complete the
caller/mode matrix or native, installed and all-platform receiving acceptance.

The [finite caller census and #12220 acceptance](journal/2026/11.12%20private%20mux%20creation%20convergence.md)
close selected-entrypoint source migration. Manager and Worktrees select the
canonical creation/execution owners; Bridge local and portable headless paths
consume the common Host; portable headed receiving now uses the copied core
for private-socket creation as well as pane execution. Original attempt,
admission, socket identity and no-replay custody remain with their callers.
This does not retire supported older-runtime adapters or close the separate
fresh/resume/reuse and attached/detached/new-window/no-mux validation matrix.
The remaining duplicate-implementation boundary must be evaluated against the
actual selected runtimes and supported rollback window, not removed merely to
close a checklist item.

## Lifetime and activity evidence

Interactive execution stays alive until Copilot is closed by the user or an
explicitly authorized owner retires it. Detach, a quiet foreground, caller loss
and caller cleanup Jobs do not supply automatic CLI expiry.

ACP can stop at caller request or after its configured idle allowance only
with no pending background tasks, agents or schedules. Replace the old
disconnect-driven interpretation for new core sessions; retain supported old
host contracts explicitly rather than reinterpreting their records.

Use native structured idle/task/schedule observations or a qualified
runtime-owned snapshot. Establish initial state as well as future changes;
a reconnect with missing events cannot invent a known-empty registry.
Coalesce observation off SDK callbacks and revalidate before expiry. Work
arrival cancels the grace decision. Unsupported or incomplete activity evidence
retains the session and reports that automatic expiry is unavailable.
Textual tool-result heuristics alone are not complete pending-work evidence.
This is distinct from an explicitly requested maintenance broadcast followed
by subsequent idle; no extra cleanup-audit gate is added to that operation.

### Inventory-capable SDK backend proposal

This refinement closes the vision's `mode-appropriate-session-lifetime` and
`idle-expiration-preserves-pending-work` requirements without changing mode
defaults. It remains a proposal until reviewed; inventory qualification is not
production adoption or automatic-retirement qualification.

The native controls in #11994, #12005 and #12008 establish that the public
Python SDK can list session-scoped client tasks, agents, shells and future
schedules. A root-idle event and an empty task list can coexist with a future
schedule. Native ACP currently does not expose that inventory: a missing method
is unavailable evidence, never an empty set. These are distinct provider
capabilities, not a reason to send SDK RPC methods over the ACP wire.

**Recommended boundary.** Preserve direct native ACP as the default headless
backend. Add an explicitly selected, optional public-SDK headless provider at
the existing core backend edge; it owns one native process and one selected
session behind the same Session Host. The SDK owns its native protocol, while
the adapter translates the supported public ACP operations and native events.
It must not implement an internal SDK wire client, copy generated SDK code,
create a parallel broker or join a second conversation merely for inventory.
Prove new/load/prompt/cancel, restoration and reconnect semantics before
migrating a caller. Unknown acceptance cannot trigger another backend or seed
replay.

The existing headed `createHeadedAdapter` remains the canonical adapter for a
launch-joined extension. Its current text-input, control-loss and native-input
readiness semantics are not interchangeable with headless new/load semantics.
Share backend-independent activity interpretation and Host lifecycle policy,
not an invented cross-language SDK transport. Python SDK qualification does
not prove that a joined JavaScript extension exposes the same inventory or
observer-drain operations; headed support needs its own native controls.
Coordinate changes in the receiver-owned auth seams through their existing
owner rather than folding them into this provider work.

**Distribution.** Keep the base core and native-ACP path usable on Python
3.10. The measured Python SDK requires 3.11 or newer; carry it only in an
explicitly selected optional application dependency closure using the existing
locked build/install owner and dependency-age rules. Selection on an
ineligible interpreter or artifact refuses before process/session creation;
there is no automatic interpreter download, global install or silent floor
bump. Declare support from the executing artifact and running backend, not
from an installed SDK version. The dependency-free compact receiver stays
TCP-only and native-ACP-only unless a separately reviewed receiving closure
qualifies SDK delivery. Ordinary base-only installation and supported rollback
must remain usable without this optional dependency. A Node SDK provider is
an alternative only after equivalent public-API and delivery qualification,
not an assumed substitute for the measured Python path.

**Observation and retirement.** Represent foreground state, session-scoped
tasks and future schedules separately, each with explicit known/unknown state.
Install observation before the initial inventory and reconcile concurrent
changes without treating a locally synthesized counter as native completeness.
Coalesce callbacks into bounded work; no synchronous SDK calls, per-event
processes or unbounded polling in event handlers. Terminal task retention is
not pending work, but idle-yet-live agents and future schedules are.
Inventory loss, session mismatch, overflow, reconnect gaps and SDK failure
invalidate the retirement decision while preserving execution and custody.

Inventory availability and automatic idle retirement are separate capabilities.
Keep `automaticIdleRetirement: unavailable` until the provider qualifies
complete initial/ongoing evidence and the final admission/retirement race,
including internally scheduled work. Two empty lists or freezing only Driver
inputs cannot establish an atomic native idle boundary. If the public native
contract cannot supply a safe boundary, retain unavailable status rather than
substituting timed polling, tool-output parsing or `wait_for_pending`.
That SDK wait covers agent/shell follow-ups, not client tasks or future
schedules.

Explicit caller stop remains distinct from idle expiry and observer retirement.
A public SDK `disconnect`/`stop` return is not proof that an old joined
observer drained or that every native descendant settled. Preserve the
existing clean-path observer-close/drain and whole-session-settlement
qualification results without reinterpreting a refusal as success. The
proposed crash path below does not require a clean SDK certificate; it still
requires current authority and independent whole-scope settlement before any
replacement. Neither proposal enables current auth recovery.
CLI foreground lifetime remains independent of all ACP idle policy.

**Promotion order and validation.** First review this boundary, then add a
provider-first inventory adapter with truthful capabilities and automatic
retirement still unavailable. Qualify through the real broker and selected
artifact: native session identity, initial nonempty client/agent/shell/schedule
inventory, future-schedule retention after root idle, isolation from a second
session, cancellation/completion/removal, callback races and unknown outcomes.
Exercise missing optional dependency/interpreter, base-only 3.10 installation,
compact-receiver refusal and supported older consumer/provider rollback.
Keep process/native/clean-room lanes explicit and bounded, not in required
unit-only CI. Only a later qualified lifecycle slice can enable idle grace;
neither this plan nor an inventory-only merge completes the parent's lifetime,
caller-convergence, receiving or observer-retirement gates.

### Uncertainty-preserving crash recovery proposal

This agent-recommended refinement addresses abrupt transport loss and observer,
process or host restart. It remains a source-contract proposal until reviewed;
no observer, service, credential mutation, live retirement or activation is
authorized here. Existing clean-path negative evidence, including #11969,
remains valid rather than being relabelled as a pass.

**Original owner and transition.** Reuse the original launch/lifecycle owner's
durable store and admission operation, not a second fleet authority or a
per-session reconnect daemon. Retain one recovery request/operation identity,
owner/worktree/source revisions, durable conversation, environment and boot,
exact native-scope incarnation, bounded observations and references to uncertain
external work. Store classifications and owner-operation references, not raw
process logs or credentials. Record intent, confirmed settlement, resume intent
and restored observation separately. Observer restart reconciles that original
transition; an uncertain effect is not repeated under a fresh request identity.

**Classify, settle, restore.** Silence, missing status and an unreachable host
are not authority or proof of absence. Distinguish active work, waiting input,
transport loss, authentication failure, proven old-boot retirement and unknown
state. Current policy and exact enrolled custody precede retirement;
independent whole-scope settlement precedes any replacement. Pending or
unobservable work remains unknown, even when the process scope is settled.
Optional clean SDK detach/drain evidence must not be invented to clear that
uncertainty.

With an approved current signed-in source and original-owner admission, restore
observation of the same durable conversation without a launch seed, last prompt
or external-action retry. With no approved source, remain held; do not perform
unattended login or substitute another identity. Restoration is not quiescence
or task completion.
Affected uncertain work stays held for reconciliation through its existing
owner. An uncertain resume result requires observation of the original
transition and possible new incarnation, not another launch.

**Keep distinct contracts distinct.** A generic native owner's root exit or
`terminate` return is not a whole-scope certificate. Qualified platform
providers retain their existing Job/guardian/census and retirement owners.
The existing exact cross-boot Worktrees restart path stages a STATUS FIRST
seed/acknowledgement; it must not be silently reused as this seedless crash
operation. The existing seedless argv helper supplies argument composition,
not launch authority or restoration proof. Local source-lease/owner-death
enforcement remains independent: an occasional remote sweep cannot guarantee
hard expiry while its observer or network is unavailable.

**First bounded leaf and acceptance.** The receiver contributor can prepare
side-effect-free crash-candidate/status export and a strict transition reducer
through an original-owner-supplied store/compare-and-swap boundary. Keep effect
callbacks unwired and native/automatic recovery unqualified. Actor-free durable
store tests cover reopen after retirement or resume intent, duplicate and
conflicting requests, changed boot/source/owner/lease, missing settlement,
unknown external effects and lost resume acknowledgements.

The actor-free reducer and `RecoveryStore` protocol landed in #12144; the
complete codec and passive projection landed in #12150. Worktrees' actor-free
original-owner store landed in #12157 through its existing guarded tracking
writer and per-worktree launch state. It is not current native admission or
productive recovery activation. Other original owners supply their own
adapters outside the standalone core. Resolve canonical owner paths
through that owner, never from untrusted descriptor strings. Compare every
descriptor field, reserve absence at revision zero, retain immutable prior
steps and operation-ID uniqueness, and acknowledge only durable atomic commit.
An ambiguous writer response requires a read of the same original operation;
it must not select direct-write fallback after possible admission.

The receiver contributor's bounded record codec and passive store-read
projection are source-complete in #12150. Preserve ordered steps, unknown
effects and strict version/type/size refusal without repair or truncation.
A reducer/CAS return, including an identical-step replay that returns a later
record, never dispatches a native effect.

**Next original-owner admission and provider boundaries.** Bind the exact
descriptor and original operation to the retained native caller/scope and the
currently approved source selection at the action boundary. Compare the
actual owner, source and descriptor revisions, conversation/incarnation,
environment/boot/scope and monotonic authority revision. An opaque lease ID,
stored evidence reference or source-file presence is not that binding.
Matching owned input is the positive source-control case; missing, stale,
revoked, expired or changed target/route evidence stays unknown or held
before effects. A lost acknowledgement reconciles the same durable operation.

When a configured source uses a Gateway, its existing owner retains
authentication, enrollment and the actual consumer/controller relationship.
SDK availability or a source-lease helper alone does not qualify the routed
grant. Keep credential responses on that owner's admitted confidential API,
not ordinary publication/subscription metadata. This is an adapter-specific
acceptance dependency, not a mandatory Gateway for unrelated sources or a
new issuer, broker, store or per-session feeder.

Qualified platform APIs currently settle through the same retained live
owner. Durable descriptor-to-owner reacquisition or authoritative old-boot
scope absence after coordinator loss remains a missing provider contract.
Do not fill it with root exit, EOF, a local deadline, an internal provider
confirmation call or a journal-generated certificate. Keep original-owner
admission/store and receiver/platform custody separate; declare and review
the binding and provider seams before any native-facing consumer leaf.

Only subsequent reviewed provider integration and independently admitted
process/native controls can qualify exact scope settlement and same-session
history restoration. Test observer death, abrupt native loss without a final
event, partition versus proven reboot, authority expiry and pending unknown
effects. Required unit-only CI is not widened; clean SDK observer close remains
an optional improvement, not this crash path's critical dependency. No current
receiver marker, refusal or activation gate changes with this proposal.

### Original-owner recovery admission binding

This is the proposed callable boundary before a native-facing consumer leaf,
not an implemented admission API. It specializes the recovery proposal's
current-authority and original-operation requirements without adding an issuer,
broker, scheduler or cross-process admission verb. The original Worktrees
launch owner supplies the adapter; the standalone core retains only its
actor-free descriptor, reducer and store protocols.

**Retain the binding at the original launch.** Alongside its existing launch
correlation, the caller retains the exact `RecoveryDescriptor`, canonical
worktree record and creation nonce, selected source consumer, approved
source-selection revision and native provider owner. The first recovery intent
reserves its operation ID durably; subsequent reconciliation retains that ID.
The descriptor's eleven fields must come from those owners' actual launch
state, not be copied from an incoming recovery request to make it match.
The native owner is the retained custody object, not a PID, process name or
stored evidence reference. A recordless caller supplies its own original
lifecycle owner; it does not fabricate a Worktrees record.

**One owner-scoped admission operation.** The proposed
`recovery_admission(descriptor, operation_id, phase, expected_revision)` belongs
inside that original lifecycle operation. `phase` is `retire_intent` or
`resume_intent`, not an arbitrary execution request. Before exposing any
effect-bearing provider call, it must:

1. Re-read canonical owner state through its existing mutation/launch fences.
   Compare the worktree incarnation, original launch correlation, recovery
   operation and every descriptor field with the independently retained
   binding. Reject changed owner intent, missing state or another operation.
   `WorktreeRecoveryStore.load` supplies durable transition state, not the
   current native/source binding or permission to act.
2. Obtain current source-selection evidence from the selected consumer's
   existing owner. Match its source owner/revision, approved target, account,
   recipient, route and controller generation to the original selection.
   Retain the credential-bearing client/lease privately; do not put tokens,
   capability files or credential responses in the descriptor or journal.
   An unavailable current selection is held, not the last successful selection.
3. Check current execution permission through the original execution-admission
   owner, separately from current account/source authorization. The
   [permit-producer proposal](#original-execution-permit-producer-proposal)
   below separates these authorities and requires an explicit contract evolution;
   it does not reinterpret existing version-one revision fields. Use the
   retained source lease's conservative `remaining_seconds()` at the action
   boundary too; token expiry, a counter, wall time or source-file presence alone
   is insufficient. Changed, revoked, expired or unconfirmed permission or
   source authorization refuses before restoration. Ordinary provider-owned
   expiry cleanup remains independent of this recovery operation.
4. Require the exact retained provider and its original conversation,
   incarnation, environment, boot and scope binding. For restoration, require
   the original operation's independently confirmed whole-scope settlement.
   A journal `settled` step is not a provider receipt. Refuse unsupported
   reacquisition after owner loss or an unproven old boot without substituting
   root exit, EOF or a private platform confirmation helper.
5. Reconcile the expected store revision, exact intent and phase. Intent
   creation belongs to the existing guarded CAS writer, after pre-effect
   binding checks; final admission re-reads that durable intent and rechecks
   the live binding. Preserve unknown external effects and all prior holds.
   A duplicate request inspects the same transition; a CAS replay or later
   record never dispatches the effect again. A lost writer or provider
   acknowledgement remains unknown under that same operation ID.

Keep the binding live through the provider's final admission callback, not as
a reusable boolean, serialized approval or journal-generated certificate.
Source-selection changes and competing original-owner operations must be
serialized or revalidated at that final boundary. Do not retain a tracking-file
lock across network calls or asynchronous provider waits. Once execution is
admitted, the selected receiver/provider retains its own hard-expiry and
owner-death enforcement; a remote observer sweep is not a substitute.

**Actual providers and remaining expansion.** The source helpers currently
return `SourceLease` or `HttpSourceLease`, with conservative remaining lifetime
and an opaque lease ID. `HttpSourceLeaseClient` retains its explicit
source/account/recipient/host and authenticated endpoint selection;
`CopilotAccountFile.install` checks the account, host and live lease before its
own credential-file effect. These helpers do not supply a Driver recovery
binding or monotonic execution-authority revision. `acquire_source_lease`,
`HttpSourceLeaseClient.acquire` and `CopilotAccountFile.renew` perform new
source or credential effects; admission inspection must not invoke them as a
read-only fallback.

`AsyncForegroundProcess` and Windows `AuthPaneChild` settle through the same
retained live owner and their public asynchronous lifetime methods. Neither
currently accepts a durable recovery descriptor for post-coordinator-loss
reacquisition. Provider expansion must declare that capability through its
existing owner before a consumer uses it. No current source helper, recovery
store, native launch-seed callback or cross-boot status-first path fills that
gap. Ordinary supported execution remains unchanged when recovery is
unavailable; there is no effectful fallback or rollout implied here.

**First implementation and acceptance.** Start with an actor-free,
original-owner admission inspector over retained owned inputs. Its output is
a refusal or candidate classification with effects explicitly unwired, never
a callable native effect or transferable authority. Exercise one matching
control, every descriptor-field mismatch, changed worktree nonce/operation/
source selection/route/controller, opaque-ID versus monotonic-revision
confusion, expired or revoked authority, missing provider owner or settlement,
concurrent intent changes and lost acknowledgements. Reopen through the actual
Worktrees store and verify that neither inspection nor duplicate reconciliation
acquires a lease, changes credentials, launches, retires or resumes anything.
Only after that contract and provider expansion clear review may an effectful
consumer be proposed and separately qualified through the real routed
source/controller and native provider. Source-only fixtures do not complete
the parent's native recovery, lifetime, receiving or rollout gates.

The passive inspector landed in #12207. It reads the actual Worktrees store
and launch journal, retains original owner intent across reopen and refuses
opaque provider state without wiring effects. Its matching candidate is not
current-source verification, a native settlement receipt or final admission.
The source/provider expansion and effectful consumer gates above remain open.

The live-only provider seam in #12299 and passive consumer in #12303 now bind
settlement inspection to the exact retained provider and its original opaque
scope token. The token is captured with the original binding, not recaptured
on inspector reopen; changed tokens refuse or require reconciliation.
The existing settlement observation alone cannot produce a restoration
candidate without the same provider's passive completed-cleanup confirmation.
Older or unconfirmed providers remain unknown. This does not publish the
complete original launch descriptor, verify current source/controller authority,
reacquire a lost owner or qualify native recovery.

### Original-execution permit producer proposal

This agent-recommended refinement addresses an actual missing contract, not
implemented support or new effect authority. The reviewed Gateway/account-source
path supplies credential-source authorization, not an original execution permit
or monotonic execution-authority revision. Authentication, application tokens,
request proofs, policy revisions and channel generations do not supply that
missing decision. Do not make Gateway a mandatory Driver dependency or ask its
credential helper to invent execution authority.

This refinement serves the Driver vision's
`prompt-and-permission-honesty` and `preserved-native-restoration` behaviors.
It preserves the pattern invariants for existing ownership, optional composition
and selected-runtime contract evolution. The standing intent is unchanged.

**Two independent decisions.** The existing source/account owner supplies
current approved source-selection evidence and bounded authorization lifetime.
The original lifecycle owner separately supplies permission for the exact
execution/recovery action. A valid credential does not permit retirement or
restoration. An execution permit does not approve another account, source,
recipient, route or controller, or authorize obtaining new credentials.

The original lifecycle owner records only an already-authorized decision
through its existing guarded launch/admission writer. It does not create a new
issuer, transferable bearer token, global policy store or Core-owned worktree
authority. Neither a launch receipt nor an initial start permission silently
authorizes a later stop, restored execution or changed resource. Missing
action-specific authority remains unavailable; a recovery request, matching
record, generated counter or saved approval reference cannot create it.

**Retained permit and current decision.** Bind the owner decision to the original
launch operation/correlation, worktree creation nonce or equivalent lifecycle
incarnation, durable conversation, exact enrolled native scope, approved source
selection and allowed action. The existing owner retains the original approval
evidence and all limits privately; the recovery journal stores bounded owner
references, not credentials or copied approvals.

The execution-admission owner advances a monotonic permit revision only as part
of a real, durably committed authorization change, including revocation. A
revision orders that owner's decisions; it is not permission by itself.
Reconciliation preserves the original operation and permit identity after a
lost acknowledgement. Observer restart cannot mint a replacement permit or
reset the revision. Current action admission rechecks the actual decision and
its limits, current source evidence and the exact native binding through the
provider's final callback. Do not hold a tracking lock across network calls.
Unknown work, legacy holds and unconfirmed effects remain independently held.

**Explicit contract evolution.** The existing version-one
`RecoveryDescriptor.lease_revision` and `RecoveryStep.authority_revision` are
not silently redefined as this permit revision. Source-lease identity,
receiver renewal ordering and execution-permit ordering are distinct domains.
Review and introduce a descriptor/intent contract that represents the distinction
before a consumer uses it. Select support from the executing owner/provider,
not package equality. Existing version-one passive records remain readable and
unqualified; an older provider refuses unsupported permit semantics before
effects. No automatic rewriting, resealing or authority upgrade of retained
records is proposed.

**First source outcome and acceptance.** After this proposal clears review,
implement an actor-free original-owner permit producer and passive inspection
over the existing guarded owner store. Its positive control must start from
independently retained, action-specific owned authorization, not from an incoming
recovery descriptor or a credential lease. Cover missing permission, start-only
permission versus restoration, source/controller mismatch, revoked or expired
decisions, competing revisions, lost commit acknowledgements and reopen without
revision reset or effect replay. Exercise current/prior contract selection
without weakening existing version-one refusal or unknown-state controls.

Complete historical descriptor publication still requires actual original
environment, boot, conversation and scope producers. The live-only native token
does not provide them or post-owner-loss reacquisition. Missing platform or
source support remains unavailable. The first implementation adds no ordinary
caller wiring, grant acquisition, credential write, observer, native effect,
automatic recovery or rollout. Effectful integration and qualification remain
separately reviewed and admitted through their existing owners.

## Evidence before each functional promotion

Retain focused tests that fail without the extraction or adapter behavior.
Use real consumer seams, not tests that merely assert a new flag or import.

1. **Headless core:** build and relocate the selected artifact, launch an owned
   mock ACP child through the actual CLI/broker, exercise init/new/load/prompt/
   cancellation/reconnect and exact child retirement, then prove Bridge
   create/resume consume that core with preserved policy and original custody.
2. **Headed core:** actual mux/pane transport and native SDK extension loading,
   one literal multiline prompt accepted once, stable native identity on
   reuse/resume, shared broker access and independent terminal attachment.
3. **Caller matrix:** Manager attached/detached/new-window, Worktrees
   embody/copilot fresh/resume/reuse, Bridge headless/headed create/resume;
   preserve no-mux and unavailable/unsupported behavior explicitly.
4. **Lifetime:** CLI stays live through idle/disconnect; ACP stop and idle grace
   with pending shell/agent/schedule controls, activity races and unknown
   snapshots. Close every fixture-owned caller cleanup Job in durability
   controls and retain a restrictive shared-outer negative control.
5. **Distribution/skew:** no guest coordination suite or source checkout,
   complete wheel/resources/notices, ordinary install and repeat/update/
   rollback, selected older peers and retained-host compatibility, no
   bootstrap resurrection or installed-state mutation during stage-only work.

Unit CI remains positively admitted and bounded. Real-process, clean-room,
Windows/native and available POSIX/external evidence remain distinct lanes;
mock ACP or fake SDK success is not native Copilot acceptance. Use admitted
test leases/containment, exact cleanup and headless spawning. Never use a
production prompt, retained worker, live shared mux or installed service as
an experimental fixture.
