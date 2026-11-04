# Admitted client construction

`fleet-manager client-state construct` constructs and locally checks a supplied,
durably admitted client release. It never selects a release, advances admission,
publishes an active selector, installs a service or obtains credentials.
It is a construction prerequisite, not a complete managed install/update/uninstall
flow or evidence of Gateway connectivity.

```text
fleet-manager client-state construct --root <private-installation> --identity identity.json --selection selection.json --release release.json --proof proof.json --policy policy.json --sources sources.json --staging-parent <separate-private-stage> --target target.json
```

First explicitly initialize and admit the selected release through
[client admission](client-admission.md). Construction refuses an unadmitted,
different or lower selection; it does not silently perform those transitions.
The owning client needs the explicit `release-verification` and `construction`
extras, its RECORD-admitted lifecycle resources, and standard-library `venv`.
The construction extra supplies the maintained pip tool in the owning runtime,
not in the candidate. Setup of that extra belongs to the normal qualified
package/setup authority; construction never installs it implicitly.
The existing POSIX process guardian also needs the operating system's `ps`.
No package index, build backend, Docker, controller or Copilot process is used.

## Explicit compatibility and supported artifacts

The trusted target input describes the API and every data schema used by
current configuration and retained state:

```json
{
  "schema": "fleet-manager.client-target",
  "version": 1,
  "authority": "example-authority",
  "installation": "example-client",
  "revision": 1,
  "components": {
    "cli": {"api": 1, "data_schemas": [1]}
  }
}
```

Component names must exactly cover the selected client closure. API values
must fall within each authenticated declaration's range, and all supplied
config/retained-data schemas must be supported. Missing, unknown, duplicate or
incompatible inputs refuse before candidate execution. This is compatibility
against explicit trusted inputs, not discovery of a server or a data migration.

This first constructor supports the existing single, pure-Python
`fleet_manager-...-py3-none-any.whl` client distribution. Other closures,
source/native artifacts and distributions refuse explicitly. Installation is
offline with `--no-index --no-deps --no-compile` through that exact owner's
`pip --python <candidate>`; no pip bootstrap or hosting extra is installed in
the dependency-free candidate. The installed
dependency check must pass. A future multi-wheel resolver or hosting lifecycle
is not implied by authenticated staging's broader component support.

The Python API is `construct_client_release(..., current_policy=...,
current_target=...)`. Both suppliers belong to the trusted caller and must
return current inputs, not historical verification reports. The CLI rereads
the supplied policy and target files at checkpoints. Construction rereads the
exact installation/admitted selection under exclusive ownership and rechecks
authentication and compatibility before executing candidate commands and before
completion. A valid signature or a newer release does not replace selection.

## Candidate ownership, health and retry

The installation owns `client-runtime/client-owner.json`, bound to its exact
initialization identity. Foreign or linked namespaces refuse. Candidates use
the canonical `versions/client-<sequence>-<manifest-sha256>` layout.
Construction, retention, admission, failed-candidate discard, source fingerprint
and completion all use the installed canonical `versioned-runtime` resource,
not a second installer protocol.

Before completion, construction also packages the canonical slot-local
`runtime_launcher.py` and runtime family in `runtime-bootstrap/`, verified
against the owning wheel's resource RECORD. The completed fingerprint includes
these bytes. [Explicit command registration](client-frontdoor.md) uses that
bootstrap to retain the runtime before site imports; it never patches an
already completed candidate or implicitly registers a command during construction.

The constructor queries the candidate interpreter's own site directory and
installs an exclusive `immutable_runtime.pth` there before fingerprinting.
It disables bytecode writes before native console entry-point imports, including
ordinary invocations without inherited environment flags. Otherwise a successful
direct command could create `__pycache__` files and invalidate its own completed
runtime. The policy is part of the immutable fingerprint, not an exclusion for
unverified cache content; a nonlocal site directory or existing policy file
refuses rather than being overwritten.
Installing this interpreter policy is separate from copying the RECORD-bound
launcher resources. Construction requires both before health and fingerprinting;
copy-only synthetic fixtures need no interpreter or process descriptor.

