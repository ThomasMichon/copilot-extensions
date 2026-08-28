# Installer/readiness module contract

`agent-installer-readiness` is the read-only, stdlib-first contract between
independently installed runtime plugins and a later installer/configurator. It
discovers plugin-owned installer and readiness metadata, validates the entire
dependency graph, and classifies a deterministic plan. It never executes an
installer, chooses consumer policy, renders a summary, or makes a plugin depend
on a central orchestrator.

## Ownership and identity

An enabled plugin with `"runtimeScope": "machine-gated"` adds one bounded
reference to `plugin.json`:

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
