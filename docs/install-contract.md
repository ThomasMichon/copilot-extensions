# Install Contract

Every plugin in this repo installs its runtime the **same way**. Because the
Copilot CLI marketplace pulls each plugin's payload **independently**, each
plugin's install flow must be **completely self-contained** — there is no
shared install module resolved at install or runtime. Shared primitives such as
`versioned_runtime.py` are **vendored in byte-identically at authoring time**
(from a single canonical source, kept in sync by a repo tool) rather than
resolved from a common location, so each installed plugin still ships everything
it needs. This document is the reference, and
`tools/check-install-contract.py` enforces conformance (run it
explicitly while developing; publication validation belongs in CI, not a
git `pre-push` hook).

Runtime installation and communication compatibility are separate contracts.
For a new or changed CLI/service boundary, apply
[service-contract-evolution](patterns/service-contract-evolution.md): publish
versioned support metadata with the owning immutable runtime slot, distinguish
it from package/build versions, and resolve the contract of the actual selected
runtime or running daemon. A newer payload or a staged slot never establishes
live peer support. Plan provider-first expansion, conditional caller migration,
and retirement after the supported upgrade/rollback window; installation
reconciliation is not a substitute for interoperability while skewed.
This is a design requirement, not a claim that every existing installer already
publishes such metadata; per-boundary adoption remains separately tracked.

Interrupted installation-context directory-lock recovery has a standalone,
stdlib-only entry independent of broken command dispatch. The
[canonical recovery contract](../libs/installation-context/README.md) preserves
live/foreign owners, binds repair to exact observed evidence and retains
quarantine receipts. Approved manual operational-lock recovery is not an
installed-code patch or an installation-policy bypass. Repair does not make a
failed update successful or establish whole-set rollback; the original
operation's completion and consumer gates remain mandatory.

Standalone wheel/sdist resources name the same primitive `_versioned_runtime.py`
instead of the plugin script's `versioned_runtime.py`. The source companion loads
exactly one ordinary, co-located primitive in either layout, never an ambient import.
Missing resources, both names present, linked files and non-file aliases are
refused rather than selecting a fallback. Fingerprinting, source snapshots and
publication/recovery commands retain the same behavior in both layouts.

Agent Containers qualifies immutable runtime slots by its durable dependency
profile (`<package-version>+base` or `<package-version>+provider-exec`).
Its installation-owned `runtime-profile.json` preserves explicit desired state
across updates; an existing unprofiled runtime with Paramiko retains that
capability, while a fresh installation defaults to base. Package version remains
unchanged in the deploy manifest's `source.version`; `runtime_version` and
`runtime_profile` identify the selected slot. Both installers use the same
stdlib-only profile coordinator and canonical construction/retention leases,
source admission, completion and activation primitives. Explicit optional-profile
failure leaves the active selector intact, never a successful base-only downgrade.
See the plugin README's **Optional restricted SSH runtime** for provisioning.

Prepared source deployments retain this independent profile dimension:
`<package-version>+<profile>.source.<commit>.<assembly>`. The coordinator validates
the full source receipt before admission, after acquiring construction ownership,
before completion and before activation. Its deploy manifest retains the original
package version in `source.version`, the profiled slot in `runtime_version`, and
the original source receipt in `runtime_source`. Source-qualified deferred stamps
copy and verify the complete prepared assembly, so first use does not depend on
the original source location.
The native installer pins the complete receipt before waiting for its transaction
lock and passes that same receipt into profile construction or deferred stamping.
A valid receipt-only change is refused even when its runtime generation is unchanged.

Source-qualified generations require an exact `current-version` or
`last-known-good` selector. If neither resolves and source-qualified slots exist,
Python and native shell resolvers refuse recovery rather than ordering opaque
hashes or silently selecting an older plain package slot. Ordinary package-only
recovery retains its existing version ordering.

Bridge, Dispatch, Index and Vault's POSIX package downgrade guards compare
numeric release and `devN` components without relying on GNU `sort -V`.
Dot/hyphen dev spellings compare equally, and a finished release outranks its
own dev builds. Source-fingerprint suffixes do not order otherwise equal
packages; full generation selection still follows the identity rules above.
Index uses the same comparison for active/stamped publication freshness.
These checks retain genuine package-downgrade refusal; portable comparison
does not authorize a forced downgrade, source replacement or runtime rollout.

The ordinary POSIX session-bootstrap hooks for Bridge, Dispatch and MCP
use the vendored resolver in read-only mode for completed-runtime no-ops.
Readiness requires the expected generation to be selected by `current-version`,
its canonical completion marker and interpreter, and matching deployed/payload
package identity. A healthy last-known-good or newest-slot fallback is recovery,
not proof that the selected generation is ready. Prepared source payloads also
retain canonical source-receipt and payload-byte validation against the deployment
manifest; repair generations retain their package version. Valid pre-marker
legacy links remain compatible, but cannot hide a broken/different selector or
missing source receipt. No readiness cache, legacy link or publication permission
is created. These predicates do not change admission, reconciliation or deadlines.
`python tools/check-bootstrap-sync.py --sync-readiness` copies the Bridge
reference predicate to these adopting families; the ordinary check detects drift.
Other deploy-model families keep their own readiness contracts.

The root `snapshot-provenance.json` installation-cell receipt is published after
assembly and is validated independently by the cell contract; it is not a runtime
source-hash input. It must still be an ordinary file. Nested files with that name
remain hashed runtime inputs, and source preparation rejects a checkout that
already supplies this reserved deployment sidecar.

When no source-qualified slots exist, the canonical Python and native shell
recovery resolvers order profiled slots by the package-version portion before
the profile qualifier. A completed final release therefore outranks its
development versions even when selectors are missing and legacy unqualified
slots coexist with profiled slots.

## Core agent service installation-cell contract

The prescriptive ownership boundary for persistent core `agent-*` runtimes is a
**marketplace installation cell**. This is deliberately not a general plugin
contract: payload-only plugins, non-`agent-*` plugins, downstream marketplaces,
and consumer harnesses never create, validate, repair, explain, or depend on
installation cells. They use ordinary Copilot plugin loading and plugin-owned
setup surfaces.

For eligible core runtimes, plugin name and version alone never select a
writable runtime or lifecycle artifact. The normative model is defined by
[`patterns/marketplace-installation-cells.md`](patterns/marketplace-installation-cells.md);
the requirements below bind installers before the staged runtime migration is
complete.

Namespaced resource qualification applies to explicit cell installations and
their lifecycle, including preparation before activation.
Non-cell hosts retain their supported legacy/default layout and user-global
plugin commands; they are not required to activate a cell or retire those
commands. Both modes preserve attributable installation ownership and exact
command selection. The mode does not authorize last-writer-wins replacement
of another installation.

1. An installer resolves a validated installation context before writing
   machine-local state. Payload-originated installs inherit attributable
   marketplace provenance; reconciliation, repair, bootstrap, and other
   out-of-payload management callers supply it explicitly.
2. Runtime slots, snapshots, markers, manifests, state, run files, logs,
   endpoints, providers, and machine-local project adoption belong beneath the
   resolved cell and plugin subtree. No new installer may introduce an
   unqualified `~/.agent-*` root.
3. The stable identity is `(marketplace-id, plugin-id)`. `marketplace-id`
   includes a normalized source fingerprint; a marketplace display name or
   transient Copilot cache path is evidence, not identity.
4. `namespace.json` records source-to-cell identity and `install.json` records
   plugin ownership. Deploy manifests, generated commands, endpoints, provider
   records, services, tasks, units, leases, and cleanup metadata carry the same
   identity or a verifiable receipt reference.
5. Child and remote processes receive an immutable context with marketplace,
   plugin, payload, runtime, state, endpoint, provider, log, cache, and
   repository roots. Launchers replace conflicting legacy root variables rather
   than inheriting them.
6. Generic plugin shims live in their owning payload and dispatch directly to
   the selected runtime. On activated-cell hosts, machine-global commands are
   limited to attributable project entry points with ownership receipts.
   Non-cell hosts retain supported user-global plugin commands, but consumers
   still invoke the selected installation's exact command. Installers never
   compete for a global generic plugin command last-writer-wins.
   Legacy project shortcuts call the exact installed user-side `agent-worktrees`
   binstub under the same resolved local-bin directory, carrying `--project`
   and the caller's arguments. They do not embed a checkout or payload path.
   Receipt-gated registration and update reconciliation migrate unchanged
   historical payload-pinned shortcuts automatically. Checkout-to-marketplace
   adoption additionally requires the same legacy runtime, plugin/repository,
   project identity, and a deploy manifest naming the selected payload and
   version; matching repository URLs alone do not authorize a transfer.
   Edited, foreign, and ambiguous launchers remain protected. Cell-scoped
   shortcuts continue to use their selected payload-local command.
   User-side shortcut bytes contain no payload identity and therefore require an
   ownership receipt; receipt-free recognition is limited to historical
   payload-bearing templates. Native installers publish the successfully
   activated runtime's manifest before shortcut registration or reconciliation.
   Intact interrupted journals for either supported resolver complete against
   their recorded owner and matching payload/user template after lineage
   admission, before reconciliation migrates the recovered shortcut.
7. Missing, ambiguous, or mismatched provenance fails closed. Same-cell sibling
   discovery is explicit, and a same-named peer from another cell is never
   selected through `PATH`, a wildcard installed-plugin scan, or a shared
   registry.
8. Legacy unqualified state is read only through an explicit compatibility
   resolver. Migration names the destination cell and writes ownership before
   legacy activation is quiesced; uninstall removes only receipt-matching
   artifacts.
9. Installation-cell activation is governed by default-off user-local policy.
   Policy records desired mode; a validated plugin activation receipt records
   the actual authoritative root. Enabling policy never creates a parallel
   runtime beside unattributed legacy state, and disabling policy never silently
   reactivates legacy writers after a cell is active.
10. Runtime selection is crash-consistent across the marker and deploy
    manifest. A durable installation transaction records the prior selection,
    validated completed target, receipt generations, and random transaction
    authorization before marker CAS. Bootstrap/retry finishes the target or
    restores the prior state; governance is rechecked before marker cutover and
    before service reconciliation.
11. Service-bearing cells separate passive instance evidence from active
    discovery evidence. A passive generation does not start/dequeue shared work
    or publish shared endpoint/running-version records before an ownership-checked
    pre-route promotion has made it read-ready. Namespaced deploy, promotion,
    and recovery require the live cell transaction receipt. Promotion requires
    the exact passive instance PID/version/installation identity and token;
    drain, undrain, and shutdown additionally bind that identity to the current
    route.
12. Cutover cleanup is installation-scoped and ownership-attested. Recovery may
    reconcile attributable passive/demoted instance receipts, but it never kills
    by process name or broad executable match; a completed transition leaves
    exactly one owned installation PID.

After canonicalizing the configured durable home, receipt validation requires
the physical path chain `marketplaces/<marketplace-id>/plugins/<plugin-id>` to
retain that exact hierarchy. Each component is one direct child of its
predecessor and none may be a symbolic link, junction, or other reparse point.
The canonical `namespace.json` and `install.json` receipt leaves likewise must
be ordinary files rather than links or reparse points.
Relocating an installation subtree therefore requires an explicit durable-home
configuration or management migration rather than an in-tree filesystem link.

### Snapshot provenance

A staged payload or snapshot directory is a location, not installation identity.
Before a snapshot can become input to provisioning, its producer publishes
`snapshot-provenance.json` at the exact canonical path
`<snapshotsRoot>/<snapshot-id>/snapshot-provenance.json` while holding the
marketplace genesis lock and then the plugin installation lock.

The sidecar schema is:

```json
{
  "schema": "copilot-extensions.snapshot-provenance",
  "version": 1,
  "marketplaceId": "example--0123456789abcdef",
  "pluginId": "agent-example",
  "source": {
    "kind": "github",
    "canonical": "github:owner/repository",
    "ref": "",
    "fingerprint": "sha256:<full digest>"
  },
  "snapshot": {
    "id": "1.0.0",
    "root": "<absolute canonical snapshot root>"
  },
  "payload": {
    "root": "<originating absolute payload root>",
    "version": "1.0.0",
    "origin": "installed",
    "originReceipt": null
  },
  "namespaceReceipt": {
    "path": "<absolute canonical namespace.json>",
    "generation": 1
  },
  "installReceipt": {
    "path": "<absolute canonical install.json>",
    "generation": 1
  },
  "createdAt": "2026-01-01T00:00:00Z"
}
```

`snapshot-stamp` requires explicit context, expected marketplace/plugin
identity, both caller-observed receipt generations, and one portable snapshot
id. The producer must first materialize a non-empty snapshot directory beneath
the canonical `snapshotsRoot`; publication neither creates that directory nor
accepts a sidecar-only snapshot. It validates active receipts under both locks,
creates only the sidecar, and treats an existing valid sidecar as immutable and
idempotent. It never overwrites malformed or conflicting provenance.

`snapshot-validate` independently validates the normalized source and full
fingerprint against the canonical namespace receipt, validates marketplace,
plugin, payload, receipt path, and pinned-generation identity against the
canonical install receipt, and requires the sidecar to remain at its exact
cell-local location. Validation also requires at least one non-sidecar entry to
remain in the snapshot directory; this is an ordering/presence check, not a
content-integrity claim. The original payload path need not remain readable
after snapshot production; the canonical receipts and fingerprint are
authoritative. If either receipt changes, the sidecar is stale and cannot
authorize a later provisioning transaction.

Both actions are non-operative. They do not create a runtime slot, activate a
root, migrate state, cut over a marker, write a tombstone, or remove anything.
Malformed JSON, duplicate keys, a BOM, wrong known-field types, unsupported
versions, path escape, copied cross-cell provenance, or any identity mismatch
fails before sidecar replacement or provisioning.

Snapshot provenance shares the receipt threat model: it prevents accidental
cross-cell adoption, stale-generation reuse, and ambiguous ownership, but it is
not a cryptographic attestation of producer execution or snapshot contents. A
malicious process running as the same user can rewrite receipts and sidecars;
later provisioning must still enforce the canonical receipt chain and its own
content-integrity requirements.

### Runtime-slot ownership

A version directory is also a location, not installation identity. Before
payload files, completion markers, launchers, or services can be attached to a
runtime slot, a provisioning transaction must revalidate the canonical context
receipt and snapshot provenance under the marketplace genesis lock and then the
plugin installation lock. It may create only the exact canonical
receipt-defined versions-root chain and
`<versionsRoot>/<runtime-version>/` directory, and must publish this immutable
marker within the slot:

```json
{
  "schema": "copilot-extensions.runtime-slot-ownership",
  "version": 1,
  "marketplaceId": "example--0123456789abcdef",
  "pluginId": "agent-example",
  "sourceFingerprint": "sha256:<full digest>",
  "runtime": {
    "version": "1.0.0",
    "root": "<absolute canonical runtime slot root>"
  },
  "snapshot": {
    "id": "1.0.0",
    "root": "<absolute canonical snapshot root>",
    "provenance": "<absolute canonical snapshot-provenance.json>",
    "provenanceSha256": "<full lowercase SHA-256 digest>"
  },
  "namespaceReceipt": {
    "path": "<absolute canonical namespace.json>",
    "generation": 1
  },
  "installReceipt": {
    "path": "<absolute canonical install.json>",
    "generation": 1
  },
  "createdAt": "2026-01-01T00:00:00Z"
}
```

The runtime version is one portable filesystem component. The slot, versions
root, and ownership marker must retain their exact canonical cell-local
locations and may not be links or reparse points. A matching existing slot is
idempotently reusable; a markerless, malformed, copied, linked, stale, or
conflicting slot fails without replacement. The ownership marker cannot
authorize a different marketplace, plugin, source fingerprint, runtime version,
snapshot, provenance byte sequence, receipt path, or receipt generation.

Creating a new slot requires the snapshot's pinned generations and payload to
match the current active receipts. After publication, a slot remains
attributable across later receipt generations so rollback is not stranded: its
marker must still match the immutable snapshot sidecar and stable current cell
identity, while the current namespace and install generations may advance but
may not regress below the pinned values.

Failed construction pins both receipt generations and the payload root/version
across candidate cleanup and reprovisioning. The Python provisioning API accepts
the paired `expected_namespace_generation` and `expected_install_generation`
arguments and rechecks them under the receipt locks before reusing or creating
ownership. A concurrent receipt publication invalidates the older constructor;
it cannot adopt the newer receipt's candidate.

Python and PowerShell publication prepare a hidden
`<versionsRoot-parent>/.runtime-slot-<slot-digest>-<nonce>/` sibling outside
`versionsRoot`, write `.runtime-slot-reservation.json` before ownership, and
use an OS-native atomic no-replace directory rename. If
another slot appears first, publication fails and preserves it. An interruption
outside normal in-process cleanup may leave the hidden sibling; it is inert,
lies outside canonical version-slot enumeration, and requires explicit
receipt-checked release rather than automatic deletion. Dependency-light Bash
reserves the final slot with atomic `mkdir`, writes the reservation receipt as
its first entry, then publishes ownership with a no-replace hard link.
Successful publication removes reservation evidence. A Bash interruption before
the first entry can still leave a markerless slot; it is never releasable.

The reservation has the exact ownership identity fields, changes `schema` to
`copilot-extensions.runtime-slot-reservation`, retains `version: 1`, and adds
`generation`, a random non-negative signed-64-bit integer. `slot-release`
requires explicit context, durable home, marketplace/plugin/runtime identity,
current namespace/install generations, the exact reservation root, reservation
generation, and SHA-256 of the observed receipt bytes. The root may be the
canonical final slot or its digest-qualified hidden sibling. Both shared locks
cover receipt, path, identity, contents, and current/LKG revalidation before
each deletion. Only a directory containing exactly that receipt is removed.
Completed, selected, LKG, non-empty, markerless, malformed, linked/reparsed,
foreign, and replaced targets fail closed. Generation drift returns
`revalidation-required`; absent replay returns `ready` with `released: false`,
without claiming that the absent target was previously owned.

Slot ownership is non-activating. Publication does not write payload content,
`.install-complete.json`, `current-version`, `last-known-good`,
`installation-activation.json`, launchers, services, state, or tombstones. It
therefore proves only that one empty runtime slot is reserved for one validated
cell/snapshot transaction; later build-completion, cutover, health, rollback,
repair, and uninstall transactions remain separately gated.
`status: "ready"` means the ownership record is attributable, not that the
current installation is active; results expose `namespaceState`, `installState`,
and `slotEmpty` for later callers. A runtime version is an immutable build
identity and cannot be reassigned to another snapshot. Markerless or conflicting slots remain protected; only receipt-only
reservations are eligible for explicit release.

Slot provisioning allows up to 30 seconds to serialize under the shared
genesis and installation locks so slower dependency-light runners retain the
same contention behavior. The ownerless-lock initialization grace remains five
seconds.

The `slot-provision` / `slot-validate` primitive is available from the Python,
dependency-light Bash, and PowerShell installation-context runners with
cross-runner fixture coverage. Agent Machines and Agent Index expose explicit
installer adapter actions that supply their own plugin id, exact payload root,
and payload version; the caller must still provide the context receipt and
expected marketplace id. The slot transaction validates those expectations
against immutable snapshot provenance under the same receipt locks before
publication or reuse. The adapters derive the payload root from their executing
plugin payload and do not accept ambient self-stage metadata as an identity
override. These actions bypass the legacy mutation path and do not
make normal install or bootstrap context-aware. Every runner preserves
exclusive no-clobber reservation and fail-closed validation while using the
platform-appropriate publication primitive described above.

Expected generation arguments use unsigned ASCII decimal syntax, normalize
leading zeroes before comparison, and must fit the portable signed 64-bit range.

**agent-worktrees' own versioned-slot build lease** (`Enter-VersionedSlotLease`/
`_acquire_versioned_slot_lease`, a plugin-local OS-backed exclusive lock
distinct from the cell-root provisioning lock above) refuses to build into a
slot another live process is already constructing. A caller that needs to
tolerate ordinary contention rather than fail on first refusal uses the
bounded-wait wrapper (`Wait-ForVersionedSlotLease`/
`_wait_for_versioned_slot_lease`): it polls for the lease on a real wall-clock
deadline, configurable via `AGENT_WORKTREES_SLOT_LEASE_WAIT_SEC` (default 180
seconds), with a poll interval via `AGENT_WORKTREES_SLOT_LEASE_POLL_MS`
(PowerShell, default 1000) or `AGENT_WORKTREES_SLOT_LEASE_POLL_SEC` (bash,
default 1). Only genuine contention is retried; any other failure of the
authoritative gate (permission/path/storage) fails immediately regardless of
the configured budget. The PowerShell adapter classifies the platform's native
lock errors: Win32 sharing/lock violations on Windows and EAGAIN/EWOULDBLOCK
on Unix; a Unix permission error must not be mistaken for contention.
Reaching the deadline without acquiring the lease fails the
build with an actionable error naming the env var to raise if builds routinely
take longer than the default.

An operative plugin adapter holds one cell-root provisioning lock across the
entire snapshot, slot reservation, venv/package build, completion, cutover, and
deploy-manifest publication transaction. Receipt primitives retain their own
short locks, but releasing those locks never permits a second caller to mutate
the same supposedly immutable slot during the build. First-use dispatch,
session-start bootstrap, and direct management calls enter through that same
outer lock. Payload snapshot publication stages into a uniquely owned sibling
below `snapshotsRoot`, copies the complete payload there, and atomically renames
that directory into the final version path before publishing provenance. A
temporary ownership marker remains until provenance publication and validation
complete. Retry may remove only a marker-proven final directory that still has
no provenance sidecar; a pre-existing, unowned, malformed, or conflicting
snapshot is never deleted or replaced.

Agent Index qualifies its runtime-slot identity by dependency profile:
`<payload-version>+host`, `<payload-version>+client`, or
`<payload-version>+unconfigured`. Its canonical `.install-complete.json`
retains exactly `version`, `completed_at`, `pid`, and `payload_hash`; the
separate immutable `.agent-index-runtime-profile.json` strictly binds
marketplace, plugin, slot root/version, role, and extras. A configured-profile
change therefore builds and selects another attributable slot rather than
mutating a completed same-version runtime.

POSIX virtual environments may use the standard slot-local `bin/python`
symlink. An operative adapter may permit only that interpreter leaf after the
slot ownership transaction validates, while also requiring an ordinary
`pyvenv.cfg`, a resolvable executable target, and an isolated
`agent_index.__file__` beneath the owned slot. This exception does not weaken
the general prohibition on linked or reparsed slot roots, parent directories,
receipts, package origins, or Windows interpreter artifacts.

After a successful plugin-level cutover, the adapter atomically republishes
`deploy-manifest.json` schema 4. Its `source` object records the most recently
reconciled payload provenance that bootstrap uses to decide whether a newly
loaded payload requires a forward update. Its `runtime` object independently
records the selected interpreter kind, version, slot path, interpreter path, and
the payload that selected it. A full provision advances both source provenance
and runtime selection. A marker-only historical rollback preserves `source`
while changing only `runtime`, so the next bootstrap does not silently reverse
the explicit rollback; a later different payload provenance still triggers
forward reconciliation. Missing, malformed, cross-cell, path-inconsistent, or
marker-inconsistent manifest data fails closed. A failed compare-and-swap does
not replace the manifest.

The same three runners expose explicit `slot-complete`,
`slot-completion-validate`, and `slot-cutover` transactions. Completion captures
strict build evidence into an immutable owned-slot receipt without selecting the
runtime. Cutover requires exact payload/snapshot identity, current
namespace/install generations, and a current-version compare-and-swap
expectation. It revalidates immutable completion under both receipt locks before
atomically replacing only the cell-local `current-version` and
`last-known-good` marker files. Both markers name the completed selected target:
last-known-good is the fallback when current-version cannot resolve, not a
rollback pointer. An already-current target is idempotent. Cutover changes the
versioned-runtime tier-1 selection inside the cell; `activated: false` means it
does not publish `installation-activation.json`. It does not mutate launchers,
services, manifests, payloads, or plugin application state.

Payload-invocation manifest version 2 is additive and leaves version 1
generation unchanged. Version 2 replaces `runtimeRoot` with
`legacyRuntimeRoot` and declares
`installationContext: "legacy" | "required"`. The generator accepts
`required` only for a runtime-bearing core `agent-*` identity present in this
suite's canonical marketplace; source provenance may still name an independent
marketplace carrying that same identity. Payload-only and unrelated identities
are rejected even when their manifests advertise commands, tools, runtimes, or
services.

Agent Machines is the first operative `required` adopter. Its payload dispatcher
uses legacy resolution unchanged when authoritative policy is absent or false.
It selects a cell root only from an active validated Agent Machines activation,
propagates that canonical context to the runtime, and never falls back to legacy
after requested-only, malformed, foreign-environment, maintenance, orphaned, or
generation-stale evidence. First-use and bootstrap reconciliation may build,
publish immutable completion, and cut over only inside an already-active cell;
neither path creates activation. Its fixed-identity `slot-cutover` installer
adapter supplies exact payload, marketplace, plugin, generation, and
current-marker expectations for forward update or explicit historical rollback.
Its explicit `cell-repair` and `cell-uninstall` actions are separate lifecycle
boundaries; neither accepts ambient authorization. Both shell adapters delegate
to one payload-local stdlib Python engine, require exact caller-supplied
payload/snapshot/runtime identity, namespace/install generations, and both
current/LKG expectations, and join the outer provisioning lock plus both receipt
locks.

Repair derives only schema-4 deploy metadata and the intended current/LKG
selection from existing validated immutable completion. It preserves
receipt-defined source provenance separately from historical runtime selection.
Agent Machines has no additional cell-local launch metadata to reconstruct.
Missing ownership/completion or snapshot evidence refuses repair; no payload,
snapshot, receipt, state/run/log/cache, service/task/endpoint, or external
resource is recreated.

Uninstall first preflights every artifact and refuses active activation, live
owned processes, or ambiguous evidence. Explicit deactivation is a separate
transaction. It clears selection by exact CAS before removing every validated
owned historical slot and snapshot, then derived deploy/launcher/run/log/cache
artifacts. Each deletion revalidates receipt generations, selection, ownership,
and the exact captured target and ancestors. POSIX venv interpreter links and
the exact `lib64 -> lib` scaffolding are removed as links, never followed;
other linked/reparsed artifacts refuse removal. Durable `state/`,
`namespace.json`, `install.json`, activation evidence, locks, and attributable
empty directory structure remain. Namespace GC stays separate. Repeated
uninstall returns `preserved`; drift returns `revalidation-required`, never
permission to delete a changed target.

### Installation-mode governance

This section is the normative authority for desired-mode policy, actual-mode
activation, legacy transfer ownership, and resolver status. The policy remains
binary: legacy is the default and namespaced installation is opt-in. Shadow
comparison is doctor-only; `shadow`, `new-only`, and other runtime modes do not
exist.

#### Canonical policy and maintenance home

Policy and user-wide maintenance state are pinned to the operating-system user
profile, independently of ordinary `HOME`, Copilot-home, or `--durable-home`
overrides:

- Windows uses `USERPROFILE`, resolved to its canonical physical path.
- POSIX uses the account home returned by the passwd database. `HOME` is
  accepted only when its canonical physical path is the same directory.
- WSL is a POSIX environment with its own passwd home. Windows and WSL never
  share policy, receipts, or roots by path inference.

The policy file is
`<os-user-profile>/.copilot-extensions/installation-mode.json`. A repository
`.copilot-extensions/` directory is never searched for user policy. An injected
policy path is allowed only for tests and an explicitly invoked management
command; it cannot authorize namespaced activation.

