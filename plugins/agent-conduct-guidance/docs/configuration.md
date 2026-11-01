# Conduct configuration

Conduct is payload guidance, not a new service/runtime. Model routing is
optional, inert input to delegated-work selection. Its complete canonical
schema is [`model-routing.schema.json`](../schemas/model-routing.schema.json),
with a [portable example](../examples/model-routing.json) and the native
[`resolve-model-routing.py`](../scripts/resolve-model-routing.py) reader.
No YAML preference store or installation/binding registry is added.

## Files, scopes and precedence

| Source | Exact file / default |
|---|---|
| Defaults | Empty purpose/model map; no model becomes demonstrated by default. |
| Repository base | `<selected-repository>/.github/copilot/model-routing.json`; honored only when the exact checkout is in operator `trustedFolders`. |
| Bound knowledge | Same file under the owner-resolved knowledge root; grafts over the base, with its **own** trusted-folder check. Binding is not trust. |
| Machine-wide operator | `~/.copilot/model-routing.json`; optional, same strict schema. |
| Repository+machine | `.github/copilot/model-routing.json` under Worktrees' selected project configuration root, scoped to exact repository ID/execution machine/installation cell. |

Order: **defaults → trusted repository + trusted bound knowledge → operator
machine → selected repository+machine**. The native launcher supplies optional
`AGENT_CONFIGURATION_CONTEXT` read-data; the
[shared adapter](../../../libs/configuration-layers/src/configuration_context/__init__.py)
validates its composer context and exact selected-root applicability. No
mandatory Worktrees/Python runtime is added to guidance emission. Without
that optional projection, the existing standalone repository/operator resolver
remains usable. Invalid explicit binding/cell/projection data is an error,
not a missing optional knowledge layer.

Report-present extra tiers require a freshly qualified, already-installed
Worktrees configuration owner from the same source. Conduct's canonical
installation SDK reads actual mode/deployment and activation state; computed
`resolve_context` placement is not activation. A completion-gated read uses the
owner's existing runtime/environment helpers, never a PATH/bootstrap fallback.
The current owner report supplies knowledge and exact roots; the input report's
root cannot make a machine source trusted. Namespaced reports match the active
owner cell; legacy reports match the actual legacy registry root, not a future
cell target. Pending activation/migration/deactivation, unready runtimes and
ownership refusals block extra tiers. Report-absent routing never loads this SDK.

`~/.copilot/config.json` (`trustedFolders`, exact resolved path strings) is
the **trust owner**, not an ordinary routing layer. JSONC comment handling
there retains native semantics. Repository routing cannot grant itself trust
or replace operator intent.

## Complete routing schema

| Field | Type / required / default |
|---|---|
| `schema` | Required literal `copilot-extensions.model-routing`. |
| `version` | Required integer literal `1`; no implicit migration/alias. |
| `purposes` | Required mapping of lowercase kebab-case purpose to entry list; empty map/list is valid inert configuration. |
| Entry `model` | Required nonempty exact string, unique per purpose/source. |
| Entry `state` | Required `demonstrated`, `candidate`, `held`, or `failed`. No state is inferred from price, model name, generation or binding. |
| Entry `surfaces` | Required nonempty unique lowercase kebab-case string list. |
| Entry `contextTiers` | Optional unique list: `default`, `long_context`; absent=no configured restriction. |
| Entry `reasoningEfforts` | Optional unique list: minimal/low/medium/high/xhigh/max; absent=no configured restriction. |
| Entry `costRank` | Optional nonnegative integer; absent=unranked/highest ordering, never eligibility evidence. |
| Entry `constraints` | Optional unique nonempty string list; every constraint must be explicitly satisfied. |
| Entry `fallbackOrder` | Optional unique nonempty model-string list; fallbacks still require demonstrated eligibility. |
| Entry `escalationConditions` | Optional unique nonempty string list; explanatory conditions, not an authority grant. |
| Entry `evidence` | Optional list, **required nonempty for demonstrated**. Each record requires nonempty `ref`, ISO `observedAt`, integer `sampleCount` ≥1; optional string `notes`. |
| Entry `recheckAfter` | Optional ISO date; expired evidence/entry is ineligible under the native selection check. |

Unknown top-level, entry and evidence fields, duplicate JSON keys, duplicate
models, malformed types, unsupported schema/version and oversized (>64 KiB)
input are invalid. Null is not a generic delete marker: fields must satisfy
their native type/schema. UTF-8 JSON is required; routing files are not JSONC.

## Native merge and selectors

Purposes merge by purpose, then by exact model identity. A higher source
**replaces the complete model entry**: it cannot splice new `state` into old
evidence, constraints or held/failed status. The adapter normalizes entries to
atomic lists before the shared mapping merge; results retain JSON-pointer
source/tier provenance. Entry arrays are not a generic concatenation.

`--repo` explicitly selects the execution repository. Selection inputs
`--purpose`, `--surface`, repeated `--available-model`, `--context-tier`,
`--reasoning-effort`, repeated `--constraint`, and `--as-of` remain native
invocation requirements, not persisted preference layers.
`--operator-model` requires a demonstrated eligible explicit choice.
`--trial-model` requires nonempty `--trial-id` and an eligible candidate;
operator/trial modes are mutually exclusive. No higher preference clears a
hold, arms a candidate trial, satisfies a missing constraint or demonstrates
a model. Invalid operator configuration suppresses lower repository choices;
repository+machine preferences do not bypass that fail-closed boundary.

Optional `--budget-posture` and `--budget-at` use the
[budget advice reader](../scripts/budget_routing.py). Posture is owner-issued
read-data, never model qualification, permission or automatic worker scaling.
Advice may refine an already eligible choice; freshness/error boundaries remain.

## Other files, reload and exclusions

The plugin's authored instruction templates, skill markdown,
`instruction-projections.json`, `plugin.json` and emitted session guidance are
versioned payload configuration, not operator preference layers. The
per-machine maintenance notice `~/.copilot-extensions/maintenance.json` is
owned operational state; its version/status/owner/start/summary fields never
become routing preferences or new authority.

The resolver reads configuration on each invocation; an emitted guidance body
or current session projection is a launch-time snapshot, not a hot reload.
Changing routing does not install a runtime, start a service, fetch credentials,
publish evidence or mutate a trust folder. Keep private evidence references,
operator model histories, credentials and machine-identifying state out of
shareable examples.
