# Installer/readiness module contract

`agent-installer-readiness` is the read-only, stdlib-first contract between
independently installed runtime plugins and a later installer/configurator. It
discovers plugin-owned installer and readiness metadata, validates the entire
dependency graph, and classifies a deterministic plan. It never executes an
installer, chooses consumer policy, renders a summary, or makes a plugin depend
on a central orchestrator.

## Ownership and identity

An enabled plugin with `"runtimeScope": "machine-gated"` adds one bounded
reference to `plugin.json`. A non-machine-gated plugin may also opt in when a
consumer has an explicit reason to include it; discovery validates and returns
that declaration, but still requires declarations only from machine-gated
plugins:

```json
{
  "name": "agent-example",
  "runtimeScope": "machine-gated",
  "installerReadiness": "installer-readiness.json"
}
```

The referenced document is owned by that payload and validated against
[`schema.json`](schema.json). It names only payload-local scripts and logical
commands already declared by `payload-invocation.json`; it cannot inject an
absolute executable or rely on `PATH`. A contract made entirely of
`payload-script` invocations does not need `payload-invocation.json`; the command
manifest is loaded and validated only when a `payload-command` is encountered.

Module ids use `<plugin>/<local-module>`. Discovery qualifies them as
`<marketplace-id>::<plugin>/<local-module>`, where `marketplace-id` comes from
the validated installation cell. Dependencies are same-cell module ids, so a
same-named plugin from another marketplace is never captured accidentally.

```json
{
  "schema": "copilot-extensions.installer-readiness",
  "version": 1,
  "owner": { "plugin": "agent-example" },
  "state": "supported",
  "modules": [
    {
      "id": "agent-example/runtime",
      "platforms": ["windows", "linux", "wsl"],
      "classification": "required",
      "installer": {
        "windows": {
          "kind": "payload-script",
          "path": "scripts/install.ps1",
          "arguments": ["update"]
        },
        "linux": {
          "kind": "payload-script",
          "path": "scripts/install.sh",
          "arguments": ["update"]
        },
        "wsl": {
          "kind": "payload-script",
          "path": "scripts/install.sh",
          "arguments": ["update"]
        }
      },
      "readiness": {
        "schema": "copilot-extensions.module-readiness",
        "version": 1,
        "configurationEmpty": "satisfied",
        "invocations": {
          "windows": {
            "kind": "payload-command",
            "command": "agent-example",
            "arguments": ["status", "--json"]
          },
          "linux": {
            "kind": "payload-command",
            "command": "agent-example",
            "arguments": ["status", "--json"]
          },
          "wsl": {
            "kind": "payload-command",
            "command": "agent-example",
            "arguments": ["status", "--json"]
          }
        }
      },
      "dependsOn": [],
      "restart": "none"
    }
  ]
}
```

An owner that intentionally does not expose modules declares that fact rather
than disappearing:

```json
{
  "schema": "copilot-extensions.installer-readiness",
  "version": 1,
  "owner": { "plugin": "agent-example" },
  "state": "declined",
  "reason": "The runtime is managed by an external platform facility."
}
```

`supported` requires modules and forbids `reason`; `declined` requires `reason`
and forbids modules. A missing reference for an enabled machine-gated plugin is
an error.

## Discovery

There are two read-only entry points:

- `discover_modules(installations)` accepts host-resolved enabled payloads with
  explicit `MarketplaceProvenance`. This is the seam for a CLI host that already
  knows each enabled manifest root.
- `discover_from_settings(settings_groups, durable_home)` accepts explicitly
  typed `SettingsGroup(..., layer=SettingsLayer.USER|PROJECT)` records. User
  groups read only top-level `settings.json` plus `settings.local.json`; project
  groups read only the Claude and Copilot-native repository paths. The complete
  stack merges user before project, with local-over-base and native-over-Claude
  precedence, **then** filters disabled plugins. Marketplace sources are
  normalized through `agent-installation-context`, joined to active
  `namespace.json` and `install.json` receipts, and the payload root is read
  from the validated receipt.

Neither path knows the Copilot installed-plugin cache layout or searches
`PATH`. The caller supplies settings roots and the source-neutral installation
home explicitly.

## Shipped adapters

