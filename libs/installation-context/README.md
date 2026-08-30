# Installation Context

Canonical, dependency-light management foundation for marketplace installation
cells. Mutation remains explicit and non-automatic:

- `installation_context.py` provides the stdlib-only management/runtime API and
  CLI.
- `installation-context.sh` plus `json-query.awk` provides a Bash bootstrap that
  does not require Python or `jq`. The Linux/WSL bootstrap requires Bash 4.4+
  plus `awk`, a SHA-256 command (`sha256sum`, `shasum`, or `openssl`), and a
  physical-path command (`realpath -m` or `readlink -f`).
- `installation-context.ps1` provides the PowerShell 5.1+/pwsh bootstrap.
- `legacy-entrypoint-probe.sh` and `legacy-entrypoint-probe.ps1` derive
  conservative legacy-footprint evidence from `payload-invocation.json` and
  gate installer/bootstrap mutation through the read-only `probe-legacy`
  decision.

All three normalize marketplace source descriptors, derive source fingerprints
and marketplace ids, resolve payload provenance, compute the approved durable
layout, strictly validate receipts, and expose the same bounded `stamp` and
`activation-cas` mutations. They also expose immutable `snapshot-stamp` and
read-only `snapshot-validate` provenance actions plus read-only `status` and
`probe-legacy` actions for installation-mode governance. `stamp` creates or
updates only `namespace.json` and `install.json`. `activation-cas` explicitly
publishes only `installation-activation.json` after pinning the caller-observed
namespace, install, and activation generations. The cross-runner actions do not
otherwise migrate legacy state, launch a runtime, or wire an automatic caller.
The Python module additionally exposes importable slot APIs. All three runners
provide equivalent `slot-provision`, `slot-validate`, `slot-complete`,
`slot-completion-validate`, and `slot-cutover` CLI actions. Ownership publication
reserves a cell-local version slot. Completion publication immutably binds that
owned slot to strict build-completion evidence without activating it. Cutover
uses explicit receipt-generation and current-marker compare-and-swap
expectations to publish only cell-local runtime markers. Agent Machines and
Agent Index expose explicit installer adapter actions for the first four
transactions;
their normal install/bootstrap paths do not call them. The adapters bind the
selected snapshot to their exact payload root and version. Every mutation
requires an explicit `--context` / `-Context`; it never adopts an ambient
`COPILOT_EXTENSIONS_CONTEXT` as authorization.

JSON inputs use one strict language on every entry point: UTF-8 without BOM,
case-sensitive and non-duplicated object names, escaped control characters, and
string types for identity, path, payload-version, locator, and manifest fields.
Filesystem plugin ids are portable across both platforms, including rejection
of Windows device basenames.

## CLI

All successful actions emit one JSON object. Ambiguous or mismatched evidence
writes an actionable error to stderr and exits nonzero.

```powershell
.\installation-context.ps1 source-id `
  -SourceJson '{"source":"github","repo":"example/example-marketplace"}' `
  -MarketplaceKey example

.\installation-context.ps1 resolve `
  -PayloadRoot $env:COPILOT_PLUGIN_ROOT `
  -CopilotHome $HOME\.copilot `
  -ProjectRoot C:\src\project

.\installation-context.ps1 validate `
  -Context $env:COPILOT_EXTENSIONS_CONTEXT `
  -ExpectedMarketplaceId example--0123456789abcdef `
  -ExpectedPluginId agent-example `
  -ExpectedPayloadRoot $env:COPILOT_PLUGIN_ROOT

.\installation-context.ps1 stamp `
  -PayloadRoot $env:COPILOT_PLUGIN_ROOT `
  -PluginId agent-example `
  -PayloadVersion 1.0.0 `
  -PayloadOrigin installed `
  -ExpectedNamespaceGeneration 0 `
  -ExpectedInstallGeneration 0

.\installation-context.ps1 activation-cas `
  -Context $env:COPILOT_EXTENSIONS_CONTEXT `
  -ExpectedMarketplaceId example--0123456789abcdef `
  -ExpectedPluginId agent-example `
  -ExpectedNamespaceGeneration 1 `
  -ExpectedInstallGeneration 1 `
  -ExpectedActivationGeneration 0 `
  -ActivationMode namespaced `
  -ActivationState active `
  -LegacyDisposition absent `
  -LegacyProbeJson '{"declared":true,"result":"absent","checkedAt":null}'

