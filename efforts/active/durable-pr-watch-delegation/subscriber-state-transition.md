# Historical watch-state physical transition

Back to the [canonical effort](README.md#subscriber-callback-context-and-truthful-legacy-outcomes-from-5947)
and [accepted callback/state contract](subscriber-callback-context.md#persistence-per-version-isolation-and-restart-admission).

**Status:** Concrete transition design reviewed and merged in #13493 before
runtime realization. The operator accepted the one-time POSIX boot-boundary requirement
as source design; the [complete decision](subscriber-callback-context.md#physical-transition-feasibility-and-accepted-migration-choice)
retains its limits. Receiver groundwork is delivered in #13320. The first
runtime slice, merged in #13508, adds an effect-free marker/path reader and
legacy-absence guards under the existing storage owner. Every present phase still refuses;
no phase label proves admission or selects an authority. Nothing here
implements migration or enables versioned/map writes. The preparation slice,
merged in #13520, adds an internal, explicitly authorized preparation API and
same-original-inode prepared recovery. It is not connected to ordinary CLI or
control-plane entry. The internal Linux source path, merged in #13594, adds physical
sealing/recovery under the same writer. It still adds no authority selection,
ordinary entry integration or qualification of an actual boot crossing. The follow-on
Windows source slice merged in #13608 implements no-delete-sharing sealing/
recovery below. Contained native Windows tests, merged in #13612, cover abrupt current-controller
exit at six transition boundaries and cold recovery without import or effects.
The selected historical source-closure packet below qualifies three legacy-home
families on Windows. Released installations, governed entry and complete
supported-window qualification remain open.
Native Linux exchange/interruption qualification remains open.

**Scope:** Historical-root admission, one recoverable state authority and
P/V/G5947-3/4 prerequisites. No service stop, installation, reboot, rollout,
credential action or retained-resource release is authorized. This supplies
mechanics for the already-selected version-aware design, not another
subscription encoding, singleton slot, support floor or historical retirement.

This serves the PR-service vision's
[centralized watcher and registrar](../../../visions/plugins/agent-pull-requests/README.md#centralized-watcher-and-registrar)
and follows [service-contract evolution](../../../docs/patterns/service-contract-evolution.md).
Preserve [authoritative runtime replacement](../../../visions/installer/README.md#authoritative-runtime-replacement):
incoming installation readiness and current selection remain independent of
state activation, pending boot qualification and outgoing retirement.

## Physical boundary and source evidence

The historical `24b79724ac` CLI acquires the native
`agent-pull-requests-watch.lock` before constructing its daemon. Its lease
opens that path with `O_RDWR | O_CREAT`, then takes a nonblocking file lock.
Its self-boot invokes its own Python module; neither new payload metadata nor
the current installation's transaction intercepts it.

A contained Windows experiment with that actual lease pauses immediately
after open and before the original lock operation. Rename refuses with
sharing violation 32 while the historical descriptor is open. The child
then acquires and normally releases its lease. Moving that closed file and
placing a directory at its old address makes a new historical lease open
refuse with `PermissionError`. No daemon is constructed or backend reached.
This is native evidence for those exact lease operations, not a complete
Windows transition, supported-reader census or historical released install.

The current-owner native Windows packet additionally exits a real child with
`os._exit` after preparation, durable seal staging, original-lease release,
the original-file move, barrier installation and sealed-phase publication.
Cold recovery checks the interrupted topology, original inode and unchanged
legacy bytes. It acquires the original lease only before the move and only
the barrier lease afterward, with no retained-inode acquisition, fetch,
notification, isolated state or activation. This proves those current-owner
process-exit boundaries, not complete historical startup-family coverage,
host power-loss durability or the POSIX boot boundary.

The explicit historical packet, merged in #13615, archives complete source closures from
`24b79724ac81d65f80a50507f6a7bef5b5dc1a33`,
`a936d3b39f4d069b4e77ed11cf8e0e59bebbcd2b` and
`3c62c9442c5944640d2c13385d7ff00db614c1aa`, including their declared
standard-library-only local dependencies. Isolated children disable site and
environment import paths and verify loaded package locations; they do not
substitute current unrelated modules. For each family, a delayed actual
historical lease open blocks the first move until normal release, and actual
legacy-home `serve` and `restart` refuse the installed directory at the exact
historical lock pathname. A cold historical lease that creates a file in the
move gap blocks sealing and recovery; both files remain intact after its normal
release, without acquiring either historical inode during recovery.
Subprocess and socket effects are prohibited inside those historical children.
Missing local source commits explicitly skip rather than fetching or falling
back to current code. This qualifies those pinned legacy-home source entries,
not released installers, governed installation/adoption, every supported
historical release or POSIX native behavior.

A follow-on packet, merged in #13618, also selects the actual promoted payloads
`9d11a84f794506956dbaeb8341578a0b0f13b899` (`0.3.1-dev1`),
`c2ee1877fb55f965c141f400a31bc5e95dbebe6b` (`0.3.2-dev1`) and
`5ee1468f15967fd6e76a0be08ee3dc69da2551bb` (`0.3.16-dev1`).
These archive the libraries actually bundled under each promoted plugin,
not the repository-root development libraries. The children verify the
observed package version as well as loaded source locations. Both scenarios
pass for all six snapshots: twelve historical cases under unchanged runner
limits. The first two promotions differ from their development anchors only
in the package version stamp; the third contains substantive source changes
and is qualified independently. These versions identify fixture inputs, not
a support floor or version-equality negotiation rule. Installer, governed
adoption, complete-window and POSIX qualifications remain separate.

POSIX rename does not revoke a descriptor for the original inode. A delayed
historical acquisition can consequently succeed after the incumbent releases
that inode, even if the old pathname is now a directory. This is presently
source/OS-semantics analysis, not a passed native Linux experiment.

The directory barrier therefore needs two distinct proofs:

- **Windows:** successful no-delete-sharing replacement plus exclusive
  creation of the directory, including every relevant historical open family.
  An opened or raced legacy file prevents completion; never delete it to
  manufacture success. The current holder must drain and release normally
  before attempting this replacement.
- **POSIX:** an installed, durably recorded barrier followed by an actual
  kernel boot change. Processes and descriptors from before the barrier
  cannot survive that boundary. A service restart, PID scan, elapsed time,
  sign-out or same-kernel container restart is not equivalent.

**POSIX sealing must never expose an absent historical lock pathname.**
Pre-create and durably identify the barrier directory at the retained path,
then atomically exchange it with the original lock file while holding that
file's existing native lease. For example, Linux `RENAME_EXCHANGE` provides
the required namespace operation; the actual filesystem and platform
implementation must be qualified. Before the exchange both delayed and cold
historical callers address the same original inode. Afterward cold callers
encounter the directory while delayed descriptors still address that one
retained inode. Controller exit on either side cannot create two independent
historical lock inodes. Never substitute rename-then-mkdir, unlink/create,
or an advisory transaction that historical callers do not honor. Unavailable
atomic exchange refuses sealing before moving the legacy lock; existing
no-replace publication helpers are not exchange implementations.

Windows retains its separate no-delete-sharing proof: the original file
cannot be moved while a historical descriptor is open. A cold caller that
creates a replacement file in the later directory-installation gap prevents
sealing, but cannot contend on a second retained inode because the successful
first move proved there were no retained historical handles. Transition-aware
callers must not open that retained inode as a recovery lease in this Windows
prepared/raced phase. Only a qualified barrier permits the new lease.

Use the already-declared `agent-update-driver` dependency's
`update_driver.boot.kernel_boot_identity` and `valid_boot_identity`, not a new
shell probe, environment override, updater operation or daemon. Compare
validated identities from the same platform/domain. Missing, malformed,
cross-platform or unchanged identity does not qualify the boundary.
Moving a state directory or volume to a different host is not this migration;
retain existing installation binding rather than treating another machine's
boot identity as a restart of the original execution domain.

### Execution-domain evidence and current-controller recovery amendment

**Reviewed in #13624; not runtime support or operational authority.** The
delivered native boot reader identifies a boot, not continuity of its physical
execution domain. The delivered journal also compares the complete original
installation binding, including its payload and receipt generations. A changed
boot UUID alone cannot distinguish an ordinary reboot from a copied state root
on another host; requiring that entire binding to equal a newer controller's
binding would instead strand the transition on its original executable.
Resolve both gaps before implementing boot qualification or final import.
This amendment follows the pattern's
[durable-state compatibility requirement](../../../docs/patterns/service-contract-evolution.md#durable-state-must-not-require-its-original-executable).
It does not weaken existing validation or authorize running an old build.

Keep three facts distinct under the existing transition owner:

| Fact | Required meaning |
|---|---|
| Original transition evidence | Immutable original installation provenance, exact state-root scope, original lease/pin and installed barrier identities, and the durably acknowledged sealed epoch. Preserve these across controller adoption. |
| Current controller admission | A freshly selected compatible executor, its own validated current installation when governed, existing lifecycle authorization and actual native custody. A stored controller field is history, not authority to acquire or mutate. |
| Physical execution-domain evidence | A versioned native domain identity and its kernel boot identity, obtained together through the existing boot-evidence owner, not inferred from installation/profile paths or supplied by the caller. |

Extend `update_driver.boot` only as needed for this evidence contract; keep the
existing boot-UUID API and its callers compatible. A qualified domain identity
must remain stable across an ordinary boot and distinguish independently
running hosts or VMs for the supported venue. Its provider must document and
test its cloning, container and mounted-home limitations. A hostname,
installation receipt, home path, arbitrary persisted UUID, service lifetime or
container restart is not that proof. A machine identifier copied with the
movable state/image is not sufficient by itself. If the venue cannot supply
qualified native continuity, report that specific unsupported boundary and
leave legacy authority intact; do not silently substitute a weaker identity.
No new inventory service, global controller, shell probe or periodic observer
is introduced.

Acquire coherent domain/boot evidence at the sealing acknowledgement and
again at qualified recovery through that provider. Validate its contract,
platform and native source; evidence that changes during sampling refuses the
attempt rather than mixing two observations. POSIX admission requires the
same qualified domain and a different valid kernel boot identity from the
durable sealed epoch. Missing, malformed, unsupported, same-boot or
different-domain evidence cannot authorize import. A copied/moved root does
not become a reboot migration, and a fresh controller cannot relabel its own
host as the original domain. A real boot-crossing fixture remains necessary;
injected identities prove only selection/refusal logic.

Evolve the private journal to version 2 before writing these new required
semantics. The existing version-1 records remain a supported recovery input,
not an instruction to start their original payload. A version-2 reader retains
the complete original `binding` and physical evidence as origin facts and
records a separately admitted current controller; it never replaces the
original generation with the incoming generation to make equality pass.
Use a documented adapter inside the existing preparation/sealing owner,
with current native custody and atomic guarded publication. Do not remove
fields from the present validator, accept a caller's claimed old binding, or
turn structural parsing into authorization.

For governed recovery, the existing installation owner must establish that
the admitted current installation addresses the same original installation
and state-root scope. Keep original source/payload/generation fields as
provenance rather than requiring the old generation to remain active. Preserve
all current receipt, process, selection and transaction checks for the
executor; do not derive permission merely by matching path strings or a
subset of receipt fields. Unsupported or conflicting scope refuses this
transition, not installation replacement. Legacy-home recovery retains its
original exact-root scope and native ownership checks. Neither path may
manufacture a migration or relocation relationship.

Version-1 prepared records can be adapted only after validating their actual
topology, pin, original-inode custody and strict final legacy source. A sealed
version-1 record lacks qualified domain evidence: a new controller cannot
retroactively attach its current domain to the old boot UUID. Reconcile the
installed barrier and retained inode through the existing authorized owner,
durably establish a fresh qualified sealed epoch without undoing the seal,
and on POSIX wait for a later actual boot of that same domain. If that
reconciliation cannot be established, retain the record and bytes and expose
the unresolved evidence. Do not infer the original host or timing from file
age, preparation time or current receipt metadata.

On Windows, controller adoption still requires the qualified no-delete-sharing
topology and existing inner writer lease. Domain metadata is not an alternative
to the historical open-handle and raced-file checks, or permission to acquire
the retained inode. Before either platform publishes an adapted record,
recheck the unchanged original journal, actual topology, current admission,
native custody and strict legacy source under the existing final-write guard.
An interrupted or uncertain adaptation preserves both origin history and the
legacy sole authority; it never selects an isolated registry or repeats an
import. Unknown callback outcomes and unrelated resource obligations survive.

Ship the version-2 receiver/adapter before any writer relies on it. Version-1
transition-aware readers must continue to refuse an unsupported present record
without mutation; historical pre-transition readers remain fenced by the
physical barrier, not JSON negotiation. Test both rollback directions through
the actual persistence boundary. No ordinary CLI syntax, map/cwd advertisement,
automatic migration or installer veto is added by this proposal.

#### Initial native-domain source scope

**Unwired source merged in #13654; native qualification remains open.**
The additive source API and its limits are documented by the
[existing boot-evidence owner](../../../libs/update-driver/README.md#execution-domain-boot-observations).
Its version-1 observation pairs the Windows VM Generation Counter with the
native kernel boot UUID, checking both for changes during sampling. The
counter's native contract separates ordinary reboot from clone/restore; a
changed generation conservatively refuses continuity. Device absence or
access denial has no fallback or elevation. Linux/WSL and macOS have no
implemented provider. Firmware IDs, machine IDs and movable state remain
insufficient substitutes.

This is an unwired source prerequisite, not actual native qualification or
subscriber migration support. Actor-free native-call and serialized-record
controls cannot close the actual provider, same-domain reboot, cloned/moved
root, container-restart or Linux-provider gates. The Windows sealed receiver and original/current controller separation are
documented below. Fresh POSIX sealed epoch, guarded final source import and
authority selection remain under the same writer owner; ordinary entry and
map/cwd writes remain unchanged.

## One owner, explicit phases

Extend the watch owner's existing journal/storage/adoption boundary. The
standalone legacy-home entry and governed entry must share the transition
reader, while governed cutover keeps its existing installation transaction
and process custody. Do not make a new global controller, lock service,
per-subscription directory or periodic boot watcher.

The names below are proposed fixed owner-relative paths, not implemented
configuration or caller-selected paths:

| Purpose | Proposed location under the existing state root |
|---|---|
| Historical admission barrier | `agent-pull-requests-watch.lock` as an ordinary directory |
| Original-inode preparation pin | `watch-state-transition.pin`, a retained hardlink, never a writer lease or staged barrier |
| Retained original lease inode | `watch-state-transition.legacy.lock`; on POSIX this initially holds the staged barrier directory and is exchanged atomically with the historical path |
| Windows staged barrier | `watch-state-transition.barrier` before its no-replace installation |
| New native writer lease | `agent-pull-requests-watch.lock/writer.lock` |
| Phase journal | `watch-state-transition.json` |
| Isolated shared per-version authority | `watch-state/subscriptions.json` |

The shared file contains the selected complete registry and retained
`stateByVersion` partitions. It is not a context sidecar. The historical
`watch-subscriptions.json` remains the sole subscription authority until
the migration commit; afterward it is retained import evidence, never a
second live registry or automatic rollback source.

The journal records its schema, phase, exact installation/state-root binding,
native barrier/retained-inode identities, boot evidence and source/candidate
state identities. Fixed paths are derived by the owner; do not deserialize
arbitrary paths to move or replace. Reparse/symlink, malformed, foreign,
missing-required or conflicting evidence refuses the affected transition.
Presence of a journal does not prove that a directory was sealed or a
writer drained.

| Phase | Sole subscription authority | Admission and permitted work |
|---|---|---|
| No transition | Existing legacy file | Preserve ordinary legacy operation and the delivered present-only receiver behavior. No automatic migration merely because a new release is installed. |
| Prepared | Legacy file | The existing owner validates scope, captures custody and stages the barrier/journal. POSIX keeps the original lease held through atomic exchange; it never creates a missing-name window. No new state format, route or callback effects follow from preparation. |
| Sealed legacy | Legacy file | Cold historical opens refuse at the directory. On POSIX, the original inode remains the one legacy writer lease until boot qualification; new writers do not switch to the new inode early. New context writes remain disabled. |
| Import ready | Final legacy file | Require qualified Windows sealing or a subsequent POSIX boot; acquire the new native writer lease and re-read/strictly validate the final legacy state. No prior staged snapshot is authoritative. |
| Active | Isolated per-version file | After durable candidate publication and journal commit, compatible owners use only the shared per-version authority under the new lease. Normal registry activation follows existing lifecycle authorization. |

Before POSIX boot qualification, a delayed historical descriptor can still
acquire the retained inode after its holder exits. Preserve its legacy writes
and registrations: a sealed pathname alone does not permit migration or
competing ownership. A transition-aware legacy resumption uses that same
retained inode, not a newly created lock with equivalent text. It retains
the existing legacy state contract and ordinary callback meaning.

This phase may span an ordinary host boot; there is no automatic reboot or
new scheduled process. Read/recover it through existing entry operations.
The existing owner must close admission and complete its drain before any
actual lease transfer. A blocked or uncertain drain keeps its real custody.
No force-release, guessed stale PID or invocation of a pending delivery is
a migration-recovery operation.

After physical admission qualifies, import only the final strict snapshot
under exclusive custody. Preserve original subscription/storage/registration
and event identities, baselines, deadlines, pending/queued payloads, attempts,
next-attempt times and legacy unknown outcomes. Import is not re-registration,
acknowledgement or permission to replay a one-attempt legacy callback.

Write the complete per-version candidate through the existing atomic
persistence owner. Its durability acknowledgement precedes the journal's
atomic `Active` commit. Recheck the phase, native custody and source identity
at the final write boundary; no pre-boot/cached snapshot can become authority.
Only after that commit may normal writer adoption install the selected
registry and activate effects. New map/cwd capability and client defaults
remain their separately qualified later releases.

## Interrupted transition and rollback

Recovery reads actual filesystem and native ownership evidence as well as
the journal phase. Do not use a phase label as proof of the preceding effect.
Every acknowledged phase must fsync its files and relevant directories
through the existing platform persistence policy.

| Interrupted boundary | Required recovery |
|---|---|
| Preparation before any move | Keep legacy authority. Reconcile only the known preparation through the existing owner; do not reset state. |
| Controller exits around POSIX sealing | The atomic exchange leaves either the original lock at the historical name or the directory there with the same original inode retained. Recover actual recorded identities, not a phase guess. A move-without-barrier state is invalid and must never be produced by this backend. |
| Windows legacy inode moved, barrier not installed | A recreated historical file or unknown custody blocks completion. Preserve known retained and raced paths; never unlink the latter, open the retained inode as a second recovery lease, or claim exclusion from holding only the former. |
| Barrier installed, sealed-phase acknowledgement missing | Revalidate exact barrier identity. Without durable evidence that it preceded the recorded boot, record a fresh sealed epoch and require a subsequent POSIX boot; do not infer timing from file age or a preparation stamp. |
| Sealed legacy, same or unavailable boot | Continue only the legacy authority and original-inode lease rules. Refuse isolated activation; installing/selecting a compatible runtime may still finish. |
| Candidate published, `Active` not committed | Candidate is staging only. Revalidate the final legacy source under custody; reconcile/rebuild staging through the owner, with no callbacks or duplicate registry. |
| `Active` committed, route not yet published | Recover the isolated shared authority through ordinary writer adoption. Never import legacy state a second time. |
| Active state or journal cannot be validated | Preserve all bytes and existing custody; expose the actual incompatibility or uncertain commit. Never fall back to the old file, drop partitions or erase the barrier. |

A boot change is descriptor-lifetime evidence, not proof of predecessor
callback success, settled effects, installation selection or release of
unrelated resources. Existing unknown outcomes remain unknown. Recovery
does not authorize deletion of retained import data or old runtimes.

Before activation, any supported rollback follows the existing owner's
verified quiescence/recovery rules; it cannot race or silently undo a
partially completed seal. After activation, retained historical runtimes
remain installed/retained as required but refuse incompatible activation.
Do not erase the generation or directory barrier when the final cwd entry
is removed. Rollback of code is not downgrade of retained state.

## Serial realization and qualification

1. The concrete transition amendment cleared review in #13493. Keep the
   receiver-only release and preserved provider prototype distinct while
   implementing the following runtime paths.
2. The shared storage reader delivered in #13508 derives fixed paths and
   observes the version-1 `agent-pull-requests.watch-state-transition` phase header.
   Missing markers preserve legacy behavior only without orphan transition
   artifacts. All present phases refuse through shared read and final-write
   boundaries; header parsing does not validate installation, boot or inode
   custody. Implement guarded migration/recovery under the existing
   standalone/governed owners next. Do not publish a capability from a
   decoder or changefile alone.
3. Use actor-free units for phase/boot/source selection, malformed/foreign
   evidence, final-write races and injected failures at every durable boundary.
   Keep required PR/promotion CI unit-only.
4. In contained native Windows/Linux fixtures, run the actual supported
   historical lease/startup families, including delayed open, competing
   creation, abrupt controller exit, same-boot refusal and ordinary recovery.
   On POSIX, schedule both a pre-opened delayed historical caller and a cold
   caller while terminating the sealing controller immediately before and
   after the atomic exchange. Prove no missing historical name, at most one
   successful historical writer, byte preservation and no concurrent registry
   mutation. An unsupported exchange must leave the original lock untouched;
   never exercise a rename/mkdir fallback as if it were the guarded path.
   Assert byte preservation, one writer and zero premature effects. A fake
   boot identity does not qualify a real reboot.
5. Before boot admission or final import, implement the reviewed
   [domain/controller amendment](#execution-domain-evidence-and-current-controller-recovery-amendment)
   through the existing evidence, installation and writer owners. Actor-free
   cases must cover same/different/unsupported domains, unchanged/changed boot,
   incoherent samples, copied roots, newer-current-controller recovery of both
   journal versions, stale/foreign controller refusal, uncertain adaptation and
   unsupported-reader rollback with unchanged legacy bytes and zero effects.
   Contained native cases must exercise the actual evidence provider and
   current-controller serialization, not caller-provided identity substitutes.
6. Qualify an actual kernel-boot-crossing lane separately when a disposable
   venue and its lifecycle authority are available. Until then, retain that
   acceptance gate explicitly; no live boot or hosted run is authorized here.
   Prove an ordinary reboot in the same execution domain qualifies, while a
   service/container restart and a copy to another domain do not. Keep
   native Linux exchange/interruption and historical-window qualification
   separate; a passing evidence provider does not close either gate.
7. Only after transition protection is released and applicable activation
   qualification passes, adapt the preserved map/cwd provider and then the
   CLI in separate reviewed releases. Resolve P/V/G5947-3/4 only with their
   actual consumer evidence; the parent effort remains Active.

Keep complete historical-runtime qualification separate from experiments
using historical lease/entry source with current unrelated imports.
Measure transition work once per phase and keep ordinary reads on the
existing owner path; no process, filesystem scan or boot probe per callback.

### Guarded preparation implementation

The prepared record requires a retained hardlink at the fixed pin path.
Create it under the original held lease and confirm the file and parent through
the existing platform persistence policy before publishing the record.
Recovery compares that pin, the historical pathname and the recorded inode
before acquisition. The retained reference prevents unlink/recreate inode
reuse; a numeric device/inode pair alone is insufficient. Missing, foreign or
symlink/reparse pin evidence refuses. An unjournaled pin remains retained,
blocking interruption evidence, with no automatic deletion or inferred record
reconstruction. Never use the pin as another lease. It does not occupy the
retained pathname needed for POSIX atomic exchange, or qualify sealing/boot.

Selected state and marker writes require an existing parent; moving the
historical root cannot cause an empty replacement directory to be created.
Legacy state uses stable regular-file descriptor reads, preserving its
existing size policy while rejecting links and reparses before adoption or
replacement. The ordinary receiver also rejects an unjournaled pin; its
absent-transition path adds one fixed metadata lookup, not a scan.

Install the preparation source/custody guard before validating an active
owner's evidence. It protects all in-flight persistence and final replacements
until workers settle or a newly authorized owner boundary revalidates state.
Validation failure closes admission and drains without persisting rejected
state, retaining the native lease for unfinished work. Safe timeout abort
requires another descriptor/path check, both after the failed drain and before
resume. A storage validation refusal invalidates readiness even when it occurs
before the final callback; repaired state must be strictly readopted, not
overwritten from the old registry. Readoption must load a stable plain source
and revalidate native custody before clearing the full snapshot/installation
guard. The instance retains strict native effects/storage admission and
refusal propagation afterward; activation cannot bypass a still-pending
preparation guard through a weaker operation.
Worker-startup admission, final source validation and preparation-guard removal
share the persistence/refusal boundary. A startup refusal must raise and
retain the installation/snapshot guard rather than report successful effects.

Every new callback admission is guarded. Rejected ownership/readiness fences
queued legacy callbacks, including the cleanup path that normally drains them.
Keep unattempted events process-local and transfer them exactly once, with
history and concurrency limits, through valid readoption; do not report an
attempt or outcome that never occurred. Acknowledged events remain pending
without an acknowledgement or spent attempt. Normal authorized drains can
still finish accepted work. Closure/release must settle running workers
without first committing an irreversible shutdown intent, then refuse
discarding a retained fenced queue. An actual external shutdown request is
never cleared. This does not turn legacy delivery into the reliable protocol.
Readoption transfers the fence as well as the queue; only authorized activation
or resumption unfences and wakes that queue. Preparation refuses publication
while queued legacy work remains, including an already-quiescent recovery
owner. Activate and complete an authorized drain before retrying preparation.

Preparation pins the prechecked historical inode through existing-only native
acquisition, including descriptor/path comparison before PID publication.
Native custody and strict legacy-entry validation run both before drain and
at the final atomic drain-state replacement. A deadline check follows all
final validation reads before marker publication. Incomplete drain preserves
previous read readiness for authorized abort; completed quiescence disables
it before publication, including uncertain publication. Proven custody loss
or malformed legacy state invalidates readiness rather than licensing abort
over rejected evidence. Inherited POSIX lease release never unlocks the
parent's shared open-file description.

The internal writer first revalidates its owner authorization and, when
supplied, the pinned current `WatchInstallation`. It recognizes only an
absent transition or a complete prepared record at the unchanged historical
lease path. It finishes application quiescence while retaining that lease,
strictly validates legacy registrations without effects and publishes the
private record through no-replace persistence. A final callback rechecks
scope, deadline, quiescence, descriptor/path identity and unchanged journal
before publication. A failed drain or uncertain commit retains accountable
custody rather than activating a successor.

Prepared recovery never trusts a phase label or opens the retained inode.
It validates exact binding, original file identity and absence of physical
transition artifacts before native acquisition, then reconfirms durability
without overwriting the record. It does not import an earlier snapshot, so a
historical writer's intervening legacy registrations remain intact. Later
phases still refuse and cannot be downgraded to preparation.

This is a source-only internal API, not a released migration command or
standalone/governed control-plane integration. Windows historical-runtime and
controller-exit qualification, native Linux exchange/interruption qualification,
final-source import, actual boot-crossing
qualification and map/cwd activation remain open.

### Prepared-only version-2 receiver

The internal `WatchWriter.recover_prepared_state_transition()` operation is
limited to completed quiescence with no queued legacy callbacks. It admits the
current controller through existing authorization, process and lifecycle checks;
governed recovery also holds the exact current installation transaction and
uses the installation owner's complete historical-scope validation. It reads
origin only from the private journal, never a caller-provided old binding.

The version-2 prepared record retains the entire version-1 record as `origin`
and its unchanged `binding` and `lease` at the top level, with a separate
`controller` binding. The latter is historical evidence on the next attempt,
not authority. Recovery accepts a later compatible current installation while
retaining both original generations and payload provenance, without accessing
an older executable. Version-1 validators remain unchanged and refuse the new
record. Shared ordinary entry recognizes the prepared header but refuses every
present transition.

Before native acquisition and at final atomic publication, the adapter checks
the exact canonical root, original lease/pin, absent physical-transition
artifacts, unchanged journal bytes, strict legacy source, native custody,
current admission and deadline. Acquisition never creates another historical
inode or opens a pin/retained file. An already-current version-2 retry confirms
durability without rewriting it. Unknown acknowledgement retains the published
origin, native custody and legacy sole authority. Both sealed inputs and
conflicting records refuse before acquiring a lease.

Contained tests exercise actual receipt/activation replacement through a third
generation, absent older payloads, serialized version-1/version-2 recovery and
rollback refusal, scope/topology/source conflicts, transaction custody,
pre/post-publication failures and uncertain acknowledgement. The focused
preparation/storage/sealing packet passes 155 tests with three platform-specific
skips on Windows; strict typing checks both Linux and Windows targets.
The final exact-transaction precondition correction passes seven targeted
negative/positive cases. Precondition refusals leave existing readiness and
effects unchanged. Admission is revalidated after the initial source read,
before acquisition; final source/admission work precedes the journal/topology
check. Candidate serialization is bounded before acquisition/publication so a
valid origin cannot produce an unreadable oversized record. Fresh current-cell
checks omit only `COPILOT_PLUGIN_ROOT`, preserving WSL distribution identity
and other observed environment evidence. Four focused propagation/identity
controls pass after this final correction; guest resolution uses the canonical
owner with fixture-only observations, not native WSL qualification.
This is neither native Linux qualification nor actual domain/boot evidence.
Sealed reconciliation, final import, ordinary command integration and the
complete historical installed window remain open. No installation, migration,
service operation or rollout is performed.

### Windows sealed version-2 receiver

**Source merged in #13662, with prepared-origin progression in #13668;
no installed migration or activation.**
The internal `WatchWriter.recover_sealed_state_transition()` source API receives
an already installed Windows barrier under fresh lifecycle authorization and,
when governed, the current installation's actually held transaction. It uses
the same original v1 record/topology validator as Windows sealing rather than
weakening or bypassing the v1 readers. The complete original private journal
remains immutable `origin`; top-level `schema`, `binding` and `lease` remain
equal to that origin. A separately validated
`controller` records the admitted current executor without requiring the old
executable to exist.

The receiver also accepts phase progression from an actual v1 prepared origin,
with or without an already staged seal, to a v2 `sealed-legacy` envelope.
It validates the original receipt under the strict v1 schema and does not
relabel its phase as an invented sealed receipt. Any seal in that origin must
remain exactly unchanged; otherwise the current seal is validated solely
against the actual installed barrier and original pin/retained file. A
temporary v1-shaped view validates those current physical facts, not origin
provenance or current admission. A sealed origin cannot downgrade or change
its seal. This receiver creates no seal and performs no move.

Only the qualified installed barrier permits acquisition of the inner writer
lease. No original/retained lease is acquired, and an incomplete preparation or
move is not silently completed by this receiver. Candidate serialization is
bounded before acquisition; the final guard rechecks strict legacy source,
fresh current admission, native custody, original journal and actual topology.
An uncertain publication retains origin, custody and the legacy sole authority;
unchanged v2 retry confirms durability without rewriting. Ordinary entry still
refuses all present records, while v1 preparation/sealing readers refuse v2.

Actor-free controls exercise the actual receiver and writer-admission boundary.
Contained Windows fixtures additionally qualify old-payload-absent current
installation adoption, exact transaction refusal, native inner-lease selection,
cold v2 retry, final-publication races and uncertain acknowledgement. These are
receiver proofs only, not a new seal, provider/boot qualification, final source
import, isolated registry or ordinary startup integration.
POSIX sealed records refuse before custody/publication because no qualified
domain epoch can be recovered from their v1 boot field. A current controller
cannot fill that historical gap with its own host observation.
Fresh POSIX epoch reconciliation and later actual same-domain boot, the POSIX
v2 sealing writer, final import/authority selection, governed entry and complete
historical-window qualification remain open under their existing owners.
No native domain probe or live migration/rollout is authorized.

### Windows version-2 sealing writer

**Source merged in #13674; no installed migration or activation.**
The internal `WatchWriter.seal_recovered_state_transition()` source API progresses
only an already adapted v2 journal. It shares the existing Windows physical
sealing owner, no-replace moves and native lease order; it does not relax the
strict v1 preparation or sealing readers. Current admission and the exact held
installation transaction, when governed, remain required at every boundary.
Only interpretation of exactly unchanged origin/controller bindings is reused
within one sealing operation; that cache never substitutes for live admission,
guard, journal, topology, source, native custody or deadline checks.
Legacy source validation precedes native acquisition. POSIX refuses before
custody or physical writes because no qualified execution-domain epoch exists.

The reader validates the complete immutable v1 `origin` independently from the
current v2 phase/topology. Schema, binding, original lease and any original seal
remain unchanged. A prepared origin can progress through staged, moved and
installed barrier topologies, including a prepared journal left after the
barrier move; a sealed origin cannot regress. No unjournaled directory, raced
historical file or conflicting isolated registry is guessed away or removed.
Before custody acquisition, staging or either move, metadata preflight reserves
the full Windows file-identity width and checks both staged and final envelopes,
including any current-controller update. Near-limit refusal therefore preserves
the actual topology and journal rather than creating an unjournaled barrier or
stranding a moved transition. Actual publication remains bounded and guarded.
Uncertain stage/move/acquisition/publication outcomes retain actual evidence for
the same owner to reconcile.

Original custody is released before either move. Recovery acquires the original
inode only while it is still at the historical path, never at the retained path.
Only the qualified installed barrier permits the inner writer lease. A later
current controller may finish an interrupted v2 transition without the old
executable; the existing sealed receiver updates its controller under that
inner lease, leaving origin and any staged seal unchanged. An already-current
completed result avoids a redundant receiver pass.

This is an explicitly selected source API, not an ordinary startup integration.
Legacy subscription bytes remain the sole authority, and persistence, read
readiness, callbacks, isolated registry creation and final import stay fenced.
The actual-domain/boot, complete released-history, governed-entry and final
authority-selection gates remain open. No installation, migration, service
change or rollout is implied.

The contained Windows historical-source packet merged in #13681 exercises both
journal versions against all six pinned historical closures, including three released-source
snapshots with their own vendored lease/startup dependencies. A delayed native
open prevents sealing until it closes; a raced cold creation remains blocking
after release rather than being overwritten or adopted. After successful
sealing, each historical `serve` and `restart` entry refuses at the physical
barrier. Separate real child processes exit abruptly at preparation, staging,
release, original-file move, barrier installation and completed sealing; cold
recovery of both journal versions preserves the original inode, legacy bytes
and disabled effects. Version 2 additionally preserves its exact prepared
origin and current-controller record. The combined packet passes 42 cases
with one Linux-specific skip on Windows. These are source-closure and native
process proofs, not complete released-install or domain/boot qualification.

### Internal Linux sealing and recovery

`WatchWriter.seal_state_transition` requires a separately completed, authorized
preparation and drained application custody. It never drains by releasing a
lease, adopts a registry or enables effects. Scope, current installation when
supplied, quiescence, queued legacy custody and the lifecycle deadline are checked
through the existing writer boundary. Unsupported platforms or an unavailable
Linux exchange entry refuse before staging or acquisition.

The internal sealing owner extends the prepared journal with `seal`, containing
the fixed backend identifier, exact barrier device/inode and initially null boot
evidence. The staged empty owner-private directory and parent are synchronized
before its identity is published. An unjournaled staged directory is retained
uncertain evidence, not something recovery silently deletes or guesses.
The journal never stores caller-chosen filesystem paths.

Only Linux `renameat2` with `RENAME_EXCHANGE` performs the namespace change.
Each pre-exchange attempt confirms the staging journal's file and parent
durability and rechecks evidence, including recovery after an uncertain commit.
There is no move/mkdir or no-replace fallback. An unsupported filesystem leaves
the original historical lock untouched, retaining staged recovery evidence.
After exchange the owner readdresses the same held descriptor to the original
inode at the retained path and validates descriptor/path identity. Cold guarded
recovery validates the recorded pin, barrier and both actual path identities
before opening only that original inode with existing-only acquisition.
No new writer lease is created or acquired.

Recovery recognizes either side of the recorded exchange, independently of its
phase label, and never exchanges an already installed barrier back. It
synchronizes the installed barrier and parent before publishing `sealed-legacy`
with native Linux boot evidence. A prepared record found after exchange obtains
a fresh epoch rather than using preparation time. This record is not a boot
comparison or permission to import; execution-domain qualification and
final-source authority selection remain later gates.

Final journal publication rechecks authorization, deadline, native custody,
unchanged journal/topology and strict legacy source validity. The writer retains
its mutation/readiness fence after both success and uncertain failure.
Ordinary adoption, callbacks and persistence cannot bypass it. Legacy
subscription bytes remain the sole authority, and neither isolated state nor
callback effects are produced by sealing.

### Internal Windows sealing and recovery

The same authorized, already-quiescent writer stages the fixed Windows barrier
directory, applies and verifies the existing owner-private security policy, and
publishes its exact identity in the private preparation journal. Every attempt
confirms journal durability before releasing original custody. Windows uses the
existing private-publication owner's write-through, no-replace move primitive;
the original descriptor must be normally released before moving its file.
No historical open handle may survive that successful move under the supported
no-delete-sharing lease family.

Moving the retained original is not exclusion by itself. A recreated historical
file blocks installation and every recovery attempt, without deletion,
replacement or acquisition of the retained inode. A moved-but-unsealed
preparation holds no new lease. Only validation of the exact directory barrier,
retained file and original pin permits creating/acquiring the fixed inner
`writer.lock`. This serializes final `sealed-legacy` publication, not registry
import. Unknown children, foreign binding, mismatched identities and isolated
state refuse recovery. The legacy subscription file remains the sole authority.

Actor-free interruption controls cover journal acknowledgement, original
release, both moves and sealed publication, plus raced-file retention,
scope/source/deadline changes and conflicting evidence. Contained native Windows
checks cover actual sharing refusal, successful sealing and cold recovery from
both move-return interruptions. These are source/native-primitive evidence, not
abrupt controller-exit or complete historical released-runtime qualification.
There is no ordinary entry integration, final import, activation or operational
migration authority in this slice.