Each candidate's private input receipt lives under `client-runtime/client-inputs`,
outside the removable slot. It binds installation identity, selected decision
and decision digest. This placement matters: a failed native removal can
delete part of an unfinished tree without erasing its only ownership evidence.
Unknown candidate contents without that receipt refuse rather than becoming
an adoptable failed build.

Authenticated immutable snapshots are copied into the candidate, never executed
from the ephemeral stage. A copied, separate venv receives only the selected
wheel. Fixed probes run its actual console `--version` and `doctor --role client`
outside the checkout, plus the offline installed dependency check. Their
environment excludes inherited credentials and uses a candidate-local home.
On Windows, `TEMP`, `TMP` and `TMPDIR` instead use the owning runtime
namespace's ordinary `process-tmp` directory. Keeping these private paths out
of the long immutable slot avoids native launcher/console startup hangs;
the candidate-local home and existing process deadlines are unchanged.
The trusted base interpreter and its standard library remain prerequisites;
this is not a hermetic interpreter or hostile-code sandbox.

The canonical installer child supervisor bounds execution and settles its
descendants. Its fixed helpers use one retained owning-SDK RECORD snapshot,
with helper bytes rechecked before every child; this is separate from fresh
release-policy authentication. Its procutil resource is a static, canonical
five-file package: `__init__.py`, `_job_scopes.py`, `_windows_job_types.py`,
`streaming.py` and `_output_relay.py`.
All have unique installed RECORD entries and are freshly read and verified
before execution. Native accounting bindings load only on native attribute
access; importing the retained package does not load a native library. The client
retains all three byte streams in one operation-owned package namespace, preserving
relative imports without an ambient package or a later unverified source read.
The operation's `finally` removes only its private module entries; it does not
discard native process/Job references retained by the existing failure owner. The standalone
wheel and sdist ship the same closure; a lone renamed module is not a complete
construction resource. The release artifact verifier binds every member to
their canonical sources. Process resource names must be canonical relative
POSIX paths on every platform; rooted or drive-qualified paths, backslashes
and alternate data streams refuse before reading resources.
POSIX retains the construction descriptor in its existing guardian.
On Windows the private guardian's stdin carries the complete eleven-member
retained executable closure: the supervisor itself, the runtime and its four
companions, and all five procutil members. A fixed standard-library bootstrap
validates the exact bounded bundle before executing its retained supervisor.
The guardian loads only those bytes into private operation-owned modules; it
does not reopen executable resources or resolve relative imports from disk.
Progress capture retains the streaming and relay modules in the same private
namespace. Relay children execute the retained relay bytes through their existing
finite launch path rather than reopening the resource file. The guardian packet
writer and output readers must all settle before capture completes.
Ordinary source-install callers snapshot the same bounded closure before
launching; this is not installed-wheel RECORD authentication.
The client caller establishes its absolute deadline before RECORD/resource
admission and forwards it unchanged to the finite owner. One monotonic execution
deadline covers guardian source preparation, startup
and target creation; neither process starts with a fresh timeout budget.
Admission rechecks that deadline immediately before guardian launch and after
confirmed settlement, so late completion cannot report success. The existing
retirement allowance and uncertain process/Job custody remain unchanged.
The finite guardian independently retains the candidate until its
native Job settles, including after caller death. Losing a caller's
construction lock alone therefore does not admit destructive cleanup while
that guardian still owns retention. This is finite caller-owned supervision,
not a new background service.

After health, `client-constructed.json` binds the input receipt to the actual
candidate file fingerprint. The canonical completion marker binds that receipt
digest. The two completion records themselves are excluded from the tree
fingerprint. Reuse checks the stored binding and actual bytes, then freshly
repeats policy, compatibility and health checks without rewriting completion.
Altered completed content requires distinct-generation repair, never an
in-place rebuild.

