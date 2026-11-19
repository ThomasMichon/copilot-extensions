# Historical watch-state physical transition

Back to the [canonical effort](README.md#subscriber-callback-context-and-truthful-legacy-outcomes-from-5947)
and [accepted callback/state contract](subscriber-callback-context.md#persistence-per-version-isolation-and-restart-admission).

**Status:** Concrete transition proposal for normal review before runtime
realization. The operator accepted the one-time POSIX boot-boundary requirement
as source design; the [complete decision](subscriber-callback-context.md#physical-transition-feasibility-and-accepted-migration-choice)
retains its limits. Receiver groundwork is delivered in #13320. Nothing here
implements migration or enables versioned/map writes.

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

1. Review this concrete transition amendment before adding its runtime paths.
   Keep the receiver-only release and preserved provider prototype distinct.
2. Implement the shared effect-free phase/path reader and legacy-compatible
   absence behavior, then guarded migration/recovery under the existing
   standalone/governed owners. Do not publish a capability from a decoder or
   changefile alone.
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
5. Qualify an actual kernel-boot-crossing lane separately when a disposable
   venue and its lifecycle authority are available. Until then, retain that
   acceptance gate explicitly; no live boot or hosted run is authorized here.
6. Only after transition protection is released and applicable activation
   qualification passes, adapt the preserved map/cwd provider and then the
   CLI in separate reviewed releases. Resolve P/V/G5947-3/4 only with their
   actual consumer evidence; the parent effort remains Active.

Keep complete historical-runtime qualification separate from experiments
using historical lease/entry source with current unrelated imports.
Measure transition work once per phase and keep ordinary reads on the
existing owner path; no process, filesystem scan or boot probe per callback.
