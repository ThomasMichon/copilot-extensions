# Pattern: So You Want to Add a Runtime Plugin with Services and Tools

**Serves:** *Vision plugin-services* §Features/`self-contained-runtime`,
`self-provisioning-runtime`, `uniform-deploy-contract`,
`a-la-carte-installability`, `graceful-composition`; and
*Vision plugin-services/installation-cells*
§Features/`marketplace-scoped-runtime-and-state`, `cell-local-invocation`,
`attributable-agent-capabilities`.
**Exemplars:** agent-machines (runtime CLI), agent-bridge (runtime service),
agent-codespaces / agent-containers (runtime CLI + namespace provider).

## The question

You want to add a runtime plugin that gives agents a command, perhaps owns a
long-running service, and may compose with other plugins. What must it carry so
it works alone, identifies its own installation, and never captures a same-named
tool from another marketplace?

The plugin id need not use the `agent-*` prefix. That prefix identifies the core
agent-fabric family and its installation-cell eligibility; payload-local command
generation and the ordinary runtime contract also support other lowercase
plugin ids.

The answer is not "add a Python package and put its command on `PATH`." A runtime
plugin is a complete delivery unit with four distinct surfaces:

1. an immutable marketplace **payload**;
2. an installer-owned, versioned **runtime**;
3. an attributable, payload-local **agent command**; and
4. optional **service/provider management** outside the agent-facing command
   glossary.

Each surface has one owner. A plugin that omits one does not degrade to ambient
discovery; it fails closed with an actionable bootstrap path.

## Pick the smallest shape

| Need | Shape |
|------|-------|
| Skills, hooks, or a session extension only | Payload-only plugin; stop here—no runtime contract |
| Agent-invoked command | Runtime CLI |
| Long-lived local daemon | Runtime service: runtime CLI plus supervision and endpoint discovery |
| Extend another service's namespace | Namespace provider: runtime CLI plus an attributable provider manifest |

Do not add a daemon merely to expose a command. Do not make a provider importable
inside its consumer's venv. Do not make a service depend on a sibling launcher.

## Required payload shape

```text
plugins/agent-example/
  plugin.json
  pyproject.toml
  payload-invocation.json
  bin/
    agent-example
    agent-example.ps1
    agent-example.cmd
  hooks.json
  scripts/
    install.sh | init.sh
    install.ps1 | init.ps1
    bootstrap-check.sh
    bootstrap-check.ps1
    emit-command-catalog.sh
    emit-command-catalog.ps1
    resolve-runtime.sh
    resolve-runtime.ps1
    versioned_runtime.py
  src/agent_example/
  skills/
  tests/
```

The payload-local shims and catalog emitters are generated, not handwritten:

```text
python libs/payload-invocation/generate.py \
  plugins/agent-example/payload-invocation.json
```

The plugin folder is independently copied by the marketplace. It must not import
an installer helper, shim, or runtime file from a sibling plugin or repository
checkout.

## 1. Declare delivery and reconciliation

`plugin.json` names the plugin, its payload surfaces, version, and runtime scope:

```json
{
  "name": "agent-example",
  "version": "0.1.0-dev1",
  "hooks": "hooks.json",
  "skills": ["skills/"],
  "runtimeScope": "machine-gated"
}
```

Choose `runtimeScope` deliberately:

- `universal` when every adopting machine needs the runtime;
- `machine-gated` when control-harness policy chooses eligible machines;
- `none` only when reconciliation is genuinely out-of-band or there is no
  installer-owned runtime.

`pyproject.toml`, `plugin.json`, and the marketplace entry carry the same plugin
version. Changing anything inside the plugin payload advances all applicable
version surfaces in the same commit.

## 2. Build one self-contained runtime

Both installers implement the shared install contract:

- POSIX and PowerShell entrypoints with matching actions;
- immutable `versions/<version>/` slots;
- atomic `current-version` and `last-known-good` markers;
- non-editable installation through `uv`;
- stamped build information and `deploy-manifest.json`;
- fast `stamp` plus deferred, serialized `provision`;
- payload self-staging so a long install never locks the replaceable marketplace
  payload;
- no fallback to a PATH Python or sibling runtime.

The runtime resolver receives an explicit plugin runtime root. During the
installation-cell transition, a new plugin takes exactly one legacy
`~/.agent-<name>` root because payload-invocation schema v1 currently requires
that shape. Phase 3 migrates that input to installation context. Do not add
additional unqualified roots, global registries, fixed service identities, or
other machine-wide singletons that later phases must unwind.

The installer also still stamps the legacy `~/.local/bin/<name>` management
wrapper. It is required today by outside-session/human callers and by current
namespace-provider registration. It is a compatibility surface, not the
agent-facing command; skills use the payload-local shim.

See [Install Contract](../install-contract.md),
[runtime-self-provisioning](runtime-self-provisioning.md), and
[uniform-runtime-resolution](uniform-runtime-resolution.md).