The acceptance fixture for
[issues #1160](https://github.com/ThomasMichon/copilot-extensions/issues/1160)
and [#1278](https://github.com/ThomasMichon/copilot-extensions/issues/1278)
requires nine plugin-owned runtime modules. `agent-worktrees` is deliberately in
that fixture even though its runtime scope remains `universal`: it is the named
setup foundation, while the other eight are the machine-gated inventory that the
generic completeness rule covers.

| Owner/module | Installer | Readiness meaning | Dependencies | Platforms | Restart |
|---|---|---|---|---|---|
| `agent-worktrees/runtime` | `scripts/install.* update` | `ready` once the payload-owned runtime command loads; project registration is not required | none | Windows, Linux, WSL, macOS | none |
| `agent-machines/runtime` | `scripts/init.* init` | `configuration-empty` when no applicable requirement package exists; malformed packages fail | none | Windows, Linux, WSL, macOS | none |
| `agent-codespaces/runtime` | `scripts/install.* update` | runtime/auth/config health only; empty config is explicit and no live CodeSpace is required | none | Windows, Linux, WSL | none |
| `agent-dispatch/runtime` | `scripts/install.* update` | the configured coordinator must answer its existing health endpoint; the probe never starts it | none | Windows, Linux, WSL | none |
| `agent-mcp/runtime` | `scripts/init.* init` | no bridge config is `configuration-empty`; duplicate normalized names fail before every candidate is parsed and validated | none | Windows, Linux, WSL, macOS | none |
| `agent-index/runtime` | `scripts/install.* update` | service failure, malformed/unreadable config, unknown corpus state, or a populated corpus without attributable sources fails; absent sources or a measured zero-chunk corpus is explicit | none | Windows, Linux, WSL | none |
| `agent-bridge/runtime` | `scripts/install.* update` | the existing service health probe must pass; installer update owns cutover, so no separate restart is required | none | Windows, Linux, WSL | none |
| `agent-containers/runtime` | `scripts/init.* init` | validates config, Docker/service health, and configured-backend tools; absent or unprovisioned fleets are explicit without creating containers or pulling images | none | Windows, Linux, WSL | none |
| `agent-vault/runtime` | `scripts/install.* update` | validates config and service/backend health without starting or unlocking; no configured database is explicit and a locked configured vault remains operational | none | Windows, Linux, WSL | none |

The empty dependency lists are intentional: optional composition is not an
installation prerequisite. The three #1278 adapters use payload-local readiness
scripts that disable their generated commands' self-provisioning path before
delegation, so an absent runtime fails as structured readiness instead of
installing anything. External prerequisites such as authenticated service CLIs
are diagnosed by the owning readiness command, not represented as fake plugin
dependency ids. Every installer action above is the plugin's existing idempotent
lifecycle action. Readiness invocations are attributable payload commands or
scripts; they do not use `PATH`, start services, create instances/configuration,
or populate a corpus.

`restart: none` means the declared installer completes its own runtime/service
cutover. It does not erase owner-specific operational rules: for example,
editing agent-dispatch `service.env` outside the installer still requires an
explicit coordinator service restart.

## Validation and planning

Discovery returns structured findings and refuses planning when any finding is
present. Validation covers:

- missing metadata and malformed supported/declined declarations;
- mismatched owners or ambiguous installation ownership;
- duplicate module ids, unknown dependencies, self-dependencies, and cycles;
- invalid platforms, classifications, restart values, invocation kinds,
  payload paths, payload command ids, or readiness states; and
- a machine-gated plugin that is neither represented by modules nor explicitly
  declined.

Readiness probes emit one strict object:

```json
{
  "schema": "copilot-extensions.module-readiness",
  "version": 1,
  "module": "agent-example/runtime",
  "state": "configuration-empty",
  "detail": "No instances are configured."
}
```

States are `ready`, `configuration-empty`, `not-ready`, and `failed`.
`configurationEmpty` in the module declaration says whether the empty state
satisfies dependents; it never silently collapses into ready or failed.

`build_plan` topologically orders modules by qualified id. Supplied failed,
unsupported, or unsatisfied-empty prerequisites block only their dependents;
independent modules remain `planned`. The plan carries required/optional and
restart metadata but performs no command and makes no overall policy decision.