#### User policy schema

```json
{
  "schema": "copilot-extensions.installation-mode",
  "version": 1,
  "installationMode": {
    "enabled": false,
    "marketplaces": {
      "example--0123456789abcdef": {
        "enabled": true,
        "plugins": {
          "agent-example": {
            "enabled": false
          }
        }
      }
    }
  }
}
```

`enabled` fields are strict JSON booleans. Effective policy precedence is
plugin > source-derived `marketplaceId` > global > implicit `false`. Readers
and diagnostics retain whether the winning value was explicit `false` or
missing/default `false`. Plugin values are objects rather than bare booleans so
v1-compatible fields can be added later.

Within supported version 1, readers ignore unknown fields and writers preserve
them at every object level. They reject duplicate keys, malformed JSON,
incorrect known-field types, a wrong schema name, and invalid marketplace or
plugin ids. A higher unsupported version blocks activation, migration,
deactivation, cleanup, and other policy-changing operations. Doctor, explicit
repair, and already-activated runtime operation continue from the validated,
pinned actual root; an unsupported policy must not strand an active cell.
Writers use atomic same-directory replacement, UTF-8 without BOM, and LF.

#### Installation activation schema

Actual mode is recorded at
`<cell>/plugins/<plugin-id>/installation-activation.json`:

```json
{
  "schema": "copilot-extensions.installation-activation",
  "version": 1,
  "marketplaceId": "example--0123456789abcdef",
  "pluginId": "agent-example",
  "mode": "namespaced",
  "state": "active",
  "environment": {
    "platform": "windows",
    "homeRealPath": "C:\\Users\\example",
    "wslDistro": null
  },
  "context": "C:\\Users\\example\\.copilot-extensions\\marketplaces\\example--0123456789abcdef\\plugins\\agent-example\\install.json",
  "namespaceGeneration": 1,
  "installGeneration": 1,
  "generation": 1,
  "legacy": {
    "disposition": "absent",
    "probe": {
      "declared": true,
      "result": "absent",
      "checkedAt": "2026-01-01T00:00:00Z"
    }
  },
  "createdAt": "2026-01-01T00:00:00Z",
  "updatedAt": "2026-01-01T00:00:00Z"
}
```

`mode` is `namespaced` or `legacy`; `state` is `active` or `deactivated`.
Version 1 accepts only the pairs `namespaced`/`active` and
`legacy`/`deactivated`.
`platform` is `windows` or `posix`; `wslDistro` is the exact WSL distribution
identity or `null`. `legacy.disposition` is `absent`, `quiesced`,
`retained-inert`, or `restored`; the recorded probe result is `absent`,
`present`, or `unknown`.

The activation is a monotonic record. Migration publishes
`mode: namespaced`, `state: active`. Explicit rollback/deactivation writes
`mode: legacy`, `state: deactivated` at `generation + 1`; it does not delete
the record. Cleanup is the only operation permitted to delete it, and only
after all companion ownership, rollback, service, task, lease, and tombstone
evidence has been cleared under the required locks.

Activation writes use compare-and-swap while holding the marketplace genesis
lock and then the cell installation lock. The fixed order prevents inverse
acquisition. While both locks are held, the writer revalidates `namespace.json`
and `install.json` and pins the caller-observed activation `generation`,
`namespaceGeneration`, and `installGeneration`; any mismatch returns
`revalidation-required` without replacing the activation receipt. Malformed,
overflowed, or foreign-environment activation receipts are never overwritten.
`revalidation-required` is a successfully constructed compare-and-swap result,
not a published activation: CLI callers receive exit 0 and must inspect
`status` and `activationChanged`. Publication requires both context receipts to
remain in `state: active`.
The environment tuple is exact. A Windows receipt never validates in WSL, one
WSL distribution never validates in another, and no roots are shared across
environments absent a future explicit sharing contract.

The shared activation CAS is a low-level explicit management primitive. It
records actual mode but does not create a runtime slot, migrate legacy state,
write the companion tombstone, launch a process, or authorize itself from
ambient policy or `COPILOT_EXTENSIONS_CONTEXT`. The caller must supply the
canonical context receipt explicitly. A migration or rollback caller remains
responsible for the larger lock and evidence transaction.

#### Legacy ownership tombstone schema

Migration writes `<legacy-root>/.installation-ownership.json`:

```json
{
  "schema": "copilot-extensions.legacy-installation-ownership",
  "version": 1,
  "marketplaceId": "example--0123456789abcdef",
  "pluginId": "agent-example",
  "activation": {
    "path": "C:\\Users\\example\\.copilot-extensions\\marketplaces\\example--0123456789abcdef\\plugins\\agent-example\\installation-activation.json",
    "generation": 1
  },
  "environment": {
    "platform": "windows",
    "homeRealPath": "C:\\Users\\example",
    "wslDistro": null
  },
  "transferredAt": "2026-01-01T00:00:00Z",
  "attribution": {
    "kind": "explicit-legacy-attribution",
    "legacyRoot": "C:\\Users\\example\\.agent-example",
    "context": "C:\\Users\\example\\.copilot-extensions\\marketplaces\\example--0123456789abcdef\\plugins\\agent-example\\install.json",
    "items": [
      {
        "kind": "path",
        "identity": ".agent-example",
        "path": "C:\\Users\\example\\.agent-example"
      }
    ]
  }
}
```

The tombstone binds the legacy footprint to the destination cell and activation
generation. A tombstone whose activation is missing, unreadable, mismatched, or
foreign is `orphaned-transfer`: all writers fail closed and legacy operation
must never resume. Explicit rollback first publishes the next legacy activation
generation and only then clears the tombstone while holding both locks.
When a caller records explicit attribution details, `attribution.items`
enumerates exactly which legacy artifacts were claimed from which paths; absent,
ambiguous, linked/reparsed, or orphaned artifacts are preserved unchanged and
remain outside that list.

#### Deactivation record schema

Explicit rollback or deactivation writes
`<plugin-root>/deactivations/activation-<target-generation>.json`:

```json
{
  "schema": "copilot-extensions.installation-deactivation",
  "version": 1,
  "marketplaceId": "example--0123456789abcdef",
  "pluginId": "agent-example",
  "context": "C:\\Users\\example\\.copilot-extensions\\marketplaces\\example--0123456789abcdef\\plugins\\agent-example\\install.json",
  "environment": {
    "platform": "windows",
    "homeRealPath": "C:\\Users\\example",
    "wslDistro": null
  },
  "target": {
    "kind": "legacy-attribution-rollback",
    "activation": {
      "path": "C:\\Users\\example\\.copilot-extensions\\marketplaces\\example--0123456789abcdef\\plugins\\agent-example\\installation-activation.json",
      "generation": 1,
      "mode": "namespaced",
      "state": "active",
      "namespaceGeneration": 1,
      "installGeneration": 1,
      "legacyDisposition": "retained-inert"
    },
    "tombstone": {
      "path": "C:\\Users\\example\\.agent-example\\.installation-ownership.json",
      "activationGeneration": 1,
      "transferredAt": "2026-01-01T00:00:00Z",
      "attribution": {"kind": "explicit-legacy-attribution"}
    }
  },
  "result": {
    "activation": {
      "path": "C:\\Users\\example\\.copilot-extensions\\marketplaces\\example--0123456789abcdef\\plugins\\agent-example\\installation-activation.json",
      "generation": 2,
      "mode": "legacy",
      "state": "deactivated",
      "legacyDisposition": "restored"
    },
    "tombstone": {
      "path": "C:\\Users\\example\\.agent-example\\.installation-ownership.json",
      "cleared": true,
      "clearedAt": "2026-01-01T00:10:00Z"
    }
  },
  "createdAt": "2026-01-01T00:10:00Z"
}
```

#### Legacy retirement record schema

Explicit compatibility retirement writes
`<plugin-root>/retirements/activation-<target-generation>--<retirement-id>.json`:

```json
{
  "schema": "copilot-extensions.legacy-retirement",
  "version": 1,
  "marketplaceId": "example--0123456789abcdef",
  "pluginId": "agent-example",
  "context": "C:\\Users\\example\\.copilot-extensions\\marketplaces\\example--0123456789abcdef\\plugins\\agent-example\\install.json",
  "environment": {
    "platform": "windows",
    "homeRealPath": "C:\\Users\\example",
    "wslDistro": null
  },
  "target": {
    "id": "global-binstubs",
    "activation": {
      "path": "C:\\Users\\example\\.copilot-extensions\\marketplaces\\example--0123456789abcdef\\plugins\\agent-example\\installation-activation.json",
      "generation": 1,
      "mode": "namespaced",
      "state": "active",
      "namespaceGeneration": 1,
      "installGeneration": 1,
      "legacyDisposition": "retained-inert"
    },
    "tombstone": {
      "path": "C:\\Users\\example\\.agent-example\\.installation-ownership.json",
      "activationGeneration": 1,
      "transferredAt": "2026-01-01T00:00:00Z",
      "attribution": {"kind": "explicit-legacy-attribution"}
    },
    "health": {
      "kind": "example-runtime",
      "status": "ready",
      "reason": "cell-runtime-healthy",
      "checkedAt": "2026-01-01T00:15:00Z",
      "evidence": {"source": "test"}
    },
    "items": [
      {
        "kind": "path",
        "identity": ".local/bin/agent-example",
        "path": "C:\\Users\\example\\.local\\bin\\agent-example"
      }
    ]
  },
  "result": {
    "items": [
      {
        "kind": "path",
        "identity": ".local/bin/agent-example",
        "path": "C:\\Users\\example\\.local\\bin\\agent-example",
        "disposition": "removed"
      }
    ]
  },
  "createdAt": "2026-01-01T00:15:00Z"
}
```

Retirement is fail-closed and auditable. A wrapper or service artifact is
retireable only when the claimed tombstone still binds that artifact to the
current active namespaced activation and the destination cell contributes an
explicit `health.status: "ready"` report from its own validated runtime or
service health path. Missing or ambiguous ownership, unhealthy replacements,
and reappeared artifacts preserve the legacy surface unchanged.

#### Retention and deactivated cells

Phase 6's audit evidence is intentionally asymmetric:

- The legacy ownership tombstone is **temporary ownership evidence**. Explicit
  rollback clears the matching tombstone under the legacy lock and the cell
  install lock after publishing the next `legacy`/`deactivated` activation
  generation and its paired deactivation record. If the tombstone remains but no
  current namespaced activation can validate it, the result is
  `orphaned-transfer` and all writers fail closed.
- `deactivations/activation-<target-generation>.json` and
  `retirements/activation-<target-generation>--<retirement-id>.json` are
  **durable audit records**. The shared installation-context library has no
  age-based expiry, background cleanup, or rotation for either directory.
  Repeating the same explicit target reuses the existing record for idempotency;
  it does not rewrite or delete it.
- `installation-activation.json` is also retained across deactivation.
  Deactivation advances it to `mode: legacy`, `state: deactivated`; it does not
  delete the record or remove the cell's runtime artifacts. Cleanup is the only
  operation permitted to delete the activation record, and only after companion
  ownership and rollback evidence has been cleared under the required locks.

Operator shorthand may call this an "inactive cell", but the authoritative
evidence is the deactivated activation record. Diagnose it by reading
`installation-activation.json` first:

- `mode: legacy`, `state: deactivated` means the cell is no longer
  authoritative.
- `legacy.disposition: restored` means rollback restored the legacy footprint
  and cleared the matching tombstone.
- `legacy.disposition: absent` means the cell was deactivated only after the
  caller proved no tombstone remained and the declared legacy probe was absent.

The plugin root may still retain runtime slots, snapshots, logs, cache, and any
deactivation or retirement records until an explicit cleanup or uninstall path
removes the artifacts it owns. Deactivation alone is evidence publication and
authority transfer, not removal.

`target.kind` is `legacy-attribution-rollback` when the caller explicitly
clears a matching tombstone under the legacy lock and `cell-deactivation` when
the caller explicitly proves no tombstone exists. The record is keyed by the
rolled-back activation generation, so repeating the same explicit target is an
idempotent no-op rather than a second destructive mutation. The active
`installation-activation.json` still advances to `generation + 1`; the
deactivation record is the auditable companion that preserves the rolled-back
target, its prior mode/state, and whether a tombstone was cleared.

Legacy footprint is qualified by ownership. Present but unattributed state
blocks automatic activation for every cell. A valid tombstone attributing an
inert legacy footprint to another marketplace cell does not block a new,
distinct cell. Installers must not infer absence when no probe is declared.
Each runtime plugin's deploy/installation metadata declares all three lists:

```json
{
  "installation": {
    "legacyFootprint": {
      "paths": [
        "<profile-relative-or-absolute-path>"
      ],
      "services": [
        {
          "platform": "windows|posix",
          "manager": "<service-manager>",
          "name": "<service-identity>"
        }
      ],
      "tasks": [
        {
          "platform": "windows|posix",
          "manager": "<task-manager>",
          "name": "<task-identity>"
        }
      ]
    }
  }
}
```

Empty lists are explicit. A missing object or missing list produces probe result
`unknown` and is treated as footprint present. Probe outcome and time are
recorded in the activation receipt; detailed evidence remains diagnostic data.

#### Resolver result and precedence

Every installer, bootstrap, hook, reconciler, launcher, supervisor, repair,
uninstall, and cleanup caller consumes this result shape:

```json
{
  "schema": "copilot-extensions.installation-resolution",
  "version": 1,
  "marketplaceId": "example--0123456789abcdef",
  "pluginId": "agent-example",
  "environment": {
    "platform": "windows",
    "homeRealPath": "C:\\Users\\example",
    "wslDistro": null
  },
  "desiredMode": "legacy",
  "actualMode": "legacy",
  "status": "ready",
  "maintenance": {
    "state": "inactive",
    "scope": "none",
    "marker": null,
    "sidecar": null
  },
  "runtimeRoot": "<authoritative-absolute-root>",
  "context": null,
  "activation": null,
  "activationGeneration": null,
  "installGeneration": null,
  "reason": "policy-default-false",
  "policy": {
    "path": "<absolute-policy-path>",
    "authoritative": true,
    "state": "missing",
    "scope": "default",
    "enabled": false,
    "reason": "policy-default-false"
  },
  "legacy": {
    "root": "<absolute-legacy-root>",
    "probe": {
      "declared": false,
      "result": "unknown",
      "checkedAt": null
    },
    "tombstone": null,
    "disposition": "active",
    "ownerMarketplaceId": null
  }
}
```

All fields are present. For `provenance-blocked`, `marketplaceId`, `context`,
`activation`, `desiredMode`, `actualMode`, and `runtimeRoot` may be `null` when
no safe value can be established. An explicitly supplied or payload-derived
plugin id remains in `pluginId` when its exact filesystem-safe identity is
trustworthy even though marketplace provenance is not. Paths are absolute
canonical paths. `reason` is a stable machine-readable code and distinguishes,
among other cases, explicit false from implicit/default false.
`maintenance.state` is `inactive`, `active`, or `stale`;
`maintenance.scope` is `none`, `user`, or `plugin`.

`policy.path` is the selected absolute path. `policy.authoritative` is true only
for the canonical operating-system-profile path; an explicitly injected path is
reported as non-authoritative even when it has the same spelling. `policy.state`
is `missing`, `valid`, `invalid`, or `unsupported`; `scope` is `default`,
`global`, `marketplace`, or `plugin`. `legacy.disposition` is `active`,
`owned-by-current-cell`, `owned-by-other-cell`, or `orphaned-transfer`.

When more than one condition applies, status precedence is:

```text
invalid
> maintenance-blocked
> foreign-environment
> orphaned-transfer
> revalidation-required
> migration-required | deactivation-required | provenance-blocked
> ready
```

Callers authorize behavior by status and reason rather than reinterpreting
evidence. Long-lived callers pin activation and install generations and
revalidate them at each iteration boundary and immediately before mutation.

Syntactically valid `status` and `probe-legacy` invocations return a complete
resolution object and exit normally even when expected on-disk evidence is
corrupt. Malformed policy or activation is `invalid`; an exact environment
mismatch is `foreign-environment`; an invalid legacy ownership chain is
`orphaned-transfer`; changed pinned generations are `revalidation-required`;
and unresolved source identity is `provenance-blocked`. Exit 1 is reserved for
malformed invocation arguments, including invalid invocation-supplied JSON, or
for a failure so early that even the diagnostic environment/result cannot be
constructed.

#### Stable resolver reasons

The following reason codes are stable for version 1:

| Reason | Meaning |
|---|---|
| `policy-default-false` | No explicit winning policy value exists. |
| `policy-global-false`, `policy-global-true` | The exact global boolean wins. |
| `policy-marketplace-false`, `policy-marketplace-true` | The exact source-derived marketplace override wins. |
| `policy-plugin-false`, `policy-plugin-true` | The exact plugin override wins. |
| `policy-invalid` | Supported-version policy has malformed known fields, schema, identity, or JSON. |
| `policy-version-unsupported` | Policy uses a higher unsupported version. |
| `policy-injected-non-authoritative` | An injected policy was evaluated for diagnostics but cannot authorize activation. |
| `activation-required` | Authoritative policy requests namespaced mode, no activation exists, and a declared probe proves the legacy footprint absent. |
| `namespaced-active` | A valid active namespaced activation pins the actual runtime. |
| `migration-required` | Namespaced mode is desired while legacy remains authoritative and its absence is not proven. |
| `deactivation-required` | Legacy mode is desired while a valid active namespaced activation remains authoritative. |
| `provenance-blocked` | Marketplace provenance cannot be established safely. |
| `foreign-environment` | Receipt environment differs from the exact current platform/profile/WSL tuple. |
| `orphaned-transfer` | A legacy tombstone cannot validate its active destination ownership chain. |
| `revalidation-required` | A pinned namespace, install, or activation generation is no longer current. |
| `maintenance-active` | A marker has a valid, live, unexpired same-host owner. |
| `maintenance-stale` | A marker exists but its sidecar is missing, malformed, foreign-host, dead-owner, not-yet-active, or expired. |
| `legacy-owned-by-other-cell` | The legacy footprint has a valid ownership tombstone and cannot be mutated by this probe. |
| `legacy-active` | Legacy is the authoritative ready runtime and mutation is permitted. |
| `namespaced-requested` | A clean namespaced request must be activated explicitly; the legacy probe refuses mutation. |

Implementations may add a more specific stable invalid-evidence reason, such as
`activation-invalid` or `context-invalid`, without changing status precedence.

#### Durable runtime-root pointer (resolver-free consumers)

The September 2026 advisory `runtime-root` file is retired as a discovery
mechanism. It was keyed only by plugin name, could be overwritten by another
marketplace, and described a runtime/state directory rather than a callable
command. Neither diagnostic action publishes, modifies, or deletes that file.
Existing files remain untouched; cleanup is a separate explicit operation.

`status`, `probe-legacy`, and the importable `resolve_installation_mode` API
are non-mutating diagnostics on every supported platform. Their result schema,
ownership checks, and refusal semantics remain unchanged. Diagnostic success
does not authorize publication, provisioning, activation, or service startup.

Resolver-free onboarding checks its own payload command and installer, using
the same readiness criteria as that payload's session command catalog; it
never executes a shim to test availability or trusts an arbitrary existing
runtime directory. If its owning payload command is unavailable, onboarding
stays silent even when a legacy global command exists; it never directs a user
to an unavailable catalog entry. This does not remove or disable supported
legacy commands.

Session callers consume the owning plugin's exact catalog `argv`. Durable
out-of-session callers use explicitly ownership-bound stable launchers.
Neither uses a plugin-name-only pointer as an invocation contract. Publication
belongs to the corresponding explicit lifecycle or session-start boundary,
not a status query.

#### Effective-mode and status table

| Policy/evidence | Activation and legacy state | Effective result |
|---|---|---|
| File or `enabled` absent | No namespaced activation | desired/actual `legacy`, `ready`, reason `policy-default-false` |
| Winning value explicitly `false` | No namespaced activation | desired/actual `legacy`, `ready`, reason identifies the explicit global/marketplace/plugin false |
| Winning value `true` | No activation and a declared probe reports `absent` | desired `namespaced`, actual `legacy`, legacy root diagnostic-only; `ready`, reason `activation-required`. `probe-legacy` refuses mutation with `namespaced-requested`; only the explicit activation transaction may publish namespaced ownership. |
| Winning value `true` | No activation and the probe is present, unknown, or undeclared/incomplete | desired `namespaced`, actual `legacy`; `migration-required`. Legacy mutation remains allowed until the explicit two-lock migration publishes the matching tombstone and activation. |
| Winning value `true` | Legacy footprint has a valid tombstone for another cell | the distinct cell does not claim or mutate that legacy footprint. With its own declared `absent` probe it may report `activation-required`; the valid tombstone still makes `probe-legacy` refuse mutation. |
| Winning value `true` | Valid active namespaced activation for this cell | desired/actual `namespaced`, `ready` |
| Winning value becomes false or disappears | Valid active namespaced activation | actual stays `namespaced`; `deactivation-required` until explicit rollback |
| Any | Tombstone exists but its activation is missing, unreadable, mismatched, or foreign | `orphaned-transfer`; no legacy or namespaced mutation and never resume legacy |
| Any | Activation/tombstone environment differs from the current exact environment tuple | `foreign-environment`; fail closed |
| Any | Observed activation, namespace, or install generation changed | `revalidation-required`; restart resolution |
| Any | Marketplace provenance is unresolved or ambiguous | `provenance-blocked`; do not create or migrate a cell. When policy is genuinely missing/default-false and the legacy root is active with no ownership tombstone, `probe-legacy` may authorize only that legacy root's bootstrap or maintenance. |
| Malformed or invalid supported-v1 policy | Pinned actual root can be validated | `invalid`; retain that root for diagnosis, but block mutation except the explicitly defined repair path |
| Higher unsupported policy version | Valid active namespaced activation | `invalid`; keep pinned namespaced runtime and doctor/repair available, but block policy-changing operations |
| Higher unsupported policy version | No valid activation | `invalid`; create or migrate nothing |
| Otherwise ready | User or cell maintenance marker exists | `maintenance-blocked`; current runtime root remains diagnostic-only and new activity stands down |
| Otherwise ready | Marker exists without a live, valid sidecar owner | `maintenance-blocked` with `maintenance-stale`; never auto-clear |
| Otherwise ready | Remote target maintenance cannot be determined | `maintenance-blocked` with `maintenance-unknown`; treat target as quiesced and do not provision |

#### Migration, locking, and legacy rollout gate

Migration is one two-lock critical section: acquire the legacy plugin's existing
lock/lease and the destination cell installation lock, then hold both across
quiescence, state transfer, tombstone and activation publication, and final
verification. Failure to acquire either lock fails closed. Publication order
for a transferred legacy footprint must never expose namespaced activation
without its matching tombstone, nor clear the tombstone before a rollback
activation is durable.
An explicit attribution command may no-op when the same destination already owns
the tombstoned footprint, but it must never silently claim ambiguous or
orphaned legacy state.

At every iteration boundary and immediately before mutation, legacy and
namespaced long-running loops recheck maintenance, the tombstone, activation
generation, and install generation. Lease renewal is refused while maintenance
applies so work drains instead of extending itself.

Before either exemplar or any later cell-aware plugin becomes operative, every
legacy installer and bootstrap entrypoint for that plugin must call the shared,
dependency-light activation probe before mutation. The probe refuses mutation
on `namespaced-active`, `orphaned-transfer`, or applicable maintenance. This is
a rollout gate, not optional compatibility hardening.

The primitive exposes two read-only actions:

- `status` emits the resolution object and exits 0 for every constructed
  diagnostic result, including blocked and invalid statuses.
- `probe-legacy` emits the same object plus `allowMutation` and `probeReason`.
  It exits 0 only when legacy mutation is allowed, 3 for a deterministic
  governance refusal, and 1 only for malformed invocation or an unconstructable
  diagnostic result.

`probe-legacy` refuses valid namespaced activation, every valid legacy
tombstone, clean namespaced pre-activation, maintenance, invalid or foreign
evidence, orphaned transfer, generation revalidation, deactivation-required
state, and any other unsafe state. Unresolved provenance remains fail-closed
for cell creation and migration; the sole legacy-mutation exception is a
genuinely missing/default-false policy with an active, untombstoned legacy
root, because no namespaced ownership has been requested or published. It also
permits a ready authoritative legacy runtime and `migration-required`, because
legacy remains authoritative until the explicit two-lock migration transaction
publishes activation and tombstone ownership. Neither action creates, modifies,
or clears any file or directory.

#### Maintenance contract

`<os-user-profile>/.copilot-extensions/maintenance` remains a parser-free
existence gate. Its strict sidecar,
`<os-user-profile>/.copilot-extensions/maintenance.json`, contains
`owner`, `host`, `pid`, `reason`, `enteredAt`, and `expectedUntil`. A cell may
also be quiesced surgically with
`<cell>/plugins/<plugin-id>/maintenance` and sibling `maintenance.json`.
User-wide maintenance takes precedence over plugin-scoped maintenance.

Marker existence always gates first. A missing, malformed, ownerless, expired,
or dead-owner sidecar reports `maintenance-stale` and is never auto-cleared.
Read-only doctor/status remains available. Repair or maintenance mutation
requires an explicit authorization flag on the invoked management command;
environment variables and inherited context cannot authorize it.
The canonical Python primitive now provides explicit `maintenance-enter`,
`maintenance-status`, and `maintenance-release` actions. Entering maintenance
returns a random token recorded only in the sidecar; a management command may
proceed during applicable maintenance only when it presents that exact token,
and release removes the marker only when the token still matches the active
owner.

Remote dispatchers query target maintenance before provisioning. If target
state cannot be determined, they treat the target as quiesced and do not
provision, reconcile, ensure, or start anything.

The canonical dependency-light primitive lives in
`libs/installation-context/`. Its `stamp` operation creates or updates
`namespace.json` under the marketplace genesis lock, then `install.json` under
the plugin installation lock. Its explicit `activation-cas` operation acquires
those locks in the same order and publishes only a generation-pinned
`installation-activation.json`. Existing-receipt mutations require the
caller-observed generations and fail when they changed. Lock directories
contain attributable live-owner receipts; writers revalidate every held
ownership token immediately before atomic same-directory replacement.
`tools/sync-installation-context.py` keeps the future `agent-machines` and
`agent-index` exemplar copies byte-identical. Their installers do not call the
activation writer and remain on legacy runtime roots.

Payload-local command shims are generated from the canonical templates in
`libs/payload-invocation/`. Each adopting plugin commits a
`payload-invocation.json` manifest plus generated POSIX, PowerShell, and CMD
files under its own `bin/` payload. CI runs the generator in `--check` mode.
During the Phase 2 transition, a generated shim may resolve a legacy runtime
root only through its own payload's resolver; it may not scan marketplaces or
fall through to a same-named global command. Phase 3 replaces that legacy root
input with the installation context without changing the payload command.

`COPILOT_PLUGIN_ROOT` is the authoritative immutable payload location on
surfaces where the host supplies it. `COPILOT_PLUGIN_DATA` / `PLUGIN_DATA` is a
candidate mutable root only when the host proves that it is qualified by
globally distinguishing marketplace provenance; the variable's presence does
not replace installation identity, and not every plugin surface receives it.

