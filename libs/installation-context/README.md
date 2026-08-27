# Installation Context

Canonical, dependency-light **non-operative** foundation for marketplace
installation cells:

- `installation_context.py` provides the stdlib-only management/runtime API and
  CLI.
- `installation-context.sh` plus `json-query.awk` provides a Bash bootstrap that
  does not require Python or `jq`. The Linux/WSL bootstrap requires Bash 4.4+
  plus `awk`, a SHA-256 command (`sha256sum`, `shasum`, or `openssl`), and a
  physical-path command (`realpath -m` or `readlink -f`).
- `installation-context.ps1` provides the PowerShell 5.1+/pwsh bootstrap.

All three normalize marketplace source descriptors, derive source fingerprints
and marketplace ids, resolve payload provenance, compute the approved durable
layout, detect existing-cell rebind requirements, and strictly validate existing
receipts. They never create or change cells, receipts, runtimes, locks, state,
or payload files.

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
```

The Python CLI uses the same lowercase long options as the Bash entry point.
Callers that already have a private Python toolchain may import
`normalize_source`, `source_identity`, `resolve_context`, and
`validate_context_receipt` directly.

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

Later slices still own vendoring into runtime plugins, receipt creation and
mutation, locking/CAS, migration, runtime-root activation, payload-invocation
schema changes, and dual-cell exemplars.