Failed construction or health leaves an unpublished owned candidate for an
explicit, bounded retry; it does not mask the original failure with eager
recursive cleanup. Retry freshly validates intent and uses canonical
failed-candidate discard before rebuilding. Foreign state, active/retained
content and cleanup failures refuse. Partial discard preserves the external
input receipt. The durable accepted floor and previous active selectors,
retained-good references and user configuration are never reduced or rewritten.
An uncertain completion outcome requires a fresh owned retry/reread.

Client lifecycle JSON journals share an attempt-local durable writer: each
exclusive temporary is fully written and fsynced before replacing the intended
journal and syncing its parent. Empty, truncated or complete abandoned attempts,
including legacy `<journal>.tmp` files, neither block retry nor supply ownership
or successful completion. They are not adopted or removed by a journal retry.
Existing candidate-discard and exact owned runtime-removal contracts still
govern deletion inside their captured runtime trees.

Initial namespace ownership is written and synced inside the existing staged
initialization directory before that directory is renamed into place. Its
recognized ordinary owner-file recovery names remain unchanged; unknown entries
are preserved and refused. This journal change does not repair a missing or
unframed created-object publication receipt header, relax acknowledged-owner
proofs, or complete installation retirement.

Command and selector producers now initialize their context and complete native
receipt header before publishing the durable pending intent, and persist that
intent before the first target effect. A failure during initial producer setup
therefore leaves only unreferenced retained history, not a pending owner whose
required proof was never completed. New receipt headers publish atomically under
their final name without replacing a collision or introducing a second hard
link. This prevents new incomplete-header publication; it does not repair or
adopt an existing missing/unframed proof. Once pending intent exists, failures
remain explicit unknown outcomes and require the existing owned recovery path.

## Reports and remaining transitions

### Independent generation prerequisite

`construct_client_generation` uses a separately initialized version-2 lineage,
not the version-1 command path above. That single SQLite authority retains the
physical lineage origin, an optional read-only import of the legacy admitted
floor, exact request/generation reservations and physical generation-root intents.
Each new request advances the current-request fence atomically with admission;
an exact retry never makes an older request current again. Same-release
generations can finish preparing independently, but only the current,
nonretired request can authorize future shared publication. A newer admitted
selection still fences lower-release construction.

The private retirement transition writes an exact reservation-bound tombstone.
It prevents later construction or shared publication for that generation without
deleting its runtime, withdrawing commands, waiting for its retention holders or
resetting the admission floor. An already running/directly usable immutable
runtime is preserved. A selected owner must first complete owned shared
withdrawal; a newer forward request does not need that retirement to finish.
Malformed fences, changed identities and same-byte root replacements refuse
rather than being repaired or adopted.

Generation construction reports its actual direct command with `installed` and
`started` true and `selected` and `registered` false. The selected-generation
field is not populated from construction health or a reservation.

`publish_client_generation` in `client_generation_frontdoor` is the separate
source-level default-command transition. It requires the exact reserved,
completed generation and an explicit existing private command directory. It
uses the existing retained-runtime launchers, canonical copy producer and
target custody, not a version-1 admission database fabricated in each generation.
The lineage binds one physical default directory and the latest immutable
publication attempt. Attempts link retained producer history; a distinct
selected-publication pointer binds the acknowledged generation to its attempt.
Neither a same-byte replacement nor a historical command's bytes alone is
ownership evidence.

Command-target custody is acquired before brief central writer custody. Intent
is committed after the canonical context/proof header is durable and before the
first command effect. The central lock and SQLite writer are released before
installed default-command health; acknowledgement reacquires them under the
still-held command targets and rechecks the request, retirement, current trust,
stage, target and actual physical copies. New generations can reserve and start
while that health is running. A superseded publisher cannot acknowledge or
restore over a winner. Failures after durable intent retain exact producer
proofs and report an unknown command outcome, not a failed runtime start.
An exact successful-copy retry can acknowledge its original objects without
republishing; a partial/recovered attempt requires a new attributed copy attempt.
Legacy command adoption reads the imported origin and existing physical
registration proofs without taking the predecessor's construction lease.
Corrupt legacy/shared proofs can refuse default cutover without blocking
independent construction in another generation.

