# Installation Context

Canonical, dependency-free **non-operative** Windows foundation for marketplace
installation cells. `installation-context.ps1` normalizes marketplace source
descriptors, derives source fingerprints and marketplace ids, resolves payload
provenance, computes the approved durable layout, detects existing-cell rebind
requirements, and strictly validates existing receipts. It never creates or
changes cells, receipts, runtimes, locks, state, or payload files.

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

`ProjectRoot` is explicit; resolution never guesses project settings from the
current directory. `COPILOT_EXTENSIONS_CONTEXT` is only a pointer to
`install.json`, not proof. `COPILOT_PLUGIN_ROOT`, when present, is validated and
never rewritten. Context, payload, Copilot-home, durable-home, and expected-root
arguments are absolute; a caller may make only its explicit `ProjectRoot`
relative to its launch directory.

Installed-payload resolution reads user settings plus every recognized native
and Claude project settings file and fails when the same weak marketplace key
declares distinct sources. Directory resolution recognizes every supported
marketplace-manifest location and honors relative `metadata.pluginRoot`.

Portable source-identity constants live in
`fixtures/source-identities.json`. The record is field ordered, LF terminated,
UTF-8 without BOM, and length prefixes every value, including `version:1:1`.

Later slices still own the Python/POSIX implementation, vendoring into runtime
plugins, receipt creation and mutation, locking/CAS, migration, runtime-root
activation, payload-invocation schema changes, and dual-cell exemplars.
