# Machine requirement configuration

Agent Machines has no monolithic runtime-preferences file. Its **human-authored
requirement packages are configuration**, with native desired-state authority,
resource admission and package discovery. They are not interchangeable ordinary
JSON preference overlays.

## Canonical sources

[`src/agent_machines/discover.py`](../src/agent_machines/discover.py) owns:

| Source | Selection |
|---|---|
| Repository `.copilot-extensions/agent-machines/all/*.yaml` | All-machine packages from adopted projects. |
| Repository `.copilot-extensions/agent-machines/machines/<logical-id>/*.yaml` | Packages for the admitted execution identity/topology aliases. |
| Repository `marketplaces/<marketplace-id>/all` and `machines/<logical-id>` beneath the same namespace | Explicit installation-marketplace package source, not another marketplace's authority. |
| User `~/.agent-machines/config/all` and `config/machines/<logical-id>` | Independently authored machine-local packages. |
| Legacy repository `.agent-machines` and `.github/machine-state` | Native compatibility fallback; canonical source selection wins rather than mixing duplicate filenames. |

`candidate_repos`, `_bound_supplement`, `resolve_registered_repo` and
`project_scope_repos` resolve adopted projects plus required registered
knowledge supplements. Explicit unresolved supplements fail; a conventional
unregistered checkout is not binding readiness. Discovery retains source repo,
source path and canonical anchor. Machine identity is logical execution
identity; an unqualified physical hostname does not stand in for a WSL guest.

## Ordinary default selection (not desired-state authority)

The native plan/validate/restore package collector additionally composes only
scalar `model`/`effortLevel` defaults from existing
`manage.copilot.settings[.<group>].{disposition: ensure-present, values|value}`.
There is **no** `prefer`/`default` disposition or new constraint registry.
Explicit package/declaration authority, owners, other settings, lists,
permissions, activation and resources are not this cohort.

`configuration.py` uses an owner-validated read-only Worktrees report (or its
native launcher-delivered projection), not a binder or host-name inference.
Default selection is built-in unset → selected base then bound knowledge
packages → native user requirement-package scalar defaults → requirement
packages beneath the report's exact `repository_machine_root`, using the same
`.copilot-extensions/agent-machines/all` / `machines/<logical-id>` layout.
These last source packages must contain only the approved scalar-floor cohort;
protected declarations refuse this portable-default source. Native package
parsing, uniqueness, gate matching and manage-only per-machine resolution run
before scalar selection. Explicit target-machine simulation is not the current
execution scope and retains native behavior instead of inventing an identity.

Package identities and desired-state declarations are not overwritten.
`surfaces.collect_contributions` projects the selected scalar defaults into
one ordinary floor contribution while retaining all other native contributions.
`surfaces.settings.apply` then applies the native live `merge_floor`: existing
user values remain untouched; collections retain their native unions.
Every native `enforce` contribution still applies afterward, and
`validator._settings_authority_analysis` still refuses conflicting enforced
values before restore mutates anything. Source-tier selection **never** turns
an enforce constraint into last-wins.

Machine-wide resource/module packages are read only for this eligible default
projection; this does not expand a repo-scoped restore's resource-application
scope. Source-qualified JSON-pointer provenance remains in the resolved package's
`ordinary_defaults` data. No optional owner preserves native standalone behavior;
invalid explicit binding/owner data fails rather than bootstrap or fallback.
Other scalar preferences remain native until an equally safe cohort is qualified.

Native plan JSON exposes `ordinary_defaults.values`, `.provenance` (JSON pointers)
and `.layers` only when an eligible selection exists. The provenance hash includes
that source-qualified selection; the effective drift key still follows actual
native settings operations. This is read/plan data, not an application receipt.

### Copyable schema examples

These are generic schema examples; replace `supported-model-id` with a model
identifier supported by your own client. They do not select a recommended model,
authorize application, or change the model-neutral built-in defaults.

Ordinary default package, for example `all/example-defaults.yaml`:

```yaml
schema_version: 4
package: example-defaults
manage:
  copilot.settings:
    disposition: ensure-present
    values:
      model: supported-model-id
      effortLevel: medium
```

Independent enforced policy package, for example `all/example-policy.yaml`:

```yaml
schema_version: 4
package: example-policy
manage:
  copilot.settings:
    disposition: enforce
    values:
      model: supported-model-id
```

Repository/knowledge/machine tiers select only the first package's ordinary
scalar defaults. Native live values win over that floor. The second package
still enforces its declared value; another conflicting equal-authority enforce
package still causes native validation failure. These are independent named
packages, never filename overlays.

Legacy owner reads use this runtime's existing
`scripts/versioned_runtime.py:resolve_python` through
`configuration_context.ready_owner_prefix`, with isolated native Python and
the peer's native environment. Missing/incomplete slots return no prefix;
they never invoke a self-provisioning front door. Namespaced reads retain
`_peer_launch` receipt and installation-cell checks.

Files are sorted independent packages with **unique package names**, not file
overlays. Native reconciliation unions their desired-state declarations and
uses its authority/collision rules. It must not merge resources, modules,
activation policy or package authority using portable preferences. Ordinary
configuration ordering does not grant apply/install/service/auth authority.

## Package schema

[`src/agent_machines/manifest.py`](../src/agent_machines/manifest.py) owns
`load_package`, `_validate_manage`, `_validate_authority`, `resolve_for_machine`:

