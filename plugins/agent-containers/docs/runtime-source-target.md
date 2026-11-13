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

For complete-application preparation, the same installed owner also accepts
`--application-profile --root <root>`, without an incoming payload. It reports
`copilot-extensions.runtime-application-profile` version one with `plugin`,
`runtime_root`, `profile` and `extras`. It uses `selected_profile` unchanged;
base yields no extras and provider-exec yields `["provider-exec"]`. It does not
calculate a legacy target version or provision anything.

The engine's existing inventory includes these read-only facts in
`runtime_sources.application_profiles`, keyed by component. Each value contains
either `selection` or an explicit `error`. The query runs only for installed
profile owners and never executes incoming code. Invalid roots, components,
versions, extras or oversized responses are refused. Unavailable new profile
support does not change ordinary marketplace update eligibility. Manager's
complete-application consumer requires the exact installed-owner profile and
extras before preparing a V2 snapshot; missing support refuses that source-input
boundary rather than guessing base. This profile-query declaration remains
version one independently of the prepared-source suite packet's version.

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

These frozen target descriptors belong to the legacy source/profile installer
boundary, not to the V2 prepared-source suite packet. V2 carries a complete
application snapshot and the installed-owner profile selection for each runtime;
the engine re-reads that owner before planning and execution. V2 currentness
requires the selected application's source/construction declaration,
hash-qualified completion, slot-local interpreter, success receipt and
last-known-good selector. A legacy source receipt or deploy manifest cannot
certify or veto V2 currentness. Package equality or an unprofiled source slot
alone is not currentness.

## Upgrade and rollback

The target-provider declaration and target result above remain generation one;
their version does not identify the prepared-source packet generation. The
executing engine advertises `copilot-extensions.prepared-runtime-sources` version
two in `runtime_sources`. Manager checks that **actual engine**, not the prepared
engine package, before self-update or suite execution. Queued V2 packets retain
the acquired `source` (`commit`, `tree`, `main_seed`) and runtime records with
complete `snapshot` directories and `profile` selections.

An old engine, old/missing owner planner, malformed declaration or unsupported
rollback gets explicit upgrade/setup readiness rather than an effectful syntax
trial, package-only fallback, base-profile substitution or source replacement.
Raw prepared-source packet V1 (`payload`/`identity` records) is retired and
refused, not adapted to V2 or used as an unprofiled fallback. Missing declarations
are not evidence of base-profile behavior. An installed provider without V2
support requires the documented standalone force-fresh migration rather than a
guessed target or marketplace substitution. Ordinary updates without a
prepared-source packet retain marketplace delivery. No service or optional role
is enabled by these contracts.