For an explicitly authorized incoming replacement, the caller can instead supply
`replacement_targets` to `publish_client_generation`. This is the exact current
physical target observation from `inspect_command_targets`, bound to the
installation identity, generation reservation and private command directory.
The CLI exposes `client-generation inspect-command-targets --root ... --identity
... --reservation ... --command-directory ...` and accepts that JSON through
`register-commands --replacement-targets <file>`. Inspection does not acquire
credentials, execute a runtime or authorize replacement. The trusted invoking
owner must already have authority to replace that exact fixed command family;
do not automatically feed an arbitrary discovered directory into this flow.

Initial incoming publication rechecks the entire observed family under the common native
target leases. An unaccounted changed object, different reservation,
installation or directory refuses without rewriting commands. With unchanged
explicit targets, historical producer receipt recovery is not a cutover
prerequisite. The common copy owner captures fresh physical baselines, preserves
backups and creates incoming receipts; actual installed default-command health
still precedes acknowledgement. Existing predecessor bytes and damaged receipts
are not repaired, deleted or treated as incoming execution authority.
Ordinary registration without explicit incoming targets retains its strict
historical-ownership behavior. Old withdrawal/removal APIs retain their own
physical proof requirements.

An incoming attempt records an optional `replacement_targets_digest` in its
existing publication intent. This correlates the original observation; it is
not effect authority. Repeating the same input can verify the current
producer's unchanged objects and acknowledge the original publication without
republishing, including an interrupted acknowledgement. Partial/recovered
progress may continue only from that attempt's captured baseline or native
producer proofs; foreign and same-byte replacement objects still refuse.
A fresh observation that matches current targets can start a new authorized
attempt without requiring old proof recovery. This continuation does not walk
predecessor-copy receipts or reacknowledge a withdrawing attempt.

This is a fixed command-publication boundary, not the complete authoritative
installer. Corrupt authoritative lineage/application state, full setup
composition, automatic-versus-explicit classification and complete applicable
installation registration still require their owning integration and
qualification. A directly healthy runtime or this observation alone is not
whole-installation readiness.

Publication results include the exact `publication` attempt ID.
`unregister_client_generation` requires that ID, the exact reservation and the
same physical command directory. It withdraws only objects proved by that
attempt's canonical producer receipts, through native recovery with an
explicitly absent baseline: it never restores predecessor commands. An older
anchored attempt reports `superseded` without touching a successor's commands,
even when both attempts belong to the same generation. Same-byte foreign
objects refuse before any removal.

Withdrawal retains an attempt-local intent and context before effects. Both
documents are fully written and synced in unique staging files, then published
through the common atomic no-replace primitive. The context is durably published
before the intent. Interrupted staging files remain evidence, never final
metadata or a reason to block the next exact retry; a foreign final-name winner
is preserved and refused.
Withdrawal holds command targets but releases the central writer during proof
reads, native removal and durability work, so another generation can reserve and start
independently. Only after proving the command family absent does it briefly
reacquire the writer and clear selection. Interrupted effects or acknowledgement
retain evidence for exact retry. Publishing after a removal intent always uses
a new attempt, never reacknowledges the withdrawing attempt. Neither withdrawal
nor supersession lowers admission, retires or deletes a runtime, modifies local
runtime selectors, waits for retention holders or edits user configuration.

`remove_client_generation` separately retires the exact reservation, then uses
the existing native completed-runtime remover under only that generation's
ownership lease. A selected generation refuses before retirement until owned
shared withdrawal completes. Cleanup verifies the retained physical root and
exact generation construction binding without reauthenticating an old release
or requiring it to match the newer admission floor.

Before deletion, cleanup briefly acquires the latest command targets and
rechecks the lineage. Present commands must belong to another generation's
canonical producer objects and match that generation's expected command
digests. Pending commands and recovery-restored predecessor baselines are not
mistaken for successor ownership. Absent commands need no producer attribution.
The central lease and SQLite transaction are released before waiting for old
generation custody, reading command proofs, walking runtime trees or deleting
files; command targets are released before native runtime removal. A successor
can reserve, start and publish while old deletion is paused.