## 3. Give the agent an attributable command

Declare every agent-facing command in `payload-invocation.json`:

```json
{
  "schema": "copilot-extensions.payload-invocation",
  "version": 1,
  "command": "agent-example",
  "module": "agent_example",
  "runtimeRoot": ".agent-example",
  "noSelfProvisionEnv": "AGENT_EXAMPLE_NO_SELFPROVISION",
  "purpose": "Operate the example service",
  "installer": "install"
}
```

Use `commands[]` when one runtime exposes several agent-facing commands. The
generator produces equivalent POSIX, PowerShell, and CMD shims. Each shim:

- derives its own payload root;
- validates `COPILOT_PLUGIN_ROOT` when supplied;
- leaves the replaceable payload CWD before long work;
- resolves only its own runtime marker;
- self-provisions only from its own payload/snapshot;
- preserves arguments, stdin, stdout, stderr, and exit status; and
- never scans marketplaces or falls through to a same-named global command.

The logical command belongs to the plugin that implements it. Another plugin may
say "`agent-example`" in static prose, but must never embed agent-example's
payload path or runtime layout.

## 4. Emit the session command glossary

Every runtime plugin complete-declares its `sessionStart` behavior in
`session-context.json`. Runtime bootstrap remains a direct,
restart-safe-idempotent hook that emits `{}`. The read-only command glossary is
a pure contributor invoked through the engine-v2 producer wrapper:

```json
{
  "schema": "copilot-extensions.session-context-contributors",
  "version": 1,
  "complete": true,
  "sessionStart": {
    "sideEffects": "restart-safe-idempotent",
    "context": "authority-aware"
  },
  "contributors": [
    {
      "id": "command-catalog",
      "pure": true,
      "order": 300,
      "timeoutSeconds": 10,
      "maxBytes": 8192,
      "bash": ["scripts/emit-command-catalog.sh"],
      "powershell": ["scripts/emit-command-catalog.ps1"]
    }
  ]
}
```

`hooks.json` keeps the bootstrap hook direct and registers one synchronized
wrapper hook for the contributor. The wrapper receives the exact
`plugin@marketplace` identity, contributor id, and payload-relative command,
and its host timeout is 30 seconds:

```json
{
  "version": 1,
  "hooks": {
    "sessionStart": [
      {
        "type": "command",
        "bash": "<payload-root>/scripts/bootstrap-check.sh",
        "powershell": "<payload-root>\\scripts\\bootstrap-check.ps1",
        "timeoutSec": 15
      },
      {
        "type": "command",
        "bash": "<payload-root>/scripts/invoke-context-contributor.sh agent-example@copilot-extensions command-catalog scripts/emit-command-catalog.sh",
        "powershell": "<payload-root>\\scripts\\invoke-context-contributor.ps1 agent-example@copilot-extensions command-catalog scripts\\emit-command-catalog.ps1",
        "timeoutSec": 30
      }
    ]
  }
}
```

Use the repository synchronizer rather than copying those schematic hook
commands literally. Before exact aggregate-authority proof, the wrapper runs
this plugin's contributor directly. After proof, it joins the pair-key
rendezvous and emits `{}`; only `context-injection` emits the aggregate.

The bootstrap hook prepares only this plugin and never runs through the
aggregator. The catalog contributor emits structured entries for every command
declared by this payload:

```text
id · argv · shell · purpose · availability=ready|unavailable
```

Both direct hooks and producer wrappers prefer the runtime-supplied plugin
root; payload CWD is only the compatibility fallback.
Some migrated plugins additionally fall back to their own runtime-deployed
bootstrap helper; that remains self-owned and must not resolve another plugin.

The glossary is a **static invocation breadcrumb**, not a state snapshot. It may
carry command ownership and stable, bounded pivots such as repository or machine
names. It must not enumerate worktrees, sessions, leases, health, live agents,
or other fast-changing resources. Agents use the mapped command to query those
live at the point of need.

Today the absolute payload-local `argv` makes the emitting payload attributable.
Phase 3 adds explicit marketplace/cell identity so two same-named catalogs can
select the requesting skill's installation cell. Missing or ambiguous glossary
ownership fails closed in both eras; it never falls through to `PATH`.

## 5. Write skills against logical commands

An operational skill begins by binding its logical command to the session
catalog:

```text
Use the exact argv[0] from the agent-example session command catalog.
Replace <agent-example catalog argv[0]> below with that path.
Never search PATH for a same-named command.
```

Examples then use `<agent-example catalog argv[0]>`, or state once that leading
`agent-example` tokens are logical and must be replaced. A no-hook fallback may
use the host's plugin-management surface to select an explicit
`plugin@marketplace` payload, then invoke that payload's generated shim directly.
It never scans all installed marketplaces or chooses the first wildcard match.
If the host cannot establish one attributable payload, the command is
unavailable.