.\installation-context.ps1 snapshot-stamp `
  -Context $env:COPILOT_EXTENSIONS_CONTEXT `
  -ExpectedMarketplaceId example--0123456789abcdef `
  -ExpectedPluginId agent-example `
  -ExpectedNamespaceGeneration 1 `
  -ExpectedInstallGeneration 1 `
  -SnapshotId 1.0.0

.\installation-context.ps1 snapshot-validate `
  -Context $env:COPILOT_EXTENSIONS_CONTEXT `
  -ExpectedMarketplaceId example--0123456789abcdef `
  -ExpectedPluginId agent-example `
  -SnapshotId 1.0.0

.\installation-context.ps1 slot-provision `
  -Context $env:COPILOT_EXTENSIONS_CONTEXT `
  -ExpectedMarketplaceId example--0123456789abcdef `
  -ExpectedPluginId agent-example `
  -ExpectedPayloadRoot $env:COPILOT_PLUGIN_ROOT `
  -ExpectedPayloadVersion 1.0.0 `
  -SnapshotId 1.0.0 `
  -RuntimeVersion 1.0.0

.\installation-context.ps1 slot-validate `
  -Context $env:COPILOT_EXTENSIONS_CONTEXT `
  -ExpectedMarketplaceId example--0123456789abcdef `
  -ExpectedPluginId agent-example `
  -ExpectedPayloadRoot $env:COPILOT_PLUGIN_ROOT `
  -ExpectedPayloadVersion 1.0.0 `
  -SnapshotId 1.0.0 `
  -RuntimeVersion 1.0.0

.\installation-context.ps1 slot-complete `
  -Context $env:COPILOT_EXTENSIONS_CONTEXT `
  -ExpectedMarketplaceId example--0123456789abcdef `
  -ExpectedPluginId agent-example `
  -ExpectedPayloadRoot $env:COPILOT_PLUGIN_ROOT `
  -ExpectedPayloadVersion 1.0.0 `
  -SnapshotId 1.0.0 `
  -RuntimeVersion 1.0.0

.\installation-context.ps1 slot-completion-validate `
  -Context $env:COPILOT_EXTENSIONS_CONTEXT `
  -ExpectedMarketplaceId example--0123456789abcdef `
  -ExpectedPluginId agent-example `
  -ExpectedPayloadRoot $env:COPILOT_PLUGIN_ROOT `
  -ExpectedPayloadVersion 1.0.0 `
  -SnapshotId 1.0.0 `
  -RuntimeVersion 1.0.0

.\installation-context.ps1 status `
  -PayloadRoot $env:COPILOT_PLUGIN_ROOT `
  -PluginId agent-example `
  -LegacyRoot C:\Users\example\.agent-example `
  -LegacyProbeJson '{"declared":true,"result":"absent","checkedAt":null}'

.\installation-context.ps1 probe-legacy `
  -PayloadRoot $env:COPILOT_PLUGIN_ROOT `
  -PluginId agent-example `
  -LegacyRoot C:\Users\example\.agent-example
```

