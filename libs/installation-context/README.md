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
`activation-cas` mutations. They also expose read-only `status` and
`probe-legacy` actions for installation-mode governance. `stamp` creates or
updates only `namespace.json` and `install.json`. `activation-cas` explicitly
publishes only `installation-activation.json` after pinning the caller-observed
namespace, install, and activation generations. No action creates a version
slot, migrates legacy state, launches a runtime, or wires an automatic caller.
The mutation requires an explicit `--context` / `-Context`; it never adopts an
ambient `COPILOT_EXTENSIONS_CONTEXT` as authorization.

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
```

The Python CLI uses the same lowercase long options as the Bash entry point.
Callers that already have a private Python toolchain may import
`normalize_source`, `source_identity`, `resolve_context`, and
`validate_context_receipt` directly. Management callers may use
`stamp_context`; its two expected-generation arguments are mandatory. An
explicit management transaction may call `compare_and_swap_activation`; all
three expected-generation arguments and validated legacy probe evidence are
mandatory.
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
signed 64-bit integers on every implementation, and mutation fails before
replacement when the next generation cannot be represented portably.

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
expected-root arguments are absolute; a caller may make only its explicit
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

Later slices still own snapshot provenance, tombstone writing, migration,
runtime-root activation, payload-invocation schema changes, reconciliation,
and dual-cell exemplars.
