# budget-guidance

`budget-guidance` resolves current, provider-neutral usage-budget readings into
one attributable posture. The offline foundation uses strict
manual/static adapters to provide partial or complete readings. Deterministic
field authority composes them, and one posture drives both JSON and concise human status.

```bash
budget-guidance status --config ./budget.json --json
budget-guidance status --config ./budget.json
```

Explicit `scripts/install.sh install` / `scripts/install.ps1 install` prepares
the manifest-declared uv-managed bootstrap Python before running Python helpers.
Preparation holds the existing install transaction lock, including uv acquisition.
New runtime environments use that prepared base, with no system/PATH-Python or
signed-system fallback. Explicit closed-wheel inputs keep their admitted
interpreter/profile contract. Missing PATH uv is established by the existing
shell-native prerequisite route in the user's command directory; no Python
command is needed to acquire it.
Windows setup checks persisted User/Machine command discovery independently
of uv's temporary process PATH prefix, preserving unrelated entries.
Closed-wheel setup requires that command prerequisite to be prepared already;
an off-PATH `-WheelUv` is construction-only and cannot silently publish a
command that lacks uv. Missing command prerequisites refuse before construction,
without acquiring tools or changing the selected runtime.

The payload-local and installed command binstubs use PATH uv with the exact
completed environment selected by the existing runtime resolver. Commands do
not discover another project, synchronize dependencies or download Python.
Both uv directory-override spellings are ignored so relative configuration
continues to resolve from the caller's directory. Setup also isolates inherited
Python-selection hints separately for environment construction and packaging,
not only bootstrap lookup.
Ordinary launch ignores inherited uv project, synchronization, isolated-run
and environment-file overrides. PowerShell binstubs restore the affected
caller variables on success and early failure. On legacy CLR hosts, already
empty values remain untouched because those hosts cannot restore them;
uv may refuse a malformed empty setting rather than mutate the caller.
Missing uv or an unprepared runtime reports setup instructions. `stamp` still
creates a source snapshot and command, but first command use no longer performs
installation: invoke the snapshot's `scripts/install.* provision` explicitly.
Retained completed environments are not relabeled or deleted by this launch
change. Startup/publication, posture-guidance and removal helper migration
remain separate from this command/setup slice.

Both native installers accept an explicitly
[prepared source payload](../../tools/prepare_runtime_source.md). The verified
receipt selects a source-qualified immutable runtime while `source.version`
remains the package version; `runtime_version` and `runtime_source` identify
the deployed assembly. Source snapshots preserve all hashed inputs and their
noneditable vendored dependency closure. Legacy payloads retain the ordinary
package-version and agent-procutil injection paths.
An existing source-qualified snapshot is verified and reused without rewriting
its contents. All mutating installer actions share the same bounded transaction
lock (`COPILOT_PLUGIN_INSTALL_LOCK_TIMEOUT_SEC`, default 300 seconds); uninstall
retains the lock and staging coordination. Default uninstall removes named
runtime slots, snapshots, selectors, generated hook/resolver files and deployment
metadata, retaining configuration, data and unknown files. It is a CLI-only
plugin and does not stop a daemon. Live consumers, linked roots, foreign
manifests and held runtime/snapshot/publication custody refuse removal.
The payload packages the canonical finite-removal helper, native engine and
installer lifecycle SDK; its version-1 generation-selection/catalog behavior is
retained, with the explicit command-preparation boundary described above.
Released lease files retain their inodes and do not imply contention.
See the [install contract](../../docs/install-contract.md).

The PowerShell installer's declared, captured human output uses the packaged
UTF-8 initializer only when requested by its admitting parent. Construction-shell
diagnostics use its existing dual-stream native helper, retaining warnings and
failure status without changing construction ownership. The staged installer
keeps the original action, arguments and output contract. This does not format
the runtime CLI's JSON or the output-free session hooks.

Without `--config`, the command reads
`$BUDGET_GUIDANCE_CONFIG` or `~/.budget-guidance/config.json`. A missing file is
reported as unavailable; it is never interpreted as zero consumption or a full
allowance.

Configuration is inert JSON. The plugin does not source configuration or execute
commands from it. [Bounded external readers](docs/external-readers.md) require
an explicit invocation-owned command grant, separate from configuration.
Automatic startup does not refresh providers. The plugin does not store a
longitudinal ledger, select models or delegation strategies, or block requested
work. Status includes optional
[monthly economy information](docs/configuration.md#optional-monthly-economy-advice),
not an upfront lookup requirement. The opt-in
[personal GitHub report adapter](docs/github-usage.md) reads usage through a
host-owned authenticated transport; it does not infer current entitlement,
reset, or provider freshness from billing reports.

The `sessionStart` hooks are output-free: runtime bootstrap and a bounded
[exact-session posture cue](docs/session-guidance.md) each emit only `{}`.
Bootstrap consults the packaged [canonical publication admission](../../libs/installation-context/README.md)
before stamping or drift reconciliation, including without Python. A refusal
does not suppress the separate posture cue; healthy runtime reuse stays read-only.
The cue reads inert configuration with the same pure posture, never authorizes
readers or refreshes a provider, and uses a reviewed pointer plus a separate
static advisory boundary. It requires an available Python 3.10+ interpreter
without provisioning one. The payload retains its command catalog emitter,
but does not register a competing startup context producer.
The standalone cue disables bytecode before importing its payload source, so
cold and resumed reads preserve every installed payload entry. Imported callers,
posture decisions, provider-refresh restrictions and the existing deadlines
remain unchanged.
Direct plugin-owned startup guidance remains a future seam after native host
composition is proven at the supported Copilot CLI version floor.

See [Configuration](docs/configuration.md) and
[Architecture](docs/architecture.md).

The [session quota reader](docs/session-quota.md) adds the on-demand
`budget_guidance_session_quota` extension tool, a bounded helper over an
existing Copilot SDK session, and a credential-filtered offline capture command.
The tool joins only the foreground session and reports unsupported when that
host lacks the public quota RPC. It never creates a fallback client.
It preserves requesting-session attribution and labels cached compatibility
freshness unknown. Optional credential-free account matching brackets the
quota read with session authentication status without inspecting credentials
or claiming an atomic account/quota binding.
Opt-in [provider evidence](docs/provider-quota.md) evaluates
one provider's observation, units, business state, and raw reset separately.
Qualified incumbent readings reuse posture arithmetic for sustainable pace.
Compatible monthly usage adds a labeled
[cycle-average projection and advice-only pacing](docs/provider-pacing.md).
Provider-observed entitlement acquisition and requesting-account proof remain
later slices.

Credential-free fresh-machine validation is available through the
[`budget-guidance-solo` clean-room scenario](../../tools/clean-room/scenarios/budget-guidance-solo/manifest.json).
It installs only inside a disposable container from a read-only candidate
checkout, proves explicit snapshot preparation without the original source copy,
checks installed source/version identity and offline posture, and runs the
isolated extension fixture. It never starts a live Copilot session, borrows
account credentials, or authorizes host rollout.
