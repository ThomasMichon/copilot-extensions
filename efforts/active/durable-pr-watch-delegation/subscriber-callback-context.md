# Subscriber-owned callback working directory

Back to the [canonical effort](README.md#subscriber-callback-context-and-truthful-legacy-outcomes-from-5947).

**Status:** Original contract reviewed and merged in #13228; operator-selected
map/version amendment reviewed and merged in #13277. The version-aware
read/final-write refusal baseline is merged in #13295. Historical-reader
transition protection and map/cwd consumer acceptance remain open. The
older-engine restart/per-version state amendment is reviewed in #13310.
**Current direction:** Continue the version-aware design with older-engine
restart refusal and per-version overwrite isolation under that reviewed
contract. The receiver-only expansion landed in #13320; physical historical
isolation and recoverable migration design is reviewed in #13493. Its
effect-free marker/path observation and legacy-absence receiver landed in
#13508; guarded migration/recovery and qualification remain open. The preserved
provider prototype is not delivered and map writes are not enabled.
**Scope:** P5947-3/4, V5947-3/4 and G5947-3/4. This does not complete those
items, expiry acceptance, M1, or the parent effort.
**Vision reconciliation:** Closes the existing
[centralized watcher and registrar](../../../visions/plugins/agent-pull-requests/README.md#centralized-watcher-and-registrar)
intent while preserving independent subscriber context. Applies the
[service-contract evolution pattern](../../../docs/patterns/service-contract-evolution.md).
No new supervisor, callback destination or remote-execution interface.

The [first version-aware refusal slice](journal/2026/11.18%20Watch%20state%20contract%20baseline.md)
adds read and final-write compatibility checks while retaining legacy-list
writes. It does not resolve historical inline-executor activation or
already-running pre-version writer protection, and enables no map/cwd support.

## Operator direction

The operator selected this design after the historical-reader risk was
explained, verbatim:

> Operator decision: avoid assigning singleton slots. Store a map of subscription ids to callback commands (each entry carrying its cwd), and add an explicit contract version to the state so older engines refuse/leave untouched any data with a newer version instead of reading it as empty or overwriting it. Proceed with that design through normal review and CI.

This supersedes the proposed choice between governed-only initial support and
legacy-home support. It does not select a governed-only restriction. One
existing watch owner continues to multiplex independent subscriptions; neither
callback directories nor compatibility protection allocate singleton slots.
The map replaces the collection encoding, not the existing observation,
delivery, storage, launch or installation owners.

The realization details below are **agent-recommended**. Normal review and CI
are authorized source procedures, not installation, service replacement,
credential, fleet rollout or retained-resource authority. The parent effort
and its other gates remain open.

### Version-aware restart and per-version state amendment

The operator further directed, verbatim:

> an older engine must recognize that it is older and NOT restart. Also make the shared state file support state-per-version for anything that risks overwrite, so versions cannot clobber each other. Continue the version-aware design on that basis, through normal review. Source/design scope only; no install, service stop or rollout implied.

This amends the agent-proposed single-map persistence realization below.
Keep the selected per-subscription command/cwd map and explicit contract
version, add per-version protection for overwrite-prone state, and enforce
older-engine restart refusal through the existing startup/activation owner.
Normal review precedes implementation. No historical-engine retirement,
installation, service stop, rollout or resource release follows from this
source/design direction. P/V/G5947-3/4 and the parent remain open.

## Current boundary and the compatibility gap

The existing local callback owner is `WatchDaemon`, not the repository-cache
service's pull/replay API. The relevant source seams are:

| Seam | Current behavior |
|------|------------------|
| `watch_subscription.subscribe` | Negotiates acknowledged/native capabilities; does not capture caller cwd. |
| `watch_notification.validate_notify` | Acknowledged notify accepts only `argv` and `timeout`; legacy notify tolerates other keys. |
| `watch_notification.default_notify` | Neither callback launch path supplies cwd. An absolute executable does not fix a relative script argument. |
| `watch_state.RegistryState` | Serializes per-subscriber notify. Legacy restore can ignore unknown entry fields; adding a field is not a downgrade fence. |
| `watch_storage.read_subscriptions_state` | Accepts legacy lists and the present-only versioned legacy partition; transition markers and orphan artifacts refuse before authority selection. |
| `WatchDaemon._reattach_from_disk` / `reload_state` | Reads before resuming callbacks/pollers; lifecycle reload builds a separate strict registry before installing it. |
| `agent_procutil.owned_process.run_owned` | Already accepts explicit cwd and owns bounded child lifetime. No process-runner API extension is needed. |

This is source analysis of the current boundary, not a passed mixed-version
test or a claim that every historically released reader has the same behavior.
Implementation must enumerate and exercise the actually supported rollback
readers. Neither a package-version comparison nor an unknown legacy field
establishes enforcement.

### Historical-reader finding: activation remains blocked

The local history identifies an earlier inline durable reader (last changed
in `24b79724ac`, #5691, following #5618) that returns an empty collection for
any non-list JSON document. The later extracted reader (`a936d3b39f`, #6019)
rejects non-lists; `3c62c9442c` (#7160) adds the explicit-root/governed
reader family. These are source lineage references, not a newly invented
support floor or a complete native qualification result.

The inline family can treat the proposed envelope as no subscriptions and
subsequently overwrite it. It also predates explicit notification-protocol
refusal. The envelope alone therefore does not establish safety across all
historical legacy-home executors. Provider expansion is local WIP; do not
publish its capability or enable context writes until the applicable support
window and transition protection have cleared review. Existing callbacks,
installation readiness and current selection remain unchanged.

An unpublished local prototype audit reproduces that empty
cold-start/overwrite with the actual historical daemon source and exercises
pre-effect cold refusal for the extracted readers plus governed reload refusal.
It substitutes their unrelated imports with the current source closure; it is
not qualification of complete historical released installations or the whole
support window. The same Windows packet runs real relative callbacks in two
retained directories for both delivery modes. All five cases pass, documenting
the gap rather than crediting the inline reader as safe. The focused current
provider/legacy suite passes 213 cases; its six changed Python files pass
whole-file strict checking for Linux and Windows plus Ruff. That prototype and
its audit tests are not delivered by this design-only amendment. Native Linux, complete
historical-runtime/transition protection and client migration remain open.

The subsequent contained Windows-native lease audit uses the actual
`24b79724ac` lease and CLI entry source, with current unrelated imports.
An independent process holding that historical lease excludes current
`WatchWriter.adopt` before state read/adoption. In the reverse direction, the
historical ordinary `serve` entry returns its existing already-running result
before daemon construction while the current writer holds custody. The
historical holder exits normally and the current owner releases its lease;
there are no fetch or callback effects. This qualifies native mutual exclusion
for those exact implementations, not a complete historical installation.

After the current owner releases custody, the same historical `serve` entry
reaches daemon construction even with a versioned state envelope present.
The fixture intercepts that constructor, so this check proves entry admission,
not another full old-daemon restore or overwrite test. The earlier inline-reader
audit already demonstrated its unsafe restore/write behavior. Together they
leave cold historical activation unprotected. Its self-booting client invokes
its own Python module directly; a guard in a newer payload launcher does not
intercept that path. Runtime retention references protect generation cleanup,
not admission of this historical entry.

**Required revised design:** a version tag alone cannot retrofit refusal into
the historical executable. The operator's subsequent direction requires
older-aware restart refusal plus per-version state isolation against clobbering;
the realization must address both, rather than silently declaring the unsafe
historical family unsupported. Exact historical behavior remains a qualification
obligation, not an assertion that new code has changed old bytes. Map/context
writes and caller migration remain disabled until the revised design clears
review and the implementation satisfies its transition tests. Legacy callback
support and ordinary installation readiness/current selection remain unchanged.

The operator has now selected the per-subscription map and explicit version
refusal above, rather than a governed-only initial scope. A version field does
not retroactively change the inline reader's executable code. The revised
design must therefore establish and qualify version-aware admission through
the existing owner/activation boundary before enabling the new writes. Do not
strip state, block ordinary runtime replacement, or declare that unsafe family
unsupported merely to avoid qualification.

## Selected contract

### Caller capture and legacy selection

Expand the provider first. After that expansion is qualified, migrate the
subscribe CLI in a separate source change: for callbacks with a nonempty argv,
capture the registering process's absolute current directory once, before
registration. Do not use the daemon's state directory, executable directory,
the daemon-starter's directory, or another subscriber's directory.

The migrated CLI defaults to caller context. A proposed strict
`--notify-context caller|legacy` selector makes the deliberate compatibility
choice explicit; these flags are **not implemented by this proposal**.
`legacy` preserves the old request/response and execution semantics and sends
no cwd fields or new protocol. It is suitable only when the caller's callback
is genuinely independent of cwd and its executable and file arguments use
absolute paths. Absolute paths alone do not prove that the callback does not
read relative configuration or data. No automatic legacy fallback is allowed
when caller context is required.

An empty/no-callback registration retains its current meaning and does not
acquire a cwd requirement. Existing callers, including existing Worktrees
callback producers, retain their wire contract until individually migrated.
Do not silently migrate an independent consumer as part of this lane.
Direct local registration callers may supply their own absolute cwd through
the same negotiated contract. The CLI's structured notify input must not
silently override its captured caller cwd; conflicting input is rejected.
No shell interpolation, environment capture or daemon-wide `chdir`.

### Runtime capability and wire admission

The actual running owner advertises `subscriber_cwd/v1` through existing
health only when it can validate, persist, restore and execute that contract.
The new CLI requires it before sending cwd. Missing support produces a
specific unsupported-subscriber-cwd error before registration, with the
explicit legacy option described as a semantic choice, not a retry.

For caller-context requests, `notify.cwd` is a nonempty absolute native path
without NUL. The receiver validates it and requires an existing directory at
registration. It does not create, repair or substitute a directory. Validation
uses the owner host's path rules; this remains a same-host local callback
contract, not a portable or remotely forwarded path.

Two wire forms retain the delivery distinction:

| Delivery | Required wire | Prior-owner protection |
|----------|---------------|------------------------|
| Legacy one-attempt | `notification_protocol: "legacy_callback_cwd/v1"` plus `notify.cwd` | The current prior owner rejects an unrecognized explicit notification protocol before registry mutation or provider fetch. |
| Acknowledged | Existing `notification_protocol: "acknowledged_notifications/v1"` plus `notify.cwd` | The current strict prior notify validator rejects cwd; ACK/event identity is not versioned or reinterpreted. |

`legacy_callback_cwd/v1` means best-effort delivery with required cwd, **not**
acknowledged delivery. The receiver must not infer `acknowledged=True` merely
from the presence of a protocol key, as the present restore path does.
Unrecognized explicit protocols and invalid combinations refuse before
registration, fetch, persistence or callback effects. There is no timeout-field
sentinel, ignored top-level extension or forced ACK migration.

Success echoes `callback_context_protocol: "subscriber_cwd/v1"` and the
accepted cwd. Acknowledged responses additionally retain their existing
notification protocol and nonempty registration identity; legacy responses
echo their new legacy protocol without inventing an ACK identity. A missing
or mismatched echo after a possibly accepted registration is ambiguous:
reconcile the original subscriber/registration through its owner. Do not
replay, cancel a replacement, or retry the legacy form automatically.
Health is discovery, not a lock on the executing generation; prior-owner
wire refusal and response validation must also cover owner replacement
between discovery and registration.

### Persistence, per-version isolation and restart admission

Retain the existing storage, writer-lease and lifecycle owners; do not allocate
installation or callback singleton slots. The operator's subsequent amendment
requires the shared authoritative state file to retain independent per-version
state, not one replaceable subscription collection. New readers continue to
accept the existing list with its original meanings before migration.

The following is an **agent-proposed shape**, not implemented syntax or a
claim that a historical reader understands it:

```json
{
  "schema": "agent-pull-requests.watch-subscriptions",
  "version": 2,
  "activeStateVersion": 2,
  "engineGeneration": "<ordered-generation-of-the-admitted-executor>",
  "stateByVersion": {
    "1": {
      "subscriptions": []
    },
    "2": {
      "subscriptions": {
        "<subscription-id>": {
          "repo": "example/project",
          "number": 42,
          "subscriber_id": "consumer-1",
          "until": ["merged"],
          "notify": {
            "argv": ["callback", "relative-script"],
            "cwd": "<absolute-native-directory>"
          },
          "notification_protocol": "legacy_callback_cwd/v1",
          "baseline": null,
          "remaining_timeout": null
        }
      }
    }
  }
}
```

The example's path, identity and engine generation are placeholders, not actual
registration inputs. `version` identifies the outer format, independently of
the selected state's version and executing engine generation. State-version
keys partition overwrite-prone durable data; subscription-map keys identify
individual subscriptions, not singleton callback or installation slots.
Each subscription value carries its callback command/cwd and the existing
target, baseline, deadline and delivery metadata. Use a stable
per-registration storage identity; caller-facing subscriber labels may repeat
on different targets and must not overwrite one another. Retain the identity
across restart and retry. A legacy storage key is not an ACK or event identity.
Do not infer acknowledged delivery from a map key or the existence of cwd.
Reject duplicate/malformed keys, inconsistent identities and malformed values
before installing any entry.

The active version contains the complete authoritative subscription map,
including every pending/queued/retry record. Other version entries are retained
state, not concurrently active registries or implicit rollback permission.
They must not start a second poller or redeliver an old pending event.
Context-bearing entries retain `notify.cwd` and the selected notification
protocol. Validate the outer envelope, selected version and complete candidate
collection before installing any entry or starting effects. Malformed or
unsupported active state refuses; do not skip an entry or silently select an
older partition. Preserve unselected version entries without interpreting,
stripping or reconstructing them through an older serializer. The in-memory
restore API enforces the same context rules as storage.

Retain all existing baseline/deadline, original registration/event identity,
pending immutable payload, queued event, attempts and next-attempt metadata.
Cwd belongs to the subscription, not to an added field in the immutable
event payload. Restart/retry uses that same retained subscription context;
re-registration is not a mechanism for rewriting a pending event.

A writer may keep the old list format before the version-aware transition.
After transition, mutate only its admitted version's state while preserving
every other retained version, under the existing exclusive writer custody and
atomic persistence boundary. Re-read the current envelope at the final write
boundary; a cached snapshot is not authority to replace another generation's
state. Use the existing definite-failure/uncertain-commit recovery semantics.
Migration must preserve complete subscription and delivery identity; retained
old snapshots are never independent sources of live delivery.

An in-file version map is not itself protection against an unaware historical
whole-file writer. Qualify the authoritative file's physical write boundary as
well as the encoding. If the historical `watch-subscriptions.json` path cannot
be guarded, the version-aware authoritative file must be isolated from that
write address through the existing owner's reviewed, recoverable migration.
That file remains the one shared per-version authority, not a context sidecar
whose loss an old engine could ignore. Do not leave an independently active
legacy copy that can replay migrated subscriptions, lose registrations accepted
during transition, or overwrite the new authority. The migration and its
crash/recovery cases precede activation; no state reset, stale-owner deletion
or silent support-floor change is permitted.

Contract-aware engines compare the explicit state contract version before
restoring entries, fetching, scheduling callbacks or writing. An unsupported
newer version is **explicit pre-effect refusal**, with byte-for-byte unchanged
state, not an empty collection or a best-effort partial import. Unknown
schema/version combinations also refuse. Existing strict extracted readers
reject a top-level object before returning any entries; the historical inline
family does not. Its current audit result is a transition blocker, not safe
rollback credit.

**Older engines must not restart.** Before ordinary cold `serve`, client
self-boot, installer-driven activation, resume or rollback can construct an
active daemon, compare the exact candidate executor's generation against the
admitted generation recorded by the shared state owner. A lower generation
refuses restart before state mutation, provider fetch, route publication or
callback effects, even if a retained partition matches its old state version.
Missing or incomparable evidence is not permission to assume "latest".
Define the ordering from attributable immutable executor evidence; a new
payload's stamp, current selection alone or a package-equality test does not
prove which engine is executing. Do not lower the recorded generation to
make rollback pass. Recheck admission at mutation/activation so a delayed
client cannot act on an earlier observation. Preserve ordinary legacy
operation before the transition instead of treating absent new metadata as
blanket refusal of all old calls.

**Version-awareness expansion and transition protection precede writes.**
First release the version-aware owner/activation guard through the existing
launch and state-adoption boundaries, without enabling new context writes or
caller defaults. The selected executor must support the retained state
contract before it can consume that state or receive context registrations.
Missing support from an actual historical executor is not guessed support.
Use contract support and generation evidence from the exact executing candidate,
not package-version equality, a new payload's stamp or a callback/installation
slot assignment.
Keep this enforcement separate from installation readiness/current selection:
an incompatible state executor refuses activation while installation may
complete, preserving the state and existing lifecycle custody.

Before a later release enables version-2 writes, prove that the ordinary
supported startup, rollback and health/register replacement paths cannot
expose the map or context registrations to an unaware inline executor. If the
existing boundaries cannot enforce that, keep writes disabled and amend the
transition design through review; a version tag alone is not the guard. Do not
silently retire the inline family or assume that a completed source PR updates
an installed launcher. Installed adoption retains its separate authority.
Include a pre-version owner already holding writer custody: a cold-reader check
does not stop a previously admitted writer from replacing newer state. Preserve
that owner's actual custody and require the existing guarded transition, not
an unverified stale-owner deletion or a second writer.

The intended supported rollback result for context-bearing state remains
**explicit pre-effect refusal**, not successful old-owner delivery.
Qualify that result through the real startup and governed reload boundaries,
not only by calling a JSON parser. Preserve the file bytes and pending event;
do not reset, rewrite to an empty list, downgrade a protocol, start a poller,
invoke a callback or activate an incompatible successor. Existing installation
custody/recovery owns a failed transition; this proposal does not claim that
reader refusal itself restores a route or permits restarting another owner.
This is refusal to consume incompatible callback state, not an installer
replacement veto: preserve
[authoritative runtime replacement](../../../visions/installer/README.md#authoritative-runtime-replacement).
Incoming installation readiness and current selection remain distinct from
state activation and outgoing retirement. Retain the subscription state for
a compatible executor; do not make installation success depend on repairing
an incumbent or cleaning up its retained resources.

New readers continue to restore old lists with their original meanings before
transition. Removing the last context-bearing subscription must not erase the
admitted generation, retained version partitions or their pending effects.
Do not automatically downgrade the shared file to an unversioned list or strip
context to make rollback pass. Test every schema family in the
actual supported upgrade/rollback window; if a supported reader does not
refuse safely, add a reviewed expansion/transition guard before enabling
context writes. Do not redefine that reader as unsupported to avoid the test.

### Physical transition feasibility and accepted migration choice

The receiver-only release does not change the historical executable's
`SingleInstance.acquire`: it opens
`agent-pull-requests-watch.lock` with `O_RDWR | O_CREAT`, then takes the
nonblocking OS lock. A guard in a newer payload cannot intercept its direct
Python-module self-boot. The existing installation transaction does not
govern that historical path either. These are execution boundaries, not a
missing JSON version comparison in the new receiver.

A contained Windows experiment with the actual `24b79724ac` lease pauses
after its file open but before its original OS-lock operation. Renaming that
open file refuses with Windows sharing violation 32. After the child is
allowed to acquire and normally release its lease, moving the file and
putting a directory at the historical lock address causes a fresh historical
open to refuse with `PermissionError`. The child exits zero; no daemon is
constructed, subscription file migrated, provider read or callback launched.
This tests only the historical lease and controlled scheduling, not complete
released installations, a crash-safe migration or a cross-platform guard.

A physical directory seal is therefore a candidate admission boundary,
not delivered protection. A compatible owner could keep the one shared
per-version authority and its writer lease inside that directory, separate
from the historical whole-file write address. It must not infer that merely
renaming a lock file revokes descriptors already opened against it.
On POSIX, an open descriptor can retain the original inode after rename;
the historical nonblocking lock can succeed on that inode after its holder
exits. That POSIX race is source/OS-semantics analysis, not a passed native
Linux experiment.

**Operator-accepted source-design choice, implementation not delivered:** qualify
the Windows no-delete-sharing seal with interrupted and competing-start
cases. For POSIX historical-root migration, stage the seal while retaining
the legacy state as the sole authority, and require a subsequent actual
kernel boot boundary before importing the final legacy snapshot or
activating isolated versioned state. That boundary eliminates pre-seal
processes and descriptors. Do not automatically reboot, treat a service
restart, sign-out, elapsed time or process scan as a boot, or make ordinary
installation readiness/current selection wait for it. No such boot or
Windows migration is authorized or executed here.

Before that boundary, a delayed historical descriptor may still acquire the
legacy inode: no versioned authority may be activated and no staged snapshot
may be mistaken for the final state. Registrations and pending delivery
changes still belong to the legacy authority. The implementation must retain
the original inode/lease custody and recover this phase without splitting
writers; simply sealing the pathname and dropping that custody is not a
complete transition.
The physical seal itself must preserve historical admission without a
missing-name gap: on POSIX, atomically exchange a staged directory and the
original leased file. Moving the file and only then creating the directory
can split delayed and cold callers across different lock inodes before any
recovery runs. That sequence is explicitly prohibited and has a dedicated
two-caller/controller-exit qualification obligation in the concrete proposal.

After physical admission is qualified, the existing owner must validate the
final complete legacy snapshot, durably publish the per-version authority,
and record the exact activation phase before effects. Original registration,
pending/queued/retry/event identities remain unchanged. A crash at every
file/journal boundary must leave exactly one recoverable authority, never
two live copies or an instruction to erase state. An existing or raced
legacy lock, unknown descriptor custody, missing boot evidence or malformed
journal is an unresolved transition, not permission to replace a live owner.

The operator accepted this one-time POSIX historical-migration boot boundary
as a **source design**, not a choice to retire historical support, enable map
writes or perform operations. Normal review of this concrete transition
amendment cleared in #13493; runtime realization follows. A boot-independent transition would
need a different enforceable historical execution boundary; the current lease
and new-version metadata alone do not provide one.

The bounded decision exchange is retained verbatim:

> The existing lease cannot fully fence an unmodified historical POSIX engine: a descriptor opened before a path change can acquire the old lock later. I recommend a one-time migration design that seals the old lock path, keeps legacy state authoritative, and activates isolated per-version state only after the next actual host boot has eliminated those descriptors. Windows may avoid that wait using its verified open-handle sharing behavior, subject to further qualification. Accept this source-design requirement? This does NOT authorize any reboot, installation, service stop, migration or rollout.

The form field was
`accept_posix_boot_boundary`, titled "Accept the one-time POSIX
historical-migration boot boundary in the proposed source design". Its
description was:

> Yes: take this design through normal review. No: retain the selected map/version goal and investigate a boot-independent historical execution guard instead. Neither answer authorizes live operations.

The operator's submitted answer was `true`. The field's default was also
`true`; the default alone is not the acceptance evidence. The actual submitted
answer accepts only the question's source-design requirement and limits.
The [concrete physical transition proposal](subscriber-state-transition.md)
defines staged authority, retained-inode custody, boot evidence, interrupted
recovery and serial implementation/qualification under the existing owner.
It cleared normal review in #13493, not runtime or installed qualification.

### Execution and missing directories

Use the existing owned-child runner with explicit `cwd` for new
caller-context callbacks in both delivery modes. Never change the parent
process's cwd. Keep the existing timeout, headless launch, bounded cleanup
and callback concurrency owners. Do not add a subprocess per health check
or a cwd-resolution daemon.

Directory existence at admission is not a promise of future existence.
If it disappears or ceases to be a directory, refuse that launch with no
fallback or directory creation. Preserve the existing sanitized
`callback_error` outcome: legacy remains one attempt with an inspectable
unknown result, while acknowledged delivery retains its original durable
pending event and bounded-backoff retry. A callback may already have effects
on other launch failures; do not label every error effect-free. The executable
launch boundary must also handle a directory disappearing after validation.

Keep old no-context callback behavior unchanged. Path values are ordinary
private local configuration, not credentials: persist only the required
cwd with existing subscriber metadata, never an environment snapshot,
credential material, callback output or raw exceptions. Do not expand
public diagnostic/history fields to disclose callback paths.

## Serial realization and qualification

1. **Expand and qualify version-aware admission first.** Review this amendment,
   add explicit state contract/version refusal and older-engine no-restart
   under the existing callback/storage/lifecycle owners, and qualify the
   actual historical families plus ordinary startup/activation and delayed
   client self-boot. Establish shared per-version state preservation and
   historical whole-file-writer isolation before context activation. Do not enable new
   map/context writes, advertise subscriber-cwd support or migrate callers in
   this release. Old callers remain unchanged.
2. **Qualify current/prior transition behavior before client migration.**
   Record the supported reader versions/contract families and use their
   actual reader/startup code in isolated fixtures. A mock that rejects an
   object, or an arbitrarily selected old commit, is not the rollback proof.
   Exercise cold restore and governed reload, preserving existing
   writer/route custody on refusal. Fix or guard the actual unsafe family
   before proceeding. After that prerequisite is released and its required
   activation protection is qualified, expand the existing provider with the
   per-subscription map, strict context protocols and explicit-cwd execution.
   Gate capability advertisement on complete support, not just parsing.
3. **Migrate the CLI conditionally.** Implement the proposed context selector,
   default caller capture, actual-health negotiation, strict response
   validation and ambiguity behavior. Publish the release that establishes
   provider support separately from the release that enables caller defaults.
   Keep the explicit legacy baseline; this lane retires no old wire form.
4. **Resolve the canonical gates only with consumer evidence.** Qualify two
   subscribers registering from different directories, with relative callback
   scripts, while the daemon uses a third directory. Prove actual child cwd,
   persistence/restart, missing-directory outcomes and cleanup for legacy and
   acknowledged modes. Then resolve P/V/G5947-3/4 as applicable. Installed
   rollout and unrelated parent/resource gates remain separately authorized.

The focused validation matrix must include:

- Old caller/new owner and explicit sufficient legacy baseline; unchanged
  legacy stdin/response, no fabricated ACK or new legacy retry.
- New caller/new owner for both delivery modes; capture once, strict path
  validation, conflicting structured input, exact response echo and no
  cross-subscriber cwd inheritance.
- New caller/prior owner and health/register generation change; required
  cwd refuses, no registration/fetch/write/callback on definite rejection,
  and ambiguous responses do not trigger fallback or blind replay.
- Actual current/prior saved-state restore with mixed old/context entries,
  pending acknowledged and continuous queued events; byte-for-byte unchanged
  state and zero effects on refusal, original identities on compatible
  restart, and unknown/malformed envelope refusal.
- Version-aware entry/activation refusal for an unaware candidate and the
  already-running pre-version writer case; no overwrite, implicit owner
  replacement or new slot allocation. A missing transition guarantee keeps
  context writes disabled without blocking installation readiness.
- Per-version writes preserve unselected version records and every retained
  pending/queued/retry identity. A lower executing generation refuses cold
  startup, self-boot, resume and rollback even when its old partition remains.
  Exercise delayed clients, final-write interleaving and removal of the final
  cwd subscription without dropping the generation fence. Actual historical
  whole-file writers cannot clobber the version-aware authority or redeliver
  a migrated legacy copy; interrupted migration preserves registrations and
  existing custody through the normal recovery owner.
- Definite write failure before scheduling and uncertain post-replacement
  recovery, retaining the existing atomic persistence owner and no duplicate
  event or legacy replay.
- Real child cwd, missing/removed directory, callback timeout and descendant
  cleanup through existing opt-in contained Windows/Linux process lanes,
  including any focused smoke subset. Required PR/promotion CI stays unit-only
  with coverage and no real-child/process smoke fallback; no new fleet-wide
  or hosted-workflow prerequisite.

No live forge request is necessary to test this local callback contract.
Use temporary test-owned roots and synthetic snapshots. This proposal grants
no installation, service replacement, credential action, provider mutation,
resource cleanup, new hosted run or production consumer activation.