Partial deletion retains the native intent, physical claims and outcomes for
exact retry. Foreign objects, changed root/owner/input, canonical retention and
live-process references still refuse. Cleanup preserves the generation root,
owner, external inputs and removal history, the lineage and admitted floor,
other runtimes and user configuration. Results distinguish
`generation_retired: true` and `runtime_removed: true` from
`installation_retired: false`; this is not whole-installation retirement.

### Explicit generation CLI

`fleet-manager client-generation` is separate from the version-1 `client-state`
commands. Every operation requires explicit `--root` and `--identity` paths.
The root must already be an existing private directory; `initialize` and the
explicit `install` composition can create the lineage database.
Optional `initialize --legacy-root <path>` or `install --legacy-root <path>` imports
the exact legacy origin and admitted floor without cleaning up or rewriting it.
An `initialize` retry must repeat the same `--legacy-root`; omitting it after
an import refuses. For `install`, a supplied origin must match the durable
import, while omitting the option continues the existing lineage without
changing its origin. Neither command rereads an already imported predecessor
or requires it to remain present. Omitting the option never discovers or imports
a legacy installation.
Other primitive commands never initialize,
chooses a release, allocates a generation/request or replays predecessor work.

| Operation | Additional explicit inputs | Outcome |
|---|---|---|
| `inspect` | Optional `--generation` or `--after` | Read-only bounded reservation/retirement page and global floor/fences/publication; no runtime or command health claim |
| `construct` | `--generation`, `--request`, `--selection`, authenticated inputs below | Independently healthy direct command and exact `reservation`; no default cutover |
| `register-commands` | `--reservation`, `--command-directory`, authenticated inputs below | Owned default-command publication; returns its exact `publication` ID |
| `unregister-commands` | `--reservation`, `--publication`, `--command-directory` | Exact-attempt withdrawal; does not remove a runtime |
| `remove-runtime` | `--reservation` | Exact-generation retirement and native runtime cleanup; never installation retirement |
| `install` | `--generation`, `--request`, `--selection`, `--command-directory`, `--replace-commands`, optional `--legacy-root`, authenticated inputs below | Explicit initialization/import when absent, healthy construction and complete default command-family selection |
| `uninstall` | `--reservation`, `--publication`, `--command-directory` | Exact-attempt withdrawal followed by exact-generation runtime removal; preserves the lineage and user state |

Authenticated inputs are `--release`, `--proof`, `--policy`, `--sources`,
`--target` and a separate existing private `--staging-parent`. Policy and target
files are reread at execution checkpoints. `--reservation` names a JSON file
containing the exact reservation object, not a construction or inspection
report. Save the construction result's `reservation`, or recover it from
`inspect`'s `reservations` mapping after an interrupted construction. Inspection
does not repair a corrupt journal, initialize missing state or execute a runtime;
stored selection/publication metadata is not fresh physical health evidence.
Save `register-commands`' returned `publication` ID for exact withdrawal.
`install` returns that same publication ID and the exact reservation, so its
result supplies both inputs needed by `uninstall`.

#### Explicit managed composition

`install` is also the forward-update operation: supply a fresh generation and
request with the independently selected same or higher release. It never picks
a release, creates directories, changes PATH or starts a service. A missing
`--replace-commands` refuses before initialization. That flag expresses the
trusted caller's existing authority over the fixed command family in the exact
supplied private directory; it is not permission to discover or take over
unrelated commands. No automatic-maintenance invocation is exposed.