> **Transition note.** Concrete `~/.agent-*` and `~/.local/bin/agent-*` examples
> later in this document describe the supported legacy/default layout on
> non-cell hosts. Those hosts need not retire that layout. Cell activation,
> migration, and cell-owned installer surfaces follow the cell contract above;
> supported default operation is not itself namespaced activation.
> Until an explicit installer or management caller wires activation and runtime
> selection, missing user policy means the legacy layout remains authoritative.
> Context receipts may be stamped without selecting the cell runtime, and the
> low-level activation CAS never invokes itself from ambient policy.

## Plugin update ≠ runtime install

### Shell-first update recovery

The installed project binstub still delegates with `--project <name>` to its
owned Worktrees front door. For `update`, that front door (including the
payload-local dispatcher) fetches the remote Manager bootstrap **before**
resolving a runtime, provisioning a venv, importing Python packages, or checking
an installed Manager's health. CMD reaches the same branch through its
PowerShell shim. Acquisition and bootstrap children remain caller-contained;
the entry does not detach or create a new console. Once the fresh host is
available, normal suite execution uses the existing durable update driver and
its finite caller-independent worker, not a second execution owner.
The existing Windows Git-sh front door selects that same native PowerShell
early entry before parsing registrations, preserving native `C:\...` roots
instead of interpreting them as POSIX paths. Only the helper filename is
converted with `cygpath`; source/root authority is not changed.
The PowerShell entry removes inherited Git repository selectors for the fresh
bootstrap rather than assigning empty values, and restores both their original
values and absence afterward. A present empty `GIT_DIR` still redirects Git.

The registered Manager root (or the existing explicit root/default when no
provider is registered) is preserved. Its durable `config.toml` `[source]`
overrides select the repository and ref; missing fields use the publication-owned
defaults in the shell entry, checked against the supported Manager bootstrap
defaults. Selection never reads `current-version`, an installed payload, or
`source_config.py`: a missing/corrupt selected runtime cannot disable recovery.
This is the existing config-over-bootstrap-defaults contract, not a second
configuration authority. Source overrides are not rewritten, and an alternate
selected control-plane provider is not replaced.

GitHub sources with portable raw URLs retain the direct-download fast path.
Other SSH, HTTPS, file, absolute or relative local Git sources use a fresh
`git init`/exact-ref fetch/detached checkout in private per-attempt scratch, then
invoke that checkout's ordinary `worktree-manager/bootstrap.sh` or `.ps1`.
Relative filesystem sources are anchored before Git changes directories.
A failed raw download may retry **only the same configured Git source**, using
normal Git credentials. No arbitrary-host raw URL is invented. Missing Git or
native acquisition failures are explicit failures, not canonical-source
fallbacks. Repository-routing environment variables are cleared without
discarding credential/SSH inputs. The shell reader supports basic and literal
quoted values, including escaped quotes/backslashes emitted by `source set`;
unsupported/malformed explicit syntax fails visibly before acquisition and is
distinct from broken-runtime recovery.

Windows scratch is created atomically with a protected DACL, owner equal to
`WindowsIdentity.GetCurrent().User`, and explicit inheritable FullControl for
that SID (plus SYSTEM/Administrators). Both file and directory inheritance are
present; Owner Rights/default elevated ownership are not used. Existing paths
are never adopted. Native Git children use no-window, caller-contained process
launches with concurrent stdout/stderr drains. Each attempt cleans up only its
own scratch, while preserving argument boundaries and native exit status.

The fetched bootstrap uses the supported Manager runtime constructor. Before
publishing a Manager, fresh installer code checks existing command bytes against
the published ownership recognizer and validates registration/root identity,
preserving foreign commands. It repeats that admission after construction,
immediately before command/provider publication, so a newly changed owner is
not overwritten by a candidate prepared against earlier evidence.
Resident Manager activation remains inside the
constructed candidate's lifetime and selector-publication transaction. It runs
with the candidate's own interpreter and dependencies; a failed or refused
cutover restores the prior selectors and stops recovery before engine repair.
Skipping the redundant Manager fetch never skips daemon activation.

Before executing an incumbent core installer, the fresh Manager validates its
captured source, installation and local-environment owner using the fresh SDK.
It rehydrates the exact registered native origin at its stable payload path
through ordinary Copilot plugin acquisition, even when the old payload or
installer is missing or corrupt. The complete registration, source fingerprint,
materialized plugin and native installer are revalidated after acquisition;
neither an exit-zero message nor an unrelated fresh checkout establishes source
ownership. Registration choices are never rewritten or relabelled.
Historical snapshots can establish an incumbent owner, but the repair always
executes the refreshed native payload's installer; missing or damaged snapshot
installers are not dependencies of that repair.

The fresh Manager then repairs the core through its
supported, source-policy/ownership-admitted installer before running its normal
whole-suite `update` orchestration, without fetching a second Manager source.
For an admitted partial shared installation, Manager requests the native
update's private missing-runtime opt-in instead of running separate provision
and update lifecycles. The opt-in requires an explicit legacy root and an
existing lifecycle ticket; the ordinary ownership and publication-policy checks
still run before staging and before mutation. It retains the Git prerequisite,
creates only an ordinary missing runtime bin, and continues through the existing
full update body. A substituted file or reparse point is refused. Ordinary
update without the opt-in still refuses an absent runtime bin, and Manager
rejects an exit-zero repair that leaves it absent.
An explicit cell retains its canonical context, actual runtime
root and publication custody throughout repair; invalid or changed cell
ownership never falls back to the shared root.
The same applies when current activation selects that cell automatically:
recovery captures its canonical context before acquisition and carries it
through repair, installed-engine command resolution and the suite child's
environment without changing the caller's environment. An attributable native
payload and its canonical registration can identify a cell-only installation;
only legacy selection requires a shared deployment manifest. Fresh orchestration
re-resolves the repaired installation instead of reusing an inherited command
that still names an older shared slot.
Project arguments and the original invocation context cross the bootstrap.
Installing the Manager alone never counts as suite success: repair failures
and the eventual update's native exit status
propagate to the caller. `--no-manager`, read-only `--auth-report`, update help,
request inspection, interrupted-request settlement, and non-update commands
keep their existing dispatch behavior. Inspecting a durable request never
triggers fresh installation.
The Windows bootstrap waits for its constructed Manager observer in a
windowless, caller-contained child and returns that child's exit status.
It explicitly forwards the caller's standard streams, preserving interactive
console input and redirected progress/diagnostics.
If any forwarded stream needs the caller's existing console, that association
is retained, including mixed redirection and console-only stderr. Fully
redirected/windowless callers never allocate a new console.
Console association is determined from the native handle with `GetConsoleMode`,
not `isatty()`, which can also classify the Windows `NUL` device as a terminal.
It does not use Windows CRT process replacement as a completion signal;
POSIX retains process replacement.
An older global helper need not equal the newly fetched payload: admission
also accepts exact bytes from the prior installation's revalidated
`commandBinding` payload or selected source snapshot. The fresh canonical
ownership reader/builder validates that existing source/installation owner
without importing the installed application or writing a new receipt.
For an older schema-3 manifest without `commandBinding`, its existing
`source.path` is a candidate locator, not ownership by itself: the same
canonical source/mode validation and exact helper-byte comparison still apply.
Runtime repair captures that validated legacy owner in memory before native
source acquisition, then revalidates its current locator and owner before
effects. Automatic namespaced activation remains authoritative when present;
no legacy receipt is written merely to make capture succeed.
Unrelated bytes, missing ownership, indirect files, and inconsistent ownership records
remain refusals; helper evolution does not require a growing release-hash list.

