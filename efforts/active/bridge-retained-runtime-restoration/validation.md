# Validation and integration gates

Back to the [effort](README.md). **Agent-recommended prospective validation.**
No test, native rehearsal or running qualification is claimed by this matrix.
It covers [custody](custody-contract.md) and the
[installer lifecycle](installer-contract.md).

## Source integration order

| Slice | Exclusive source responsibility | Exit gate before integration |
|-------|---------------------------------|------------------------------|
| A - Proposal | This effort, parent specialization links and effort index | Independent architecture review and normal proposal landing; implementation remains paused until that gate |
| B - Admission and inventory | Existing Bridge DB/acquisition/session/host owners and focused helpers | Final-admission/closure interleavings, strict inventory negatives and explicit legacy/incomplete outcomes |
| C - Consumer/native custody | Existing inbox/control rows, extension consumer, native turn/host owners | Original-operation reconciliation across poll/send/ACK, launch/accept/settle and consumer/native restart; no replay on ambiguity |
| D - Target and custody transfer | Current deploy owner, target declaration, passive runtime, accepted host/retention transfer | Real positive qualified path plus unsupported targets; complete state compatibility and continuing-custodian proof |
| E - Installer convergence | Dedicated shell branches, wrappers, platform integration and publication recovery | Full current-controller restoration with exact executable/route/manifest/native integration readback; no forbidden fallback |
| F - Native/release/adoption | Assigned qualification and integration owners | Platform-specific evidence, ordinary source review/release and separately authorized installed-cohort acceptance |

The [provenance layout prerequisite](provenance-layout.md) refines only the
source-evidence portion of this matrix. Its own review precedes implementation;
complete lossless provenance, immutable artifact integrity and all canonical
reader/writer entry points must pass before returning to the inbox source PR.
It does not change the native, custody, installer or adoption gates below.

The [consumer enrollment and recovery refinement](consumer-recovery.md) defines
the next C-slice prerequisite: user-owned local trust, authenticated
cross-container proxy ingress and lossless original-message recovery. Its
validation obligations refine C7-C9 and C13 without adding per-consumer
authentication or weakening actual SDK outcome evidence.

Do not edit another contributor's uncommitted work or assume an adjacent
lifecycle fix has landed. The Integration owner supplies reviewed committed
dependencies and resolves content conflicts before each slice. Shared library
changes, if needed, have separate explicit ownership and canonical vendoring;
this effort does not authorize casual shared ZDD/cold-takeover changes.

Dispatch's coordinator/supervisor enrollment and adopter integrations remain
separate dependencies of the parent effort. Bridge-local success cannot close
those gates. No direct external DB inspection or adopter mutation is a test
fixture for Bridge qualification.

## Objective regression matrix

