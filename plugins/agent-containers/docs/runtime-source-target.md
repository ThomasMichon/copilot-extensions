# Prepared-source profile targets

[Containers runtime profiles](../README.md#optional-restricted-ssh-runtime) |
[Prepared-source suite input](../../agent-worktrees/docs/cli-reference.md#prepared-source-suite-input)

Containers owns dependency-profile selection. A prepared source identifies the
original package, commit, tree and assembly, but its unprofiled source version is
not the installation's actual profile-qualified slot. The updater must preserve
these separate identities rather than infer or default a profile itself.

## Provider declaration and read-only planning

`runtime-source-target.json` is an inert payload declaration:

```json
{
  "schema": "copilot-extensions.runtime-source-target-provider",
  "version": 1,
  "planner": "scripts/runtime_profile.py"
}
```

The prepared payload and installed command owner must publish the same supported
declaration. The planner is an ordinary, owned script below `scripts/`; links,
unsafe paths, unknown versions and conflicting declarations are refused.
Only the **installed owner's** planner executes, after that owner's existing
source validator has verified the complete prepared selection. Prepared code is
not imported or executed to discover profile policy.

The existing stdlib coordinator accepts `--plan-source`, `--root`, `--payload`,
`--expected-source-version` and `--expected-source-identity`. Planning requires
both exact source arguments and rejects provisioning/profile/output-mode
arguments. It uses the same selection and version calculation as construction,
including durable profile intent and legacy selected-runtime Paramiko metadata.
It does not import the installed application, create a root or profile, acquire
construction ownership, invoke UV, or write selectors, config or data.

Its JSON result has exactly these fields:

| Field | Meaning |
|---|---|
| `schema`, integer `version` | `copilot-extensions.runtime-source-target`, generation `1` |
| `plugin` | `agent-containers` |
| `runtime_root` | Canonical absolute installation root |
| `source` | Original `{runtime_source, runtime_version}` source selection |
| `profile` | Existing `base` or `provider-exec` selection |
| `runtime_version` | Actual profile-qualified source slot |

This read-only mode is necessary because only the native owner knows profile
policy; it is not a new daemon, universal handshake or admission authority.

## Frozen execution and currentness

The updater retains the result separately from the original source pin. Its
transaction-local `COPILOT_EXTENSIONS_RUNTIME_SOURCE_TARGET` contains that result;
`COPILOT_EXTENSIONS_RUNTIME_SOURCE_PIN` still identifies the original unprofiled
assembly. Neither changes the installation owner, root or desired profile.
Ordinary installer environments discard inherited values of both variables.

The updater revalidates source and target before every attempt, including module
fallback. Under the native installation transaction, the coordinator recomputes
the target and refuses a changed root, source, profile or target before profile
construction. A source-pinned transaction without a frozen target is refused by
both native frontends before staging/state writes. Direct native calls without
the collected-transaction pin retain their prior source/profile arguments and
defaults; deferred `stamp` remains source-only.

Currentness requires the original full source receipt, actual selected profiled
version, matching outer profile and venv, matching slot profile/source receipt,
and the canonical completion probe with that slot's recorded payload hash.
Package equality or an unprofiled source slot alone is not currentness.

## Upgrade and rollback

The executing engine advertises generation-1 target support as additive
`runtime_sources.target_contract` metadata in its existing inventory. Manager
checks that **actual engine**, not the prepared engine package, when a prepared
payload declares this requirement, before self-update or suite execution.
The queued generation-1 source packet remains unchanged: each record has only
`payload` and `identity`.

An old engine, old/missing owner planner, malformed declaration or unsupported
rollback gets explicit upgrade/setup readiness rather than an effectful syntax
trial, package-only fallback, base-profile substitution or source replacement.
Unprofiled portfolios without target declarations retain the existing V1
baseline. That baseline explicitly excludes Containers: it already owns
profile-qualified slots, so missing declarations on both old sides are not
evidence of unprofiled behavior. Its supported rollback outcome is tooling
readiness, not guessing a target. No old CLI syntax is retired; no service or
optional role is enabled.