An entry with `availability: unavailable` is also unavailable: surface the
reported state rather than improvising an installer, global wrapper, or sibling
runtime. Absence of an explicit `ready` is not ready.

Cross-plugin prose follows the same rule: refer to the peer's logical command,
whose owning plugin emits the mapping. Never point into another plugin's
`bin/`, `scripts/`, runtime root, venv, or state.

## 6. Add service behavior only when needed

A runtime service additionally owns:

- a platform-native, per-user supervisor;
- a single-instance lease scoped to its installation;
- an OS-native or dynamically allocated local endpoint;
- endpoint/routing records validated against installation identity;
- health-gated cutover, drain, rollback, and cleanup;
- durable state separate from immutable executable slots.

Service startup and provider registration are management surfaces. They may use
installation-local launchers and explicit provider manifests, but they do not
become dynamic entries in the session command glossary.

A namespace provider registers a command through the consumer-owned registry
and communicates over the process boundary. In current legacy reality,
`register-bridge-provider.*` requires the provider's machine-global
`~/.local/bin/<name>` wrapper and skips registration when it is absent. That
wrapper is installed as a management compatibility surface, not emitted in the
session glossary. Installation-cell rollout replaces it with an attributable
installation-local command. In either era the consumer never imports or
re-points the provider's runtime.

See [service lifecycle supervision](service-lifecycle-supervision.md),
[local endpoint discovery](local-endpoint-discovery.md),
[graceful daemon cutover](graceful-daemon-cutover.md), and
[a-la-carte independence](a-la-carte-independence.md).

## 7. Prove the complete shape

Before landing:

```text
python libs/payload-invocation/generate.py --all --check
python plugins/context-injection/scripts/aggregate_context.py --validate \
  --marketplace-root . --json
python tools/sync-versioned-runtime.py --check
python -m pytest -q libs/payload-invocation/tests
python tools/check-install-contract.py
python tools/check-bootstrap-sync.py
python tools/check-runtime-resolution.py --strict
python tools/check-marketplace-isolation.py
python tools/check-version-consistency.py
python tools/check-docs-consistency.py
python tools/check-skills.py
python tools/run-plugin-tests.py agent-example
```

`check-marketplace-isolation.py` is report-only during migration. A new plugin's
one required legacy root/wrapper may be an expected baseline finding; adding
extra unqualified ownership surfaces is not.

The roster guard
`libs/payload-invocation/tests/test_agent_plugin_coverage.py` requires every
runtime-bearing marketplace `agent-*` plugin to declare generated commands and
wire attributable bootstrap and catalog hooks for both platforms. It also
rejects skills that hardcode another plugin's payload bin path.

Add plugin tests for:

- missing runtime → first-use provision → reuse;
- concurrent first callers;
- payload-root mismatch;
- arguments/stdin/exit-code parity;
- hook absence and single-payload fallback;
- service start/health/cutover when applicable;
- missing sibling/provider graceful degradation; and
- Windows and POSIX behavior.

Register a new plugin in `.github/plugin/marketplace.json`, link it from the root
`README.md`, and update roster/count phrases in the root README and
`docs/architecture.md`. Add its bootstrap scripts to the appropriate
`tools/check-bootstrap-sync.py` family. Sync, rather than hand-author,
`scripts/versioned_runtime.py` and `scripts/resolve-runtime.*`.

The version surfaces are `plugin.json`, `pyproject.toml`,
`.github/plugin/marketplace.json`, and `src/<package>/_build_info.py` when that
stamped source file exists. The payload manifest's `installer` must match the
actual canonical entrypoint (`install` or `init`).

## Review checklist

- [ ] The plugin is the smallest appropriate shape.
- [ ] It works with no sibling plugin or central coordinator.
- [ ] Both installers implement the same lifecycle.
- [ ] The manifest's `installer` matches the canonical `install`/`init` entrypoint.
- [ ] Runtime versions are immutable and marker-resolved.
- [ ] Vendored runtime primitives are synchronized and bootstrap family is classified.
- [ ] Every agent-facing command is generated from one payload manifest.
- [ ] Direct bootstrap and authority-aware command-glossary hooks are attributable on both platforms.
- [ ] `session-context.json` is complete and side effects are absent from pure contributors.
- [ ] Catalog `availability` is handled fail-closed.
- [ ] Skills use logical commands and never another plugin's direct path.
- [ ] Initial context contains no fast-changing resource snapshot.
- [ ] Services use discovered endpoints and installation-scoped ownership.
- [ ] Missing provenance, peer, payload, or runtime fails with the real cause.
- [ ] Marketplace registration, README links/counts, and all version surfaces agree.
- [ ] Tests, install guards, generated-file guards, and version bumps pass.

## See Also

- [Architecture pattern hub](README.md)
- [As-is architecture](../architecture.md)
- [Install contract](../install-contract.md)
- [Marketplace installation cells](marketplace-installation-cells.md)
- [Payload invocation generator](../../libs/payload-invocation/README.md)