| ID | Interleaving or negative control | Required result | Evidence lane |
|----|---------------------------------|-----------------|---------------|
| C1 | Close concurrently with keyed allocation, start/resume and queue admission | Exactly one serial order; admission first retains the original outstanding operation, closure first prevents its effect | Deterministic owner/DB tests, then isolated independent processes |
| C2 | Already prepared producer reaches its final effect after closure | Final owner rejects fresh execution or retains a previously accepted operation; no check-then-untracked-effect gap | Controlled native/SDK boundary |
| C3 | Terminal task/session, old acquisition or newer wake hides pending custody | Inventory still includes the original operation regardless of status, expiry or newer work | Full-row DB fixtures |
| C4 | Missing tables/fields, unknown cohort or unregistered background producer | Explicit unsupported/incomplete; never empty or acknowledged | Unit/legacy fixture matrix |
| C5 | Cached index differs from disk; malformed/duplicate/linked/foreign host evidence | Strict locked read refuses or reports the fresh complete set without silently skipping records or rewriting evidence | Filesystem units and actual concurrent index owners |
| C6 | Host membership/settlement advances between owner snapshots | Revision/epoch recheck forces a bounded fresh inventory or blocker | Deterministic cross-owner race and isolated processes |
| C7 | Poll response precedes closure; SDK send/ACK is delayed or lost | Retained original handoff blocks acknowledgement until real settlement; second poll grants no duplicate send | Actual extension helpers with controlled SDK, then process loss |
| C8 | SDK accepted but not consumed; consumption observed but task/effect still active | All three facts remain distinguishable; no fabricated semantic completion | Consumer and receipt contracts |
| C9 | Interrupt, alias rollover, consumer restart or stale ACK | Same operation/incarnation binding; stale response cannot authorize a new send; unknown is not re-keyed | Extension/DB mixed-version fixtures |
| C10 | Native accepts an invocation before controller/DB settlement crashes | Original turn/invocation reconciles from native-owner evidence or remains unknown; no blind resend | Native protocol fixture and isolated host process |
| C11 | Claimed control or native launch has unknown outcome | Retain claim/launch custody; timeout cannot become not-applied/not-started | Existing controls/acquisition/host tests plus fault points |
| C12 | Closure versus ordinary undrain/watchdog, internal recovery or teardown | No unrelated path reopens the retained epoch or creates work under cleanup authority | Actual owner methods and controlled background tasks |
| C13 | Another installation/scope, protected owner or unknown remote participant | No control/inspection/termination of foreign work; bounded explicit refusal | Isolation negatives |
| C14 | Same DB schema and HTTP generation, incompatible target state contract | Refuse before installer effects despite those matching proxies | Positive/negative target declarations |
| C15 | Compatible target loses a custody variant or cannot preserve monotonic epochs | Refuse; no migration, lossy restore or history deletion to gain compatibility | Actual target reader against synthetic durable state |
| C16 | Passive target tries background work, migration, dequeue, claim, relay adoption or credential creation | Qualification fails; no shared effect escapes passive preparation | Isolated target execution with effect sentinels |
| C17 | Lost handoff/handback reply, reordered acknowledgement or restarted native peer | Re-observe original transition; stale native/generation proof cannot move authority | Private host/native process controls |
| R1 | Tampered payload/slot/record, source mismatch, editable/dirty/linked/foreign/stale capture | Strict refusal before selection, launch or publication | Record/assembly owner tests |
| R2 | Original record replaced after validation but before child starts | Child receives only the pinned record for the leased assembly | Actual child-input boundary |
| R3 | Concurrent install/update, slot cleanup or publication replacement | Canonical locks/leases serialize; no in-place rebuild, foreign overwrite or dropped target | Real-process lock/GC race |
| R4 | Caller shell or Job exits while child/custodian remains unresolved | Accepted custodian survives actual cleanup and holds real retention; evidence names the owner | Linux and Windows native process lanes |
| R5 | Custodian dies, or transfer reply is lost | Recovery retains the original operation and required slot protection; no false surviving lease or automatic release | Isolated crash/cleanup/recovery owners |
| R6 | Target selected while routed predecessor is unchanged or forwarded | No success from selectors; foreign/forwarded route refuses; current controller remains the lifecycle owner | Controller/route fixtures and actual loopback processes |
| R7 | Failure at each artifact replace, route publication, native handoff and manifest boundary | Honest unchanged/compensated/partial/recovery-needed state, proof-checked compensation and continuing custody | Fault injection at every effect boundary |
| R8 | Changed PID/birth/image, same-version unrelated responder or route generation | Refuse before control/publication/retirement; no broad kill | Native identity and guarded route tests |
| R9 | Published candidate may have accepted work before cleanup failure | Retain/reconcile its custody; no shutdown from health alone or reopening an unknown drain | Candidate owner/process rehearsal |
| R10 | Unit/task disabled, masked, missing, foreign, inaccessible or concurrently edited | Preserve policy/foreign bytes; no enable/start/re-register/elevation fallback | Platform mocks plus real native configuration readback |
| R11 | Successful restore without rebuilding | Exact retained executable/assembly serves; wrappers/platform/selector/provenance agree; one accepted destination owner; complete admission settlement | Real isolated positive end-to-end fixture on each supported platform |
| R12 | Ordinary non-retained deploy/reconcile/cold policy | Existing supported behavior unchanged; strict retained path never falls back into those policies | Existing focused regression families |

Real independent-process controls must retain the actual kernel locks, native
references and publication primitives. Mocks may coordinate an external SDK or
fake effect sink, but cannot mock away the custody boundary being qualified.
No test uses a live transcript, protected owner, real credential or external
service database.

## Bounded execution and portfolio

Preserve [TESTING.md](../../../TESTING.md) and the current positively admitted
unit-only, impact-scoped PR/promotion gate. Do not enroll subprocess/native/
installer tests by name or widen required CI as a fallback. New pure units need
review of their actual import/effect closure before packet admission.

Periodic/development integration uses the repository's isolated devcontainer
runner inside the invoking environment's required bounded supervisor. Stage
new source/test paths before its tracked-file snapshot; an omitted new test
is not evidence. Select the smallest affected families, preserve item/process
deadlines and report literal failures before remediation. Do not install
dependencies except after the permitted missing-dependency failure.

Suggested new test groups are strict restore records, Bridge admission epochs,
strict custody inventory, extension handoff receipts, native-send settlement,
target compatibility, continuing retention and retained installer convergence.
Reuse existing acquisition, queued-prompt, live-control, private host,
serving-reconcile and installer admission regressions instead of duplicating
their ownership models.

Proposal publication runs documentation-required checks and normal hooks only,
without runtime tests or an expanded CI portfolio. Documentation readback,
structure checks and diff inspection are not runtime qualification.
Implementation later adds a patch changefile through `tools/changefile.py`,
with no hand-edited versions or hook bypass.

## Evidence and completion rules

Each result records source revision/assembly, case IDs, actual platform,
executed nodes, skips and explicit unsupported cases. A skipped Windows case or
mocked Linux test does not qualify Windows, native ownership or live operation.
Power/process loss, ordinary process exit and caller Job cleanup are different
boundaries and need distinct controls.

The positive restore proof must read the actual serving route/native image and
effective platform integration, not just a successful helper return or selected
marker. Historical production captures cannot satisfy a new custody contract.
Where the existing canonical cleanup/retention owner cannot preserve an
unresolved transition's demanded slots, R5 stays open and restoration stays
unqualified.

Architecture acceptance, proposal landing, source implementation/review, release,
installation, participant enrollment and authorized running adoption are
independent gates. This planning slice closes none of the implementation,
native or operational gates. The effort remains Draft until its proposal clears
review, and cannot become Done after only one source PR or successful rehearsal.