| Fields | Type/default/requirement |
|---|---|
| `schema_version` | Required integer `1`, `2`, `3`, `4`; current version `4`. |
| `package` | Required nonempty string, stored as `RequirementPackage.name`. There is no top-level `name` alias. |
| `description` | Optional descriptive metadata; not a reconcile directive. |
| `authority` | Optional bounded integer under v4, default `0`; declaration tracking remains separate from value. Native authority selection only. |
| `gate` | Machine-name string list, default empty/all; `*` applies to all. Matching is native case-insensitive against admitted execution labels. |
| `manage` | Mapping from managed key to specification object, default empty. |
| `aliases` | Optional native alias mapping, default empty. |
| `per-machine` / `per_machine` | Alternative spelling of one machine-keyed manage overlay; declaring both fails. Default empty/null. Case-insensitive duplicate keys and multiple matching aliases fail. |
| `bootstrap-floor` / `bootstrap_floor` | Native bootstrap-floor mapping, default empty; critical core plugins/marketplaces cannot be removed. |
| `exclude` | Native secret/capture exclusion list, default empty. |
| `modules`, `resources` | Ordered declaration lists, default empty; independently validated desired-state inputs. |

Manage specification families are `disposition` (default `ignore`),
native `value`/`values`, `keys`, and optional v4 `authority`.
Dispositions: `enforce`, `ensure-present`, `capture-only`, `ignore`, `exclude`,
`prune`, `prerequisite-check`, `ensure-absent`. `ensure-absent` requires v3+
and exactly plugin-activation `keys.enabledPlugins` with unique fully qualified
plugin IDs. Authority cannot be placed on activation, tombstone, marketplace or
removal specifications. `[-1000..1000]` authority bounds are defined by `authority.py`.
Native surface handlers in `surfaces/__init__.py` and `reconcile.py` interpret value families;
an unknown managed group does not imply support or permission.

Within one package, per-machine overlay supports **manage only**:
`modules`, `resources`, `authority` at that level fail. Nested manage authority
still passes the native validator. Maps deep-merge; **null deletes** a native
manage leaf, lists/scalars replace. This null-delete behavior is an explicit
owner exception, not generic JSON null replacement. Overlay resolution occurs
before cross-repository union and leaves protected declarations untouched.
Unknown metadata is not automatically executed; known fields remain subject
to the exact validators above. Parsing never rewrites a package.

## Module and resource families

Modules require `name`, and may declare native `authority`, `maintenance_safe`
(boolean), `invocation`, arguments and platform/gate selectors. Invocation is
a mapping with qualified `plugin` (`name@marketplace`) and `command` identifier;
`arguments`, `dry_run_arguments`, `apply_arguments`, `platforms` are lists.
`modules.py` owns module lookup/execution and custody, not the composer.
The alternate native platform blocks are `windows`, `linux`, `wsl`; each carries
`command` argv and optional `dry_run_args` argv. WSL falls back to Linux.
`gate` defaults to the package gate. `elevated` is informational, not permission
to request elevation. Without `dry_run_args`, a platform command is skipped during
dry-run rather than executed speculatively. `invocation` takes its native
payload-owned command path; it does not inherit an unrelated CWD/PATH script.
The runner's timeout defaults to 1800 seconds; that is a caller bound, not an
unvalidated extra manifest field.

The complete resource field reference is [resources.md](resources.md), coupled
to `manifest.py`'s `KNOWN_RESOURCE_TYPES`/`REQUIRED_FIELDS` and
`resources.py`'s native handlers:

| Type | Identity/field families |
|---|---|
| `package` | Required `id`, `manager`; optional `version`, `state` (`present`), `pin`, `process_guard.names` nonempty process-name list. |
| `file` | Required `path`; optional `id` (path), `format` (`text`/`json`), `content`, `strategy` (`enforce`), managed-block `block`/`begin`/`end` and `state`. |
| `registry` | Required `path`; value name/data/type (`String`, `ExpandString`, `MultiString`, `DWord`, `QWord`, `Binary`), state/strategy. |
| `feature` | Required `id`, `manager` (`windows-optional-feature`, `windows-capability`, `linux-systemd`); state. |
| `power-setting` | Required `subgroup`, `setting`; scheme and at least one of `ac`/`dc`, unsigned indexes or accepted symbolic action; no `state`. |
| `self-update` | Required `tier` (`watchdog`, `sweep`); unattended update policy. |
| `fleet-update` | Required `tier` (`sweep`); native fleet/update scheduling policy. |
| `copilot-cli-update` | At least `auto_update` boolean or `pinned_version` string. |
| `gh-account` | Required `host`, `login`, `credential_source`; read-only credential/account prerequisite. |
| `git-config` | `scope` only `global`, required `entries`; strict entry schema in `git_config_transaction.py`; never maintenance-safe. |

Common families include `authority`, `platforms`, `gate`, `owner`,
`maintenance_safe`, and applicable `state` (`present`/`absent`) / `strategy`
(`enforce`/`ensure-present`/`managed-block`). The resource reference accounts for
handler-specific fields/defaults; no generic merge expands this native schema.
Paths retain canonical package/source-anchor and location-class rules, not
arbitrary caller CWD. Resource collisions and bootstrap/secret protections remain
independent vetoes even when a lower-priority ordinary value could be composed.

## Reload, generated state and safety

Discovery reloads YAML per operation; active reconciliation works from its
validated plan. Capture and apply are explicit native writes, never consequences
of reading or composing. Installation receipts, service/task registrations,
update markers, drift observations, credentials and generated reconciliation
state are not requirement-package configuration. Keep secrets excluded rather
than placing sample credentials in `values`.

Focused native coverage lives in `tests/test_discover.py`,
`test_manifest.py`, `test_machine_alias_matrix.py`, `test_authority.py`,
`test_resources.py`, `test_reconcile_resources.py`, `test_fleet_update.py`.