```bash
./installation-context.sh source-id \
  --source-json '{"source":"github","repo":"example/example-marketplace"}' \
  --marketplace-key example

./installation-context.sh resolve \
  --payload-root "$COPILOT_PLUGIN_ROOT" \
  --copilot-home "$HOME/.copilot" \
  --project-root /path/to/project

./installation-context.sh validate \
  --context "$COPILOT_EXTENSIONS_CONTEXT" \
  --expected-marketplace-id example--0123456789abcdef \
  --expected-plugin-id agent-example \
  --expected-payload-root "$COPILOT_PLUGIN_ROOT"

./installation-context.sh stamp \
  --payload-root "$COPILOT_PLUGIN_ROOT" \
  --plugin-id agent-example \
  --payload-version 1.0.0 \
  --payload-origin installed \
  --expected-namespace-generation 0 \
  --expected-install-generation 0

./installation-context.sh activation-cas \
  --context "$COPILOT_EXTENSIONS_CONTEXT" \
  --expected-marketplace-id example--0123456789abcdef \
  --expected-plugin-id agent-example \
  --expected-namespace-generation 1 \
  --expected-install-generation 1 \
  --expected-activation-generation 0 \
  --activation-mode namespaced \
  --activation-state active \
  --legacy-disposition absent \
  --legacy-probe-json \
  '{"declared":true,"result":"absent","checkedAt":null}'

./installation-context.sh snapshot-stamp \
  --context "$COPILOT_EXTENSIONS_CONTEXT" \
  --expected-marketplace-id example--0123456789abcdef \
  --expected-plugin-id agent-example \
  --expected-namespace-generation 1 \
  --expected-install-generation 1 \
  --snapshot-id 1.0.0

./installation-context.sh snapshot-validate \
  --context "$COPILOT_EXTENSIONS_CONTEXT" \
  --expected-marketplace-id example--0123456789abcdef \
  --expected-plugin-id agent-example \
  --snapshot-id 1.0.0

./installation-context.sh slot-provision \
  --context "$COPILOT_EXTENSIONS_CONTEXT" \
  --expected-marketplace-id example--0123456789abcdef \
  --expected-plugin-id agent-example \
  --expected-payload-root "$COPILOT_PLUGIN_ROOT" \
  --expected-payload-version 1.0.0 \
  --snapshot-id 1.0.0 \
  --runtime-version 1.0.0

./installation-context.sh slot-validate \
  --context "$COPILOT_EXTENSIONS_CONTEXT" \
  --expected-marketplace-id example--0123456789abcdef \
  --expected-plugin-id agent-example \
  --expected-payload-root "$COPILOT_PLUGIN_ROOT" \
  --expected-payload-version 1.0.0 \
  --snapshot-id 1.0.0 \
  --runtime-version 1.0.0

./installation-context.sh slot-complete \
  --context "$COPILOT_EXTENSIONS_CONTEXT" \
  --expected-marketplace-id example--0123456789abcdef \
  --expected-plugin-id agent-example \
  --expected-payload-root "$COPILOT_PLUGIN_ROOT" \
  --expected-payload-version 1.0.0 \
  --snapshot-id 1.0.0 \
  --runtime-version 1.0.0

./installation-context.sh slot-completion-validate \
  --context "$COPILOT_EXTENSIONS_CONTEXT" \
  --expected-marketplace-id example--0123456789abcdef \
  --expected-plugin-id agent-example \
  --expected-payload-root "$COPILOT_PLUGIN_ROOT" \
  --expected-payload-version 1.0.0 \
  --snapshot-id 1.0.0 \
  --runtime-version 1.0.0

./installation-context.sh status \
  --payload-root "$COPILOT_PLUGIN_ROOT" \
  --plugin-id agent-example \
  --legacy-root "$HOME/.agent-example" \
  --legacy-probe-json \
  '{"declared":true,"result":"absent","checkedAt":null}'

./installation-context.sh probe-legacy \
  --payload-root "$COPILOT_PLUGIN_ROOT" \
  --plugin-id agent-example \
  --legacy-root "$HOME/.agent-example"

python installation_context.py slot-provision \
  --context "$COPILOT_EXTENSIONS_CONTEXT" \
  --expected-marketplace-id example--0123456789abcdef \
  --expected-plugin-id agent-example \
  --expected-payload-root "$COPILOT_PLUGIN_ROOT" \
  --expected-payload-version 1.0.0 \
  --snapshot-id 1.0.0 \
  --runtime-version 1.0.0

python installation_context.py slot-validate \
  --context "$COPILOT_EXTENSIONS_CONTEXT" \
  --expected-marketplace-id example--0123456789abcdef \
  --expected-plugin-id agent-example \
  --expected-payload-root "$COPILOT_PLUGIN_ROOT" \
  --expected-payload-version 1.0.0 \
  --snapshot-id 1.0.0 \
  --runtime-version 1.0.0

python installation_context.py slot-complete \
  --context "$COPILOT_EXTENSIONS_CONTEXT" \
  --expected-marketplace-id example--0123456789abcdef \
  --expected-plugin-id agent-example \
  --expected-payload-root "$COPILOT_PLUGIN_ROOT" \
  --expected-payload-version 1.0.0 \
  --snapshot-id 1.0.0 \
  --runtime-version 1.0.0

python installation_context.py slot-completion-validate \
  --context "$COPILOT_EXTENSIONS_CONTEXT" \
  --expected-marketplace-id example--0123456789abcdef \
  --expected-plugin-id agent-example \
  --expected-payload-root "$COPILOT_PLUGIN_ROOT" \
  --expected-payload-version 1.0.0 \
  --snapshot-id 1.0.0 \
  --runtime-version 1.0.0
```