Receiver support is declared in the selected immutable runtime's packaged
`fleet_manager/client-lifecycle-contract.json`, schema
`fleet-manager.client-lifecycle-contract`, version `1`, capability
`explicit-generation-installation-v1`. It describes version-1 install/uninstall
reports, not package-version equality or release permission. Read it from the
runtime actually selected for execution, never a newer payload or another
interpreter's package. A missing declaration means unknown managed-composition
support: preserve the old primitive interface and do not automatically emit
`install`/`uninstall` or replay an older effectful sequence after a failure.
This is provider-first expansion; no autonomous caller migration or old-command
removal occurs here. Manual users can also inspect that executable's
`client-generation --help`. Rollback to an older provider removes this optional
capability, not the previously supported commands.

The composition initializes only an absent journal; existing version-2 lineage
state is validated by its owners, not reinitialized or reset. Legacy adoption
remains the explicit initialization operation above so the accepted floor is
imported, never guessed. Construction, compatibility and installed direct health
precede incoming publication. The existing publication owner proves every
applicable default launcher and acknowledges selection only after their actual
installed diagnostics pass. A directly healthy runtime alone is not successful
`install`.

Before publication, the composition durably saves the initial physical command
target observation as `client-installation-targets.json` in the exact owned
generation root. It uses existing generation custody and private, synced atomic
metadata publication. A retry rereads this original observation; it does not
silently treat newly changed command objects as a fresh replacement decision.
The existing producer's baseline/progress proof admits an interrupted attempt's
own changes. Missing incoming observation after publication refuses an exact
retry rather than fabricating one. A new explicitly selected generation does
not read or repair that predecessor's observation or copy receipts.

The result is `fleet-manager.client-generation-installation`, version `1`,
with explicit intent, direct command, reservation, publication and independently
proved `installed`, `started`, `selected` and `registered` outcomes. These facts
are local client readiness, not Gateway authentication or live fleet readiness.
An error retains the exact request and real partial outcomes for inspection or
retry; it does not undo a successful incoming runtime, lower the admission floor
or clean up its predecessor.

`uninstall` composes only the existing strict withdrawal and native runtime
removal operations. A foreign command or busy retained runtime can refuse; retry
keeps the original reservation/publication. Superseded commands remain selected
for their successor, while exact-generation removal retains its independent
proof requirements. The uninstallation result distinguishes absent commands
from superseded commands. Neither outcome removes the lineage database,
generation history, admission floor, configuration, credential references,
backups or other retained runtimes. This is not whole-install identity retirement
or a system package-manager uninstall.

Inspection returns at most 64 reservations in generation-ID order. The
`retired` mapping covers that same page; `reservation_count` reports the total
retained requests. Pass a non-null `next_after` value to `inspect --after` for
the next page, or use `inspect --generation` to retrieve one exact reservation
without walking history. Global current/selected generation IDs remain visible
even when their reservations are outside the displayed page.
Each call observes a fresh read-only snapshot, not one stable transcript across
concurrent writes. History is neither deleted nor capped, and existing JSON
limits remain unchanged. Incoming command-target inspection resolves its exact
reservation independently from the displayed page.
Expected target-proof, native lease and I/O failures use the normal
`fleet-manager:` CLI refusal with no inspection JSON, rather than a traceback;
no target or foreign object is adopted or changed by that refusal.

A refused publication does not undo an independently started direct command.
Retry the same explicit request rather than silently allocating new identifiers.
No command changes PATH, deletes the lineage, retires the installation or grants
live rollout. Complete installation retirement, automatic maintenance, broader
setup integration and live-adopter qualification remain separate work.

A successful `fleet-manager.client-construction`, version `1`, reports
`constructed: true`, `healthy: true`, the candidate/runtime fingerprint,
current target digest, local health result and whether it reused a completed
candidate. `active`, `activation_authorized` and `live_readiness_verified`
remain `false`. A failure exits nonzero; partial owned files can remain.
The report does not convey a live retention lease or execution/activation grant.

[Owned runtime activation](client-runtime-activation.md) separately reacquires
ownership, revalidates current policy, selection, compatibility and health, and
publishes physically attributed local selectors. Frontdoor registration,
removal, retained-good recovery, identity retirement, controller/node assembly,
artifact publication and authorized rollout remain separate gates.
