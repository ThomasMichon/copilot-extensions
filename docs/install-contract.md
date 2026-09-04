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
manually or wire it as a git `pre-push` hook).

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
   that cell's runtime. Machine-global commands are limited to attributable
   project entry points with ownership receipts; installers never compete for a
   global generic plugin command last-writer-wins.
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

Python and PowerShell publication prepare a hidden
`<versionsRoot-parent>/.runtime-slot-<slot-digest>-<nonce>/` sibling outside
`versionsRoot` and use an OS-native atomic no-replace directory rename. If
another slot appears first, publication fails and preserves it. An interruption
outside normal in-process cleanup may leave the hidden sibling; it is inert,
lies outside canonical version-slot enumeration, and requires explicit
reconciliation rather than automatic deletion. Dependency-light Bash reserves
the final slot with atomic `mkdir`, then publishes the marker with a no-replace
hard link from a completed temporary file within that reserved slot. Ordinary
in-process failures remove the still-empty reservation they own; an interruption
between reservation and marker publication can leave a markerless slot. That
visible slot is ambiguous, remains untouched, and fails closed until an explicit
repair/release transaction.

Slot ownership is non-activating. Publication does not write payload content,
`.install-complete.json`, `current-version`, `last-known-good`,
`installation-activation.json`, launchers, services, state, or tombstones. It
therefore proves only that one empty runtime slot is reserved for one validated
cell/snapshot transaction; later build-completion, cutover, health, rollback,
repair, and uninstall transactions remain separately gated.
`status: "ready"` means the ownership record is attributable, not that the
current installation is active; results expose `namespaceState`, `installState`,
and `slotEmpty` for later callers. A runtime version is an immutable build
identity and cannot be reassigned to another snapshot. Markerless or conflicting
slots require an explicit future repair/release transaction; this foundation
does not delete or reclaim them.

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
Repair/release and uninstall remain separate lifecycle boundaries.

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
  "transferredAt": "2026-01-01T00:00:00Z"
}
```

The tombstone binds the legacy footprint to the destination cell and activation
generation. A tombstone whose activation is missing, unreadable, mismatched, or
foreign is `orphaned-transfer`: all writers fail closed and legacy operation
must never resume. Explicit rollback first publishes the next legacy activation
generation and only then clears the tombstone while holding both locks.

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
> later in this document describe the currently deployed legacy layout. They
> remain valid only for unchanged legacy installers during the phased migration.
> New or migrated installer surfaces follow the cell contract above.
> Until an explicit installer or management caller wires activation and runtime
> selection, missing user policy means the legacy layout remains authoritative.
> Context receipts may be stamped without selecting the cell runtime, and the
> low-level activation CAS never invokes itself from ambient policy.

## Plugin update ≠ runtime install

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

  A Python `agent-*` runtime that exposes an agent-facing command additionally
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
> (dotfiles #1025). The per-PR **version-bump guard** (`check-version-bump.py`)
> makes the same-version-drift case rare in the first place, so `--force` stays a
> last resort.

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

A plugin declares whether — and where — its runtime should be reconciled via a
**`runtimeScope`** field in its `plugin.json`:

| `runtimeScope` | Meaning |
|----------------|---------|
| `none` | The reconciler never touches the runtime. Use for skills/agents/hooks-only plugins, **plugin-contributed extensions** whose payload *is* the runtime (e.g. `context-handoff`), **and** plugins whose runtime is managed out-of-band (per-machine, by hand). |
| `universal` | Reconcile the runtime on **every** machine (a non-Python runtime that every machine needs and that deploys outside the plugin payload). |
| `machine-gated` | Reconcile the runtime only on machines in the plugin's allowed set (e.g. `agent-bridge`, `agent-codespaces`, `agent-containers`, `agent-mcp`). |

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
`aperture-labs`) match this repo's reference facility. With no gate info
available, a `machine-gated` runtime is **skipped** (safe default — never
auto-install a machine-specific runtime where the policy is unknown).
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
     installer's `install` / `update` / `start`, **and a `sessionStart` `ensure`
     hook**, all funnel through this same idempotent ensure. A Copilot session
     therefore guarantees the daemon, so it survives reboots **without** an
     AtLogon scheduled task.
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
      (Windows: `~/.<name>/snapshots/<ver>/` + a `payload-dir`/`stamped-version`
      marker; POSIX records a `payload-dir` pointer) and deploy the
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
transport scripts. The contained test runner adds detection-only defense in
depth: it snapshots the real User and Machine registry environment keys and
fails the test flow if drift is observed. It never rolls back the whole key,
because doing so could overwrite a legitimate concurrent edit.

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
copy, never the whole (possibly-wedged) install. Guards:
- `COPILOT_PLUGIN_INSTALL_STAGED=1` prevents a re-exec loop; the stage path (not under
  `installed-plugins`) is a second guard.
- `COPILOT_PLUGIN_STAGED_FROM=<real payload path>` preserves marketplace detection
  (see [Source](#source--where-the-installer-runs-from-no-flag)).
- **Reap is pid-guarded:** a sibling stage dir is removed only if its owner pid (the
  `<ts>-<pid>` suffix) is **dead** — a concurrent or stalled installer's dir is never
  touched. *A stalled install must never block another copy.*

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

### Completion marker — no corpse reuse, clean retry

Completion was inferred only from the runtime-root `deploy-manifest.json` /
`running-version.json` + the active-version marker. A killed/crashed build left a
**half-built `versions/<v>` slot** on disk that the next `uv venv --allow-existing`
could silently reuse.

Fix — a per-slot **`.install-complete.json`** marker, written **atomically right after
the slot passes its isolated health gate** (so "marker present" == "healthy, complete
build"), owned by the shared `versioned_runtime.py`:
- `mark-complete <v>` / `is-complete <v>` (+ optional `--payload-hash` to force a
  rebuild when a dev-checkout changed the payload without bumping the version).
- `slot <v> --clean-incomplete` tosses an **incomplete** slot before building (never
  the current/active slot); `toss-incomplete` / `gc --toss-incomplete` reap markerless
  non-current slots.

Because **activate (marker publish + binstub re-point) runs only after the health
gate + marker**, a
watchdog-killed build never becomes the live version — the old daemon keeps serving,
and the markerless corpse is tossed + rebuilt on the next run (automatic retry).

> **Test it:** `tools/test-install-flow.ps1 -Plugin <name>` (Windows) and
> `bash tools/test-install-flow.sh --plugin <name>` (Linux/WSL) are the turn-key
> mini end-to-ends that assert all of the above in an isolated sandbox (STAGED,
> NOT-IN-PAYLOAD, PAYLOAD-FREE-during-install, MARKETPLACE-preserved, NO-COLLISION,
> WATCHDOG whole-tree kill, MARKER/TOSS, NO-ORPHANS, BOUNDED) — via a
> `COPILOT_PLUGIN_INSTALL_SMOKE` seam, without a heavy venv build.

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

So every Python runtime plugin **self-reconciles at session start**: it declares
a `sessionStart` hook that re-runs its own installer **only when the deployed
version drifts** from the payload.

- `plugin.json` sets `"hooks": "hooks.json"`.
- `hooks.json` `hooks.sessionStart` runs the plugin's `scripts/bootstrap-check.{ps1,sh}`
  — either the copy the installer deploys to `~/.<plugin>/bin/`, or (the
  self-locating variant) the one shipped in the plugin payload's `scripts/` dir.
- `scripts/bootstrap-check.{ps1,sh}` is a **version-gated reconcile**: it compares
  the deployed version (`~/.<plugin>/deploy-manifest.json` → `source.version`) to
  the payload (`pyproject.toml`) and, on drift (or a missing venv), re-runs the
  plugin's canonical installer (`init.*`, or `install.* install`) **in the
  background** — the atomic versioned-venv swap keeps concurrent use safe, and
  backgrounding keeps session start non-blocking.
- A namespaced Agent Machines cell uses the schema-4 distinction defined above:
  bootstrap compares the loaded payload only with reconciled `source`
  provenance, validates `runtime` against the cell-local marker/interpreter, and
  does not treat an intentionally selected historical runtime as payload drift.
- A separate `sessionStart` command hook runs
  `scripts/emit-command-catalog.{ps1,sh}` from `COPILOT_PLUGIN_ROOT` and emits
  exact payload-local `argv` plus `availability`. It never snapshots dynamic
  runtime state or falls through to the legacy global wrapper.
- Roster-wide tests run every runtime `agent-*` plugin's cheap stamp in an
  isolated profile with no installation-mode policy and require all declared
  commands to have a `~/.local/bin` fallback. This protects legacy/default
  operation while namespaced rollout is still opt-in.

This reconciles the **tool**, never machine state or config. First install remains
the one-time setup step; the hook only keeps an installed runtime current.

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
- each Python runtime wires the **session-start reconcile hook** above
  (`plugin.json` `hooks` → a `sessionStart` `bootstrap-check`), unless its
  payload manifest explicitly sets `sessionStartBootstrap: false` for a
  repository-gated, explicit-first-use runtime whose session start is required
  to remain non-mutating. Plugins predating
  the invariant are listed in `EXEMPT_SESSION_HOOK` (tracked in dotfiles#779) —
  new runtime plugins must comply, not be added to that set.
- every plugin PowerShell script routes persistent User/Machine environment
  access through the shared contained-test adapter; direct access is rejected.

Wire it as a `pre-push` hook (see `tools/hooks/pre-push`, which also runs
`tools/check-no-internal-identifiers.py` — a repo-wide guard that fails the push
if any privately-configured internal identifier leaks into the tree; it no-ops
unless a denylist is configured, see the agent-codespaces README "Local
identifier guard").