The Python CLI uses the same lowercase long options as the Bash entry point.
Callers that already have a private Python toolchain may import
`normalize_source`, `source_identity`, `resolve_context`, and
`validate_context_receipt` directly. Management callers may use
`stamp_context`; its two expected-generation arguments are mandatory. An
explicit management transaction may call `compare_and_swap_activation`; all
three expected-generation arguments and validated legacy probe evidence are
mandatory.
Snapshot producers may call `stamp_snapshot_provenance`; consumers may call
`validate_snapshot_provenance`. Both require explicit context, exact
marketplace/plugin identity, and a portable snapshot id. Publication additionally
requires both caller-observed receipt generations and an existing non-empty
snapshot directory beneath the canonical `snapshotsRoot`; it writes only the
sidecar. Validation requires non-sidecar snapshot content to remain present.
The sidecar is immutable, cell-local, and non-operative. It provides same-user
ownership consistency and stale-generation detection, not cryptographic
attestation of snapshot contents.
Management callers may use the cross-runner `slot-provision` action, or Python's
`provision_runtime_slot` API, to create one empty
`<versionsRoot>/<runtime-version>/` directory containing only
`.runtime-slot-ownership.json`. The transaction revalidates the context and
snapshot provenance while holding the marketplace genesis lock and plugin
installation lock. Existing slots are reusable only when their immutable marker
exactly matches the requested marketplace, plugin, source, runtime version,
snapshot provenance bytes, receipt paths, and pinned generations. Markerless,
malformed, copied, linked, stale, or conflicting slots fail without replacement. `slot-validate`
and `validate_runtime_slot_ownership` perform the same read-only validation.
Optional expected-payload-root/version arguments additionally bind the selected
snapshot sidecar to the invoking payload; exemplar installer adapters require
both.
Creating a new slot requires the snapshot generations and payload to match the
current active receipts. Once published, an owned slot remains attributable
when those receipts advance for later updates: its marker must still match its
immutable snapshot sidecar and stable cell identity, and current generations
may not regress below the pinned values.
Python and PowerShell publication prepare a reserved
`<versionsRoot-parent>/.runtime-slot-<slot-digest>-<nonce>` sibling outside
`versionsRoot` and use an OS-native atomic no-replace directory rename. An interruption
that bypasses in-process cleanup can leave such a hidden sibling; it is inert,
lies outside canonical runtime-slot enumeration, and requires explicit later
reconciliation rather than automatic deletion. Bash uses atomic final-slot
`mkdir` reservation followed by no-replace hard-link publication of the
ownership marker from a temporary file inside the reserved slot. An ordinary
in-process failure removes its still-empty reservation; an interruption between
those operations leaves a visible markerless slot that every runner preserves
and rejects until a later explicit repair/release transaction.
Neither action writes completion markers, current/LKG markers, activation
receipts, launchers, services, state, or tombstones. The parity-proven primitive
is deliberately not an adoption surface until an installer or bootstrap
explicitly wires it. Results expose
`namespaceState`, `installState`, and `slotEmpty`; `ready` means attributable
ownership, not an active or complete runtime. Runtime versions cannot be
reassigned to another snapshot, and this foundation intentionally provides no
automatic repair, release, or deletion of conflicting slots.

After a build has populated an owned slot, its producer writes
`<slot>/.install-complete.json`. Completion publication accepts that evidence
only as an ordinary non-link file containing exactly `version`, `completed_at`,
`pid`, and `payload_hash`. The version must equal the runtime version,
`completed_at` must be a valid second-precision UTC RFC3339 timestamp, `pid`
must be a JSON integer from `0` through `9223372036854775807`, and
`payload_hash` must be lowercase 64-hex equal to the independently computed
snapshot content digest. Booleans, fractions, exponent-form numbers, negative
values, and overflow values are rejected. Malformed evidence is preserved and
rejected.

