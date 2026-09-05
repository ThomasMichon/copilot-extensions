# Phase 3 — Installation Context and Dual-Cell Exemplars

[Effort](README.md) · [Architecture](design.md) ·
[Implementation issue #1104](https://github.com/ThomasMichon/copilot-extensions/issues/1104)

## Status

**Command-only and service-bearing exemplars operative on the accepted Linux
arm; repair/release and uninstall design accepted, implementation pending.**
The reviewed contract, cross-platform resolver, immutable runtime-slot
ownership/completion/cutover, Agent Machines payload/runtime flow, and Agent
Index service/runtime implementation are in place. Each path may use only its
own already-active validated cell; absent/false policy remains legacy. Agent
Index passed its full Linux lifecycle. Windows clean-room arms are waived for
this effort; deterministic PowerShell parity remains required. Issue
[#2122](https://github.com/ThomasMichon/copilot-extensions/issues/2122) carries
the remaining Agent Machines repair/release and uninstall implementation.

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
- Prove the contract with two simultaneous Linux/WSL cells containing the same
  plugin name and version, and preserve deterministic PowerShell parity for the
  waived Windows clean-room arm.

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
- The Python, dependency-light Bash, and PowerShell runners also expose explicit
  `slot-complete` and read-only `slot-completion-validate` transactions. Both
  require a caller-supplied context, expected marketplace/plugin ids, exact
  expected payload root/version, snapshot id, and runtime version; ambient
  context never authorizes either action.
- Completion accepts only the existing versioned-runtime
  `<slot>/.install-complete.json` evidence as an ordinary non-link file with
  exactly four fields: `version`, `completed_at`, `pid`, and `payload_hash`.
  The runtime version must match exactly, the completion time is a valid
  second-precision UTC RFC3339 timestamp, the pid is a JSON integer from `0`
  through `9223372036854775807`, and the payload hash is lowercase 64-hex equal
  to the independently computed snapshot content digest. Booleans, fractions,
  exponent-form numbers, negative values, and overflow values are rejected.
  Malformed evidence is preserved and rejected.
- The snapshot content digest recursively covers every regular file, including
  dotfiles, without following links or reparse points. Relative paths use `/`
  and are ordered by UTF-8 bytes. The only exclusion is the root
  `snapshot-provenance.json`; nested files with that name remain content. Each
  file contributes
  `F\0<UTF-8 relative path>\0<lowercase file SHA-256>\n`; empty directories do
  not contribute. Links, reparse points, pipes, devices, sockets, and other
  non-regular entries fail closed.
- Digest traversal permits at most 100,000 non-root entries, 4,096 UTF-8 bytes
  per relative path, and 4,294,967,296 total regular-file bytes, with inclusive
  boundaries. Files and directories both count. The excluded root
  `snapshot-provenance.json` also counts toward all three limits; only its
  digest record is omitted. Sorting is O(n log n) by exact UTF-8 bytes and does
  not buffer file contents.
- Every digest pass records each directory identity, mutation-relevant metadata,
  and exact entry-name/type membership before hashing and requires the same
  state afterward. First completion publication then recomputes the complete
  digest immediately before no-replace publication and requires equality.
- Under the genesis lock and then the plugin installation lock, first
  publication revalidates the current active receipt generations, exact current
  snapshot provenance, immutable slot ownership, and snapshot contents. It
  captures one complete strict build receipt and validates its payload hash
  before it
  no-replace publishes
  `<slot>/.runtime-slot-completion.json` with schema
  `copilot.extensions/runtime-slot-completion/v1`, binding marketplace/plugin
  and source identity, canonical runtime root/version, snapshot provenance and
  digest plus content digest, ownership marker and digest, historical build
  receipt path/digest, validated payload digest and pid, pinned
  namespace/install receipt paths and generations, and the build's own
  `completed_at` value.
- The completion receipt is deterministic across runners and immutable.
  Matching replay returns `created: false`; malformed, copied, or conflicting
  receipts are preserved and fail closed. Python and Bash use a same-directory
  temporary file plus a no-replace hard link; PowerShell uses a same-volume
  no-overwrite file move. Every runner reasserts both lock owners immediately
  before publication.
- Read-only completion validation and idempotent `slot-complete` replay accept
  monotonic namespace/install generation advancement so an older immutable slot
  remains attributable for later rollback, but reject generation regression,
  foreign ownership or completion receipts, linked/reparse snapshot content,
  snapshot mutation, and any identity mismatch. Once the completion marker
  exists, neither path requires, reads, nor compares the current
  `.install-complete.json`; legacy evidence crosses a one-time capture boundary
  at first publication. Later rewrite or removal is irrelevant, and an atomic
  replacement after capture cannot poison publication. New publication remains
  stricter and requires current active receipts and snapshot provenance.
- Immutable completion-marker reads require the named file identity and
  metadata to remain stable through capture. Concurrent atomic replacement
  fails closed without the reader replacing the observed marker.
- Completion is still non-activating and non-operative. The transaction writes
  no `current-version`, `last-known-good`, activation, launcher, service, state,
  endpoint, task/unit, or tombstone artifact.
- The Python, dependency-light Bash, and PowerShell runners expose a separate
  explicit `slot-cutover` transaction. It requires the same exact context,
  marketplace/plugin, payload root/version, snapshot, and runtime identity as
  completion plus caller-observed namespace/install generations and exactly one
  current-marker CAS expectation: an exact current version or marker absence.
- Under the genesis lock and then plugin installation lock, cutover revalidates
  the current receipt generations and immutable target completion, validates
  existing `current-version` and `last-known-good` as ordinary non-link
  single-version files, and re-reads both immediately before mutation.
  Generation or current-marker drift returns `revalidation-required` without
  mutation.
- `last-known-good` retains its established versioned-runtime meaning: the last
  version successfully selected when `current-version` cannot resolve, not a
  rollback pointer. Initial install, update, and explicit historical rollback
  publish the completed target to both markers; a target already named by both
  markers is an idempotent no-op. Each changed marker is atomically replaced under both
  ownership locks and revalidated after publication. A caller that observes a
  partial failure revalidates the markers and retries with their new explicit
  current expectation before activation.
- `slot-cutover` writes no activation receipt, launcher, service, manifest,
  payload, state, endpoint, task/unit, repair, release, garbage-collection, or
  uninstall artifact. Health qualification, activation publication, operative
  exemplar adoption, repair/release, and uninstall remain separate lifecycle
  boundaries.
- Versioned-runtime receives the resolved `pluginRoot` explicitly.
- `current-version`, `last-known-good`, process checks, and garbage collection
  never enumerate outside that plugin root.
- Service/task/unit launchers pin the context receipt and immutable version
  slot.

### Update, rollback, repair, and uninstall

- Update may replace the payload root only within the same validated cell.
- Rollback explicitly selects only a completed historical slot owned by the
  same `install.json` and compare-and-swaps from the observed current version.
  After selection, both runtime markers name that rollback target.
- Interrupted runtime reservations become releasable only through a new
  `.runtime-slot-reservation.json` receipt. Python and PowerShell publish it in
  the hidden sibling before the no-replace rename; Bash publishes it as the
  first entry immediately after the final-slot `mkdir` reservation. The Bash
  directory may therefore be filesystem-visible briefly, but it remains inert
  and ineligible for attributable enumeration or reconciliation until the
  receipt is durably published. The receipt mirrors the immutable ownership
  identity: marketplace/plugin/source identity, canonical runtime root/version,
  snapshot id/root/provenance digest, canonical namespace/install receipt paths
  and generations, a non-negative reservation generation, and an RFC3339 UTC
  creation time.
- Successful ownership publication replaces the reservation receipt with
  `.runtime-slot-ownership.json`. Existing markerless hidden or final
  reservations remain protected: absence of the reservation receipt is never
  proof of ownership or permission to delete.
- The shared Python, dependency-light Bash, and PowerShell primitives expose
  `slot-release` (the release half of the repair/release lifecycle). It requires
  explicit context, marketplace/plugin ids, runtime version, reservation
  generation, current namespace/install generations, and durable home. Under
  the marketplace genesis lock and plugin install lock it revalidates the
  receipt and target immediately before deletion.
- `slot-release` removes only a slot containing the exact matching reservation
  receipt and no other entries. It refuses completed, selected, last-known-good,
  non-empty, markerless, malformed, linked/reparse, foreign, generation-drifted,
  or receipt-replaced targets. Matching replay after release is idempotent.
- Agent Machines exposes `cell-repair` as an explicit installer action requiring
  caller-supplied context, marketplace id, payload root/version, snapshot id,
  runtime version, receipt generations, and an exact current-selection CAS.
  Ambient context never authorizes repair.
- Repair is derived-only. It may recreate missing or invalid
  `deploy-manifest.json` and Agent Machines cell-local launch metadata only
  from a validated completed slot, snapshot, payload, and receipt chain. It may
  restore `current-version` and `last-known-good` only when the caller supplies
  the exact intended runtime and the current-selection CAS still matches under
  both locks. It never rewrites payload/snapshot contents, ownership or
  completion receipts, state/run/log/cache data, services, tasks, endpoints, or
  external machine resources. A missing immutable completion receipt is a
  refusal, not repairable evidence.
- Agent Machines exposes `cell-uninstall` as an explicit installer action
  requiring caller-supplied context, marketplace id, receipt generations, and
  exact current/LKG expectations. Ambient context never authorizes uninstall.
- Uninstall acquires the same two locks and revalidates `namespace.json`,
  `install.json`, canonical roots, generations, and the exact target immediately
  before every destructive step. It clears current and LKG markers by CAS,
  removes every validated owned runtime slot and snapshot, then removes derived
  deploy/launcher/run/log/cache artifacts.
- Uninstall preserves `state/`, `install.json`, `namespace.json`, and the plugin
  directory structure required to retain attribution. It refuses every foreign,
  malformed, linked/reparse, markerless, non-empty-unattributable, active, or
  otherwise unvalidated artifact rather than partially claiming it.
- Repeated repair returns already healthy; repeated uninstall returns preserved
  when only durable state and receipts remain. Generation or selection drift
  returns revalidation required without deleting the drifted target.
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
marketplace id, then reserves, validates, completes, or validates completion of
only the payload version's owned slot after matching snapshot provenance to the
exact installer payload root and version. Completion reads the build evidence
from that canonical slot. Ambient context is not authorization, and these
adapter actions do not complete the operative exemplar slice.

The operative payload dispatcher evaluates installation-mode governance before
runtime resolution. Absent/false policy preserves the legacy root; an active
validated Agent Machines activation selects only its cell-local runtime.
Requested-only, malformed, foreign, maintenance, orphaned, and stale evidence
never falls back to legacy. First use and bootstrap reconciliation may snapshot,
build, complete, and cut over only after that active proof; neither path writes
activation. A fixed-identity `slot-cutover` adapter selects a completed current
or historical slot under receipt-generation and current-marker CAS, covering
update and rollback without adding repair or uninstall behavior. Its schema-4
deploy manifest records reconciled payload provenance separately from active
runtime selection: historical rollback preserves the former and changes the
latter, so bootstrap leaves the rollback selected until a different payload
provenance requires forward reconciliation. Snapshot publication stages into an
owned sibling and retries may reclaim only a marker-proven, unproven
interruption.

### Service-bearing: agent-index (implementation-ready; acceptance pending)

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

Its pre-activation adapter has the same explicit authorization, ownership, and
completion boundary as Agent Machines, including exact snapshot payload
root/version matching. It does not build the runtime or mutate service, engine,
task/unit, endpoint, current/LKG, or activation identity.

The implementation-ready payload dispatcher applies installation-mode governance
before runtime resolution. An active validated Agent Index cell selects only
its cell-local runtime and qualifies durable state, run/routing, logs, cache,
configuration, launchers, and engine roots beneath that installation. Service
launch uses an installation-local launcher and an OS-assigned loopback endpoint;
it publishes no generic systemd unit, scheduled task, or machine-global command.
Cell provisioning carries snapshot provenance through immutable slot ownership
and completion before marker cutover, and schema-4 deploy manifests keep
reconciled payload provenance separate from the selected runtime so historical
rollback does not masquerade as a payload downgrade.

Marker CAS and manifest publication are joined by an installation-scoped
transaction receipt. Bootstrap/retry can finish a validated target or restore
the prior selection after interruption, including historical rollback.
Governance is rechecked immediately before marker cutover and before service
reconciliation. A passive daemon publishes only its per-instance ownership
receipt until the new route selects it and an exact-instance,
transaction-authorized promotion starts task adoption and publishes active
evidence. Namespaced deploy/recovery is therefore reachable only through the
cell transaction; successful and recovered cutovers reconcile exact owned
instance receipts until one installation PID remains.

The clean-room-only restriction is an operational rollout policy: namespaced
Agent Index lifecycle is exercised only in disposable fixtures until both
acceptance arms pass. It is not a host-detection security boundary, and the
implementation does not rely on a spoofable "clean room" marker.

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
   Non-operative adapter sub-slices may wire both exemplars to
   `slot-provision`, `slot-validate`, `slot-complete`, and
   `slot-completion-validate` before their separate operative conversions; this
   does not complete slices 5 or 6.
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
| Build completion publication | Exact owned slot + current snapshot and generations; immutable deterministic receipt; no activation |
| Historical completion validation after receipt advance | Same immutable slot remains attributable; generation regression fails |
| One cell updates or rolls back | Other runtime/service remains unchanged and available |
| Receipt changes during mutation | Generation check restarts; stale writer cannot overwrite |
| Crash after marker CAS or before/after manifest publication | Transaction retry finishes the validated target or restores the exact prior selection |
| Governance changes before cutover or service reconcile | Completed target stays inert; selection/service remain or return to the prior owned state |
| Passive, flipped, draining, or committed cutover crash | Installer recovery converges to one ownership-attested installation PID |
| Ordinary namespaced payload invokes deploy/recover | Rejected without the live cell transaction id and random token |
| Stale same-cell control endpoint | Routing PID/version and exact instance token mismatch is rejected before control |
| Two waiters reclaim one stale install lock | One atomically claims a tombstone; neither can delete the other's new lock |
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
