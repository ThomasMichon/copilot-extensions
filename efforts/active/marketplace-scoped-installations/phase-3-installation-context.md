# Phase 3 — Installation Context and Dual-Cell Exemplars

[Effort](README.md) · [Architecture](design.md) ·
[Implementation issue #1104](https://github.com/ThomasMichon/copilot-extensions/issues/1104)

## Status

**Non-operative foundation.** The reviewed contract and cross-platform resolver
foundation are in place before the installation root becomes operative. Phase 2
payload-local shims continue using legacy runtime roots.

## Goals

- Resolve one stable marketplace installation cell before a plugin runtime or
  private Python toolchain exists.
- Keep the installation-context primitive self-contained and vendorable inside
  each independently installed plugin payload.
- Carry the same identity through payload launch, self-stage, snapshot,
  provision, runtime dispatch, service launch, update, rollback, repair,
  remote execution, and uninstall.
- Make a missing or conflicting context fail closed instead of selecting a
  legacy root or same-named command.
- Prove the contract with two simultaneous cells containing the same plugin name
  and version on Windows and Linux/WSL.

## Non-goals

- Migrating existing `~/.agent-*` state. Phase 6 owns attribution and migration.
- Removing compatibility global binstubs. Phase 2 retires them after attributable
  project and management entry points exist.
- Converting every service, venue, or repository registry. The exemplars prove
  the primitive; Phases 4–5 roll it out.
- Treating ownership receipts as a cryptographic trust boundary. They provide
  attributable mutation ownership and collision detection, not protection from
  a malicious process running as the same user.
- Requiring a central daemon or global mutable registry to resolve a cell.

## Terminology

- **Marketplace locator** — weak host-local evidence that points toward
  provenance: an installed marketplace key within one Copilot home, or the
  canonical root of a directory marketplace. A locator helps find source
  declarations; it is not installation identity.
- **Portable source identity** — the normalized, globally distinguishing
  marketplace source. It is required before durable cell state is created and
  is what remote propagation carries.
- **Source fingerprint** — the full SHA-256 digest of the normalized portable
  source identity.
- **Marketplace id** — a filesystem-safe, stable id derived from the source
  fingerprint, with a readable marketplace key used only as a prefix. It selects
  one directory beneath the durable copilot-extensions home.
- **Plugin installation** — `(marketplace-id, plugin-id)`. Runtime versions are
  immutable slots inside this installation.
- **Installation context** — the validated, host-specific paths and identity
  used by one operative process.

## Bootstrap provenance boundary

The plugin runtime receives its own payload root, but the Copilot CLI currently
does not expose a machine-readable marketplace-source descriptor to the child.
`copilot plugin marketplace list` displays registered sources for a human but
has no JSON form. A first-use shim also cannot assume Python, `jq`, a sibling
plugin, or agent-worktrees already exists.

The first evidence available on both supported platforms is the payload
boundary:

```text
<copilot-home>/installed-plugins/<marketplace-key>/<plugin-id>/
```

The key is a **locator, not identity**. User settings and multiple repositories
can declare the same key with different source definitions while sharing one
`installed-plugins/<key>` directory. The resolver uses the key to find the
effective user/repository declarations but never mints durable state from the
key alone.

Installed-payload locator evidence has this shape:

```text
{
  "kind": "installed",
  "copilotHome": "<canonical Copilot home>",
  "marketplaceKey": "<configured key>"
}
```

A directory-marketplace locator has this shape:

```text
{
  "kind": "directory",
  "marketplaceRoot": "<canonical absolute root>",
  "marketplaceKey": "<optional configured key>"
}
```

The dependency-light resolver reads the user and current-project marketplace
declarations identified by that locator and normalizes the source. If the same
key has conflicting declarations, provenance is ambiguous and resolution fails.
It does not choose whichever declaration happened to be read last.

POSIX bootstrap may use a vendored dependency-free settings reader or a private,
payload-keyed temporary toolchain that is removed after resolution. That
bootstrap scratch space is not a cell, contains no plugin state, and cannot be
used as a runtime-root fallback. The implementation choice must preserve the
same source-normalization fixtures as PowerShell and Python.

If no source declaration can be resolved, first use stops with an actionable
provenance error. It does not create a provisional cell. This is a deliberate
fail-closed response to the current host-runtime metadata gap; an explicit
context supplied by management remains available for out-of-session use.

An explicit caller may supply an opaque globally unique source descriptor. A
staged payload carries the source descriptor of its origin; its temporary path
never becomes identity.

When a source fingerprint already owns a cell under another key or prior
locator, resolution reports that cell and requires an explicit rebind. Creating
a parallel cell for the same source requires an explicit `new cell` management
intent. Conversely, a key remapped to another source never adopts the prior
cell; it produces a different source-derived id.

## Canonical ids

The hash input is a field-ordered, length-prefixed UTF-8 record, not serialized
JSON. This avoids key-order, escaping, newline, and BOM differences between
shell, PowerShell, and Python. A conceptual GitHub record is:

```text
version:1:1
kind:6:github
source:23:github:owner/repository
ref:0:
```

Each value is preceded by its UTF-8 byte length. Writers use LF and UTF-8
without BOM. Source-kind normalization strips credentials, normalizes scheme and
host case, removes a trailing `.git` where the provider treats it as
equivalent, and preserves provider-significant path/ref case. Git URL paths use
URI-escaped bytes, decode only percent-encoded ASCII unreserved characters,
uppercase retained escapes, treat backslashes as path separators, collapse
leading separators and dot segments, preserve interior empty path segments, and
remove repeated trailing separators. Source URLs reject control characters and
hosts outside the canonical DNS/IPv4/IPv6 spelling accepted by the primitive.
Explicit non-HTTP(S) ports remain identity-significant; an explicit default
HTTP/HTTPS port is omitted after numeric normalization.

The id is:

```text
<readable-slug>--<first-16-hex-of-source-sha256>
```

The readable slug comes from the configured key when available, otherwise the
source name or `marketplace`. It is display-only. The normalized source record
and complete digest are stored in `namespace.json`; a truncated-id collision
whose full digests differ fails closed.

For directory marketplaces, canonical physical paths form the portable source
identity unless the caller supplies an explicit stable source id. Windows and
WSL spellings of the same physical directory therefore create distinct cells.
Even with the same explicit source identity, their activation environments,
policy homes, receipts, and runtime roots remain separate; cross-environment
root sharing requires a future explicit contract and is never inferred from
path similarity.

The digest implementation is identical on Windows and POSIX. POSIX tries
`sha256sum`, `shasum -a 256`, then `openssl dgst -sha256`; PowerShell uses the
platform SHA-256 API. If no digest implementation is available, resolution fails
and never falls back to a legacy root.

## Durable layout

```text
~/.copilot-extensions/
  marketplaces/
    .locks/
      <marketplace-id>.genesis/
    <marketplace-id>/
      namespace.json
      .locks/
        <plugin-id>.install.lock
      plugins/
        <plugin-id>/
          install.json
          deploy-manifest.json
          launchers/
          versions/<version>/
          snapshots/<version>/
          current-version
          last-known-good
          state/
          run/
          logs/
          cache/
      repos/
        <stable-repo-id>/
          identity.json
          <plugin-id>/
```

There is no generic `bin/` directory in the cell. Agent-facing commands remain
in the immutable marketplace payload. A plugin may deploy an installation-local
launcher for services and other out-of-session callers beneath its own plugin
root, but that launcher is never found through ambient `PATH`.

## Receipts

### `namespace.json`

```json
{
  "schema": "copilot-extensions.marketplace-namespace",
  "version": 1,
  "marketplaceId": "example--0123456789abcdef",
  "source": {
    "kind": "github",
    "canonical": "github:owner/repository",
    "fingerprint": "sha256:<full digest>"
  },
  "locators": [{
    "kind": "installed",
    "copilotHome": "<canonical path>",
    "marketplaceKey": "example",
    "declaredIn": "user|project:<canonical path>"
  }],
  "generation": 1,
  "state": "active",
  "createdAt": "<RFC3339>",
  "updatedAt": "<RFC3339>"
}
```

The source is always resolved before the receipt is created. Locator history is
bounded but identity-preserving: a moved Copilot home, renamed key, or relocated
directory can rebind to an existing cell only after the full source fingerprint
matches. Conflicting same-key declarations are recorded in diagnostics, not
adopted into the receipt.

### `install.json`

```json
{
  "schema": "copilot-extensions.plugin-installation",
  "version": 1,
  "marketplaceId": "example--0123456789abcdef",
  "pluginId": "agent-machines",
  "pluginRoot": "<absolute cell plugin root>",
  "namespaceReceipt": "<absolute namespace.json path>",
  "payload": {
    "root": "<current immutable payload root>",
    "version": "<plugin version>",
    "origin": "<installed|directory|staged|explicit>",
    "originReceipt": "<optional staged provenance sidecar>"
  },
  "roots": {
    "versions": "versions",
    "snapshots": "snapshots",
    "state": "state",
    "run": "run",
    "logs": "logs",
    "cache": "cache",
    "launchers": "launchers"
  },
  "generation": 1,
  "state": "active",
  "createdAt": "<RFC3339>",
  "updatedAt": "<RFC3339>"
}
```

Paths under `roots` are relative to `pluginRoot`. Readers reject absolute or
escaping values. The absolute payload root is expected to change after a
marketplace update; changing it is allowed only when the invoking payload
resolves to the same marketplace and plugin identity.

Both receipts are written atomically with UTF-8-no-BOM and LF. Cell genesis uses
an atomic claim beneath `marketplaces/.locks/` before the cell directory exists.
All implementations use the same lock-directory protocol and liveness receipt;
the `.locks` roots are removed only by explicit namespace garbage collection.

Readers accept one strict JSON language on every platform: UTF-8 without BOM,
case-sensitive and non-duplicated object names, no unescaped control characters,
and string-typed identity, path, payload-version, locator, and manifest fields. Portable
plugin ids also reject Windows device basenames on every platform.

Every mutation validates the receipt while holding the corresponding lock and
holds that lock through the complete mutation. Receipt updates use a monotonic
`generation` compare-and-swap; a writer whose observed generation changed must
restart resolution rather than overwrite it.

## Resolved installation context

The canonical library returns an immutable value equivalent to:

```text
marketplace_id
marketplace_slot
source_fingerprint?
plugin_id
payload_root
cell_root
plugin_root
versions_root
snapshots_root
state_root
run_root
logs_root
cache_root
repos_root
namespace_receipt
install_receipt
```

Only one inherited environment variable points at context:

```text
COPILOT_EXTENSIONS_CONTEXT=<absolute path to install.json>
```

The pointer is not self-attesting. Readers require the receipt at exactly
`<durable-home>/marketplaces/<marketplace-id>/plugins/<plugin-id>/install.json`;
validate its namespace receipt inside the same cell; recompute the source
fingerprint and marketplace id from `namespace.json`; reject escaping roots; and
compare the expected marketplace/plugin identity supplied by the launcher.
Payload shims additionally require the current payload root to match. Service
launchers pin marketplace id, plugin id, context path, and immutable version slot
in their launch arguments.

Child launchers reconstruct and validate the context from that receipt, then
replace conflicting **legacy runtime-root** variables with compatibility values
derived from the validated context. A host-supplied `COPILOT_PLUGIN_ROOT` is
never rewritten: payload entry points validate it against their own immutable
location and fail on mismatch.

Remote execution serializes the normalized portable source identity, source
fingerprint, marketplace id, plugin id, and expected version/API contract.
Absolute local paths and host-local locators are never serialized as identity.
The destination resolves or explicitly adopts its matching local cell; unresolved
source provenance fails before launch.

## Resolution precedence

Given an expected payload root and plugin id, invalid evidence at a higher tier
never falls through to a lower tier.

1. **Explicit receipt pointer** — load `COPILOT_EXTENSIONS_CONTEXT` or an
   explicit installer argument; validate its canonical location, source-derived
   id, namespace, plugin, expected payload/launcher identity, generation, and
   every derived root.
2. **Staged provenance** — load the generated provenance sidecar copied beside a
   staged payload; independently confirm its source fingerprint against an
   existing namespace or explicit management context. The stage path is never
   identity.
3. **Installed payload boundary plus declarations** — recognize
   `<copilot-home>/installed-plugins/<marketplace-key>/<plugin-id>`, then resolve
   that key's user/current-project source declarations. Conflicting or missing
   declarations fail.
4. **Directory marketplace** — walk upward to a recognized marketplace catalog,
   verify that its plugin entry resolves to the expected payload, and normalize
   the directory source or explicit stable source id.
5. **Explicit development mode** — accept a caller-supplied marketplace source.
   A bare checkout is not guessed from its git remote.
6. **Existing-cell rebind** — scan the bounded
   `marketplaces/*/namespace.json` receipts for the full source fingerprint and
   require explicit rebind or `new cell` intent when the locator/key changed.
7. **Declared legacy compatibility** — a payload-invocation manifest still in
   legacy mode may resolve its declared legacy root. This path is explicit,
   remains isolated from the new receipt resolver, and is never used after
   higher-precedence evidence is present but invalid.
8. **Fail ambiguous** — print the payload and attempted evidence; do not use
   `~/.agent-*`, `PATH`, or another cell.

A conflicting explicit receipt is an error; resolution never falls through to a
lower-precedence source after finding invalid higher-precedence evidence.

## Primitive packaging

Add a canonical `libs/installation-context/` primitive. The implementation
provides management/runtime APIs plus dependency-light POSIX and PowerShell
bootstrap entry points. A shared fixture corpus proves source normalization,
hash input, ids, receipt validation, and paths are identical across
implementations.

Each runtime plugin vendors the files it needs. No installed plugin imports the
library from another plugin or from a source checkout. Synchronization follows
the existing versioned-runtime vendoring model.

Payload-invocation schema v2 is additive: v1 manifests and generated files remain
valid during Phase 2. V2 renames the compatibility field to
`legacyRuntimeRoot` and adds `installationContext: "legacy" | "required"`.
Generated required-context shims pass the resolved `pluginRoot` to the unchanged
versioned-runtime resolver. Enforcement flips only after later rollout issues
convert every manifest.

## Lifecycle invariants

### Stamp and snapshot

1. Resolve or create the namespace receipt under the genesis claim.
2. Resolve or create the plugin installation receipt under its installation
   lock.
3. Snapshot the payload together with a generated provenance sidecar that names
   the origin slot and receipt.
4. Write `payload-dir` and `stamped-version` only beneath the resolved plugin
   root.

The self-stage prologue preserves the original payload evidence through
`COPILOT_PLUGIN_STAGED_FROM`; the throwaway install-stage directory never
becomes provenance. Phase 3 does not change the byte-identical v4 self-stage
prologue or its legacy temporary location; that path remains an annotated
compatibility seam until the shared installer phase moves it beneath the cell.

The provenance sidecar is written by the staging producer after it has resolved
source identity. It contains the normalized source, full fingerprint,
marketplace/plugin ids, originating payload, and namespace receipt. A consumer
accepts it only when the fingerprint independently matches an existing
namespace or explicit management context. If the original payload has already
been replaced, the receipt/fingerprint remains the confirmation source; the
temporary sidecar alone is never trusted.

The canonical sidecar is
`<snapshotsRoot>/<snapshot-id>/snapshot-provenance.json`. `snapshot-stamp`
publishes it only while holding the marketplace genesis lock and then the plugin
installation lock, after the producer has materialized a non-empty snapshot
directory at that exact cell-local root. Publication writes only the sidecar;
it does not create the snapshot root or accept a sidecar-only snapshot. The
immutable version 1 record carries:

- normalized source kind/canonical/ref plus the full fingerprint;
- exact marketplace and plugin ids;
- the originating payload root, version, origin, and nullable origin receipt;
- the canonical snapshot id/root;
- canonical namespace/install receipt paths and both pinned generations; and
- one RFC3339 UTC creation timestamp.

`snapshot-validate` receives an explicit canonical `install.json`, expected
marketplace/plugin ids, and snapshot id. It revalidates both receipts, recomputes
the source fingerprint and marketplace id, compares all payload and receipt
identity, requires active receipts, and rejects a sidecar after either receipt
generation changes. It does not require the original payload bytes to remain at
the recorded path after staging; the canonical receipt chain remains authority.
Both publication and validation require at least one non-sidecar snapshot entry
to remain present; this enforces operation ordering without asserting content
integrity. An existing malformed or conflicting sidecar is never overwritten.

The sidecar inherits the receipt threat model above. It detects accidental
cross-cell copying, stale generations, and ambiguous ownership; it does not
cryptographically prove which same-user process produced either the snapshot
contents or the record. Provisioning therefore validates this receipt chain and
separately owns any content-integrity guarantee.

Canonical receipt validation also pins the physical ownership chain after the
durable home: `marketplaces`, the marketplace cell, `plugins`, and the plugin
root are exact direct children and may not traverse a symbolic link, junction,
or reparse point. The canonical `namespace.json` and `install.json` receipt
leaves must also be ordinary files rather than links or reparse points.

Both actions return `operative: false`. This slice creates no version slot,
marker, activation, migration, tombstone, launcher, cutover, rollback, or
uninstall behavior.

### Provision and cutover

- A snapshot installer validates its sidecar against `install.json` before
  building a version.
- The Python, dependency-light Bash, and PowerShell runners expose explicit
  `slot-provision` and `slot-validate` transactions. Provisioning revalidates
  the receipt chain under the genesis and installation locks, then exclusively
  publishes only
  `<versionsRoot>/<runtime-version>/.runtime-slot-ownership.json`.
- The ownership marker pins marketplace, plugin, source fingerprint, runtime
  version/root, snapshot id/root/provenance and provenance digest, canonical
  receipt paths, and namespace/install generations. Matching ownership is idempotent; markerless,
  malformed, copied, linked, stale, or conflicting slots fail without
  replacement.
- New publication requires current active snapshot provenance. Existing slot
  validation continues across later receipt generations by matching the
  immutable snapshot and stable cell identity while rejecting generation
  regression, so update and state transitions do not strand rollback slots.
- Python and PowerShell publication use a reserved hidden sibling outside
  `versionsRoot` and an OS-native atomic no-replace rename. Interrupted hidden siblings
  are inert, remain outside canonical slot enumeration, and require explicit
  reconciliation. Bash uses atomic final-slot `mkdir` reservation followed by
  no-replace hard-link marker publication from within the reserved slot.
  Ordinary failures remove their still-empty owned reservation; an interruption
  between those steps leaves a markerless slot that remains untouched and fails
  closed pending explicit repair/release.
- Results distinguish attributable ownership from lifecycle readiness with
  `namespaceState`, `installState`, and `slotEmpty`. Runtime versions remain
  immutable build identities; conflicting slots await a separate explicit
  repair/release transaction rather than automatic reclamation.
- Slot ownership remains non-activating. Agent Machines and Agent Index expose
  explicit installer adapter actions that supply their fixed plugin identity
  plus exact payload root and current payload version to the parity-proven
  primitive, but no normal install or bootstrap path calls them. The snapshot
  must match those payload expectations under the receipt locks. The adapters
  derive the root from the executing plugin payload rather than ambient
  self-stage metadata. The actions do
  not write payloads,
  completion/current/LKG markers, activation receipts, launchers, services,
  state, or tombstones.
- Versioned-runtime receives the resolved `pluginRoot` explicitly.
- `current-version`, `last-known-good`, process checks, and garbage collection
  never enumerate outside that plugin root.
- Service/task/unit launchers pin the context receipt and immutable version
  slot.

### Update, rollback, repair, and uninstall

- Update may replace the payload root only within the same validated cell.
- Rollback selects only a slot owned by the same `install.json`.
- Repair recreates only artifacts whose ownership receipt matches.
- Uninstall validates the namespace and plugin receipt immediately before every
  destructive step. It never removes the namespace or repo state merely because
  the last payload is absent.
- Namespace garbage collection is a separate explicit management operation and
  acts only on attributable, inactive cells.
- Existing installations do not switch to cell mode merely because an exemplar
  ships it. Cell activation follows the normative
  [installation-mode contract](../../../docs/install-contract.md#installation-mode-governance).
  An exemplar reports existing unattributed legacy state as
  `migration-required` and remains on its legacy root until two-lock migration
  publishes a legacy ownership tombstone and valid activation receipt.
- Before an exemplar becomes operative, every one of its legacy installer and
  bootstrap entrypoints calls the shared activation probe and refuses mutation
  for namespaced-active, orphaned-transfer, or applicable maintenance.

## Exemplars

### CLI-only: agent-machines

agent-machines already has generated payload-local commands and command catalogs.
It proves:

- first-use context resolution with no service or sibling dependency;
- two same-name, same-version payloads dispatch to different version roots;
- independent stamp, provision, current-version, rollback, cache, and uninstall;
- exact Windows/PowerShell/CMD and POSIX argument/exit-code parity.

Before activation, bootstrap-check and agent-worktrees reconciliation gain
cell-aware deployed-version lookup. A converted exemplar must not appear
perpetually missing, recreate its legacy root, or trigger install on every
session. This compatibility prerequisite coordinates with #1105 without moving
agent-worktrees' own project state in Phase 3.

Its pre-activation adapter requires an explicit context receipt and expected
marketplace id, then reserves or validates only the payload version's empty
owned slot after matching snapshot provenance to the exact installer payload
root and version. Ambient context is not authorization, and this adapter does
not complete the operative exemplar slice.

### Service-bearing: agent-index

agent-index was the first payload-invocation pilot and has strong clean-room and
cutover coverage. It proves:

- durable index state remains separate from immutable runtime versions but
  inside the owning cell;
- service, engine, task/unit, endpoint, lock, log, and cache identities are
  cell-scoped;
- two same-name services run concurrently without fixed-name or endpoint
  collisions;
- updates and rollbacks affect only their own service and routing records;
- a stale or mismatched context cannot start, stop, repair, or uninstall the
  other cell.

The agent-index conversion is a reference implementation for Phase 4 service
rollout, not permission to convert unrelated services in the same PR.
It does not alter the Phase 2 payload-command contract. Its cell-scoped service
identity is the reference implementation that #1108 generalizes.

Its pre-activation adapter has the same explicit authorization and empty-slot
boundary as Agent Machines, including exact snapshot payload root/version
matching. It does not build the runtime or mutate service, engine, task/unit,
endpoint, current/LKG, or activation identity.

## Delivery slices

1. **Contract and fixture corpus** — land this proposal and the normative
   policy, activation, tombstone, resolver, and effective-mode contract in
   [`docs/install-contract.md`](../../../docs/install-contract.md#installation-mode-governance);
   no runtime behavior changes.
2. **Primitive foundation** — add the canonical Python/POSIX/PowerShell
   implementations and unit tests; bounded receipt mutations do not make
   payload shims leave legacy roots.
3. **Cell-aware reconciliation prerequisite** — teach bootstrap checks and the
   agent-worktrees reconciler to recognize a context-selected deploy manifest
   without migrating agent-worktrees project state.
4. **Activation governance prerequisite** — implement the shared default-off
   desired/actual resolver, legacy-entrypoint probe, and explicit
   generation-pinned activation CAS from the
   [install contract](../../../docs/install-contract.md#installation-mode-governance)
   without automatically changing a runtime root.
   A non-operative adapter sub-slice may wire both exemplars to
   `slot-provision` / `slot-validate` before their separate operative
   conversions; this does not complete slices 5 or 6.
5. **agent-machines exemplar** — make installation context explicitly operative
   for one CLI-only runtime and add dual-cell install/update/rollback tests.
6. **agent-index exemplar** — namespace its service and durable state and add
   concurrent dual-cell clean-room coverage.
7. **Contract enforcement** — make new payload-invocation manifests require
   installation context; retain explicit compatibility annotations only for
   plugins assigned to later rollout issues.

Every operative slice includes Windows and Linux/WSL validation on the same
receipt fixtures. A platform implementation does not become mandatory while the
other platform still derives a different marketplace id or root.

## Acceptance matrix

| Scenario | Required result |
|----------|-----------------|
| Same plugin/version, two installed marketplace keys | Distinct ids, roots, locks, state, and commands |
| Same plugin/version, installed plus directory marketplace | Distinct cells; neither claims the other |
| Same marketplace key, source fingerprint changes | Conflict requiring explicit transfer or new key |
| Same key declared by two repositories with different sources | Ambiguous; no cell is created or adopted |
| Same source registered under another key/locator | Existing cell reported; explicit rebind or new-cell intent required |
| Staged payload | Resolves origin cell, never temporary stage path |
| Digest implementation unavailable | Fails before creating durable state |
| Current marker missing/corrupt | Fallback remains within the same plugin root |
| Context points at another plugin/cell | Fails before provisioning or launch |
| Context variable inherited into another cell | Canonical-path and expected-identity checks reject it |
| Concurrent first use in both cells | One provision per cell; no cross-cell lock contention |
| Concurrent first use in the same new cell | One genesis and one untorn receipt |
| One cell updates or rolls back | Other runtime/service remains unchanged and available |
| Receipt changes during mutation | Generation check restarts; stale writer cannot overwrite |
| Service runs while receipt/payload updates | Service keeps its pinned immutable slot |
| Payload disappears | Cell remains attributable and inert |
| Uninstall with mismatched receipt | Refuses every destructive action |
| Windows payload replacement during command | No retained payload CWD/handle blocks replacement |
| Remote context propagation | Identity preserved; destination paths resolved locally |
| Converted exemplar with legacy state | Remains legacy or reports migration; never silently appears empty |
| Converted exemplar with legacy reconciler | No repeated installs or legacy-root recreation |
| Windows and WSL see one directory marketplace | Separate policy, receipts, cells, and roots; neither validates or operates the other |

## Open questions resolved by implementation review

- Exact command names for context inspection and explicit cell transfer.
- The installation-local launcher filename used by service supervisors.
- Retention policy for snapshots and inactive version slots after both exemplars
  prove independent garbage collection.

These choices do not change the identity, ownership, layout, or fail-closed
contract above.