The snapshot content digest is SHA-256 over a deterministic record stream.
Runners recursively walk `snapshotRoot` without following links or reparse
points, reject every entry that is not a regular file or directory, include
dotfiles, normalize relative paths to `/`, and order records by the UTF-8 path
bytes. The root `snapshot-provenance.json` sidecar is the only excluded entry;
a file with that name below a subdirectory remains content. Each regular file
contributes exactly
`F\0<UTF-8 relative path>\0<lowercase file SHA-256>\n`. Empty directories do
not contribute. A link, reparse point, device, pipe, socket, or other
non-regular entry fails closed.

Hashing is bounded to 100,000 non-root entries, a 4,096-byte UTF-8 relative
path, and 4,294,967,296 total regular-file bytes. Each boundary is inclusive.
Files and directories both count as entries. The excluded root
`snapshot-provenance.json` still counts as an entry and toward the path and
regular-file byte limits; exclusion applies only to the digest record. Runners
use an O(n log n) ordinal/byte-key sort and never retain file contents merely to
sort them.

Each pass captures every directory's identity and mutation-relevant metadata
plus the exact child name/type manifest before hashing, then requires the same
tree state afterward. File reads separately require stable opened identity and
metadata. First publication calculates the desired digest and immediately
recalculates it before the atomic no-replace write; any add, remove, rename,
type change, directory replacement, or content change fails before a completion
marker can appear.

`slot-complete` and Python's `complete_runtime_slot` API require explicit
context, marketplace/plugin identity, exact expected payload root/version,
snapshot id, and runtime version. Under the genesis and installation locks they
revalidate the current receipts, snapshot provenance, immutable slot ownership,
and snapshot contents. On first publication only, they capture one complete
strict build receipt, verify its payload hash against the snapshot content
digest, and then publish
`<slot>/.runtime-slot-completion.json` with schema
`copilot.extensions/runtime-slot-completion/v1`. The deterministic receipt binds
the source and installation identity, runtime root/version, snapshot provenance
digest and content digest, ownership-marker digest, historical build-receipt
path/digest, validated payload digest and pid, pinned receipt paths/generations,
and the build's own completion timestamp.

Publication is first-writer/no-replace and interruption-safe. Python and Bash
publish with a same-directory temporary file plus a no-replace hard link;
PowerShell uses a same-volume file move without overwrite. A matching existing
receipt is idempotent (`created: false`); malformed or conflicting receipts are
preserved and fail closed. `slot-completion-validate` and
`validate_runtime_slot_completion` are read-only. First publication requires
the current active snapshot and receipt generations, while historical
validation permits monotonic receipt advancement and rejects regression. Once
the immutable completion marker exists, validation and idempotent
`slot-complete` replay do not require, read, or compare the current
`.install-complete.json`; that legacy evidence is a one-time publication input.
Rewriting or removing it cannot invalidate a completed slot. Publication uses
the single captured receipt even if another process atomically replaces the
legacy file after capture, so replacement cannot poison the marker or make
post-publication validation depend on mutable evidence.
The immutable `.runtime-slot-completion.json` is stricter: its named file
identity and metadata must remain stable from open through parse. Concurrent
atomic replacement fails closed and the reader never replaces the observed
marker.
Completion results always report `activated: false` and `operative: false`.
They do not write `current-version`, `last-known-good`, activation, launcher,
service, state, or other lifecycle artifacts.

`slot-cutover` and Python's `cutover_runtime_slot` API require the same explicit
context, payload, snapshot, and runtime identity as completion plus exact
current namespace/install generations and exactly one current-marker
expectation: an exact runtime version or absence. Under the genesis and
installation locks they revalidate the current receipts and immutable target
completion, then re-read both runtime markers immediately before mutation.
Generation or current-marker drift returns `status: revalidation-required`
without mutation.

`last-known-good` follows the established versioned-runtime meaning: the last
version successfully selected by cutover, used only when `current-version`
cannot resolve. It is not a rollback pointer. Initial installation, update, and
explicit rollback therefore publish the target version to both markers; a
target already named by both markers is an idempotent no-op that preserves the
marker bytes.
Every marker is a strict ordinary,
non-link, single-version UTF-8 file and is atomically replaced under both
ownership locks. The transaction validates the published markers and reports
`activated: false` and `operative: false`; activation publication, launchers,
services, health qualification, repair/release, uninstall, and normal-flow
adoption remain separate lifecycle boundaries.
Read-only callers may import `resolve_installation_mode` and
`probe_legacy_entrypoint`. Their optional `os_profile`, `platform`,
`wsl_distro`, `current_time`, `host`, and `pid_is_live` arguments are explicit
test/diagnostic seams; they are not ambient authorization.

