# Pattern: installer/readiness modules

**Serves:** *Vision installer* §Features/`prerequisite-provisioning`,
`plugin-updating-and-alignment`, and
§Behaviors/`knows-the-plugins-without-coupling-to-them`; *Vision
plugin-services* §Features/`a-la-carte-installability`,
`self-provisioning-runtime`, and `uniform-deploy-contract`; *Vision
plugin-services/installation-cells*
§Behaviors/`provenance-carried-end-to-end`.

**Foundation:** `libs/installer-readiness/`. Plugin adapters and a consumer
orchestrator are intentionally separate work.

## Problem

An out-of-plugin installer/configurator can see that a project enables several
`runtimeScope: machine-gated` plugins, but it must not encode each plugin's
private installer flags, runtime paths, readiness rules, or empty-configuration
semantics. Hardcoding those details in the consumer creates a second source of
truth. Looking up a bare plugin command on `PATH` or assuming an
installed-plugin cache path also loses marketplace provenance, so an
independently installed same-named plugin can be selected by accident.

Silent omission is equally unsafe. If an enabled machine-gated plugin publishes
no setup contract, a whole-machine setup can appear successful while leaving
that runtime absent.

## Standard approach

**Each plugin owns a bounded declarative module manifest; a consumer discovers,
validates, orders, and later executes those modules.**

The plugin's `plugin.json` contains only
`"installerReadiness": "installer-readiness.json"`. The referenced payload file
uses `copilot-extensions.installer-readiness` version 1 and declares either:

- `state: supported` with one or more modules; or
- `state: declined` with a non-empty reason and no modules.

An enabled machine-gated plugin with neither declaration is invalid. A decline
is intentional and visible; absence is never interpreted as decline.

### Installation-qualified identity

The plugin-owned document names its owner plugin and module ids as
`<plugin>/<module>`. Discovery adds the validated installation cell:

```text
<marketplace-id>::<plugin>/<module>
```

Settings contribute desired enablement and normalized marketplace source
provenance. Active `namespace.json` and `install.json` receipts contribute the
unique installation owner and current payload root. A host that already has an
equivalent, identity-verified enabled-plugin manifest may provide those records
directly. Discovery never guesses an installed-cache path, chooses by plugin
name alone, or searches `PATH`.

Two active cells matching one source fingerprint are ambiguous and fail before
planning. Two different marketplace cells may publish the same plugin/module id
without colliding because their qualified ids differ.

### Bounded attributable invocation

Installer and readiness invocations use one of two forms:

- `payload-script`: a regular, non-link `.ps1` or `.sh` path contained in the
  owning payload; or
- `payload-command`: a logical command declared by that payload's
  `payload-invocation.json`, resolved to its generated platform shim.

Both forms carry an argument array, never shell text. Absolute paths, path
escapes, undeclared command ids, platform/suffix mismatches, and missing targets
are invalid. The contract therefore points at the plugin's real independently
runnable installer and probe without copying their behavior into the consumer.

### Module semantics

Each module declares:

- one explicit owner-derived module id;
- supported `windows`, `linux`, `wsl`, and/or `macos` platforms;
- `required` or `optional` classification;
- installer and readiness invocation per declared platform;
- prerequisite module ids in the same installation cell;
- restart boundary: `none`, `shell`, `session`, or `machine`; and
- whether `configuration-empty` is `satisfied` or `unsatisfied` for dependents.

A readiness probe emits the strict
`copilot-extensions.module-readiness` version 1 object with the local module id
and one state: `ready`, `configuration-empty`, `not-ready`, or `failed`.
`configuration-empty` means the runtime can be healthy while there is no
configured work; it is never silently recast as success or failure. The owning
module decides only whether that state satisfies its dependents.

## Validate before mutation

Discovery is read-only and returns structured findings. Planning is unavailable
while any finding remains. At minimum validation rejects:

- missing module metadata for an enabled machine-gated plugin;
- mismatched owners, duplicate module ids, or ambiguous installation ownership;
- unknown or self dependencies and dependency cycles;
- invalid platform, classification, restart, invocation, command, or readiness
  state values; and
- contradictory supported/declined fields.

This is aggregate validation: every enabled plugin is inspected and all
actionable findings are returned before a consumer is allowed to mutate the
machine.

## Deterministic graph plan

The shared planner topologically orders modules with qualified-id tie-breaking.
It accepts readiness results but executes nothing. A failed, unsupported, or
unsatisfied-empty prerequisite blocks its transitive dependents. Independent
modules remain `planned`, so a later consumer may continue useful work without
pretending the blocked branch succeeded.

The plan carries required/optional classification and restart metadata but does
not choose exit policy, prompt, summarize, or run commands. Those are consumer
responsibilities.

## Independence boundary

Publishing a module does not make an orchestrator part of the plugin's runtime.
The declared installer and readiness probe remain payload-owned and
independently runnable. A plugin still self-provisions and supervises itself
through its own layers; the optional installer/configurator merely composes
those public surfaces when present.

The base contract does not provide plugin-specific adapters, runtime execution,
machine-gate policy, prompts, or whole-run summaries.

## Required tests

- every enabled machine-gated fixture is represented by modules or an explicit
  decline;
- removing one fixture declaration fails as `missing-module-metadata`;
- same-named modules in different cells remain distinct;
- ambiguous cells, duplicate ids, unknown/self dependencies, and cycles fail;
- invalid enum, command, path, schema, and supported/declined combinations fail;
- a failed prerequisite blocks its dependents while an independent module stays
  planned; and
- Windows and POSIX payload invocation resolution is covered.

## See also

- [`marketplace-installation-cells.md`](marketplace-installation-cells.md)
- [`runtime-self-provisioning.md`](runtime-self-provisioning.md)
- [`a-la-carte-independence.md`](a-la-carte-independence.md)
- [`runtime-agent-plugin.md`](runtime-agent-plugin.md)
- [`../install-contract.md`](../install-contract.md)