The opt-in [real local-Git acceptance lane](../TESTING.md#shell-first-update-real-local-git-acceptance)
uses harmless fetched bootstraps and actual project CMD/PowerShell/POSIX entries;
it is separate from required unit promotion and from production installation.

`copilot plugin update <name>` only refreshes the plugin's **marketplace
payload** — the cached source plus any skills/hooks/agents — under
`~/.copilot/installed-plugins/copilot-extensions/<name>`. It does **not** run the
plugin's runtime installer: the venv (`~/.<runtime>/.venv`), the `~/.local/bin`
binstubs, and any long-running service stay on the **old** version. Its
"updated successfully (vX → vY)" message refers to the payload only — the Copilot
CLI emits it and we cannot change it, so a runtime plugin can read "updated"
while its actual runtime has not moved.

Consequence — a rule for every plugin in this repo:

- A plugin that ships **only** skills, hooks, and/or agents needs no installer:
  `copilot plugin update` fully deploys it.
- A plugin that ships a **runtime** — anything beyond skills/hooks/agents: a
  venv, command, or long-running service — **must** ship:
  1. matching `scripts/install.{ps1,sh}` or `scripts/init.{ps1,sh}` entrypoints
     implementing this contract, and
  2. an **install skill** an agent can trigger to deploy/refresh that runtime
     **from the source folder** after a payload update. The skill's job is to run
     the plugin's manifest-selected canonical entrypoint with its documented
     reconcile action (for example, `install.* update` or `init.* --force`) from
     the source dir (the marketplace plugin dir, or a local checkout — see
     [Source = where the installer runs from](#source--where-the-installer-runs-from-no-flag)).
     Existing examples: `agent-worktrees:copilot-extensions-setup` (agent-worktrees +
     agent-bridge), `agent-codespaces:codespaces-setup` (agent-codespaces), `agent-containers:containers-fleet`
     (agent-containers).

  A Python runtime that exposes an agent-facing command additionally
  ships:
  3. `payload-invocation.json` plus its generated POSIX, PowerShell, and CMD
     payload-local command shims, and
  4. an attributable `sessionStart` hook for `emit-command-catalog`, plus
     `bootstrap-check` unless `payload-invocation.json` explicitly declares
     `"sessionStartBootstrap": false`. That exception is for repository-gated,
     explicit-first-use runtimes whose session start must remain non-mutating;
     their catalog and payload dispatcher must share the same fail-closed
     activation gate.

Every agent-facing command belongs to its implementing payload. Skills consume
the owning plugin's exact catalog `argv`; they do not hardcode another plugin's
path or discover a same-named command through `PATH`. The command glossary is
static invocation context, not a snapshot of live worktrees, sessions, leases,
or service health. See
[`patterns/runtime-agent-plugin.md`](patterns/runtime-agent-plugin.md).

So the full deploy of a runtime plugin is two steps — a payload refresh **then**
its runtime installer — but **you do not run them per plugin by hand.** The
unified **`<repo> update`** (`agent-worktrees update`) performs BOTH for **every**
registered plugin at once: it refreshes each payload (the `copilot plugin
update` step, invoked for you) and then runs each runtime's installer + cutover,
and fast-forwards the anchor checkouts. This contract exists precisely so
`update` can orchestrate that self-contained per-plugin flow uniformly; the
per-plugin install skills below are its internals (and a local-testing /
recovery path). Never hand-copy source into the deployed runtime dir — that
bypasses the venv sync, binstub/SAC handling, `_build_info.py` stamping,
manifest, and service restart (see "What NOT to Do" and "Deploying: one command
— `<repo> update`" in `CONTRIBUTING.md`).

> **Runtime reconcile is version-keyed; `--force` overrides it.** `update` runs a
> plugin's runtime installer only when its deployed runtime version differs from
> the freshly-refreshed payload version (an equal version is assumed current and
> skipped for speed). `--force` re-runs every enabled plugin's runtime installer
> regardless — the escape hatch for a same-version content drift (a dev checkout,
> or a marketplace artifact whose version stamp lagged the code). This covers
> **every** enabled runtime plugin, not just agent-worktrees and the
> `modules.json` services, so a runtime like agent-codespaces can no longer have
> its payload refreshed while its venv silently keeps serving stale code
> (dotfiles #1025). The per-PR **changefile-presence guard**
> (`check-changefile-presence.py`) makes the same-version-drift case rare in
> the first place, so `--force` stays a last resort.

The explicit updater collects the engine, selected modules and other required
registered runtime commands once after payload acquisition. Before launching
any runtime installer, its `runtime_preflight` checks the whole selected set's
installation ownership/root, machine gate, version/force decision, installer
and interpreter availability, and effective deadline. Execution uses those
collected arguments, environment snapshots and roots rather than repeating
discovery after the engine has already changed. Required preflight failures
name their targets and stop all runtime installation and local integration.
Intentional exclusions and already-current runtimes do not require unused
installers; existing module update/install fallback and cell-specific invocation
contracts remain intact.

This is a cheap configuration preflight, not dependency admission, a retained
prepared candidate, or a readiness/activation transaction. The installers still
own their combined construction and activation, live revalidation and recovery.
The pull-free, cache-aware session-launch reconciler remains a separate path.

Payload recovery attempts a target at most twice, with a 240-second attempt
budget and a 120-second cap per command. A retry uses the remaining budget,
re-inspects that target's installed payload and waits one second after an
explicit connection/reset, temporary DNS, gateway or Windows sharing/lock
failure. It does not revisit successfully acquired peers. A matching plugin's
explicit "not installed" ledger error may instead select the existing
activation-preserving install path; arbitrary failed updates no longer
authorize an install fallback. The engine's own acquisition stays update-only.

Authority, permissions, source-policy, integrity, certificate and unknown
failures stop without automatic retry. Numeric OS errors use the same
host-aware matchers as update outcome classification, including localized
permission refusals. An install wrapper's OS error is terminal because it may
be activation restoration failing, not a retryable spawn failure.
The canonical activation-state owner first retries restoration itself once
after one second for an explicit Windows sharing/lock error (32/33), including
a wrapped state-read error. It reuses the original activation snapshot, not the
post-install state, and never replays the command. Partial settings restoration
is retained while inventory restoration is retried. Permission errors, malformed
state and exhausted restoration still propagate as failures.
Windows payload updates and inventory installs run in a required, non-breakaway
Job through the existing process owner. On every exit, including an owner
deadline, that owner terminates the Job and positively queries zero active processes, then drains
and reaps the root within one thirty-second cleanup grace. That grace is
reserved inside the remaining acquisition budget. Only an update's confirmed
owner timeout may retry, within the same two-attempt limit; captured authority or
integrity refusals still stop. A CLI exit code 124 alone is not proof of an
owner deadline. Failed termination, accounting or output drain is an explicit
cleanup failure and cannot license replay. Native spawn errors retain their
codes and existing diagnostic/retry handling.
The strict child receives the auth session's exact command-scoped environment,
including an explicitly empty environment; it never substitutes global state
for that snapshot. Auth usage is retained for a launched child even when
cleanup fails, but a refused launch does not claim credential use. Post-launch
I/O errors are cleanup failures, not retryable spawn contention.

Windows inventory installs additionally retain their original activation
snapshot through canonical `plugin-activation` preservation, under one
operation-scoped shared lease for the resolved Copilot home. A durable
quiescence witness follows positive Job cleanup and root reap/drain before
restoration begins. Later invocations finish only witnessed restoration,
verify both activation postconditions and clear the record before capturing
another snapshot or launching a command. A partial restoration retains the
original snapshot, refreshed inventory and unrelated settings. A pre-witness
interruption stays blocked even if its parent PID is gone. Invalid or foreign
records and unverified cleanup never authorize another writer.
Every Windows acquisition enters this custody before auth refusal or
update/install selection and holds it through the target's attempts. A payload
already copied by the interrupted install therefore cannot bypass restoration
by selecting update. Original-state recovery itself needs no remote credentials.
Update-only custody does not capture a new install snapshot.
Snapshot and settlement-witness publication retry explicit Windows lock errors
32/33 once, against the same transition and original snapshot. The possible
witness-publication delay and final restoration backoff are reserved inside the
existing target budget; prior recovery time reduces the remaining command
allowance. This coordinates participating preservation calls, not unrelated CLI/user writers, and provides
process-interruption recovery rather than universal power-loss durability.

Install timeouts still stop: confirmed cleanup may permit restoration but
automatic install replay remains disabled. An install's watchdog exit 124
cannot retry even when its output also contains a transient marker; captured
timeout refusals retain precedence. POSIX timeouts also stop: the current
session owner intentionally permits detached daemons to survive and cannot attest
the entire payload writer tree. Existing runtime-installer daemon/breakaway behavior
is unchanged; the strict contract is opt-in for Windows payload acquisition.
Retried failures remain advisory attempt history; the final required outcome
decides the run. This is in-run recovery, not a persistent acquisition
checkpoint or permission to retry combined runtime activation.

From a source checkout on an interactive Windows desktop, run
`python tools/run-plugin-tests.py agent-worktrees --test-file tests/test_update_payload_windowless.py`
for the opt-in live launch regression. It observes two `pythonw.exe` parent
cycles with real console descendants, requires no new visible windows or
foreground transitions, forces owner deadlines and checks native child handles
are signalled. A missing observable desktop is reported as unavailable, not
headlessness proof. This host-dependent case is outside the fast guard lane.

### What the marketplace vendors (copied vs loaded)

`copilot plugin update` copies the **entire git-tracked plugin folder** — the
`source:` path in `.github/plugin/marketplace.json` — into
`~/.copilot/installed-plugins/copilot-extensions/<name>/`, **not** just the
skill/agent subfolders. Everything committed under the plugin dir travels:
`skills/`, `src/`, `scripts/`, `docs/`, `tests/`, `bin/`, `extensions/`,
`plugin.json`, `pyproject.toml`, `README.md`, … The **only** exclusions are the
gitignored build/cache artifacts (`.venv/`, `build/`, `uv.lock`,
`.pytest_cache/`, `.ruff_cache/`). (Verify on any machine: compare a plugin's
repo folder to its installed dir — a runtime plugin's `docs/` and `tests/` are
present in the install even though neither is needed to run it.)

Two consequences worth separating:

- **Copied ≠ loaded.** The whole tree lands on disk, but `plugin.json` governs
  what the CLI **loads into a session**: the declared `skills` paths, `hooks`,
  and any auto-discovered `extensions/`. A plugin's `docs/` (and `tests/`) ride
  along in the payload but are **reference material** — read by an agent or user
  who navigates to them (in a checkout or at the install path), not injected into
  session context. The runtime-operative content an agent actually loads is the
  plugin's `skills/`.
- **A plugin carries its own copy; version-bump by where the file lives.** Each
  payload is vendored **independently and self-contained** — a plugin must not
  reference another plugin's files or a repo-root path at runtime; put anything it
  needs inside its own folder. It follows that:
  - a file **inside a plugin folder** (including that plugin's `docs/`) **ships in
    its payload**, so changing it **requires that plugin's version bump** (the
    marketplace detects updates by version — an unbumped change is silently
    skipped);
  - a **repo-root `docs/`** file (this contract, `harness-runbook.md`,
    `architecture.md`, `plans/`) is **not** part of any plugin payload — it is
    fetched by URL or read in a checkout — so changing it needs **no** version
    bump.

### Automatic reconciliation at launch (`runtimeScope`)

`agent-worktrees` closes this gap automatically for **repo-adopted** plugins.
When a session launches in a repo whose `.github/copilot/settings.json`
`enabledPlugins` enables `<name>@copilot-extensions`, the launcher runs
`agent-worktrees reconcile-plugins`, which:

1. ensures each enabled plugin's **payload** is installed (and refreshes it on a
   throttled cadence), and
2. ensures its **runtime** matches the installed payload version — comparing the
   payload `plugin.json` `version` against the runtime
   `~/.<name>/deploy-manifest.json` `source.version`, and running the plugin's
   own `scripts/install.* update` (or `init.*`) only on drift.

For a plugin that vendors the installation-context library, reconciliation
locates and validates that plugin's active core-marketplace `install.json`
receipt before running its installer. The child process receives that exact
receipt through `COPILOT_EXTENSIONS_CONTEXT`; caller payload roots, Python
paths, and any foreign plugin context are removed. A missing or invalid receipt
blocks that plugin's mutation with an explicit diagnostic rather than falling
through to an unattributed legacy root.

Installation-mode probes and explicit-context validation in read-only launch
preflight invoke the owning payload's portable
`installation_context.py` with the running runtime's Python in isolated UTF-8
mode, with explicit UTF-8 output decoding, including on Windows. Windows still
requires its native helper to be present and uses it for older payloads without
the portable helper. Selected-context validation retains the existing
same-plugin payload-root and cross-plugin expected-cell-root constraints,
fresh receipt checks and fifteen-second deadline. Both helpers explicitly
produce UTF-8 JSON, so context validation must not decode a non-ASCII root
using the caller's locale. Probe failures
remain explicit failures, never reasons to fall back to another authority.
Pre-launch configuration omits foreign control-plane PR overlays, which do
not participate in bootstrap-service selection; ordinary PR operations retain
their full configuration.

A plugin declares whether — and where — its runtime should be reconciled via a
**`runtimeScope`** field in its `plugin.json`:

| `runtimeScope` | Meaning |
|----------------|---------|
| `none` | The reconciler never touches the runtime. Use for skills/agents/hooks-only plugins **and** plugin-contributed extensions whose payload *is* the runtime (e.g. `context-handoff`). A plugin that ships a runtime installer (`scripts/install.*` or `scripts/init.*`) must not declare `none` -- `tools/check-install-contract.py` rejects it, because neither the launch-time reconcile nor `update` would ever rebuild that runtime. |
| `universal` | Reconcile the runtime on **every** machine that enables the plugin (e.g. `agent-worktrees`, `agent-ssh`). |
| `machine-gated` | Reconcile the runtime only on machines in the plugin's allowed set (e.g. `agent-bridge`, `agent-codespaces`, `agent-containers`, `agent-mcp`). |

The unified updater resolves machine identity from layered local configuration,
then canonicalizes that identity against the enabling repository's registry.
It does not replace an explicitly configured execution space with hostname-only
inference; missing required configuration or failed detection blocks preflight.

The same scope governs the unified `update` flow: `update` rebuilds a
`machine-gated` runtime only where the machine gate described below permits it
(the same `runtime_allowed` decision), resolving the gate from the repository
that enables the plugin, and
reports any other machine as `SKIPPED (not allowed on this machine)` -- even
under `--force`, which re-deploys only runtimes this machine may run.

The install-contract guard exercises the real policy, gate, registered/module
planners and execution barrier with isolated configuration and installer
boundaries. It covers both platform plans, forced updates, enabling-repository
selection, missing manifests and detection failures without importing an
installed runtime or executing an installer. Source-mutation regressions in
the existing guard suite prove that bypassing these decisions fails the guard.

The reusable installer/readiness contract for `machine-gated` plugins is defined
by [`patterns/installer-readiness-modules.md`](patterns/installer-readiness-modules.md)
and implemented by `libs/installer-readiness/`. A participating plugin points
`plugin.json` `installerReadiness` at one bounded payload manifest. That manifest
either publishes installer/readiness modules or intentionally declines with a
reason; omission is an error. For eligible runtime-bearing `agent-*` plugins in
the `copilot-extensions` marketplace, discovery may join the module to a
validated installation-cell receipt. Every other plugin remains outside the
cell system. Discovery resolves only payload-local scripts or commands declared
by the owning `payload-invocation.json`--never an installed-cache convention or
`PATH`.

The base contract validates and plans but does not execute installers, interpret
machine-gate policy, or summarize a run. Plugin-specific declarations and the
out-of-plugin consumer are separate adoption slices. Until those declarations
land, the existing reconciler below remains the operative behavior.

The machine set for `machine-gated` plugins is **not** hard-coded in the plugin:
the reconciler reads it from a **control-harness gate manifest** — by default a
file named `external-repos.yaml` (`repos.*.services[].{name, deploy_machines}`),
resolved from the current repo first and then, if a gate anchor repo is
configured, from that repo via the repos registry. Both knobs are **pluggable**
via environment variables — `WORKTREE_GATE_MANIFEST` (the filename) and
`WORKTREE_GATE_ANCHOR` (the anchor repo name) — so any control harness can point
the gate at its own manifest; the defaults (`external-repos.yaml`, anchor
`private downstream reference repo`) match this repo's reference facility. With no gate info
available because no gate manifest exists, enabling the plugin permits its
local runtime. A present manifest remains authoritative: omitted plugins or
machines are skipped.

Deployment selectors may name the canonical physical machine shared by its
declared environments. Update and launch reconciliation resolve a registered
execution ID through the same `machine_transport` physical/environment map;
custom execution keys and physical names ending in platform-like suffixes are
not rewritten. Update and deferred launch planning select the target repository's
project configuration explicitly, without changing the resident's active project.
That configuration follows both execution detection and physical projection, so
a stateless harness retains its declared knowledge-overlay machine registry.
Machine-local registry sources use that target project's configuration root,
never the resident's unrelated or cleared active project.
Registry lookup prefers checkout-local data. If absent, a linked checkout may
use its configured owning anchor's registry/overlay only after Git confirms that
ownership; plugin settings and deployment gates still come from the launch path.
Exact execution-ID selectors remain compatible and select only
that execution address. Unknown names retain exact-match behavior, while
ambiguous or conflicting registry selections refuse. This deployment decision
does not change installation identity, native/guest locality or record ownership.
Reconciliation is local and version-keyed, so a re-launch with no version change
does ~no work; the network payload refresh is throttled via a small cache under
`~/.agent-worktrees/`. Opt out per session with `WORKTREE_NO_RECONCILE=1`.

> **Headless caveat.** This runs only on **interactive** launches.
> `copilot -p --autopilot` (an autopilot/headless harness) does not merge repo
> `enabledPlugins`, so harness machines still need required runtimes installed
> globally, out-of-band.

> **Windows caveat — prefer a local checkout.** When a plugin is loaded in the
> running Copilot session, `copilot plugin update <name>` can fail outright on
> Windows: the live CLI holds handles inside
> `~/.copilot/installed-plugins/copilot-extensions/<name>`, so the update's
> rmdir hits `EBUSY` and not even the payload refreshes. The reliable path is to
> run the plugin's `scripts/install.* update` from a **local checkout** of this
> repo (which flips `source.kind` to `local`); the install skill should drive
> that. A future wired-in install hook would have to tolerate this loaded-plugin
> lock (e.g. an out-of-process staged swap).

## The flow (all plugins)

```
uv venv  ~/.<runtime>/.venv
uv pip install [--reinstall-package <pkg>] "<plugin_dir>"   # NON-editable
            └─ resolves deps from pyproject.toml (pyyaml, ssh-manager, …)
stamp _build_info.py  →  INTO the installed site-packages copy (after install)
binstub  ~/.local/bin/<name>.ps1 (+ .cmd fallback)  →  signed venv python -m
write deploy-manifest.json  (schema_version 3, source block, atomic temp+move)
```

> **Immutable-versioned layout (dotfiles #581 — the default; enforced).** The
> `~/.<runtime>/.venv` above is really an **immutable, versioned** layout: each
> version is built into `~/.<runtime>/versions/<version>/`, and the active one is
> published by a `~/.<runtime>/current-version` **plain-text marker file**. The
> installer re-points its **version-pinned binstubs** (+ scheduled task / deploy
> manifest) straight at `versions/<version>/…`, so `update` builds a new version
> dir and **republishes the marker** instead of mutating a live venv (binding
> invariant *Runtime installs are immutable and versioned* — see
> [`patterns/README.md`](patterns/README.md)). **On Windows there is no junction at
> all** — a reparse point was blocked by RedirectionGuard (WinError 448) on managed
> devices, so the marker + pinned binstubs replace it; **on POSIX** the marker is
> equally authoritative — the runtime is resolved **directly from it** via the
> canonical resolver (`versioned_runtime.resolve_python` /
> `libs/versioned-runtime/resolve-runtime.sh`), with **no `venv`/`.venv` link in
> the resolution path** (the retired stable-link; uniform marker-only resolution,
> effort `uniform-runtime-resolution`). This is the
> **only** layout for every Python runtime plugin, on both OSes: the installers
> are **always versioned** and the `AGENT_<NAME>_VERSIONED` /
> `COPILOT_EXT_NO_VERSIONED` opt-out (and the legacy in-place-venv fork it
> selected) are **retired**.
> `tools/check-install-contract.py` (run in
> CI) **enforces** it: every runtime ships a `versioned_runtime.py` primitive
> **byte-identical to the canonical source**
> (`libs/versioned-runtime/versioned_runtime.py`, vendored in by
> `tools/sync-versioned-runtime.py`) and wires the `install-contract:v3
> versioned-venv` block. The venv is still `uv pip install`ed exactly as below;
> only *where* it lives and *how* it is activated change. The per-plugin installer
> points its venv/binstub/manifest at the slot the primitive returns.

The canonical Python API additionally supplies OS-backed
`construction_lease` and shared `retention_lease` lifetimes. Builders transfer
to candidate retention before releasing construction; completed reuse rechecks
its content under retention; candidate and rollback owners retain references
through cutover/drain or rollback settlement. Canonical cleanup takes
construction, exclusive retention, then selector-publication ownership and
rechecks selectors before deleting; activation shares the same protection.
The install-contract guard executes a small fresh-root lifetime/reclamation
probe as well as checking byte-identical fan-out. See
[`versioned-runtime`](../libs/versioned-runtime/README.md) for the exact API,
ordering, storage-failure behavior, and installer-adoption boundary. Shipping
this helper does not itself enroll an old shell installer's build lifetime.
Installation-cell provenance/receipt governance and its receipt-only
reservation release remain independently binding.

Read-only helper execution must not mutate the admitted payload. The runtime
primitive loads its canonical sidecar source directly without creating or
consuming bytecode caches; both native Index role probes similarly suppress
bytecode writes. A cold first invocation must preserve the same full payload
fingerprint through construction completion. Do not weaken the fingerprint
by ignoring newly generated files to make an unstable constructor pass.

Optional PowerShell construction cleanup probes the loaded `Function:` provider
with `Test-Path -LiteralPath`, not `Get-Command`. Command discovery can import an
autoloadable module even with `-ErrorAction SilentlyContinue`; a cold installer
must not trigger that side effect merely to decide whether cleanup is present.
An already-loaded cleanup function still runs, and outer `finally` blocks retain
their existing lock-release and caller-error behavior.

The attached Bash construction adapter owns `INT` and `TERM` while a candidate
is held. Both terminate through its `EXIT` settlement, which finishes candidate
cleanup before chaining the installer's previous exit handler and releasing
its install lock. A signal must not release that outer lock directly or return
to a builder that no longer owns it. This transport supports Bash 3.2, including
the stock macOS shell; the required scoped macOS lane exercises its real native
construction, interruption and prior-runtime preservation contracts.

### Hard rules

1. **No file-copy of the package** into `~/.<runtime>/lib`. Install via
   `uv pip install <plugin_dir>` (non-editable). Retire any legacy `lib/`.
2. **No `PYTHONPATH` to a `lib/` dir.** A binstub that points `PYTHONPATH` at a
   loose `…/lib` dir and runs `python -m <pkg>` is forbidden — the package must
   be `uv pip install`ed into the venv's site-packages (rule 1), not imported
   off a sidecar path. The binstub launches identically on both OSes — it
   resolves the active slot **directly from the `current-version` marker** via the
   canonical resolver (marker → `last-known-good` → newest complete slot;
   junction-free, **never** a `venv`/`.venv` link, **never** a PATH python), the
   one uniform method for every binstub, hook, service unit, and agent
   (`versioned_runtime.resolve_python` for Python callers,
   `libs/versioned-runtime/resolve-runtime.sh`/`.ps1` for shell):
   - **Linux/WSL:** `exec` the marker-resolved slot interpreter
     `…/versions/<v>/bin/python -m <pkg>` — a shebang interpreter, no Smart App
     Control concern.
   - **Windows:** launch `…\versions\<v>\Scripts\python.exe -m <pkg>` (resolved via
     the `current-version` marker — there is no `.venv` junction), **never** the
     generated `…\Scripts\<name>.exe` console-script trampoline. That trampoline
     is an unsigned, zero-reputation PE that Smart App Control blocks
     (CodeIntegrity 3077). See [SAC-safe launchers (Windows)](#sac-safe-launchers-windows).
     The binstub itself is a `.ps1` (primary) plus a `.cmd` (fallback) — see
     [Binstub format (Windows)](#binstub-format-windows).
3. **Deps come from `pyproject.toml`**, not ad-hoc `uv pip install pyyaml`.
   Sibling libs not on PyPI (e.g. `ssh-manager`) are `uv pip install`ed from
   their vendored dir **before** the plugin.
4. **`readme` in `pyproject.toml` must be a path inside the plugin dir**
   (`README.md`), never `../../README.md` — the latter breaks `uv pip install`
   in the marketplace-vendored layout.
5. **`_build_info.py` is stamped into the installed site-packages copy** after
   install. Resolve the dir with `PYTHONPATH` cleared so a stale `…/lib` can't
   shadow it; retire `lib/` **before** the probe.
6. **Create the venv before installing the package** (the install targets it).
7. **The installer process is never elevated, and never self-elevates as a
   whole.** An installer must run to a useful result as the ordinary user. It may
   *skip* steps that genuinely require admin (warning with remediation), but it
   must not gate the whole run behind, or silently escalate to, Administrator.
   Re-running the entire script under UAC (the legacy `Invoke-SelfElevated`
   pattern) is retired.
8. **Scheduled-task registration is user-mode, idempotent, and update-in-place;
   only a dedicated task-scheduling action may (opt-in) elevate that one step.**
   - Default is a **per-user auto-run** task (`AtLogon`, `LogonType Interactive`,
     `RunLevel Limited`) — no elevation. Flows that *require* elevation (e.g. an
     `AtStartup` "run whether logged on or not" task under SYSTEM/stored creds)
     are **opt-in only**.
   - Be **idempotent**: if the task already matches the desired shape, do nothing.
   - Prefer **`Set-ScheduledTask`** to update an existing task in place — it
     modifies a task the user already owns *without admin*, unlike
     `Register-ScheduledTask -Force` (which some locked-down machines refuse to
     non-admins even for a per-user task).
   - If a **missing** task cannot be created without admin, **do not elevate the
     installer** — warn with remediation and continue; any existing task keeps
     running.
   - The **only** place elevation may happen is a dedicated task-only action
     (agent-index: `install.ps1 register-tasks -AllowTaskElevation`) that
     self-elevates **only that step**, never install/update.
9. **The default service lifecycle is user-mode; scheduled tasks are an opt-in
   advanced tier and are never in the start/stop path.** Start/stop/keep-alive of
   the daemon must not depend on any component that can require elevation.
   - **Default (no elevation, no task):** the daemon runs as a plain user process,
     started and kept alive by a user-mode *ensure* — health-gate the live routing
     endpoint; if unhealthy, start it via the user-mode CLI (agent-index:
     `agent-index deploy` on Windows; `systemctl --user` / nohup on POSIX). The
     installer's explicit `install` / `update` / `start` actions funnel through
     this idempotent ensure. **Index sessionStart is metadata/binstub-only:**
     it never invokes install/ensure/provision, acquires packages, creates
     venvs, registers autostart or starts services/engines. A session stamp is
     not a daemon guarantee. Index's first admitted operational binstub use
     provisions through its runtime gate after repository opt-in and explicit
     role/setup intent; direct `<repo> update` remains an explicit deploy path.
     Legacy POSIX stamps also provide bounded migration of creator-owned
     group-writable stamp destinations. They pin exact no-follow identities,
     regenerate known marker/launcher bytes atomically, preserve the lock inode,
     and never trust writable deployed-version state or arbitrary marker bytes.
     Foreign owners, world-writable paths, links, multi-link files and unsafe
     unrelated ancestors still fail explicitly. This is metadata repair, not
     installer invocation or runtime provisioning. Namespaced cells retain
     strict admission without this legacy permission migration.
   - **Windows:** the default path must NOT call `Install-Service` /
     `Register-ScheduledTask` / `Start-ScheduledTask`. Those live only behind the
     opt-in `register-tasks` action (rule 8). POSIX `systemd --user` units are
     already user-mode and remain the POSIX default.
   - **Never** gate *starting* the service on a step that may require elevation
     (e.g. don't `if (Register-Task) { Start }` — a locked-down box that can't
     create the task would then never start the daemon). Registration and
     start are independent; start is always user-mode.
10. **Fast install + deferred self-provision — the entrypoint declares `stamp`
    and `provision` (dotfiles#1393).** The base install a `sessionStart` hook /
    `copilot plugin update` triggers must be **fast** and must never hold the
    singleton marketplace payload open long enough to wedge a concurrent update:
    - **`stamp`** — snapshot the payload SOURCE into the versioned slot area
      (Windows: `~/.<name>/snapshots/<ver>-<unique>/` + a `payload-dir`/
      `stamped-version` marker; POSIX records the same `payload-dir` pointer)
      and deploy the
      **self-provisioning binstub** — **no inline venv build**. Fits a hook grace
      window; frees the payload immediately (it copies from the already
      self-staged `$PluginDir`).
    - **`provision`** — the deferred heavy build (venv + `uv pip install` +
      versioned activate + manifest), run **from the slot-local snapshot** the
      binstub invokes on first use. Decoupled from a payload Copilot may have
      already replaced.
    - The **self-provisioning binstub** fast-paths the built slot python; if no
      slot is built yet (a `stamp` deferred it), it runs the snapshot's
      `<entry> provision` then dispatches. Opt out with `<NAME>_NO_SELFPROVISION=1`.
    - Enforced by `tools/check-install-contract.py` (`_declares_stamp_provision`);
      the ps1-lane exemption seam `BASELINE_NO_STAMP_PS1` tracks the not-yet-ported
      Thread-B service runtimes and must shrink as each lands. See the
      `correct-install-flows` effort.

### Contained-test persistent environment contract (Windows)

Redirecting `USERPROFILE` or `HOME` does not redirect .NET
`EnvironmentVariableTarget.User` or `EnvironmentVariableTarget.Machine`; those
targets still address the real Windows registry. Every PowerShell installer or
transport setup script that reads or writes persistent User/Machine environment
state must use the shared `install-contract:test-persistent-environment`
adapter. When `COPILOT_EXTENSIONS_TEST_CONTAINED=1`, the adapter maps those
operations to Process scope. A subprocess launched by pytest receives the same
mapping through pytest's inherited `PYTEST_CURRENT_TEST` marker, including when
the suite is run directly instead of through the repository runner. Outside
those test contexts, the adapter preserves the real User/Machine behavior
required by production installation.

`tools/check-install-contract.py` scans every plugin PowerShell script, rejects
multiline .NET calls whose target is not explicitly Process (including target
variables and `EnvironmentVariableTarget.User` / `.Machine`), and rejects direct
access to the persistent User or Machine environment registry paths outside the
byte-identical adapter. This automatically covers new installers and nested
transport scripts. **It also sweeps every other tracked `.ps1` in the repo**
(tests, helpers, shared libs — anything outside `plugins/`) for the same direct
violations, so a test/integration-harness script that has no reason to carry
the full adapter is still blocked from calling the real registry-backed
target directly — a live-hit failure mode: an untracked ad-hoc test
invocation once leaked dead entries into the operator's real, persistent
Windows User `PATH`. Only the `plugins/**/*.ps1` installers must additionally
carry the adapter itself; a non-installer script may instead pass an explicit
`Process` target. The contained test runner adds detection-only defense in
depth: it snapshots the real User and Machine registry environment keys and
fails the test flow if drift is observed. It never rolls back the whole key,
because doing so could overwrite a legitimate concurrent edit.

## Update-source authentication

`agent-worktrees update` reports authentication before acquiring its configured
GitHub marketplaces and HTTPS repo anchors. `update --auth-report` performs only
that read-only report; it does not update payloads, install runtimes, write
telemetry, or change saved credentials. `--json` returns the sanitized report.
Sources and account mappings come from effective user/repository settings and
the repository registry, not an updater-specific credential configuration.
User `settings.local.json` overrides `settings.json` before repository overrides;
an exact registered repository keeps its own account override, not a sibling's.
Repository-account matching retains the remote host and port across HTTPS, SSH
and SCP spellings; an enterprise source never inherits a public-host sibling's account.
Default network ports are canonicalized consistently across registry lookup,
token discovery, helper replay and API selection: `github.com:443` is public
GitHub, while a non-default enterprise port remains a distinct authority.
The report resolves ancestor or transported marketplace settings through the same
context selection as normal update, including its active-project fallback.
Bare and non-bare anchors on detached or non-default branches are excluded from
preflight. Branch selection is isolated from caller Git repository selectors
and shared with synchronization rather than inventing an auth requirement.

Public probes clear source-scoped HTTP headers as well as disabling helpers.
When a clean public probe succeeds, an effective-header diagnostic avoids
another network request unless configured headers exist. Working configured
headers remain untouched; a failing configured path can use an anonymous,
source-scoped header-clearing overlay, without acquiring credentials.
Other sources first exercise the
caller's configured credentials through a read-only router, then try an
environment token, the mapped `gh` account, and `git credential fill`. A candidate
uses case-insensitive host matching; enterprise environment tokens require the
matching explicit `GH_HOST`, and discovery and replay share token selection. It
must pass both its mapped-account check and a read-only Git contents probe;
repository metadata permission alone does not establish fetch permission. Each
subprocess is bounded, non-interactive, headless on Windows, and retains at
most 1 MiB per output stream. Windows requires a no-breakaway Job; POSIX probes
own their session and nested helper children remain inside that boundary.
Owned sessions are swept after success too, including redirected descendants;
inherited sessions are never swept by an inner helper.
Contained tests retain their test runner's ownership instead of detaching.
Reachability does not require an advertised `HEAD`: an unborn or dangling default
branch is not an authentication failure.

A working setup gets no environment overlay. Only failed sources acquire an
environment-only Git command-scope overlay, appended after inherited command
configuration so local helpers cannot override it. Overrides use counted Git
entries and, when inherited, appended `GIT_CONFIG_PARAMETERS`; the original
parameter bytes are retained for original-configuration replay. Routes are qualified by
host and repository: fixing or denying one repository must not change another
working repository under the same owner. Other requests delegate to Git under
the caller's original configuration at the child's actual working directory,
preserving native helper matching, ordering, resets, and includes. Credential
discovery and replay use the source URL's actual path without adding a `.git`
suffix. Tokens are never written into the overlay. Read-only probes suppress
credential approval and rejection; no path switches the active `gh` account.
Discovery negotiates native `authtype`/`credential` responses as well as password
credentials. Native identity validation preserves the helper's HTTP scheme,
uses an isolated, bounded stdlib probe with credential input over stdin rather
than argv, refuses redirects, and still checks the mapped principal and Git
contents access. The resulting fallback stores only the fill route, never the
credential; acquisition replays the original helper response.
The slot-interpreter helper runs in isolated Python mode so checkout-local
modules and inherited Python import paths cannot replace its code or standard library.
Marketplace contexts and individual anchors have separate reports and overlays,
even when they reference the same URL with different local credentials.

Unresolved required sources are denied without retrying a stalled helper and
contribute `auth_required` (exit 4, unless a worse failure applies). The shared
exit remains 4 for auth-only acquisition failures even without telemetry; another
required command failure still wins. The shared
Manager result retains that precedence with or without telemetry; a recorded
Manager authentication failure cannot hide an independent engine command failure.
The shared
status includes `auth.report` and `auth.fallback_used`; helper command values
are not included. Preflight reports fallback availability separately from use.
A source enters `fallback_used` only after its acquisition child receives the
overlay, including a child that later times out. Failed spawns, activation
preconditions, ordinary anchor inspection, and unattempted anchors do not count;
activation restoration failures after launch do not erase the usage evidence.
`CE_UPDATE_AUTH=report-only` applies no credential fix, so
fixable sources remain unresolved. `CE_UPDATE_AUTH=off` skips this preflight.
There is no persistent configuration cleanup or cleanup-consent flag.

### Recovery of an adopted standalone Manager

The normal updater repairs an unusable, already-adopted Worktree Manager before
profile integration. Admission requires one selected provider registration,
its Manager root and an exact known Manager-owned launcher. A healthy registered
command is reused without source acquisition; an unadopted host is not enrolled.
Recovery snapshots the selection, registration, launcher and source evidence,
then fetches only the Manager's own configured repository/ref, with its normal
authentication admission. Missing canonical recovery support at that ref blocks
the update; it never substitutes the updater's marketplace or a different branch.

The new Worktrees installer runs this check under its constructed interpreter
before settling publication, so an older running updater can hand off during
self-upgrade. The new update caller also checks before local integration when
the Worktrees runtime is already current. An old caller which skips installation
after concurrent publication cannot acquire new in-memory code: the next normal
invocation performs that check. No force flag or standalone bootstrap is needed.

The Manager adapter constructs through the canonical engine, retains candidate
and rollback custody, and runs completion/mux activation under the constructed
Manager interpreter. It does not import application dependencies into the
stdlib bootstrap interpreter or recursively run the full update flow. Adoption
is reverified before publication; failed activation restores both selectors
using canonical publication receipts.

The Worktree Manager consumes the same report through its existing engine JSON
process seam before its own Git fetch. Its engine environment preserves an
overlay when present while still discarding parent Python runtime variables.
Reused staging checkouts supply their own acquisition cwd to the report, so
local helpers and conditional includes are observed in the right checkout.
First clones use an empty staging directory with a discovery ceiling for both
preflight and acquisition, retaining global/command credentials without reading
the caller's repository-local configuration. Relative install roots and local Git
sources are anchored before changing cwd; URLs and SCP-style sources stay intact.
The machine report transports
explicit environment removals as well as additions. Local-only anchors without
a configured remote produce no HTTPS auth source.
The internal `--auth-source`/`--auth-environment` report options transport only
child environment differences over that pipe; never persist or publish that
environment. Explicit-source reports can run from an unadopted directory without
a selected project; ordinary updates still require their normal project context.
Repository-isolated engine Git calls continue to discard arbitrary
inherited Git selectors, including inherited updater snapshots, and receive only
the updater's explicitly scoped auth overlay. Anchor preflight uses that same
isolation. Every anchor operation is isolated even for local/SSH sources or when
auth preflight is disabled; isolation alone does not suppress the legacy account
policy. Auth-managed anchors suppress legacy injected headers even when
configured credentials passed, so acquisition cannot override the report's
credential authority.
The Manager's own updater remains independently usable: a missing/older engine
or a source outside this reporter's supported shape produces an explicit notice
and retains the existing Git authentication path, not a false auth refusal.
Known failed credentials reported by a capable engine still block that fetch.

## Update-flow robustness — self-stage, watchdog, completion markers (#935)

The runtime is (re)installed by **four** cooperating mechanisms, and the danger is
that they collide:

1. **`<repo> update`** updates every enabled plugin's payload (`copilot plugin
   update`) and re-runs each runtime installer.
2. **Worktree launch** runs `<repo> update` as a pre-flight.
3. **Copilot auto-update** refreshes the user/repo `enabledPlugins` payloads on its
   own cadence — it must *replace* `installed-plugins/<mkt>/<plugin>` on disk.
4. **Session-start hooks** (`bootstrap-check`) kick each plugin's installer to
   reconcile a drifted runtime.

(3)+(4) are meant to make (1)+(2) *unnecessary* day-to-day; running (1)/(2)
*guarantees* the payloads and runtimes don't drift apart. Two failure classes made
this fragile — **file locks** (in 3) and **stall-outs** (in 4). Three mechanisms fix
them; every Python runtime installer carries them (byte-identically where noted),
enforced by `tools/check-install-contract.py`.

### Self-stage — the installer never holds the singleton payload (fixes file locks)

An installer reads its own payload (`src/`, `libs/`, `pyproject.toml`) to build the
venv, so **while it runs it holds the singleton `installed-plugins/<mkt>/<plugin>`
dir open** (CWD/handles). On Windows a concurrent Copilot auto-update (3) then fails
to replace that dir with **os error 32** ("used by another process") — the payload
freezes at the old version and reconcile keeps reverting the runtime toward it (the
dev214↔dev230 drift saga).

Fix — the **`install-contract:v4 self-stage`** prologue (byte-identical, at each
installer entry): when running from the marketplace payload, copy the **whole**
payload into a **unique per-invocation** dir `~/.<name>/.install-stage/<ts>-<pid>/`
and **re-exec from there**. The singleton payload is then touched only for the fast
copy, never the whole (possibly-wedged) install. PowerShell copies ordinary files
through the native .NET file API rather than paying provider overhead per file.
The complete tree is retained, including hidden entries, empty directories and
bytecode; reparse points retain `Copy-Item` semantics. Copy errors still reach
the existing staging failure path. PowerShell 5.1 and 7 use the same helper.
The byte-identical `payload-copy` block follows required output initialization
and precedes contextual staging. Worktrees also uses it for its structured
payload copy, without changing context selection, canonical-library handling,
staging paths or watchdog ownership.
Guards:
- `COPILOT_PLUGIN_INSTALL_STAGED=1` prevents a re-exec loop; the stage path (not under
  `installed-plugins`) is authoritative. An inherited staging flag from another
  installer cannot suppress staging when the actual script still resides in the
  installed payload. Both staging and leaving the payload must succeed; failure
  aborts instead of falling back to an in-place build.
- Maintenance/reconciliation launchers start their children from HOME. Windows
  launchers set `WorkingDirectory` explicitly; PowerShell provider location and
  native process CWD are distinct. The staged installer also runs from HOME,
  resolving all build inputs by absolute runtime-owned staged paths. Ordinary
  task/workspace commands retain their caller's project context.
  Standalone shell bootstrap processes relocate before directly backgrounding
  `nohup`, rather than putting an unprotected subshell in front of it; the
  invoking agent's separate process retains its workspace.
- Structured worktrees installs retain their context-owned staging path even
  with inherited flags; they never fall through into legacy staging. Read-only
  index status and dependency-light cell-slot actions remain exempt from legacy
  staging and its filesystem writes. Index cell coordinators and their context
  helper children keep HOME as CWD even while waiting on installation locks.
- POSIX watchdogs re-exec as a command rather than retaining the original payload
  script as their input. On Windows, MSYS/Cygwin emulated exec additionally keeps
  the original launch process and its native CWD alive: launch those scripts from
  HOME, or use the native PowerShell entrypoint. A shell installer started inside
  an installed payload on those hosts fails explicitly instead of pinning it.
- `COPILOT_PLUGIN_STAGED_FROM=<real payload path>` preserves marketplace detection
  (see [Source](#source--where-the-installer-runs-from-no-flag)).
- **Reap is pid-guarded:** a sibling stage dir is removed only if its owner pid (the
  `<ts>-<pid>` suffix) is **dead** — a concurrent or stalled installer's dir is never
  touched. *A stalled install must never block another copy.*

`tools/test_payload_cwd_contract.py` exercises first-touch staging with absent and
inherited flags, failed relocation, and directory rename/replacement while the
installer/watchdog is still alive. Generated Windows command shims check both
logical and native CWD before redirecting off a payload, including when a caller
has already used `Set-Location` without releasing its native directory handle.
When that logical location is already a safe workspace, only the native CWD is
updated; the caller's workspace is preserved rather than replaced by HOME.
Only FileSystem-provider locations qualify as native directory candidates;
registry or other provider containers use the validated project/HOME fallback.
Relevant dev PRs run the same focused Linux/Windows smoke inside CI's existing
native runner jobs, so their outcomes feed the required PR aggregate without
another workflow or check. Changed-path reads use the real PR head and fail on
Git errors. The path-gated `Payload CWD contract` workflow retains non-dev PR
coverage; its scheduled/manual lane runs the full inherited-flag matrix.

### Watchdog — a stalled install self-terminates (fixes stall-outs)

The session-start hook (4) launches the installer **detached with no deadline**;
before this, a wedged `uv pip install` (no network timeout) leaked forever — orphans
piled up one-per-session and (pre-self-stage) locked the payload.

Fix — the **self-stage parent doubles as a watchdog**: already outside the payload and
wrapping the child's whole lifetime, it enforces a deadline and, on expiry, kills the
**whole tree** via `taskkill /T` (Windows' subprocess-kill leaves grandchildren),
logs `WATCHDOG-KILL` to `~/.<name>/reconcile.err.log`, and exits `124`. Deadline:
`<NAME>_INSTALL_DEADLINE_SEC` → `COPILOT_PLUGIN_INSTALL_DEADLINE_SEC` → **480s**
default; `<=0` disables. Secondary: `UV_HTTP_TIMEOUT` bounds each uv request so a hung
download degrades to "failed + retryable" rather than wedging. Backstop:
`bootstrap-check`'s single-flight + stale-reap.

Unified `agent-worktrees update` gives module and registered-runtime installers
the selected positive watchdog deadline plus 30 seconds for cleanup and failure
publication, instead of preempting the default watchdog at 300 seconds. A disabled
watchdog (`<=0`) still leaves a finite 510-second updater safety bound. The
worktrees self-installer retains its existing 600-second minimum, extended when
a positive watchdog override plus cleanup grace exceeds it. Invalid deadline
values fail explicitly before launching the installer.

The shared command-lifecycle runner passes the caller's stdout and stderr
explicitly to its native installer child, including the strict Windows Job
containment path. On Windows, a hidden child with only implicit OS stream
inheritance can lose both streams when the caller was launched through a
PowerShell native-command pipeline. Explicit handles preserve installer and
self-staged grandchild diagnostics without capturing them in memory, changing
their destinations, or changing admission, containment, or exit-code behavior.

Plugins with a watchdog default other than 480 seconds declare that positive
integer as `installerDeadlineSeconds` in `plugin.json`; unified update reads it
only when neither deadline environment override is set. The declaration must
match the plugin's own cross-platform installer defaults. Agent-dispatch declares
1050 seconds for its longer activation/cutover lifecycle, so its default caller
budget is 1080 seconds; an explicit disabled-watchdog override still uses the
finite 510-second fallback. Invalid declarations fail before installer launch.

The **agent-bridge Windows hook** serializes reconcile admission and status
publication with an installation-root-scoped global mutex, including across
Windows login sessions. Each launch has an
`attempt_id`; after the launch record is published, `launched_pid` identifies
the actual PowerShell installer supervisor, with `worker_started_at` guarding
against PID reuse and `wrapper_pid` retaining the headless console wrapper for
diagnostics (`wrapper_started_at` protects the initial pre-worker record).
Legacy live PIDs without birth evidence are not automatically force-reaped.
Stale reaping terminates that supervisor's whole tree, not only
the console wrapper. The target's native handle is pinned and its birth identity
revalidated immediately before signaling. Completion updates only its own
attempt, so a late worker
cannot overwrite a replacement's status. Invalid prior ownership metadata
defers reconciliation with a diagnostic instead of risking another installer.
The supervisor invokes the installer in a separate PowerShell `-File` process
and records that process's exit code, not a possibly stale native-command code
from inside a successfully completed installer script. Its file-backed stdout
(`log`) and stderr (`stderr_log`) capture avoids waiting for EOF on pipes that
an installer descendant may retain after the installer exits. Watchdog diagnostics
continue using the separate `reconcile.err.log`.
The launcher starts outside the singleton payload and clears inherited
`COPILOT_PLUGIN_INSTALL_STAGED` / `COPILOT_PLUGIN_STAGED_FROM` in the worker;
staging and its watchdog belong to this installer invocation, not to an
unrelated parent installer.

### Agent Machines Windows first-use diagnostics

The [Agent Machines Windows dispatcher](../plugins/agent-machines/scripts/invoke-payload-runtime.ps1)
implements the [self-provisioning runtime intent](../visions/plugin-services/README.md#self-provisioning-runtime)
for an unresolved runtime. It invokes the owning installer's legacy
`stamp`/snapshot `provision` or active-cell `cell-provision` path. Commands with
an already-resolved runtime bypass this installer-output path.

The dispatcher launches the current PowerShell executable with text-format
output and separate file-backed stdout/stderr capture. This keeps nested
installer output outside PowerShell's native CLIXML/error-record parser. Both
streams are forwarded to **stderr**, leaving command stdout available for the
runtime's result. Complete CR/LF-terminated records are forwarded while the
installer runs; final unterminated text is emitted after exit and draining.
There is no line-count truncation. Order is preserved within each stream;
chronological ordering across the two streams is not guaranteed.

Incremental byte reads use a persistent decoder per stream. The inherited
console encoding is the default; a BOM selects UTF-8, UTF-16, or UTF-32 with its
byte order. Partial BOMs and multibyte characters survive a growing file's
temporary EOF. Decoders flush only after installer exit and complete draining.
Read-buffer size controls transfer granularity, not output retention. Capture
files are deleted during cleanup and their on-disk size is not capped; they
are temporary transport rather than an archival log.

The dispatcher retains the process handle for PowerShell 5.1 exit-code reporting
and waits on the direct installer, rather than using a whole-descendant-tree
wait. The installer's existing self-stage/watchdog owns staged-child deadlines
and exit `124`. If capture fails while the owned installer is still running,
the dispatcher attempts PID-scoped tree termination. Resource disposal and
capture-file deletion are attempted independently; secondary cleanup failures
are reported without replacing the installer result or original exception.

The [dispatcher regressions](../plugins/agent-machines/tests/test_payload_provisioning_powershell.py)
cover live/full output, argument forwarding, encoding, retry, and warm-runtime
behavior. The [lifecycle regressions](../plugins/agent-machines/tests/test_payload_installer_lifecycle_powershell.py)
cover cleanup-failure outcomes, split decoding, direct-child waiting, and real
installer self-staging/watchdog behavior through its offline smoke seam.
These suites do not establish a full network/package installation or every
cancellation and descendant-termination race.

### Completion marker — no corpse reuse, clean retry

Completion was inferred only from the runtime-root `deploy-manifest.json` /
`running-version.json` + the active-version marker. A killed/crashed build left a
**half-built `versions/<v>` slot** on disk that the next `uv venv --allow-existing`
could silently reuse.

Fix — a per-slot **`.install-complete.json`** marker, written **atomically right after
the slot passes its isolated health gate** (so "marker present" == "healthy, complete
build"), owned by the shared `versioned_runtime.py`:
- `mark-complete <v>` publishes numbered evidence once; exact content reuse
  preserves its bytes/identity/timestamp/PID, while conflicting or malformed
  evidence refuses. Optional `--payload-hash` binds completion to attributable
  content, never permission to rebuild a numbered slot with changed content.
  Only an explicitly claimed `dev` slot permits completion replacement.
- `is-complete <v>` (+ optional `--expect-hash`) checks completion without
  mutation. Actual marker storage failures propagate rather than looking absent.
- `slot <v> --clean-incomplete` tosses an **incomplete** slot before building (never
  the current/active slot); `toss-incomplete` / `gc --toss-incomplete` reap markerless
  non-current slots.

Numbered publication uses an atomic hardlink from a unique, flushed temporary
file under shared retention and selector-publication ownership. It never
replaces an existing numbered marker or falls back to overwrite when hardlinks
are unavailable. See the [canonical API contract](../libs/versioned-runtime/README.md#create-once-completion-publication).
This protects completion evidence, not package writes by legacy installers:
admission before construction and candidate retention through promotion/drain
remain separate installer-adoption requirements.

Because **activate (marker publish + binstub re-point) runs only after the health
gate + marker**, a
watchdog-killed build never becomes the live version — the old daemon keeps serving,
and the markerless corpse is tossed + rebuilt on the next run (automatic retry).

A refused or failed `mark-complete` (conflicting evidence, a storage failure, or no
bootstrap interpreter to run it) **stops activation**: the installer reports why
and exits non-zero instead of activating a slot whose completion was never
published. `tools/check-install-contract.py` rejects a completion-marker helper
that does not fail when its writer fails, and any call site that ignores the
helper's result. A call is accepted only as a checked statement
(`helper || exit 1`) or inside an `if` whose failure branch fails on every
path, in that script's language (shell: a literal non-zero `exit`/`return`
or the writer's own captured status; PowerShell: `return $false`, `throw` or
a non-zero `exit`, since `return 1` is truthy there); any other form,
such as assigning the result, is rejected as unrecognized.
`tools/test_check_install_contract.py` also runs every shipped shell helper
against a stub writer.

> **Test it:** `tools/test-install-flow.ps1 -Plugin <name>` (Windows) and
> `bash tools/test-install-flow.sh --plugin <name>` (Linux/WSL) are the turn-key
> mini end-to-ends that assert all of the above in an isolated sandbox (STAGED,
> NOT-IN-PAYLOAD, PAYLOAD-FREE-during-install, MARKETPLACE-preserved, NO-COLLISION,
> WATCHDOG whole-tree kill, MARKER/TOSS, NO-ORPHANS, BOUNDED) — via a
> `COPILOT_PLUGIN_INSTALL_SMOKE` seam, without a heavy venv build.

> **`worktree-manager` is a standalone canonical Python-engine adopter.**
> It remains outside the marketplace/plugin installation-cell contract, but
> ships byte-identical engine, seasoning and versioned-runtime helpers in its
> own `scripts/` payload. Its adapter uses governed platform-runtime package
> acquisition and isolated installed-origin health before canonical completion,
> selector publication and mux cutover. Runtime slots are venvs; the materialized
> source snapshot lives under `payload/`. Legacy payload-only selected slots
> require a distinct constructed repair generation, never an in-place rebuild.
> See its [README](../worktree-manager/README.md).

### Install-transaction lock — mutating runs never interleave, never run unlocked

Several callers can start the same plugin's installer at once: the launch-time
reconcile, `update`, a session-start bootstrap, and a binstub's first-use
`provision`. An adopting installer (`agent-pull-requests`, `agent-logger`,
`agent-vault`, `agent-mcp`, `agent-containers`, `agent-ssh`) therefore holds one exclusive
lock on `<install-dir>/.install.lock` for every mutating action
(`stamp`/`install`/`update`/`provision`, plus `uninstall` where provided) across its whole
build + publish transaction (Windows: a `FileShare.None` handle; POSIX: `flock` on
fd 8 (fd 7 for `agent-ssh`), or a PID-bearing fallback entry
`<install-dir>/.install.lock.pid` where `flock` is unavailable). In the matching
agent-vault, agent-mcp, agent-containers and agent-ssh fallback blocks, every
acquisition and stale-owner cleanup holds the short, PID-bearing
`.install.lock.pid.reap` admission mutex. The owner is read and checked while
that mutex is held, and a confirmed stale link is removed and replaced before
admission is released. An empty or malformed owner read never authorizes
deletion; an absent link is retried as acquisition, not erased by path.
This prevents a delayed reaper from removing a newly admitted live owner.
A `.reap` link left by a process that died during admission is deliberately
never reclaimed automatically, even when the main PID link is absent: runs
fail closed at their deadline until the stale mutex is explicitly repaired.
The four matching fallback bodies retain one identical protocol. Creation uses
the POSIX `link` utility with a privately staged regular receipt, never bare
`ln -s` at either shared lock pathname. `link` calls `link(2)` for the exact
destination: an existing directory or directory symlink fails rather than
receiving a child link. This remains atomic when the destination appears
immediately before creation; a check-then-`ln` sequence is not sufficient.
The receipt is one newline-terminated ASCII line of at most 64 bytes, including
the newline: `copilot-extensions.install-lock-pid:v1:<pid>`. Owner reads capture
a private hard-link snapshot and reject non-regular nodes before opening them,
including a raced FIFO. A single bounded, NUL-delimited Bash read accepts only
EOF within that byte bound, rejecting NUL anywhere (including after the newline)
before Bash can discard it. Missing or extra newlines and malformed records
never authorize admission, stale deletion or owner-checked EXIT cleanup. Older
dangling numeric PID symlinks remain readable; symlinks resolving to filesystem
nodes are refused, not erased as stale PID evidence. Legacy `readlink` output is
captured in a private file, never normalized by command substitution before
validation. It must contain one digit-only target of at most 63 bytes followed
by exactly the utility's one newline terminator. Embedded or trailing newlines,
carriage returns, NUL, oversized output, missing terminators and utility errors
are refused without deleting the original link or authorizing EXIT cleanup.

The fallback requires Bash, `mktemp` and the standard `link` utility and
same-filesystem hard-link support; missing utilities fail closed before
admission. It adds no runtime library/interpreter or GNU-only `ln -T` option.
The exact-path utility contract is defined by
[POSIX link(1)](https://pubs.opengroup.org/onlinepubs/9799919799/utilities/link.html)
and is also documented by the
[macOS ln/link manual](https://keith.github.io/xcode-man-pages/ln.1.html) and
[FreeBSD ln/link manual](https://man.freebsd.org/cgi/man.cgi?query=link&sektion=1&format=html).
Regular receipts avoid the platform-dependent handling of a symbolic-link
source by `link(2)`. Native macOS/BSD execution remains a separate validation
lane; the Linux tests do not claim to have exercised those kernels.
As with the admission-race correction, incompatible pre-repair installer
processes must not be run concurrently with a repaired transaction; this
source repair neither quiesces nor deploys host runtimes.
The transaction lock is
acquired **after** the self-stage
re-exec (the staging parent never holds it) and released in `finally` / an `EXIT`
trap. The OS releases kernel lock handles on process death; fallback entries
retain the dead-owner recovery and dead-admission explicit-repair rules above.

The lock is **fail-closed**: a run that cannot acquire it within its timeout
(300s; overridable for `agent-vault`, `agent-mcp`, `agent-containers`, and `agent-ssh` via
`COPILOT_PLUGIN_INSTALL_LOCK_TIMEOUT_SEC`) reports that it could not acquire the
install transaction lock and exits non-zero — it never proceeds unlocked.
Read-only actions (`status`) and service controls do not take it.
`agent-vault`, `agent-ssh`, and `agent-pull-requests` also serialize uninstall
against the same install/update lock. Vault and SSH uninstall previews do not
acquire or create the transaction lock or remove runtime/config contents.
The normal transient payload self-staging protocol still applies.
`agent-logger` already locks uninstall and keeps its state directory.

For those three uninstallers, removing runtime/config contents must **not**
remove the lock inode or in-flight staged sources. The canonical
`Remove-InstallRuntimeContent` / `remove_install_runtime_content` helper keeps
`.install.lock`, `.install.lock.pid`, `.install.lock.pid.reap`, and
`.install-stage` in place; the install root may therefore remain after purge.
Dead-owner staging reap remains with the self-staging protocol on subsequent
invocations, not with purge. Moving the lock to a new
path would not exclude an older installer holding the original lock, so purge
preserves the existing identity instead. Removal errors stop that purge rather
than reporting success. Nested links are unlinked, never traversed; filesystem
roots, user homes, and linked install roots are refused. This changes no
uninstaller's config-retention default and introduces no uninstall action for
init-only plugins.

`agent-bridge` holds the same fail-closed lock for `install` and `update` (POSIX:
`flock` on fd 8; without a `flock` binary, the same `flock(2)` lock taken through
Python, so it still excludes PowerShell and `flock` holders of `.install.lock`;
the pid-symlink fallback only when neither is available, which excludes only other
runs that also lack `flock(2)`; default wait 150s). It
distinguishes the two non-success outcomes: another install holding the lock for
the whole wait exits **75** (reported by `update` as `blocked_by_lock`, never as
success), and a lock fault exits 1.
It is distinct from the binstub's `.provision.lock`, which only single-flights
first-use provisioning around the installer call in unadopted wrappers.
The canonical version-1 payload shim and Bridge/Dispatch retained command gates
serialize stale PID-link revalidation with `.provision.reap.mutex` when `flock`
is unavailable. They install cleanup before acquisition, release only a mutex
they acquired, relinquishing custody before directory removal so a signal cannot
remove a peer's replacement. They exit 130/143 on INT/TERM rather than continuing provisioning.
An unowned mutex is never removed automatically: after 30 one-second retries,
the caller exits 126 with an explicit repair diagnostic. This also bounds refusal
after an untrappable interruption or death between `mkdir` and ownership recording.
Waiting for a live provisioning PID remains separate and is not subject to that
reaper-mutex retry budget.
Vault's cold primary/payload commands and legacy session bootstrap instead submit to the packaged
installation driver's canonical queue before taking any outer provisioning
lock. They retain the inner canonical installer lock, publication admission,
source identity and default service recipe. Healthy invocation bypasses driver
work; missing support and unsupported cold namespaced/platform paths refuse
without a legacy foreground fallback. Vault's hook selects only canonical
stamp or provision, retains its healthy/non-deployment no-ops and emits only
`{}` plus a diagnostic launch acknowledgement, not a completion receipt.
It neither holds nor reaps the old PID-based reconciliation guard.
Other bootstrap hooks and explicit installer entry points remain separate,
unadopted callers. The
[driver contract](../libs/update-driver/README.md#fixed-first-use-handoff-and-vault-command-adoption)
owns the native waiter, receipt/result and qualification boundaries.

### Update-in-progress marker — installer self-reports a live transition (#5066)

A **separate** concern from the completion marker above: that one records
whether a *build* finished; this one records whether the *live-service*
update/start lifecycle (drain → stop → stage → start, or a zero-downtime
cutover) is **currently in flight** — seconds to a couple of minutes, during
which the daemon can legitimately be briefly down or mid-handoff. Before
this, nothing locally visible could distinguish "correctly mid-transition"
from "actually dead and never came back" (an interrupted agent-bridge cutover
left its daemon unavailable for several days), so a local liveness watchdog had to rely on a
caller-side wrapper around every manual update — exactly the kind of
fragile convention this marker eliminates, including for *automatic*
self-update cutovers that nothing ever wraps.

Each live-service installer (`agent-bridge`, `agent-dispatch`) writes its
own well-known, documented marker at `$INSTALL_DIR/update-in-progress`: a
file containing a single epoch-seconds expiry (20 minutes past write time
by default — generous past any observed real cutover), atomically published
(temp-file + rename, never a truncate-then-write race for a reader) at the
start of `do_update`'s/`do_start`'s live-service branch, **after** every
"nothing to do" early return (an already-healthy daemon, a forwarded host
route, a lock-contention defer) so the marker is never set when nothing is
actually happening. Any downstream consumer (a local liveness watchdog, a
diagnostic tool) can check
`[[ -f "$INSTALL_DIR/update-in-progress" ]] && (( $(cat ...) > $(date +%s) ))`
without needing any plugin-specific caller-side cooperation.

**Reference-counted, not single-owner.** A long-running `do_update`/
`Invoke-Update` and a short `do_start`/`Invoke-Start` can legitimately
overlap (neither serializes against the other beyond the install lock each
already takes independently). A naive "last writer wins" marker breaks
here: the short call's exit-time cleanup would delete the marker while the
long call is still genuinely mid-transition. Both languages instead guard
a small on-disk counter with a dedicated lock — **`flock` on a separate fd
(never the main install-lock fd)** for `.sh`, a named
**`System.Threading.Mutex`** for `.ps1` (the Windows cross-process
primitive; `flock` has no Windows equivalent) — so the first caller to
enter (count 0→1) writes the marker with a fresh expiry, later concurrent
callers just increment, and only the caller that brings the count back to
0 actually removes the marker. A same-process nested call (`do_update`
calling `do_start` internally, where that pattern exists) is tracked via a
process-local "already held" flag so it reuses the outer holder's slot
rather than double-incrementing.

**Scope: covers the installer's own lifecycle only.** This marker is
written by the `do_update`/`do_start` (`Invoke-Update`/`Invoke-Start`)
code paths in these install scripts. It does **not** cover
`agent-dispatch`'s coordinator-driven self-deploy/successor-respawn paths
(the coordinator process redeploying or replacing itself without going
through the installer's CLI entry point) — a consumer watching this marker
sees no transition during those, and must not assume it has full coverage
of every way the daemon can restart.

Cleared on **every** exit path uniformly — success, a cutover-then-fallback,
a failed update's rollback, or an unhandled error — via a single cleanup
hook per language rather than scattered manual clears at each return site:

- **`.sh`:** a **global `trap _clear_update_marker EXIT`**, set immediately
  after the first `_write_update_marker` call — never a function-local
  `RETURN` trap, since under `set -euo pipefail` an unexpected command
  failure terminates the whole script rather than returning normally, and
  only an `EXIT` trap is guaranteed to still fire in that case.
- **`.ps1`:** `Write-UpdateMarker` followed by `try { ... } finally {
  Clear-UpdateMarker }` wrapping the remaining live-service body —
  PowerShell's `trap` statement only fires for terminating *errors*, never
  a clean `return`, so `finally` is the correct primitive here (the same
  one `agent-bridge/scripts/install.ps1`'s `Invoke-Update` already relies on
  for releasing its install lock on every exit path, `exit` included).

### POSIX parity (`.sh`)

The `.sh` installers carry the **same** `install-contract:v4` blocks as `.ps1`,
byte-identical per language and enforced by `tools/check-install-contract.py`
(self-stage prologue, smoke seam, and the `_source_kind` env-fallback that honors
`COPILOT_PLUGIN_STAGED_FROM`). Two POSIX-specific choices mirror the Windows
behavior:

- **Watchdog kill uses the process group, not `taskkill /T`.** The staging parent
  launches the staged child under bash **job control** (`set -m`), giving it its
  own process group; on deadline it kills the **whole group** with
  `kill -- -<pgid>` (the POSIX twin of `taskkill /T`), so grandchildren die too.
- **Exit-code propagation via `wait`, not `setsid -w`.** Job control's `wait`
  returns the staged child's real exit code, so a genuinely failed install
  surfaces non-zero. `setsid -w` is deliberately avoided — on some util-linux
  builds it swallows the child's exit code (returns 0), which would mask a
  failed install.

The completion-marker primitive (`versioned_runtime.py`) is already
cross-platform, and **all 11 `.sh` installers are fully wired** (self-stage +
watchdog + smoke + `_source_kind` + toss-before-build + mark-after-health-gate).
The marker/toss body-wiring is per-plugin, not mechanical: the link-name is
derived as `basename "$LINK_DIR"` (`venv` vs `.venv`), and mark-complete is
placed relative to each plugin's health gate — on its own line before
`_versioned_activate` for the external-gate (daemon) plugins, or inside
`_versioned_activate` (after the gate) for the CLI plugins that fold the gate in.
`agent-bridge/scripts/install.sh` is the reference.

## SAC-safe launchers (Windows)

Smart App Control (SAC), enforcing on Windows 11, hard-blocks two unsigned,
zero-reputation binaries that a default `uv` install produces:

1. the uv-managed venv `python.exe`, and
2. the per-entry-point console-script trampoline `…\Scripts\<name>.exe`.

Both fail with `CodeIntegrity` event **3077** ("did not meet the Enterprise
signing level requirements"). Because these plugins ship publicly on GitHub, the
fix must **not** require downloaders to disable SAC.

### Rules (Windows `install.ps1` / `init.ps1`)

1. **Build the venv from a PSF-signed base Python via `--copies`.** Resolve a
   signed interpreter (`py -3.x` whose `Get-AuthenticodeSignature` reports
   `Valid`) and run `& $signedBase -m venv --copies $VenvDir`. `--copies`
   embeds a real copy of the signed `python.exe` in the venv (Authenticode
   survives the copy), which SAC trusts. Rebuild an existing **unsigned** venv
   the same way. Fall back to `uv venv` (unsigned) only when no signed Python
   exists, with a loud warning — those hosts stay SAC-blocked until a signed
   Python (python.org / Store) is installed.
2. **Launch via the signed venv python, never the trampoline.** Every launch
   path — the `~/.local/bin/<name>.cmd` binstub, the service start script, the
   scheduled-task action, and any in-installer `version` / status probe — must
   invoke `"<venv>\Scripts\python.exe" -m <package>`. Never invoke
   `…\Scripts\<name>.exe`.
3. **The legacy `<name>.exe` may still be *matched* for migration** (e.g. a
   `Get-RunningProcess` PID/path lookup that also recognizes the old trampoline
   process), but it must never be *launched*.
4. **Reputable unsigned wheel `.pyd`s** (pydantic_core, etc.) pass SAC via ISG
   reputation — only the locally generated, zero-reputation trampoline and the
   uv-managed python are blocked, so dependencies need no signing.
5. **Strip the trampolines after install.** `uv pip install` regenerates the
   `…\Scripts\<name>.exe` console scripts every time, so each installer removes
   them (every `agent-*.exe`, incl. sibling provider trampolines pulled into a
   shared venv) right after the package install via the shared
   `Remove-ConsoleTrampolines` helper (`# install-contract:v3 strip-trampolines`
   block, byte-identical across plugins). Nothing launches them — binstubs,
   services, and probes all use `python.exe -m <pkg>` — so removal is safe and
   keeps the venv free of SAC-blocked PEs. POSIX console scripts are the
   sanctioned launch path and are **not** stripped.

Reference implementation: `Get-SignedBasePython` + `New-SignedVenv` and the
`"$VenvPython" -m <pkg>` launchers in
`plugins/agent-bridge/scripts/install.ps1` (mirrored in `agent-worktrees`,
`agent-codespaces`, and — in their `init.ps1` — `agent-containers` and
`agent-mcp`). `tools/check-install-contract.py`
flags any `install.ps1` that launches the `…\Scripts\<name>.exe` trampoline.

> **Enforcement scope:** `check-install-contract.py` enforces each plugin's
> *canonical* runtime entrypoint — `install.ps1`/`install.sh` when present,
> otherwise `init.ps1`/`init.sh`. `agent-containers` and `agent-mcp` ship only
> `init.*`, so they are checked there; plugins with both (`agent-codespaces`,
> `agent-worktrees`) have `init.*` delegate to `install.*`, so only `install.*`
> is enforced. The SAC trampoline rule applies to whichever `.ps1` is the
> canonical entrypoint.

## Binstub format (Windows)

The SAC rule above fixes *what the binstub launches* (`python.exe -m <pkg>`).
This rule fixes *what the binstub is*. Each Windows entry point in
`~/.local/bin` is deployed as **two files**:

- **`<name>.ps1` — the primary.** PowerShell's command resolution ranks an
  ExternalScript (`.ps1`) above an Application (`.cmd`/`.exe`) **within the same
  directory**, so a bare `<name>` typed (or spawned) in pwsh resolves to the
  `.ps1` — no `PATHEXT` change required. The body forwards the argument array
  verbatim with `@args`:

  ```powershell
  $env:PYTHONUTF8 = '1'
  & "<venv>\Scripts\python.exe" -m <pkg> @args
  exit $LASTEXITCODE
  ```

- **`<name>.cmd` — the fallback.** Kept for non-PowerShell callers (cmd.exe, a
  bare `CreateProcess`/`PATHEXT` spawn, `cmd /c` Windows Terminal profiles, ssh
  launchers) that cannot resolve a `.ps1`. Forwards with `%*`.

### Why `.ps1` is primary, not `.cmd`

A `.cmd` forwarding `%*` **re-tokenizes** the command line through cmd.exe's
parser, which mangles — and can *inject* — shell metacharacters. For a payload
like `agent-bridge send peer 'echo "x" && ls | grep $HOME'`, cmd strips the
quotes, splits the argument, and executes `ls`/`grep` as separate commands
(operator injection). `setlocal enabledelayedexpansion` + `!args!` does **not**
fix this (embedded `"` still breaks it, and `!` is corrupted as the expansion
sigil). PowerShell hands the script an already-parsed argv array and `@args`
splats it to the child with correct Windows quoting — one parse, no injection.
Validated against quotes, `&&`, `|`, `;`, `!`, `$`, and globs. This matters
most for `agent-bridge send … '<cmd>'` and `agent-codespaces ssh … --remote-cmd
'<cmd>'`, whose payloads are themselves shell commands.

### The earlier-PATH-shadow gotcha

PowerShell prefers `.ps1` over `.cmd` **only within one directory**. Resolution
is still PATH-order first: a same-named stub in an *earlier* PATH directory
wins regardless of extension. A stray `pip install`'d `<name>.exe` in a system
`Python3xx\Scripts` that precedes `~/.local/bin` will shadow the binstub (both
`.ps1` and `.cmd`) and silently re-introduce SAC blocks and arg mangling. When
diagnosing, check `Get-Command <name> -All` resolves to `~/.local/bin` first;
if not, uninstall the shadowing package from the offending Python.

### Rules

1. Deploy **both** `<name>.ps1` and `<name>.cmd`; the `.ps1` body uses `@args`,
   the `.cmd` body uses `%*`. Both launch `python.exe -m <pkg>` (SAC rule).
2. Write the `.ps1` **after** (or alongside) the `.cmd` in the same dir so it is
   the preferred resolution; never deploy a `.cmd` without its `.ps1` sibling.
3. `uninstall` removes **both** files; `status` reports the `.ps1` as primary
   and warns if only the `.cmd` is present.

Reference: `Write-Binstubs` in `plugins/agent-bridge/scripts/install.ps1`,
`Deploy-Binstub` in `agent-codespaces`, `Deploy-Binstub` /
`Deploy-GlobalBinstub` (+ static `bin/agent-worktrees.ps1`) in
`agent-worktrees`, and the `init.ps1` binstub deployers in `agent-containers`
and `agent-mcp`.

## Deploy manifest (schema_version 3)

Written atomically (temp file → move). One shape for all plugins:

```jsonc
{
  "schema_version": 3,
  "service": "<plugin>",
  "deployed_at": "…Z",
  "deployed_by": "<machine>-<platform>",
  "source": {
    "kind": "local" | "marketplace",
    "path": "<plugin dir>",
    "repo": "copilot-extensions",
    "plugin": "<plugin>",
    "version": "<pyproject version>",
    "commit": "<short>|null",   // local only
    "branch": "<branch>|null",  // local only
    "dirty": false              // local only
  },
  "venv": "<venv dir>",
  "runtime": "python"
}
```

## Source = where the installer runs from (no flag)

The footprint's source is **inferred from the installer's own location**, never
a flag:

- plugin dir under `~/.copilot/installed-plugins/copilot-extensions/…`
  → `source.kind = marketplace`
- anything else (a git checkout) → `source.kind = local`

Run the installer from the marketplace plugin dir → marketplace takes over;
`update` keeps pulling from marketplace. Run it from a local checkout → local
takes over. Switching is an explicit act: invoke the installer from the other
location. `status` always reports the current `source.kind`.

An explicitly prepared merged-source payload may additionally carry
`runtime-source.json`. Native Python-runtime installers consume this optional
identity to select a distinct immutable generation while retaining the
original package version in `source.version`; `runtime_version` and
`runtime_source` identify its deployed source assembly. Source-kind inference
and installation ownership are unchanged; payloads without a source receipt
retain their ordinary package-version behavior. Source readers pin the complete
receipt across construction and publication, and deferred source stamps use the
canonical leased snapshot publisher rather than copying a checkout reference.
See the [source preparation tool](../tools/prepare_runtime_source.md) for the
single owner of preparation syntax and metadata semantics. Preparing this
payload does not change Copilot's plugin-content source or disable hooks.

The explicit Worktrees updater's internal `PreparedRuntimeSource` input keeps
the actual installed payload/command owner separate from that prepared artifact.
Registered, module and engine plans resolve the installation root and incumbent
scope through the owner; the prepared artifact must independently pass its own
runtime-scope/machine gate. Commands, deadlines and source-qualified target come
from the admitted artifact. Execution holds the existing owner's managed
`command_binding.py --run-lifecycle` custody, including on the same artifact's
module install fallback. This does not create an installation cell, data root,
new driver, or a public alternative installation command.

The full canonical source pin crosses that child boundary in the call-scoped
`COPILOT_EXTENSIONS_RUNTIME_SOURCE_PIN` environment entry. Its version-1
capability declaration is the literal `SOURCE_PIN_ENV` assignment in the exact
prepared `versioned_runtime_source.py` that the native frontend will execute,
not a package-version inference or a declaration from a different payload.
The installed owner's existing read-only source CLI first validates the full
pin and assembly. The receiving source CLI then enforces the transaction pin
independently of any frontend-local `--expected-source-identity` sample; neither
can override the other. Revalidation precedes each installer attempt. Native
initialization must invoke that validation whenever the transaction pin is
present, even if `runtime-source.json` disappeared since planning; absence
cannot downgrade the transaction to a package-only build.
Old callers without the entry retain the existing CLI contract; an older
prepared helper without that declaration refuses before installer execution
rather than silently ignoring a required pin. Nothing retires the existing
explicit CLI pin.

A package-version match alone cannot skip a prepared-source update. Its receipt
must match the full source identity and runtime version, select that exact
existing completed slot, and name its venv. Completion-probe subprocess failures,
including timeouts, become explicit receipt failures on the collected step,
not exceptions that abort the remaining update portfolio. Native candidate construction and
health remain the installer's responsibility. Ordinary runtime updates clear
inherited transaction pins. This is provider-side, unprofiled legacy-runtime
execution support: Manager's non-marketplace suite-update preflight remains
closed until acquisition, whole-portfolio execution and outcome handling are
connected. Namespaced source execution and profile-specific target selection
are not inferred from this internal API.

> **Self-stage caveat (#935).** When the `install-contract:v4 self-stage` prologue
> re-execs out of the marketplace payload, the installer's *live* path is a
> throwaway `~/.<name>/.install-stage/…` dir — which would read as `local`. The
> resolver therefore honors **`COPILOT_PLUGIN_STAGED_FROM`** (the real payload path
> the prologue recorded) so a staged marketplace install still resolves to
> `marketplace`. Keep this env-fallback in the byte-identical resolver.

The source-kind resolver is the one block tagged for byte-identical replication
across plugins:

```
# === install-contract:v3 source-kind … ===
… Get-SourceKind / _source_kind …
# === end install-contract:v3 source-kind ===
```

## Non-Python plugins (extensions and payload runtimes)

Most plugins here ship a **Python** runtime (a venv + package + binstubs). Some
ship no Python at all (**no `pyproject.toml`**). The Python-specific rules above
— `uv pip install`, the venv build, SAC-safe venv launchers, `_build_info.py`
stamping, `~/.local/bin` binstubs — **do not apply** to these. There are two
shapes:

### Plugin-contributed extension (preferred — no install scripts)

A Copilot CLI session extension can be shipped **inside the plugin** and
discovered directly by the CLI, with **no install step**. Place each extension
at `extensions/<name>/extension.{mjs,cjs,js}` in the plugin; the CLI scans an
**enabled** plugin's `extensions/` dir at session startup and loads it as a
`plugin`-source extension. The canonical example is **context-handoff**
(`plugins/context-handoff/extensions/context-handoff/extension.mjs`).

Such a plugin ships **no `scripts/install.*`**, no deploy manifest, and copies
nothing to `~/.copilot/extensions/`. `copilot plugin update <name>` (or repo
`enabledPlugins` auto-install) is the entire deploy. Two conditions gate
loading, both handled outside the plugin:

- the plugin must be in `enabledPlugins` (a marketplace plugin's `extensions/`
  dir is only scanned when enabled);
- `experimental: true` must be set in `~/.copilot/settings.json` (the CLI gates
  *all* extension loading on it) — ensured by the **agent-worktrees** installer
  (`Ensure-CopilotExperimental`), not by the extension plugin.

Because it ships no install or init scripts, `check-install-contract.py` does not
include it (the checker only scans plugins that have `scripts/install.*` or
`scripts/init.*`).

### Payload runtime with installer (legacy)

The older shape deploys a non-Python artifact **outside** what the CLI can
discover from the plugin dir — so it needs an installer to place the payload and
record a footprint. It is identified structurally by having `scripts/install.*`
but no `pyproject.toml`. Prefer the plugin-contributed-extension shape above for
new extensions; reach for an installer only when the artifact genuinely must
land somewhere the CLI will not scan from the plugin. When an installer is used,
the Python rules still do not apply, but what does:

1. It is still a **runtime** (it deploys beyond what `copilot plugin update`
   does), so it **must** ship `scripts/install.{ps1,sh}` plus an **install
   skill** that runs `install.* update` from the source dir. The two-step deploy
   (payload update → run installer) is unchanged.
2. It **must** write a `schema_version` 3 deploy manifest with a `source` block,
   written atomically (temp+move). `venv` is `null`; `runtime` names the payload
   kind (e.g. `"extension"`); add an `extension_path` (or equivalent) pointing at
   the deployed artifact.
3. It **must** carry the byte-identical `# === install-contract:v3 source-kind`
   resolver block, exactly as the Python plugins do — `update` still re-installs
   from whatever footprint (marketplace vs local) the installer was run from.
4. Output stays ASCII unless the script establishes a UTF-8 context (the
   installers here use `[OK]` / `[WARN]` markers).

`check-install-contract.py` scans plugins that ship a runtime entrypoint —
`scripts/install.*` or, failing that, `scripts/init.*`. Plugin-contributed-extension
plugins (no install/init scripts) are not included at all. For a
payload-runtime-with-installer plugin it detects the absent `pyproject.toml` and
skips only the `uv pip install` check; the manifest and resolver checks are
still enforced.

## Within-plugin consolidation

A plugin's own `scripts/*` and `src/<pkg>/installer.py` ship together, so they
may share freely. Secondary entry points (e.g. `init.ps1`/`init.sh`) should
delegate to the canonical `install.*` rather than duplicate the deploy logic.

## Shared installer-engine helpers (`libs/installer-engine/`)

### Independent Windows evaluator bootstrap

`bootstrap-python.ps1` is a standalone, read-only selector for an existing
external CPython base that can evaluate the owning payload's stdlib SDK.
`sync-installer-engine.py` delivers byte-identical copies only to the declared
Containers and Codespaces adopters. Loading the file or delivering a copy does
not opt a caller into another evaluator or change its installer selection.

`Resolve-EvaluatorBootstrapPython -ExcludedRoots <runtime/cell roots>` reads
PEP 514 `PythonCore` registration as data, honoring current-user shadowing
without interpreting opaque tags as versions or executing a launcher.
It admits only ordinary, fixed-local-drive, non-venv bases: no remote or
redirected paths, embedded path overrides or addressed runtime. Actual PE
version/architecture, offline Windows Authenticode trust and the verified
signer's CPython publisher establish admission before any Python runs.
Final native paths and guarded metadata prevent a short-path alias or changed
file from substituting for an independent executable.
Candidate and addressed-root ancestors are inspected from the drive downward,
before descending through any reparse point. A remote or redirected addressed
root cannot establish independence and leaves the optional bootstrap unavailable;
a genuinely missing local root suffix remains valid for first use.
Native drive-root handle identity also rejects DOS-device/SUBST aliases on
candidate or addressed-root drives, including missing suffixes beneath an alias.

The result has `Status`, `Reason`, `Python` and `Guard`. `selected` retains a
read-only file guard; the caller must dispose it after its finite SDK reads.
`unavailable` has no executable or guard. Unexpected I/O or native-verifier
errors throw rather than becoming successful absence. There is no persistent
path/admission cache, state write, interpreter acquisition or Manager dependency.
The native verifier uses System32 libraries, no UI and cache-only URL retrieval.

The selector never invokes Python itself. An opting-in caller chooses native
PowerShell before evaluation when no independently admitted bootstrap exists.
Once evaluation starts, interpreter/SDK failures remain terminal, not reasons
to try another authority. SDK execution uses isolated no-site startup in a fresh
process; ordinary plugin package-site initialization remains unchanged.
Selection and evaluation together, including native compilation, still need
the caller's real cold/first-touch and refusal qualification.

Codespaces' and Containers' Windows runtime gates opt in for their initial
`status` and selected-context `validate` reads. They pass the same explicit SDK
expectations and the native evaluator's explicit Copilot home, even when `HOME`
differs from `USERPROFILE`. They retain complete JSON and generation checks, and dispose the executable guard before
resolving the ordinary plugin runtime. Missing optional bootstrap support,
unavailable admission, or path inputs requiring native environment/drive
semantics select PowerShell before evaluation. Publication probes and installer
selection remain on their existing paths. POSIX dispatch is unchanged.

### Binstub publication failures

The simple binstub writers reject unavailable supplied resolver sources and
propagate directory, copy, write, permission and rename failures. Bash writers
reject directory destinations, including links to directories, before publishing
any member of the requested resolver/command family; a successful move into a
directory is not publication.
Bash callers must not depend on `errexit` surviving a conditional invocation;
PowerShell publication operations terminate on error even when the caller normally
continues. Each resolver or generated command is staged beside its destination
and published only after its complete write succeeds. Windows replacement uses
`File.Replace` for an existing file and a non-overwriting move for first
publication, not `Move-Item -Force`. Temporary cleanup is attempted on failure;
sharing or permission failures remain errors rather than a successful install.
Success is reported only after all required files are published.
This contract includes independently deployed Python companion and resolver
bundles, not only generated command files.
This is per-file atomicity, not a multi-file transaction or selector rollback;
the lifecycle caller still owns candidate health and activation ordering.

Dispatch and Index's POSIX callers use the shared ordinary-file publication
backup/restore helpers while holding their existing installation publication
lock. Required late-write failures restore prior selectors, manifests and
command files only while each target still belongs to that attempt; completed
candidate generations remain reusable. An unchanged selector or matching file
bytes alone is not ownership evidence. An unexpected selector, foreign target,
unsafe destination or restoration I/O failure is reported, with unrecovered
backups retained. Shell writes stage the exact destination basename in a private
sibling directory, move it to the destination parent and verify the resulting
file identity. A directory-target race must fail rather than move the staged
file inside that directory and report success. Cleanup removes only identified
owned stages and empty private directories; unknown artifacts retain recovery
evidence. This mechanism does not roll back service state or broaden the
caller's supervision authority.

### Source-stamp redirect transactions

Source-qualified installers use the canonical `versioned_runtime.py` command
`--root <installation> publish-redirects --payload-dir <snapshot> --version
<generation> --expected-source-identity <selected-json>`. When a caller also
publishes `payload-origin`, it supplies that value in the same transaction.
Source-only fast stamping may omit `--version` to preserve its existing
no-version-update contract.

The helper stages every requested UTF-8/LF marker and recovery copy before
replacing a live file. Its kernel publication lease has a different key from
selector/outer installer locks. It revalidates the complete selected source
receipt inside that lease, before each replacement and before commit, rather
than trusting only the generation string. Replacement failures restore the
previous requested marker set, including prior absence. Observed concurrent
changes, unsafe temporary/recovery files or restoration failures are explicit
errors with recovery files retained; they never authorize overwriting a peer.
Temporary cleanup failures are reported separately from an already-committed
publication. Default stdout is empty; `--json` names the exact published markers.

Outer installer recovery can request `--ownership-receipt <file>` from redirect
publication, activation and Index session-stamp publication. The canonical
`versioned_runtime_publication.py` companion records a versioned, root- and
target-bound JSONL receipt using the actual created stage descriptor before
rename. It persists and fsyncs physical identity, mode, size, modification time
and content hash before mutation, including recovery-stage writes. Reading the
destination after a producer returns never confers ownership: a peer's new file
with identical bytes remains foreign.

Native Python validates opaque producer receipts and performs their recovery;
shell `stat` output is not interchangeable with native Python file identity.
Required missing, corrupt or insufficient proof refuses the whole restoration
set and retains recovery evidence. Failed producers can still supply complete
proof of their own partial writes or recovery. Redirect and Index publications
share the redirect lease; selector activation retains its selector lease.
Default calls without a requested receipt participate in the same relevant
serialization. Recovery acquires those publication leases in a consistent order
without reacquiring the caller's installation lock. The optional receipt does
not change ordinary stdout or JSON output.

This is serialized publication with recovery on caught failures, not simultaneous
multi-file visibility, process-crash journaling, or rollback of service state.
Callers retain source admission, their outer lifecycle locks and subsequent
activation duties. Machines' source fast-stamp keep/replace decision shares the
full stamp lock; it cannot decide against an unlocked stale marker view.
Python-free legacy stamps remain unchanged; their native failure-preserving
publication counterpart is tracked in #7250 rather than gaining a new Python
prerequisite.

Source deployment manifests retain the full identity selected and validated
before commitment. Prepare mutable-input metadata and content hashes before
activation, or obtain them from the already-pinned receipt and validated
immutable payload afterward. Re-reading the mutable origin after selectors or
redirects commit must not turn a valid installation into a reported source
validation failure. Runtime-source provenance does not replace the separate
marketplace payload's command-binding ownership.

### Captured native human output

Declaring PowerShell installers source their packaged
`installer-human-output.ps1` after strict error setup and before installer
output. Only an explicitly requested, redirected child initializes UTF-8;
ordinary standalone invocation does not load the helper and retains its
existing output encoding. Requested missing helpers fail closed. The initializer
changes only the producer's stdout/stderr text writers, preserving native
command decoding and pipeline stdin encoding.
`tools/sync-installer-engine.py` vendors this helper and generates
`<entrypoint-stem>.human-output.json`, binding the declaration to both source files
(for example, `install.human-output.json` or Containers' and MCP's
`init.human-output.json`). Standalone adopters declare their actual entrypoint;
using the output initializer does not adopt the shared installer engine.
Regenerate after changing either file. Release materialization validates all
original declarations before any owner rewrites the tree, including when the
source and destination are the same promotion scratch tree. It refreshes the
entrypoint digests after all owning materializers finish (including the engine's
own installer, which has no engine reference) and refuses conflicting helper
assets. The helper is packaged with the installer, under the same
native execution-policy and payload-ownership requirements.

Containers' PowerShell Docker-version preflight uses its existing asynchronous
dual-stream native helper. It retains native decoding and `--version` arguments,
then writes diagnostics through the producer's stderr text writer instead of
letting raw child bytes bypass the declared encoding. A failed Docker probe
reports its exit code as a non-fatal warning, not a successful version check;
Docker remains optional for installing the CLI. The POSIX preflight likewise
preserves stderr and reports nonzero status without aborting installation.

MCP's PowerShell construction-shell and GC count subprocesses likewise use its
existing dual-stream helper, forwarding diagnostics through the producer's
text writer. The preceding GC command's explicit stderr suppression remains
unchanged. Scalar count output retains its single-line shape. Construction
ownership, failure decisions and the MCP stdio protocol are not changed.

Budget Guidance's PowerShell construction-shell call uses the same existing
dual-stream helper and producer-writer boundary, retaining diagnostics even
when the native command succeeds. Its actual self-staged installer preserves
requested UTF-8 output and literal forwarded arguments; the staging/watchdog
authority and runtime CLI JSON are unchanged. POSIX has no corresponding
PowerShell text-writer boundary.

Machines' actual PowerShell producer also admits requested UTF-8 output. Its
construction-shell diagnostics use the existing native helper, while its
finite Python lifecycle producer initializes only its own requested text
writers, preserving JSON and the original encoding-error policies. The
shared legacy decision child used by Machines and Index suppresses automatic
PowerShell module-preparation progress, not diagnostics or refusal status.
Staging, receipt validation and lifecycle authority are unchanged.

This declaration admits the existing Python updater's human-stream adapter;
it does not replace signature/provenance checks or change `-File` arguments,
script identity, exit handling, runtime construction or activation. Missing,
unknown or stale declarations retain inherited native streams with a bounded
presentation-gap notice. Such a producer is not reported as successfully
captured or universally time-framed.

The human-output adapter retains the updater's installer process-tree owner,
deadline and deployed-version receipt judgment. Streaming output does not turn
a zero exit with a missing target receipt, an expired deadline or a failed
cutover into a successful operation.

Bridge, Dispatch, Vault, Pull Requests, SSH, Budget Guidance, Worktrees and
Codespaces emit ordinary
progress-helper lines for install/update action identity instead of decorative
blank/banner blocks. Existing start/completion locations and useful action
names remain; Dispatch's superseded-install line retains its qualification as
skipped progress. Captured parents normalize these helpers to running, done or
skipped diagnostics with elapsed time. Only the parent's actual completion
records an operation duration and result. This is emitter-side cleanup, not
generic banner recognition or suppression of arbitrary empty child lines.
Stamp actions likewise use the existing progress helpers, retaining whether
runtime construction is deferred to first use or to explicit setup. Containers,
MCP and Machines initialize with ordinary progress, without a second decorative
completion banner before cleanup; the admitting parent records the final exit
only after cleanup. Bridge's provisioning action follows the same convention.
Status/uninstall presentation and programmatic CLI/protocol streams are
unchanged.
Worktrees and Codespaces retain explicit dev claims, distinct repair
generations and first-use deferral in their construction progress. Codespaces'
runtime-only provisioning identifies itself as provision rather than install.
These lines describe the selected action, not completed construction or health.

### External dependency seasoning

External build, runtime, development and selected optional dependencies must
have been released for at least **604800 seconds (seven days)** before their
literal floors/pins are declared or their registry artifacts are installed.
Attributable repository-owned source dependencies are exempt; their external
dependency closure is not. Unknown or future upload timestamps never prove age.
The declaration audit also attributes an ordinary versioned first-party
dependency when the exact plugin test consumer explicitly installs that
inventoried project's literal source path. Removing or redirecting that
consumer binding revokes attribution; a package name alone is never exempt.
Being a local path is not attribution: local wheels, archives and unattributed
projects are refused, except Manager's temporary exact-checksum
[dbus-next 0.2.3 wheel](../worktree-manager/vendor/README.md).
Source-directory admission verifies project identity,
resolved containment and the recursive local dependency graph, including
symlink escapes and hidden requirement-file references.

The CLI authenticates its own canonical repository or materialized payload
anchor. In a materialized repository, transitive sibling references retain the
roster-owned plugin's embedded `libs` namespace; only explicitly named,
identity-matching projects enter the admitted graph, not unrelated directories
or another payload's libraries. Other callers provide separately attested
absolute roots with repeated
`--trusted-source-root` options before `--`; the Python API's
`trusted_source_roots` defaults to empty and does not infer trust from the
requested path or working directory. Python 3.10 can use the proper TOML parser
bundled in its standard `ensurepip` wheel without installing bootstrap packages;
an interpreter without an available parser fails before dependency writes.

Trusted registry-only tooling can select `--registry-only` before `--`.
This skips product-root discovery and passes no trusted local project roots
to the same canonical installer policy. It is mutually exclusive with
`--trusted-source-root`; ordinary CLI source admission is unchanged. Local
projects, editable sources and configured local-source mappings remain refused.
The selected registry, resolver cutoff, dependency resolution, caller-required
hashes and retained-input checks still apply. In particular, metadata-validator
bootstrap must not derive unrelated product graphs merely to install its
hash-pinned registry toolchain.

`tools/check-dependency-age.py` audits canonical declarations against public
release metadata; configured-feed availability remains an installation check.
Dependabot's seven-day cooldown is complementary, not a substitute for that
guard or the resolver cutoff.

`installer_dependency_policy.py` is the canonical stdlib-only installer policy,
with private settings-projection and configuration-retention primitives in
`installer_dependency_config.py`; both ship beside each versioned runtime helper
and the shared engine. The native
UV configuration probe is bounded, captured without printing credential-bearing
settings, and fail-closed on an unsupported projection. Installation uses the
stronger of seven days or the configured global cutoff, preserves stricter
package cutoffs, and refuses disabled/weaker package overrides. Index-specific
cutoffs are unsupported because UV can assign a synthetic age to artifacts
without upload timestamps; flat indexes are unsupported because they bypass
timestamp filtering. UV's exclusive millisecond-resolution cutoff may
conservatively reject an artifact exactly on the seven-day boundary. The
installer does not add an epsilon that could admit younger bytes.
Supported UV cutoff projections include legacy absolute/relative values and
structured timestamps with an optional relative span. The structured form
uses the earlier captured timestamp or relative boundary for the enforced
global cutoff. Each effective package cutoff must be at least as strict as that
global boundary. Every admitted package override, including environment and
configuration-file values, is frozen to an absolute CLI cutoff before execution;
span metadata never excuses a younger executed override. Retained
comparison accounts for that intentional span-to-absolute conversion, preserving
the admitted timestamp and every other resolver setting even as probe and
execution clocks advance.
On Windows, an installer-selected absolute native UV `.exe` path is resolved without
traversing links before configuration probing and retained execution. Both
use the same physical target of that selected command; another UV on PATH is
not substituted, and no PATH, mitigation, index or seasoning setting is
changed. This handles an otherwise usable WinGet UV binary whose link is
rejected by inherited RedirectionGuard. Missing targets refuse explicitly.
Python-owned construction applies this same selection before its own strict
path resolution, so a traversing caller cannot fail before SDK admission.
For a named native Windows command (`uv` or `uv.exe`), the SDK uses the
read-only `SearchPathW` API with the process's existing system search policy,
then resolves that exact logical result without traversing its links.
Manager's default availability check uses the same selection before `is_file`;
`shutil.which` can otherwise hide a guarded but usable WinGet entry.
No PATH or search-mode setting is changed, and lookup failure never substitutes
another UV. Non-Windows, explicit relative paths and non-native wrappers retain
their existing behavior.
The existing two-file SDK closure includes a generated projection of
`agent_procutil._interpreter.physical_executable` in the configuration helper.
Regenerate it with `tools/sync-installer-engine.py`, then fan runtime helpers
out with `tools/sync-versioned-runtime.py`; the SDK sync check rejects drift
from the canonical primitive rather than maintaining a separate resolver.
`--reinstall` forces actual resolution rather than
trusting a previously installed, potentially newer package. Skipping dependency
or isolated-build resolution is not an age-enforcement fallback.

`run_uv_install` owns admission and execution as one lifetime. UV consumes
private retained source metadata, recursive requirements and configuration,
with an explicitly captured environment, rather than reopening the caller's
mutable inputs after preflight. The retained resolver must match the native
configured resolver, including index order/authentication, TLS, workspace
constraints and stricter package cutoffs; an unsupported merge or mismatch
refuses installation. `prepare_uv_install` is only a preflight API, not an
installation boundary.

`run_uv_compile` and `retain_uv_compile` expose the same retained transaction
for bounded `uv pip compile` exports. They admit `PipCompileSettings` directly,
not a renamed install projection. Positional requirements files (including
recursive includes) and attributable `pyproject.toml` metadata are copied and
revalidated with the effective resolver. Exports force `requirements.txt`,
hashes, a fresh resolution (`--upgrade`, ignoring existing output pins), and no
header or source annotations. An explicit output path remains relative to the
original effective working directory, never the temporary snapshot. Without
an explicit output, UV writes to the caller's captured standard output;
ambient file redirection, package omissions, index-URL emission, directory
dependencies (editable or not), local artifact mappings and unsupported compile
options refuse. Metadata-only project inputs remain supported; every positional
input is canonicalized into the retained graph, including absolute path aliases.
Recursive requirement/constraint include targets are likewise rewritten by
canonical identity rather than lexical text substitution.
Already-retained include targets do not pass through lexical remapping again.
`prepare_uv_compile` is only
preflight. This is candidate lock construction, not target-profile approval,
wheel acquisition, feed qualification or runtime activation.

`retain_uv_source_compile` is a separate, explicit candidate-export mode. It
retains the attributable first-party project graph using the same transaction.
It derives `--no-emit-package` only from unconditional local mappings required
by each owning project's unmarked base dependencies, never from the inspected
project set alone. Root metadata does not suppress its own registry identity.
UV still resolves local dependencies' transitive requirements. Effective
omissions must match exactly, source mappings cannot be disabled, and ambiguous
identities, direct local directory requirements (including inactive markers or
unselected extras), conditional/alternative local mappings, mappings used only
by optional or conditional branches, context-only project mappings, local
artifacts and remote sources refuse. Source mode refuses explicit `--project`/
`UV_PROJECT` contexts; discovered configuration cannot add projects outside the
compiled input graph.
Editables remain private metadata inputs, never references back
to live code. Ordinary compile exports keep their directory-dependency refusal.
The caller independently verifies the selected source, retains the transaction
through output consumption, and binds its own lock/trigger before assembly.
An export alone proves neither source approval nor governed acquisition.

The repository-only `tools/runtime_source_lock.py` adapter binds this candidate
export to the existing installation-owned source-mode request and independently
selected `SourceIdentity`. It uses the existing receipt/assembly validator,
compares the complete selected identity before and after retained resolution,
checks the actual selected interpreter's marker environment and freezes target,
capability, extras, fallback trigger, exact pins/hashes and requirements digest.
Native candidate mode refuses universal or overridden platform resolution,
including `UV_PYTHON_PLATFORM`, rather than letting a lock for another marker
environment pass as the selected interpreter's lock. Before compilation, the
retained copy must cover the existing installer's complete source inventory and
match the independently verified original digests; checking the original again
after resolution is not sufficient to detect a transient edit during copying.
The caller holds the retained payload through candidate consumption. Deadline,
resolution, integrity and output-limit failures return no candidate and release
invocation-owned inputs. The deadline begins before retention entry and bounds
UV settings probes and per-chunk source copying, not only the compiler child.
Ordinary callers retain their existing default preparation behavior.
No artifact manifest or successful publication is
needed. This is not an origin authenticator, feed acquisition boundary,
complete inspected-wheel profile, reusable admission receipt or installer
entrypoint. Existing source selection and subsequent governed acquisition own
those decisions; the private candidate's installation policy is not a public
release record.

Retain complete admitted projects before merging their declared build-resource
directories. A resource may be a package subdirectory of another admitted
project; its presence must not make that parent look fully copied while its
`pyproject.toml` and other build inputs are still missing.

Retained-input cleanup preserves an active exception or a nonzero UV child
result, including captured output and the original exit status. A secondary
filesystem cleanup failure is reported independently as `retained-input-cleanup`,
not as dependency-seasoning rejection. Cleanup failure after otherwise successful
execution raises `RetainedInputCleanupError` and makes the CLI fail; it never
turns an incomplete cleanup into success. Context-owned runtime construction
uses the same exception-preserving cleanup path.

Absolute, non-editable source-only installations retain the common source/resource
layout without reproducing unrelated caller-directory or staging ancestry.
An unrelated caller uses a separate private working directory; its effective
configuration and target interpreter are still captured before relocation.
Relative operands, requirement files, dependency groups, explicit project
contexts, source-bearing configuration/metadata and editable installations
retain their existing relative layout. This includes relative source traversals
that leave and re-enter a project, rather than rewriting those declarations.
This reduces backend build-path depth without relocating the checkout or
weakening source attribution, seasoning or resolver-equivalence checks.

Agent SSH's deferred-runtime snapshots materialize development source pointers
to the libraries copied inside the snapshot before publication. Both stamp
implementations keep this metadata transformation runtime-free; first use must
not depend on the original checkout or references outside the retained payload.

Editable source retention supports static `setuptools.build_meta` projects
using the repository's `src` layout. Retained metadata selects compatibility
editable mode and references the original live `src` through a same-volume
relative package directory; installed imports survive snapshot cleanup and
observe subsequent source edits. Retained `setup.cfg` preserves existing raw
configuration values while setting `egg_info.egg_base` to the private project
directory, so build metadata cannot be emitted into the live `src` tree.
Malformed or unreadable/unwritable retained configuration refuses installation;
the original configuration is never rewritten. On Windows the private snapshot
is a sibling of a shallowest admitted project or build-resource root on the editable source
volume, outside every copied input. A volume-root input with no outside
temporary location fails explicitly. This avoids long parent-relative paths from an unrelated
temporary directory; setuptools requires relative package directories.
Dynamic metadata, other editable backends or
layouts, competing editable-mode settings and mixed source volumes on Windows
fail explicitly rather than installing references to disposable source trees.
On Windows, editable metadata is staged beside the common source ancestry so
setuptools' relative metadata renames stay short. Retained filesystem copies,
TOML I/O and cleanup use native extended paths where needed; UV arguments and
file URLs retain ordinary canonical spelling. This preserves the configured
resolver and attribution boundary rather than bypassing retention for long
snapshot paths. Parent-relative config selections are normalized lexically
before extended prefixing, preserving explicit `--config-file` and
`UV_CONFIG_FILE` behavior.

The policy does not replace the adopter's configured feed, disable TLS
verification, or retry against a public index. A selected extra's dependency
failure fails construction; retrying the base package in the same partially
populated candidate is not successful installation. Unsupported pip fallback
paths must refuse rather than resolve unseasoned packages.

The repository's locked npm projects, including the rasterizer and workflow
helpers, use
`worktree-manager/scripts/picker-snapshot/install.py`. It queries npm's configured
registry for every locked dependency, including transitive and optional entries,
checks age and binds the admitted artifact to the lock's integrity before
`npm ci`. Strong SRI evidence uses a valid SHA-256, SHA-384 or SHA-512 digest;
matching SHA-1-only metadata and locks are not sufficient. Missing or
SHA-1-only registry integrity additionally requires npm's tarball verification
against the strong lock digest. npm's configured default-registry
host substitution is preserved; private registry identities are never written
into the shared lock. `npm ci --before` alone is not an age gate for locked
versions. Installation disables dependency lifecycle scripts and must import
the explicitly selected health modules successfully. No-argument use installs
and checks the rasterizer; other projects supply `--directory` and repeat
`--health-module` for their selected consumers.
Installation consumes retained manifest and lock bytes in a private, same-volume
snapshot with the project's npm configuration. Only its health-checked
`node_modules` is published; admission, install, health or publication failure
preserves the prior dependency tree. A concurrent manifest, lock or configuration
change refuses publication rather than installing an unaudited closure.
Inputs are rechecked after the candidate tree's rename, before retiring the
previous tree; a mismatch at that linearization point rolls publication back.

### Inert exact-profile preparation

`tools/runtime_profile.py` provides a repository-only versioned exact-record
validator and pure selector. It is not shipped in runtime payloads or invoked
by an installer. Its [reviewed contract](../efforts/active/governed-python-artifact-promotion/exact-profile-contract.md)
defines the schema and explicit boundary: canonical whole-record identities,
release/target/mode compatibility, complete asserted wheel metadata graphs
with extras/markers, and externally supplied whole-record approval references.
Record validation/selection does not authenticate approvals, verify a source
release, download wheels, detect hardware, resolve dependencies or activate a
generation. `runtime_profile_wheels.verify_profile_wheels` separately checks
the complete retained wheel set against the exact record, as described below.

Actual consumers still need integration with the existing installation authority
and verified release/configuration channels, followed by admitted byte/metadata
checks and immutable construction. Candidate release-input generation is a
separate concern (#6941); neither constraints nor this inert record prove an
approved, available CPU/accelerator closure. No production caller is enabled by
these preparation and inspection APIs.

The installation-context Python `resolve_runtime_profile_policy` API and
`runtime-profile-policy` CLI now resolve configuration intent through the
existing installation owner. No profile/approval file is required for the
CPU-first, artifact-preferred, governed-source-permitted baseline. Optional
`runtimeProfiles` settings in the existing owner-bound deployment policy can
disable source fallback or explicitly select a capability. Retained capability
and explicit unprofiled migration remain distinct. Optional exact
`downloadOrigins` names additional HTTPS file origins through that same owner;
missing/empty values preserve the baseline, and nonempty values enter its policy
identity. This is not an index allowlist, profile approval or a new policy file.
See the
[library contract](../libs/installation-context/README.md) for fields and
readiness/refusal behavior. Its owner/effective-policy digest is an invalidation
input, not whole-record approval, source-byte authentication, a feed permission
or an admission receipt. Explicit profile settings therefore hold ordinary
publication until a profile-aware lifecycle enforces them; a valid configuration
cannot silently be ignored by an older construction path.
This includes explicit management admission and borrowing an existing custody
ticket; managed-only publication without profile settings is unchanged. Pending
installation-mode activation never yields profile intent for the legacy root,
even when the underlying mode resolver reports readiness.
Native constructors do not yet consume this new API;
the actual closure, authority composition and constructor acceptance stay open.

`tools/runtime_profile_release.py` adds the repository-only release-channel
adapter. An optional `runtime_profiles` extension in the existing
`python-artifacts.json` index carries schema
`copilot-extensions.release-runtime-profiles`, version 1, and a nonempty
`profiles` array. Pre-signing artifact validation checks each complete record
against its validated first-party bundle: the raw manifest digest, artifact ID,
`<component>:<version>` release identity, payload digest, component, primary
wheel and every selected first-party wheel digest must agree. An unused optional
vendored library need not enter the applicable closure. Records use the fixed
`promotion-main` approval reference and artifact mode. Source mode remains independent of
artifact infrastructure and is not implemented by this adapter.

`select_release_profile` retains the exact bounded index bytes under the
installer's existing private temporary-input owner, then invokes
`publish_python_artifacts.verify_attestation_subject` against the independently
selected repository and pinned hosted Main workflow/source identity. It checks
the same bytes after authentication before constructing its internal
whole-record approval mapping. Caller-supplied approval maps, ambient producer
hints and a subject's own signer declaration cannot establish approval.
Selection additionally requires the independently selected source release,
artifact/manifest pair, target, component, roots and capability. Returned
evidence identifies the attested subject digest and producer; it is not feed
admission or an installed-generation receipt.

The producer does not yet generate or publish real profile records, and no
runtime caller uses this adapter. Composition with the resolved adopting-policy
baseline, independent source authentication/locking, actual governed acquisition,
seasoning/revocation, measured production limits and consumer integration
remain prerequisites. Existing indexes without the extension retain their
original validation path and neither install nor update requires profiles.

`tools/runtime_profile_wheels.py` adds repository-only actual-byte verification,
not an approval adapter. `verify_profile_wheels` requires the complete exact
filename-to-binary-stream set, caller-supplied total archive/unpacked byte
budgets and a per-metadata-file budget. It compares every archive's SHA-256 and
actual Requires-Python, Provides-Extra and Requires-Dist with the validated
record. The shared wheel inspector checks distribution/version/tag identity,
safe members and each RECORD entry without extracting or executing code.
Runtime RECORD entries support SHA-256, SHA-384 and SHA-512; weak/unsupported
algorithms refuse, while archive locks remain SHA-256. Distribution aliases and
runtime versions compare by normalized identity. First-party promotion retains
its locked generator, exact version spelling and SHA-256 RECORD policy.
The shared filename grammar lives in the static-admission module so byte
inspection never imports builder/feed machinery. A third-party wheel's
generator is not an approval source.

Streams remain caller-owned and must remain immutable under retained custody
through subsequent construction. Verification detects changed bytes during its
own inspection, but produces no admission/reuse receipt and grants no trust to
a path, feed, release or record. The caller still owes authenticated approval,
source/feed/seasoning/revocation evidence, real device eligibility and the
installation-authority integration. Explicit test budgets are not production
acquisition defaults or permission to enable a consumer.

### Closed inspected-wheel construction

`runtime_profile_wheels.profile_wheel_inputs` composes the same complete-profile
verification with `WheelInstallInputs`, inert construction data containing the
whole canonical profile binding and actual inspected distribution versions.
The caller retains the existing `WheelSnapshot` context through installed
health. This is not an approval token: the existing authority still owes
authenticated release/profile/feed/target admission before invoking a consumer.
No production installer selects this mode automatically.

On Windows, original and retained wheel handles allow shared reads but deny
writers and deletion for their held lifetime, including extended-length paths.
NTFS read-access updates can advance handle change time, so that timestamp is
not treated as a byte revision under this native read guard. Path/handle
identity, birth time, size, mtime and full SHA-256 custody checks still apply;
non-Windows change-time checks remain in place. Original handles close after
the complete set is copied; retained handles close before owned cleanup.

The Python engine's explicit `numbered_candidate(wheel_inputs=...)` path uses
the same `_candidate` and `_construct` owners, independent venv creation,
construction/retention leases, owned children, installed health and completion
boundary as source construction. It retains the source graph for bootstrap
process ownership and validates the existing extras/local-package selection.
It does not introduce another allocator, mutable venv, cutover or fallback.
Mutable dev construction refuses this mode.

Only with inspected `wheel_inputs`, an explicit `local_packages=()` selects no
first-party distribution. This supports a separate immutable build-tools
environment while retaining authenticated source for process ownership and
identity. Local extras are refused for that empty selection. Ordinary source
construction keeps its existing primary-distribution inclusion.
`WheelSnapshot.subset()` borrows an exact nonempty subset of the existing
read-only handles; the original snapshot owner must remain open.

The canonical dependency-policy owner projects only the complete retained
wheel set into a private hash-required requirements file. Named local wheel
URLs carry the exact SHA-256 fragment as well as `--hash`. UV runs offline,
without indexes, ambient UV/PIP/Python configuration, caches, dependency
resolution, source discovery, interpreter downloads or build backends, using
copy link mode and the exact candidate interpreter. Ordinary source policy
continues to reject `--no-deps`; there is no blanket seasoning exemption.
The policy owner resolves a caller-relative UV path or selected command name
to an absolute executable before changing to the retained working directory.

The existing `.runtime-input-origins.json` uses schema version 2 for this path
(source receipts remain version 1). It binds the full profile/lock, mode,
release, source-or-artifact, target and policy data, actual distribution
versions and SHA-256-qualified origins. UV currently records local wheel hash
fragments in `direct_url.json` URLs with empty `archive_info`; installed health
requires that exact hash-qualified origin and rejects conflicting archive hash
fields. It also requires the exact complete installed distribution set, with no
unexpected or duplicate distribution. Receipt/binding/origin/version changes
refuse reuse. The health child reads that existing owned receipt and compares
its canonical wheel evidence with a parent-supplied SHA-256 binding; fresh
origin maps have their own parent binding. Complete profiles/maps never enter
argv, so valid large closures do not hit native command-line size limits.
These bindings check consistency, not authority. Source content plus the whole
wheel binding enters the existing
immutable construction identity, so a changed policy, target or profile cannot
reuse a numbered identity. A new retained copy of unchanged cached bytes can
reuse a healthy runtime without a feed request or reconstruction.

This component does not authenticate a profile, acquire/cache wheels, select
hardware, admit a feed, supply production budgets or choose verified-source
fallback. Missing or rejected inputs never trigger an automatic source or
public-index fallback. Those authority/acquisition and consumer-enabling
contracts remain required.

### Exact-wheel acquisition preparation

The stdlib-only `installer_wheel_acquisition.acquire_wheel_inputs` mechanism
acquires already-selected filenames/hashes, never resolves or substitutes a
dependency. It checks each initial link and redirect before issuing a request,
preserves file upload-time/seasoning and yanked/withdrawn decisions, hashes
streamed bytes and hands them to the existing exact `WheelSnapshot` owner.
Index credentials stay on their own origin; receipts contain opaque feed/policy
identities and origin-chain hashes, not credentials or URLs.

Callers declare inclusive package/request/redirect/candidate, metadata,
per-wheel/closure and total temporary file-content budgets and one monotonic deadline.
One invocation-owned worker uses the existing installer process owner; DNS,
headers and body reads cannot renew the deadline. Its frozen helper/control
bytes count against storage and are removed after child settlement, before
snapshot copying. Peak storage is the greater of control plus downloads and
the original/retained wheel copies. No service, proxy or alternate constructor
is introduced. Filesystem allocation/metadata overhead remains a separate
measurement before enabled defaults. A windowless Windows caller uses the
existing process primitive to select a console interpreter for captured worker
protocol, without allocating a visible console. Native extended paths preserve
long selected wheel names. The same deadline caps closed-wheel constructor children and
retry waits through installed health; ordinary source child defaults are
unchanged.

The repository-only `runtime_feed_configuration.retain_native_feed_inputs`
composes an existing release selection with freshly resolved installation
policy and the canonical retained UV preflight. It does not implement another
configuration-precedence reader or execute dependency compilation. Native
interpreter facts, effective indexes, credential inputs, TLS roots and seasoning
are captured before HTTP. Effective policy identities exclude retained paths
and the advancing current-date cutoff, but bind semantic seasoning and actual
source/trust policy. Named environment credentials, retained URL credentials,
bounded netrc input and native UV credential lookup remain private.

The initial adapter supports Windows/Linux system certificate trust plus
captured native explicit CA certificates. Unsupported proxy/bypass, keyring,
bundled-root/Darwin trust providers, resolver-altering constraints, metadata
overrides and package-specific index routing defer explicitly before HTTP;
they are never silently ignored or translated into different settings.
Unsafe trust, source ambiguity, corrupt metadata or digest mismatch remain
refusals, not availability. A typed acquisition-unavailable outcome is input to
the existing authority, not permission to change mode or waive a restriction.
No production caller or enabled default budget is supplied.

`AcquiredWheels.construction_inputs` binds validated acquisition facts into
`WheelInstallInputs` and the existing owned generation receipt without mutating
the approved profile/lock. Actual policy/file bindings enter construction
identity and installed health; changing or stripping them cannot reuse that
generation. Legacy wheel inputs without this optional evidence keep their
existing receipt shape. Per-attempt request, redirect, byte and storage counters
remain available through `AcquiredWheels.evidence()` but do not enter stable
generation identity or installed health; unrelated index growth must not
invalidate the same admitted policy and exact wheels.
This consistency binding is not approval or offline
reuse authority: authenticated admission, supplied revocation, source fallback,
real release/client integration and measured production budgets remain parent
gates.

### Authenticated source-build preparation

The repository-only `runtime_source_build.retain_source_runtime_inputs` composes
the actual configured-runtime-source acquisition owner with canonical source
preparation, the retained native compiler and the same governed feed worker.
It uses no artifact index or fabricated release selection. An unsupported
default marketplace source provider remains unavailable rather than guessed.

One native source lock includes declared runtime/build requirements and the
frontend's `packaging` dependency. Its actual installation owner validates
the held native indexes, credential/trust providers and supported configuration
before the compiler can make package requests. The same retained transaction
then resolves dependencies and supplies acquisition; no second resolver pass
or artifact selection is introduced. Exact native wheel witnesses must be
unambiguous; inspection precedes backend execution. A separate numbered
build-tools environment runs the existing frontend offline, with owned
deadline-bound children. Built runtime metadata must agree with authenticated
static declarations. The final complete runtime profile contains only its
reachable runtime closure, not build-only packages. Generation identity binds
source, lock, build and actual acquisition facts through the existing receipt.
Wheel completion uses `runtime_source_wheels.retain_source_profile`, not a
second source-profile builder. Its optional admitted build role must match
the same source, target, component, declared build roots, exact packages and
acquired files before backend execution. Only that explained build-only
closure may be excluded from the final runtime. Its tools binding records the
actual admitted profile, not fabricated promotion-toolchain provenance; the
ordinary promotion-toolchain and generator checks remain unchanged.
When native source output lacks an asserted Python hint, acquisition retains
the selected index hint and source admission compares it with actual wheel
metadata before running a backend; artifact profiles retain their exact check.

Keep the returned context open through installed health and recheck `verify()`
before consumption. Pass its `local_packages` selection to the existing
constructor alongside its wheel inputs. Current owner/source policy and byte custody remain live
checks before each backend and around consumption, through the existing
candidate verifier's nonserialized owner hook, not an approval map. This explicit composition does not enable a
production consumer, persistent offline discovery, source-cache GC, activation
or a live-feed exception. See [source preparation](../tools/prepare_runtime_source.md)
and [validation coverage](../TESTING.md#wheel-input-inspection).

### Bounded wheel inspection

`libs/installer-engine/installer_wheel_inputs.py` is a stdlib-only input
inspector, vendored with the Python engine. Callers supply explicit positive
archive, expanded/member, individual/aggregate metadata, member-count and expanded-tag
limits, plus a finite monotonic deadline. It streams the archive digest and every RECORD
member digest, bounds metadata before parsing, and refuses duplicate/ambiguous
members, unsafe paths, encryption, links, malformed headers and changed source
files. It neither extracts package contents nor runs package code or a backend.
Promotion uses the same archive verifier while preserving its additional
source/toolchain identity checks.
Stored and raw DEFLATE streams are consumed independently of `ZipExtFile`'s
declared-size truncation, checking full length, CRC and end-of-stream before
metadata parsing or hash acceptance. Trailing-dot/space path components refuse
as Windows aliases; decompression failures remain structured integrity errors.
On Windows, reserved device names and invalid filename characters also refuse.
Metadata-directory suffixes are checked case-insensitively, and local-header
offsets are bounded before a member is opened. Header parsing follows PyPA's
[core metadata `compat32` convention](https://packaging.python.org/en/latest/specifications/core-metadata/),
preserving raw dependency fields rather than decoding email encoded words.

Compressed tags are deduplicated and bounded before Cartesian expansion,
including the selected filename's tags. Member ancestry uses a deadline-checked
component trie rather than repeatedly joining every prefix of deep paths.
ZIP32/ZIP64 preflight bounds actual central-directory entries and directory
bytes before `ZipFile` allocates an index; the aggregate metadata budget also
caps that directory. Declared entry counts cannot bypass the actual scan.

The complete-profile verifier above uses this bounded core through the shared
promotion inspector, not a separate per-wheel semantic facade. Its existing
byte budgets retain their total-closure and per-metadata-file meanings; callers
may additionally supply `WheelLimits` and a shared monotonic `deadline`.
The initial archive hash uses the smaller of the remaining closure budget and
the optional per-wheel archive limit; default runtime inspection derives its
archive cap from the caller's closure budget rather than promotion's cap.
Per-metadata-file limits do not reduce a separately supplied directory/aggregate
budget. Without explicit `WheelLimits`, the archive budget bounds the directory
and the three individual file ceilings bound metadata; first-party promotion
retains its own stricter directory budget.
Record validation and its dependency walk share the same deadline and bound
compressed filename and target tags before calling the packaging parsers.
Runtime RECORD policy permits SHA-256/384/512, while
source-binding member facts always retain SHA-256 and first-party promotion
still requires SHA-256 RECORD entries. Neither facts nor a matching digest
authenticate the profile, admit a feed or authorize cached input. A successful
inspection is not a retained snapshot: construction must still own immutable
input custody and obtain authenticated selection/acquisition evidence. No
installer selects or activates this preparation API automatically.

### Shared engine delivery

A canonical pair of files, `libs/installer-engine/installer-engine.ps1` and
`.sh`, carries the installer logic that was mechanically duplicated across
every Python runtime plugin's own `install.*` before this existed: native
command capture, the transient-uv-race retry helpers (SRE module mismatch
`#6785`, `pyvenv.cfg` corruption `#6852`), signed-venv creation, `uv`
self-bootstrap, schema-version-3 deploy-manifest writing, and a minimal
self-provisioning binstub writer (`Write-SimpleBinstub`) for a plugin with no
scheduled-task/service lifecycle. Adoption is **opt-in and byte-vendored, not
imported** at ship time, but there are now **two valid dev-time authoring
forms**:

- **Byte-vendored form** — the adopter keeps a real
  `scripts/installer-engine.{ps1,sh}` copy in its own tree, byte-identical to
  canonical and kept in sync by `tools/sync-installer-engine.py` (`--check`
  verifies; write mode repairs or refreshes the local copies).
- **Canonical-reference form** — the adopter's `install.sh` / `install.ps1`
  sources `libs/installer-engine/installer-engine.{sh,ps1}` directly while
  working on `dev`, and keeps **no** plugin-local engine copy there.

Adopters that scrub generated build residue before a package attempt must do so
before every bounded retry, not only before entering the shared helper. The
PowerShell helper accepts `BeforeAttempt`; Bash accepts a shell-function name
through `INSTALLER_ENGINE_BEFORE_ATTEMPT` and its source-path argument through
`INSTALLER_ENGINE_BEFORE_ATTEMPT_ARGUMENT`. A failed callback stops installation.

Regardless of the dev-time form, the **shipped payload is always materialized
back to a real local `scripts/installer-engine.{ps1,sh}` copy** before release:
`materialize_main.py` / `preview_release.py` copy canonical into the plugin's
own `scripts/` directory and rewrite the wrapper back to the local source line,
so the installed artifact never reaches across a plugin boundary at install or
runtime. `tools/sync-installer-engine.py`'s own `unregistered_adopters()`
check also fails closed if a plugin references or carries installer-engine
content without being registered in the tool's `ADOPTERS` tuple, so a
hand-added adopter cannot drift silently forever.

Referenced Python support assets are materialized with the engine. The same
canonical dependency-policy bytes also fan out through
`tools/sync-versioned-runtime.py` to runtime plugins that have not adopted the
shared shell engine; those installers must not invent a second policy.

`agent-worktrees`' packaged non-editable launch fallback follows the same
contract: the authoritative launch-wrapper scripts live under
`worktree-manager/bin/` on `dev`, but release/preview materialization copies the
manifest-declared wrapper set into `plugins/agent-worktrees/bin/` before ship,
so `deploy_wrappers()` in a packaged payload never depends on a sibling
checkout being present.

The engine deliberately does **not** cover per-service concerns that
genuinely differ across plugins — scheduled-task/service lifecycle,
sibling-plugin installs, or any bespoke per-service config — those stay in
each plugin's own `install.*`, which sources the vendored engine file for the
shared primitives and supplies its own config/parameters around it. Vendoring
(byte-copy + `--check` drift detection) was chosen over a runtime
git-fetch/import specifically to preserve this doc's existing
self-containment constraint (a plugin's installer must not reach across a
plugin boundary at install time) — see the `vendored-installer-engine`
effort's Journal for the full git-fetch-vs-vendoring reasoning.

### Python-owned construction foundation

For the earlier, Python-free Windows admission boundary, the canonical
`agent-procutil` package also carries native daemon/owned-child helpers, and
`single-instance-lease` carries the native counterpart of `OperationLock`.
They reuse WMI outside-Job admission and the exact existing kernel lock
protocol; owned writers enter their strict Job atomically at creation.
Their API and qualification boundary are in the
[procutil bootstrap reference](../libs/agent-procutil/README.md#python-free-windows-bootstrap-admission).
They do not themselves construct a driver foundation or migrate any first-use
publisher. Bootstrap source admission, durable interruption fencing and
immutable publication remain consumer obligations.

The finite Windows standalone consumer is
`libs/update-driver/scripts/foundation-produce.ps1`: it composes those primitives
with source-bound progress, frozen canonical acquisition and immutable
construction under the same authority. The
[producer contract](../libs/update-driver/README.md#finite-windows-foundation-producer)
owns its API and qualification boundary. Ordinary first-use publication and
queue-aware foundation upgrades are not enabled by that consumer.

`libs/installer-engine/installer_engine.py` is the in-progress Python ownership
seam for the mutable-dev-slot effort, not a completed installer adoption.
Construction follows the complete transitive local `tool.uv.sources` graph.
The canonical dependency policy's `retain_uv_install()` transaction is the
single owner of its source snapshot, resolver configuration and admitted argv;
the Python lifecycle retains that context through child settlement and installed
health. It verifies the copied file coverage and content against the admitted
source identity, and records the transaction's actual non-editable installation
origins, not a second snapshot's paths. `freeze_inputs()` remains an explicit
source-copy utility for packaging fixtures, not the construction path.
Within that transaction, path-replacement forms and their ordered matcher are
prepared once rather than rebuilt for each metadata scalar. Overlapping local
package roots share verification only for the same original/retained file pair;
every distinct pair still compares both content hashes and contributes its
original-source hash. Neither result survives the transaction or replaces the
construction owner's source-identity and fingerprint checks.
The bootstrap requires Python 3.11 or newer.
The POSIX guardian observes its direct child without reaping it until the
owned process group is settled. Python 3.11/3.12 on macOS lack `os.waitid`;
there it uses the existing `ps` process-state dependency to observe zombie
state without releasing the child's PID/PGID. It does not raise the declared
Python floor or use a reaping `Popen.poll()` fallback.
Worktrees' Windows pre-lifecycle admission independently requires Python 3.10+,
the declared minimum for its payload and shared human-output helper, before
reserving ownership or staging the installer. A working but older `py -3`
selection is not sufficient: discovery probes the registered selectors listed
by `py -0`, then real PATH interpreters, excluding WindowsApps aliases. There
is no fixed upper version bound for launcher discovery. An explicit
`-BootstrapPython` must be an existing absolute, compatible interpreter; an
invalid explicit selection is refused rather than silently replaced. Discovery
never installs Python or changes PATH. Interpreter paths cross the native-output boundary as
ASCII-safe JSON strings, preserving non-ASCII paths without changing the host's
console or pipeline encoding.
The payload-only admission CLI and its interpreter probes use `-I -S -B`:
they need only the standard library and explicitly loaded payload helpers,
not site-package initialization. This does not change candidate construction
or installed-runtime interpreter startup.

When a Worktrees caller explicitly supplies its existing interpreter
(`-BootstrapPython` on Windows or `--runtime-publication-python` for Bash),
early context reads use the payload-owned canonical Python validator in
isolated, no-site UTF-8 mode. Windows reuses the existing hidden-process reader and
retains diagnostic stderr and the actual exit; empty output is not turned
into a blank output record. Its consecutive initial validate and status
queries run through one finite payload-local transport process. That process
calls the same canonical SDK CLI actions with their original arguments,
preserves each raw JSON response, and stops on the first failed query. It
does not cache a decision, introduce another authorizer, or combine reads
across staging or construction. Publication and later revalidation remain
fresh calls at their existing boundaries.
The same validate, status, publication and
governance-revalidation checks remain at their original boundaries, with
their original arguments. Without the hint, POSIX retains the Python-free
Bash reader and Windows retains the native PowerShell reader. This selects
an existing read adapter, not a different installation owner, construction
interpreter, publication lease or recovery authority; it never installs Python.

Canonical admission uses the requested package root as its working directory.
Relative package/configuration relationships are retained without accumulating
the caller's absolute checkout ancestry on each control/dev transaction. The
native regression checks nested retained graphs against Windows' build-directory
limit; long-path file support alone does not make a deep backend working directory
acceptable to process creation.

`numbered_candidate()` constructs or admits an ordinary numbered identity and
keeps its shared retention descriptor until the calling lifecycle exits the
context. Exact reuse never obtains exclusive construction ownership or invokes
venv/package writers, even when construction tools are absent. Unfinished
builders recheck under the canonical OS lease; completed content drift or
invalid evidence refuses instead of rewriting a published slot.

An optional owner-supplied `validate_candidate(slot)` runs after installed
health but before a new completion marker, and before exposing any reused
candidate. Source-profile callers use it to recheck retained source and the
installed interpreter target under the same custody. A rejected new candidate
remains incomplete; the hook does not activate or rewrite a published slot.

An explicitly selected `extras` tuple on `numbered_candidate()` installs only
declared optional groups on the primary package, inside that same retained UV
transaction. Names are normalized and ordered; unknown, duplicate and mutable
selections refuse before runtime writes. Non-base admission fingerprints bind
both the complete source fingerprint and the selected extras. An empty selection
retains the existing source-only identity and origin-receipt compatibility.
Changing selection cannot reuse or rewrite another completed profile under the
same slot name. The installed-origin receipt records non-base selections and is
checked again on reuse, even after frozen inputs have been retired.
The caller still owns the distinct slot name, profile-specific health/launch
contract and lifecycle; this seam does not select a machine's desired profile,
activate a runtime, provision sibling environments or qualify a native platform.
Mutable `dev_candidate()` does not accept non-base profiles.

For an independently declared role, `numbered_candidate(local_packages=(...),
extras=(...))` additionally selects the exact local distribution closure to
install. Names are normalized and must belong to the admitted source graph;
source construction always includes the primary distribution. The caller supplies every required
transitive local distribution, not paths or replacement project metadata.
UV remains the dependency resolver: an incomplete closure fails resolution,
rather than fetching an omitted first-party distribution from a registry.
Installer-owned contradictory constraints forbid those omitted names, including
prereleases. Selected roots alone supply runtime requirements and package
operands; unselected roots do not contribute unconditional dependencies.
The retained dependency-policy owner creates these generated constraints
through `retain_uv_install(excluded_local_packages=(...))`, snapshots and
audits them before execution, and keeps them outside the caller-relative
source-layout anchor. Deep source paths therefore do not become deeper
retained backend working directories merely because a role excludes packages.
This does not modify `pyproject.toml`, infer a machine role or duplicate a
PEP 508 resolver.

The complete local source graph is still validated, snapshotted and fingerprinted
under the canonical retained transaction, including build/ownership helpers and
unused roles. The selected extras and local names additionally bind candidate
identity and installed-origin evidence. Fresh and reused health require selected
non-editable installed origins and refuse any omitted local distribution that
is actually present. `ReadyCandidate.local_packages` reports the normalized
selection. Without inspected `wheel_inputs`, an explicit empty tuple selects only the primary distribution;
`None` preserves the existing all-local-input behavior and its identity/receipt
compatibility. Mutable `dev_candidate()` refuses explicit local selections.
The caller owns the named role, distinct immutable slot, role-specific health
and ordinary publication/removal lifecycle; this construction seam does not
make an installer, service supervisor or selected command catalog by itself.

Reused installed-health failures request distinct-generation repair when the
probe exits unsuccessfully, cannot create its child, or reaches its deadline
after every owned writer has settled. Process creation is distinguished by
`agent_procutil.SyncProcessLaunchError`; the installer propagates a separate
settled-child timeout type. Missing-file launch errors also remain
`FileNotFoundError` instances, preserving missing authentication-tool detection.
Ownership, Job admission and cleanup failures are
not repair licenses. Fresh construction still fails its health gate rather
than reporting an unhealthy candidate ready.

For a materialized source deployment, the Python lifecycle validates
`runtime-source.json`, publishes the canonical source snapshot and selects its
exact source-qualified runtime identity. It does not coalesce across source
generations or silently downgrade to a plain package slot. Package provenance
remains the declared package version; deployment evidence retains the full source
identity, including when an explicit repair adds its own generation suffix.

Worktrees retains the installed payload's command owner separately from those
source inputs. Its Python publication binds the deployment manifest through
the canonical legacy manifest-publication context, retaining the admitted
command lease or publication lock through selection, commit and rollback.
The resulting manifest includes the validated command receipt and completed
lifecycle evidence, rather than replacing them with an unbound installed-build
record. A prepared source artifact does not become the installation owner:
the existing global command, runtime root and durable state remain addressed
by the original installation identity. Namespaced publication retains its
existing receipt and lock path.

An installed Worktrees `dev-release` can restore a completed retained slot even
when the original checkout and installed publisher are unavailable. Its exact
completed-slot bootstrap validates the incumbent command receipt's normalized
source, installation root and current environment, retaining the original
publisher identity rather than attributing the bootstrap as a new source.
Lifecycle evidence must be absent or complete for that same owner. Legacy
publication custody precedes whole-operation cutover custody and is reused
through nested settlement. This restoration does not override managed-deployment
policy, admit unfinished lifecycle evidence, or grant ordinary source publishers
permission to copy an incumbent receipt.
Both legacy lock paths forward the caller's remaining wait budget; ordinary
lifecycle admission consumes that same budget before acquisition.

Reuse also runs an isolated installed-origin health probe under retention. Its
historical, non-editable local origin remains attributable to this runtime's
frozen-input staging root even after that build's snapshot is removed. Failed
health preserves the published bytes and selectors; it never becomes permission
to rebuild that identity. A Python health callable can exercise lazy imports
before both fresh publication and healthy reuse; Worktrees requires its full
command surface, not just a package import.

An optional installed `module:function` construction callable receives JSON
data under the same owned, isolated candidate interpreter. It runs only for
fresh construction, before the installed-origin/full-surface health gate and
immutable completion publication; exact reuse never reruns it. Worktrees uses
this seam to stamp source provenance, the frozen package version, UTC build
time and a distinct `runtime_generation`, and to remove unused unsigned
Windows `agent-*.exe` console trampolines. POSIX scripts and the signed Windows
interpreter are preserved. Package-version comparison accounts for normalized
distribution metadata while retaining the declared version spelling. Its
health callable checks the stamped identity and full command surface; missing
or conflicting metadata is a repair refusal, not permission to restamp a
published slot.

Worktrees' native install/update/provision entrypoints now invoke its vendored
Python lifecycle driver; native prerequisite and platform/project setup remain
outside that operation. Publication executes under the retained candidate's
own interpreter, not by importing its site-packages into a possibly
ABI-incompatible bootstrap interpreter. The parent keeps candidate/rollback
custody while awaiting that receiver. Receiver diagnostics stream to the caller
even while settlement is pending. A custom root that is not the resolved
Worktrees runtime root never cuts over the host's monitor.

Required native helper callbacks render a complete owner-declared copy inventory
under the existing borrowed installation authority; their prepared
acknowledgement does not claim publication success. The retained Python owner
validates that inventory and publishes it after candidate health, before
selection, using the canonical
[created-file publication custody API](../libs/versioned-runtime/README.md#created-file-publication-custody-python-api).
Helper custody follows actual runtime settlement through monitor and manifest
publication: keep candidate helpers after a real commit, or recover uncontested
outputs when the previous runtime wins. Scoped/global behavior and normalized
PowerShell no-ops remain distinct from deployment-policy opt-in.

The caller-owned monitor seam can return actual passive **and restart**
process handles without releasing its cutover guard. Supplying both guard and
handles transfers settlement responsibility to that lifecycle; older callers
retain their existing asynchronous recovery backstop. Spawn/commit return
values do not prove service readiness or process exit. Candidate readiness,
failed-child exit and predecessor retirement are observed under retained
custody; uncertain durable publication requires a terminal owner-led recovery
witness, never a missing/corrupt breadcrumb interpreted as success. Selector
publication, monitor transition, and selector settlement share one exception-safe
owning boundary: failures before either selector write, between writes, or after
both writes retain custody through rollback or observed commit-forward, then
rethrow the original failure. A transient rollback-write failure does not release
the generations. Selector recovery accepts the exact prior bytes or the candidate's
native newline spelling, never an unrelated concurrent selection.

The serving monitor's attributable slot is retained even when it differs from
the previous selector. For an uncertain publication, the same owning receiver
can retry recovery under native ZDD transition authority instead of waiting for
another installer blocked by its own guards. Recovery validates its breadcrumb,
actual spawned handle, process start identities, and strict routing state.
It confirms durable publication and actual predecessor drain before
commit-forward, or restores and verifies the predecessor before retiring a
ready unpublished passive. Unknown evidence, formerly-published unhealthy
candidates, and storage failures retain custody rather than manufacturing a
terminal outcome. Promoted monitor demand uses its existing worktree-status
subscription, renewed under the lifecycle's transition stack through actual
predecessor retirement. The successor is reattested after that exit. Windows
exit evidence tests the native process signal, not whether a retained exited
process object can still be opened. These
pieces do not yet constitute complete adoption: dev/release, distinct-generation
repair, generation propagation and process-death/live monitor recovery still require their
own implementation and evidence.

Construction uses the explicitly supplied base interpreter's `venv --copies`
and one `uv pip install --no-sources --no-editable --link-mode copy` invocation
over every frozen local package. It preserves the caller's configured indexes;
it neither fetches unpublished local libraries from an index nor silently
downgrades to another interpreter. An isolated installed-origin health probe
precedes immutable completion publication. Retention is acquired before
construction ownership is released, so GC cannot discard a ready candidate
waiting for cutover.

Worktrees opts into a process-retention bootstrap before completing a fresh
candidate. The frozen input's stdlib runtime helper and `runtime_launcher.py`
are copied into the unpublished slot's `.runtime-bootstrap/`; reuse never
patches a completed slot. Monitor launch arguments preserve that slot before
Windows direct-base-interpreter conversion. The slot-local launcher runs with
`-I -S -B`, admits attributable completion and acquires the canonical shared
retention lease before `site.main()`, `.pth` files, or application imports.
It verifies the resulting interpreter prefix and retains the lease through the
module's entire lifetime, including after the launching owner dies. A partial
bootstrap refuses launch; an older numbered runtime without any bootstrap
uses the explicit, warned legacy path until a new version is installed.
Pre-3.14 venv prefix initialization is exercised separately from 3.14's early
initialization. Default GC needs no process scan to protect these children.

`retention_custody()` exposes the retained slot and its borrowed kernel
descriptor to owned children; `retention_lease()` remains the compatible
path-only context over that same protocol. `ReadyCandidate.descriptor` is
valid only within the candidate context and must not be closed by its caller.
This keeps read-only health children under actual custody without taking a
construction lease on reuse. The single-file, independently vendored helper's
reviewable ceiling increases from 2,362 to 2,376 lines for this accessor; it
introduces no deploy-time import or alternate lock protocol.

`installer_engine_process.py` owns bounded child execution. Windows uses the
frozen canonical `agent-procutil` strict, non-breakaway Job implementation.
For candidate-backed Windows children, a finite guardian acquires the existing
canonical candidate retention lease before starting the Job. It observes its
caller's liveness pipe and retains that lease until the Job is settled even
after caller death; release of the caller's construction byte lock alone does
not admit destructive cleanup. The supplied runtime root, version and helper
path must form a complete custody context. Child launch and settled-timeout
outcomes retain their existing explicit error classes.
POSIX uses an owner-death pipe guardian retaining the construction descriptor,
an unreaped process-group leader and verified process-group teardown. The
canonical POSIX lease releases on final descriptor close, not an explicit
unlock that could revoke a live guardian's inherited custody.
An unavailable teardown attestation is not permission to unwind construction:
the owner logs the failure and keeps its actual kernel custody and frozen
inputs until the Job or process group is verified settled. It then surfaces
the original failure, so a recovered teardown cannot publish the failed
candidate. The guardian does not reap its group leader before confirming
that no live descendant writers remain.

The existing Worktrees monitor cutover API now accepts the owning Python
lifecycle's already-held guard. It validates the exact runtime-root lock and
does not acquire it again or release it on success or failure. The legacy
restart path accepts the candidate interpreter separately from the bootstrap
interpreter. These are integration seams, not evidence that an entrypoint
already holds the repair guard or waits through actual monitor settlement.

Both Worktrees ordinary native entrypoints now call this foundation.
Installation-cell authority,
governance/transaction lock ordering, actual monitor cutover and rollback,
claimed dev/release, distinct-generation repair, generation propagation and
self-contained release materialization remain integration gates. Native
Windows fixture tests exercise real offline uv construction, two-process
coalescing, source-independent installed imports, exact reuse, GC retention,
failed health, timeout and abrupt caller death. Equivalent POSIX execution and
the authorized installed signed-Python deployment lane remains unverified; these
fixtures do not complete the first-adopter or portfolio acceptance gates.

`New-SignedVenv` retains seeded stdlib construction by default. UV-owned
callers can pass `-WithoutPip`: the signed `--copies` path then also uses
`--without-pip`, avoiding an unused `ensurepip` installation. Bridge, Logger,
Vault, Pull Requests, the SSH UV branch and the foundation prerequisite producer
use that option. It does not remove pip from existing slots or change genuine
Python/pip fallback construction. Signature, slot-health and publication checks
remain unchanged.

`agent-codespaces` uses the canonical-reference form for both installers.
Its Connection Owner service, complete explicit local closure, and Windows
signed-Python recovery/venv health probes remain plugin-owned; uv acquisition,
transient retries, and deploy-manifest writing use the shared engine.
Both installers submit the eight helper roots and plugin root in one retained
UV transaction, preserving every named `--reinstall-package` selection or all
nine editable roots. Normal installs explicitly pass `--no-editable` so canonical
development mappings cannot conflict with those non-editable direct roots.
UV resolves their dependency order once; a closure failure
still refuses construction. Windows requires UV and omits pip seeding from
its signed-copy venv as well.
PowerShell `stamp` materializes both engine files and all declared local
libraries into the snapshot, rewriting canonical references to snapshot-local
paths before publishing it. POSIX `stamp` retains its owning-payload pointer;
release materialization makes that payload self-contained before staging.

`agent-dispatch` uses the same canonical-reference form. Its PowerShell
snapshots materialize both engine files and all six declared local libraries,
including snapshot-local, non-editable dependency references. Local-checkout
`stamp` uses a content-addressed identity over the complete materialized tree:
unchanged stamps reuse one snapshot, while plugin, engine or library edits
publish a new immutable identity. Direct local builds retain their checkout
paths. The POSIX stamp remains an owning-payload pointer. Dispatch
records the latest same-version local snapshot candidate under the snapshot
lock and rechecks it under the publication lock, so a delayed stamp cannot
overwrite newer content or launchers. The locks remain sequential to avoid
inverting runtime installation's lock order. Dispatch
owns signed-Python validation/recovery, pre/post-build artifact scrubbing,
dependency order, optional MCP-extra fallback, launchers and its complete
coordinator/supervisor lifecycle; only shared acquisition, retry and manifest
mechanics move into the engine.

`agent-index` uses canonical references on `dev` and shared acquisition,
native capture, transient retries and manifest writing. Its versioned CLI,
host-only server sibling and separately provisioned durable embedding venv
all use those shared build mechanics. Signed-Python discovery, signature/venv
health validation, dependency order, Torch/CUDA feeds, engine configuration and
every activation/service lifecycle remain local. Signed results are accepted
only after checking the interpreter's venv prefix and `pyvenv.cfg`; an
unusable result is removed before fallback, even when creation returned zero.
An existing interpreter alone never proves the engine venv reusable.
POSIX likewise executes an isolated interpreter probe and requires a distinct
venv prefix matching the physical venv root before CLI, server or engine reuse
and after creation. A corrupt, base-interpreter, wrong-prefix or failed-probe
runtime is repaired or explicitly rejected. uv interpreter selection remains
unpinned; Windows CLI/server retain signed-first and Python/pip fallback policy,
while the independent embedding engine keeps its uv-first policy.
PowerShell package paths consult only the executable accepted by `Ensure-Uv`;
a rejected but still-discoverable PATH uv is never selected again for packages.
If no validated uv remains, CLI/server/engine package paths use Python/pip.
Native primary CLI environments are base-only for both roles. Host candidates
must provision their slot-owned `[store,server]` sibling, including local base
libraries, and pass dependency/package-origin health before completion and
activation. Hosted commands dispatch to that runtime; missing host dependencies
never silently fall back to the light CLI. A complete service cell or standalone
explicit-source host continues using its already-owned complete runtime.
Index uv acquisition and executable validation use one installation-wide guard
shared by CLI and durable-engine callers. Windows uses the existing mutex
adapter's `Uv` scope; POSIX uses fd7 with the same OS advisory locking and
explicit unlock as its other guards. Acquisition admits for at most 180 seconds
and releases before package/build/publication work. It never waits while owning
build fd8 or publication fd9; a cheap cached-engine check releases its build
guard before any acquisition. The shared canonical engine stays unchanged.
An exception at the optional server repair boundary warns and retains the
primary runtime's fallback, restoring the caller's error preference in `finally`.

PowerShell Index stamps materialize both engine files and every declared local
library (`zdd`, `agent-procutil`, `dropin-registry`), with snapshot-local
non-editable references. The existing ordinal whole-payload hash supplies
content-addressed immutable snapshot identity, including plugin, engine and
library bytes and case-distinct paths. Unchanged stamps reuse identical
snapshots; existing corrupt snapshots fail without replacement. Snapshot
production and marker/launcher publication use sequential locks, with a
same-version candidate recheck before atomic marker publication. A delayed
stamp cannot overwrite a newer candidate's markers or launchers. The same
installation-keyed publication mutex covers runtime preparation's marker
removals, activation, payload/launcher publication, manifests and direct
launcher repair. Runtime freshness is rechecked under that mutex, and activation
plus related payload/launcher writes remain one publication transaction.
Windows attempts a global named mutex so separate logon sessions share the guard.
Only an UnauthorizedAccessException during Global creation permits Local
fallback, with an explicit warning that cross-session serialization is unavailable;
unrelated creation errors propagate. Equivalent trailing-separator paths resolve
to one lock identity, including reentrant Local acquisitions.
Snapshot production releases its separate lock before publication; no
publication holder acquires the snapshot lock. Namespaced cell adapters retain
their independent receipt-lock contract. The POSIX
stamp retains its owning-payload pointer, whose released payload is already
self-contained. Neither stamp provisions the embedding stack.

Direct runtime installation additionally holds a target-venv build lock before
any slot cleanup/repair through package installation, health, activation and
related publication. Its slot path supplies version scope, so different versions
can build independently. The optional server sibling inherits that guard; the
durable engine uses its own target-venv guard. Build admission uses the existing
180-second window, and all returns/failures release in `finally`. Lock order is
build then publication; stamp takes snapshot, releases it, then takes publication,
never a build lock while holding publication. POSIX follows the same target-keyed
build scope using bounded flock admission or bootstrap Python's stdlib
`fcntl.flock` on the same inherited descriptor. Both paths explicitly unlock
before closing the descriptor, so a descendant retaining that open-file
description cannot retain admission after the callback returns. The stable lock
inode is not unlinked or replaced, and legacy PID markers are ignored rather
than reclaimed. These guards do not alter daemon/engine lifecycle.
POSIX publication uses a separate installation-wide advisory descriptor (fd9),
while target/version build admission owns fd8. Invalid-marker preparation and
final freshness checks, activation, payload/launcher writes, stamping and
manifests all enter that publication guard. Nested publication calls reuse the
owning scope without reopening fd9; no publication scope acquires build admission,
and package work stays outside publication.

An original snapshot origin remains provenance, not a required surviving
authoring directory. The supported PowerShell runtime gate and installer
authorization prologue use the validated owning payload for the mandatory
legacy probe when the origin no longer supplies its invocation declaration.
The probe still validates the complete declaration and installation governance;
malformed declarations and denied namespace/legacy authorization remain blocking.

Only a plugin explicitly opted into `tools/sync-installer-engine.py`'s
`ADOPTERS` tuple is expected to carry (and keep in sync) the vendored engine
files; this is a phased rollout, not a blanket requirement for every runtime
plugin yet. `agent-worktrees` carries a standing, permanent exception (it is
the control-plane plugin, by far the largest and most bespoke installer, and
already opts out of the related `versioned_runtime.py` fan-out for the same
specialization reason) — it is not expected to adopt this engine, now or
later.

## Runtime self-reconcile and command glossary (session-start hooks)

A Python runtime plugin currently installs a legacy `~/.local/bin/<name>`
management wrapper, but a
`copilot plugin update` refreshes only the cached payload — it does **not**
redeploy the binstub/venv. Without a nudge, the runtime silently lags the payload
until someone re-runs the installer by hand.

Until a plugin has a validated namespaced-active installation, absent/default
installation-mode policy remains legacy and its cheap `stamp` path publishes a
global compatibility wrapper for **every command** declared by
`payload-invocation.json`, not only the primary plugin command. Those wrappers
are backups: agent-facing guidance still uses the attributable payload command
catalog, and multi-command compatibility wrappers should delegate to the owning
payload shims rather than independently selecting a runtime. A namespaced-active
installer suppresses legacy wrapper publication only after the shared
installation-mode resolver proves that actual mode. Transition cleanup
removes only ownership-proven legacy wrappers, preserving unrelated user
commands; a desired flag or environment hint alone is insufficient.

Most Python runtime plugins **self-reconcile at session start**: they declare
a `sessionStart` hook that re-runs its own installer **only when the deployed
version drifts** from the payload -- **unless the bootstrap-killswitch is
active**, in which case that plugin's hook skips its own reconcile entirely for
this session (see `libs/bootstrap-killswitch/README.md`; amends the
*self-provisioning-runtime* vision in `visions/plugin-services/README.md`).
Every adopting plugin's `bootstrap-check.{ps1,sh}` checks its vendored
`bootstrap-killswitch-guard.{ps1,sh}` as its very first step, before any
version comparison below, against one shared, cross-plugin state file
(`~/.copilot-extensions/bootstrap-killswitch.json`) -- an operator/agent hand-
diagnosing a venv or install path directly sets this switch so a background
reconcile can never race that diagnosis; it fails open to "reconcile proceeds
normally" on a missing or malformed state file. This switch applies **only to
the default (non-namespaced) installation**: a namespaced marketplace cell's
own `COPILOT_EXTENSIONS_CONTEXT` reconcile path is a deliberate no-op for this
guard, so the switch can never cross an installation-cell boundary (see the
[Marketplace Installation Cells](../visions/plugin-services/installation-cells/README.md)
vision).

- `plugin.json` sets `"hooks": "hooks.json"`.
- `hooks.json` `hooks.sessionStart` runs the plugin's `scripts/bootstrap-check.{ps1,sh}`
  — either the copy the installer deploys to `~/.<plugin>/bin/`, or (the
  self-locating variant) the one shipped in the plugin payload's `scripts/` dir.
- For those self-reconciling plugins, `scripts/bootstrap-check.{ps1,sh}` is a **version-gated reconcile**: it compares
  the deployed version (`~/.<plugin>/deploy-manifest.json` → `source.version`) to
  the payload (`pyproject.toml`) and, on drift (or a missing venv), re-runs the
  plugin's canonical installer (`init.*`, or `install.* install`) **in the
  background** — the atomic versioned-venv swap keeps concurrent use safe, and
  backgrounding keeps session start non-blocking.
- **Index is the explicit-first-use exception:** its session-start bootstrap
  checks the default-installation killswitch, resolves repository and installation
  admission, then publishes only metadata/binstubs. A validated namespaced-active
  context stamps only cell-local metadata, never a global compatibility launcher;
  the default-installation killswitch does not apply to that cell. The hook never
  invokes an installer, provisions a venv or starts/registers a service or engine.
  First admitted operational binstub use or explicit `<repo> update` owns
  provisioning and reconciliation. A stamp is not runtime readiness.
- A namespaced Agent Machines cell uses the schema-4 distinction defined above:
  bootstrap compares the loaded payload only with reconciled `source`
  provenance, validates `runtime` against the cell-local marker/interpreter, and
  does not treat an intentionally selected historical runtime as payload drift.
- A separate `sessionStart` command hook runs
  `scripts/emit-command-catalog.{ps1,sh}` from `COPILOT_PLUGIN_ROOT` and emits
  exact payload-local `argv` plus `availability`. It never snapshots dynamic
  runtime state or falls through to the legacy global wrapper. Agent Worktrees
  additionally reports a read-only, source-qualified ownership binding from
  lifecycle metadata or validated cell receipts; this is not runtime health.
  The [shared catalog binding contract](../libs/payload-invocation/README.md#source-qualified-command-binding)
  owns its schema, compatibility, and refusal rules.
- Roster-wide tests run every runtime `agent-*` plugin's cheap stamp in an
  isolated profile with no installation-mode policy and require all declared
  commands to have a `~/.local/bin` fallback. This protects legacy/default
  operation while namespaced rollout is still opt-in.

This reconciles the **tool**, never machine state or config. First install remains
the one-time setup step; a self-reconciling hook keeps an installed runtime
current. Index instead defers that work to admitted first use or explicit update.

## Enforcement

`tools/check-install-contract.py` verifies, per plugin, against its canonical
runtime entrypoint (`install.*` if present, else `init.*`):
- `uv pip install` is used (no package file-copy) — **skipped for
  payload-runtime plugins** (no `pyproject.toml`; see
  [Non-Python plugins](#non-python-plugins-extensions-and-payload-runtimes)),
- no binstub sets `PYTHONPATH=…/lib`,
- no canonical `.ps1` entrypoint launches the `…\Scripts\<name>.exe`
  console-script trampoline ([SAC-safe launchers](#sac-safe-launchers-windows)),
- a `schema_version` 3 manifest with a `source` block is written,
- the source-kind resolver is identical across plugins (per language),
- each Python runtime's `scripts/versioned_runtime.py` is byte-identical to the
  canonical `libs/versioned-runtime/versioned_runtime.py` (edit the canonical and
  run `python tools/sync-versioned-runtime.py`; `--check` verifies in CI/pre-push),
- an adopting plugin's `scripts/installer-engine.{ps1,sh}` is byte-identical to
  the canonical `libs/installer-engine/installer-engine.{ps1,sh}`, and no
  unregistered plugin carries a drifted copy (`python
  tools/sync-installer-engine.py --check`, wired into CI's `guards + lint`
  job; see [Shared installer-engine helpers](#shared-installer-engine-helpers-libsinstaller-engine)),
- each Python runtime wires the **session-start reconcile hook** above
  (`plugin.json` `hooks` → a `sessionStart` `bootstrap-check`), unless its
  payload manifest explicitly sets `sessionStartBootstrap: false` for a
  repository-gated, explicit-first-use runtime whose session start is required
  to remain non-mutating. Plugins predating
  the invariant are listed in `EXEMPT_SESSION_HOOK` (tracked in dotfiles#779) —
  new runtime plugins must comply, not be added to that set.
- every plugin PowerShell script routes persistent User/Machine environment
  access through the shared contained-test adapter; direct access is rejected.
- every plugin's declared `runtimeScope` is one the reconciler accepts, and a
  plugin that ships a runtime installer declares `universal` or `machine-gated`
  (see [Automatic reconciliation at launch](#automatic-reconciliation-at-launch-runtimescope)).

Wire it as a `pre-push` hook (see `tools/hooks/pre-push`, which also runs
`tools/check-no-internal-identifiers.py` — a repo-wide guard that fails the push
if any privately-configured internal identifier leaks into the tree; it no-ops
unless a denylist is configured, see the agent-codespaces README "Local
identifier guard").