`status` emits a complete
`copilot-extensions.installation-resolution` object and exits 0 whenever a
diagnostic result can be constructed, including invalid or blocked on-disk
state. `probe-legacy` emits the same object plus `allowMutation` and
`probeReason`; it exits 0 when legacy mutation is allowed, 3 when governance
refuses it, and 1 for malformed invocation input or failure to construct the
diagnostic result. Invalid invocation-supplied legacy probe JSON is exit 1.

Both actions require an absolute legacy root. Legacy probe evidence has the
shape
`{"declared":bool,"result":"absent|present|unknown","checkedAt":string|null}`;
missing evidence defaults to undeclared/unknown. `--policy-path` /
`-PolicyPath` evaluates an alternate file for diagnostics only. The canonical
operating-system-profile path remains the sole policy authority, so an injected
true value cannot authorize namespaced activation and cannot strand an already
valid active namespaced runtime.

The governance boundary is intentionally non-automatic. A clean authoritative
namespaced request reports `activation-required`, while `probe-legacy` refuses
with `namespaced-requested`; only an explicit `activation-cas` transaction may
publish actual mode. Present, unknown, or undeclared legacy evidence reports
`migration-required`, and legacy remains authoritative until a later migration
transaction publishes both activation and its matching tombstone.

Cell genesis and plugin installation mutations use the same directory-lock
protocol on every platform. Each lock contains a strict `owner.json` naming the
marketplace, plugin when applicable, host, process, and random ownership token.
A live same-host owner is waited out briefly. Dead, cross-host, ownerless, or
malformed ownership fails closed and requires explicit repair; automatic stale
reclamation is forbidden because a pathname-only takeover cannot fence a newer
lock incarnation.
Receipt replacement is same-directory and atomic, and the lock token is
revalidated immediately before replacement. Existing receipt updates compare
the caller-observed namespace and install generations while holding their
respective locks; stale writers must resolve again. Generations are positive
signed 64-bit integers on every implementation. CLI expectations use unsigned
ASCII decimal syntax and normalize leading zeroes before comparison. Mutation
fails before replacement when the next generation cannot be represented
portably.

Activation CAS acquires the marketplace genesis lock and then the plugin
installation lock, revalidates both context receipts while both are held, and
replaces the activation receipt only when all three observed generations still
match. The stable lock order avoids inverse-order deadlocks. A generation
mismatch returns `revalidation-required` with exit 0 and no replacement, so
callers must inspect `status` and `activationChanged` rather than treating a
zero exit as proof of publication. Publication also requires active namespace
and install receipts. Malformed, overflowed, or foreign-environment activation
receipts fail closed and are not overwritten.

`ProjectRoot` / `--project-root` is explicit; resolution never guesses project
settings from the current directory. `COPILOT_EXTENSIONS_CONTEXT` is only a
pointer to `install.json`, not proof. `COPILOT_PLUGIN_ROOT`, when present, is
validated and never rewritten. Context, payload, Copilot-home, durable-home, and
expected-root arguments are absolute (fully qualified on Windows; drive-relative
and root-relative spellings are rejected); a caller may make only its explicit
project root relative to its launch directory.

Installed-payload resolution reads user settings plus every recognized native
and Claude project settings file and fails when the same weak marketplace key
declares distinct sources. Directory resolution recognizes every supported
marketplace-manifest location and honors relative `metadata.pluginRoot`.

Portable source-identity constants live in
`fixtures/source-identities.json`. The record is field ordered, LF terminated,
UTF-8 without BOM, and length prefixes every value, including `version:1:1`.
The test suite runs the same vectors and behavioral cases through PowerShell,
Python, and the no-Python POSIX bootstrap.

`python tools/sync-installation-context.py` vendors byte-identical resolver
copies into the Phase 3 adopters and the legacy-entrypoint callers into the two
exemplar payloads. No exemplar installer or bootstrap calls `activation-cas`;
the existing callers only protect legacy mutation and do not activate a
namespaced root.

Later slices still own normal-flow runtime-root cutover adoption, explicit
historical rollback selection, activation,
health qualification, tombstone writing, migration, rollback/uninstall enforcement,
payload-invocation schema changes, repair/release, and operative dual-cell
exemplars.
