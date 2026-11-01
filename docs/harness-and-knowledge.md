# Harness and Knowledge Repositories

A **harness repository** holds reusable instructions, plugin selection and
execution conventions. A **knowledge repository** holds an adopter's private
notes, activity, preferences and topology. Keeping them separate lets the
harness be shared without publishing the adopter's state.

The split is optional for the toolkit. A project can keep its instructions and
configuration together. A particular harness can require external state through
its repository policy; that requirement does not apply to unrelated projects.
Neither repository name is a toolkit default.

## Binding is a relationship, not permission

Use [harness-knowledge](../plugins/harness-knowledge/README.md) to establish the
chosen relationship and the Worktrees
[state-root contract](../plugins/agent-worktrees/docs/cli-reference.md) to
resolve it. A checkout discovered by name is not a registered binding. An
explicitly configured but unresolved binding is an error, not an invitation to
guess another repository.

The selected knowledge anchor identifies the repository. It is not necessarily
the writable workspace for a task. Resolve the task's paired knowledge worktree
before changing private state, then follow that repository's publication rules.
Do not write personal state into a shareable harness as a fallback.

Configuration reads and state writes are different operations. Reading a
knowledge configuration does not adopt a project, acquire a venue, start a
service, install a runtime, authorize publication or transfer a resource claim.

## Four configuration tiers

The shared composition contract orders ordinary behavior preferences from low
to high:

| Tier | Meaning | Example |
|------|---------|---------|
| Defaults | Values supplied by the owning plugin | A default timeout or workspace convention |
| Repository | The selected base repository, then its bound knowledge configuration | Shared workflow defaults with an adopter's preferred venue grafted over them |
| Machine | Settings for the selected logical execution machine | Host-specific paths or a declared machine's pool preference |
| Repository + machine | An override for that exact repository on that exact machine | A project-specific local workflow preference |

**Knowledge grafting happens within the repository tier.** It is not an extra
highest-priority tier: a machine or exact repository+machine preference can
override an ordinary value contributed by knowledge. Durable named-machine
declarations and machine-local runtime settings have different owners even when
both contribute at the machine tier.

Explicit command options and environment selectors retain their plugin-owned
semantics. Selecting a bridge configuration, data repository or remote target
does not silently change the executing repository or installation cell.
The [configuration map](configuration.md) routes existing files and precedence
to their owners. The tier vocabulary alone does not make every existing reader
support every tier; use the selected plugin's reference for its actual consumer
contract.

## Composition and source ownership

The common merge rule recursively combines mappings. A higher list, scalar or
explicit `null` replaces the lower value; an absent key leaves the lower value
unchanged. A plugin with a schema-specific list identity, alias or validation
rule retains that rule in its own parser rather than inferring it from YAML.

A contributed relative path belongs to the source that supplied it. For
example, a path declared by the knowledge repository does not become relative
to the harness just because the effective configuration contains both sources.
Diagnostics should retain the winning source and tier, without printing secrets.
Canonical and legacy filenames are alternative inputs owned by each plugin,
not independent tiers.

Repository+machine selection uses the resolved stable repository identity and
logical execution-machine identity, not a checkout basename or an incidental
hostname. Two unrelated repositories named `sample-app` must not share local
overrides. A Windows machine and its separately addressable WSL execution space
retain separate settings and resource ownership.

An [installation cell](configuration.md#marketplace-installation-cells) also
qualifies local configuration and runtime state. Sharing repository preferences
does not merge cells, registries, credentials, worktrees or service instances.

## What precedence cannot override

The four tiers compose eligible preferences, not authority. Repository hosting
and publication vetoes, contribution and review gates, credential custody,
installation admission, current resource ownership and model eligibility retain
their existing owners and checks. A higher preference cannot turn a protected
refusal into permission.

Keep tokens, private keys and generated runtime descriptors out of committed
examples. Refer to the credential provider or named vault through its supported
contract instead. Installation/update remains machine-local; it does not rewrite
either repository's configuration or establish a knowledge binding.

## A hypothetical split

`control-kit` contains reusable instructions and plugin selection. `notes-store`
contains private activity and adopter preferences. The operator explicitly binds
the two, and chooses writable task worktrees through Worktrees.

A shared repository preference is read from `control-kit`; an eligible same-key
preference in `notes-store` wins at that tier. A declared machine preference then
wins over either, and an exact repository+machine override wins last. These are
composition examples, not filenames to copy into an arbitrary plugin: use its
owner reference for the accepted schema, selectors and application boundary.

## Consumer support

For the configurable preference surfaces below, the ordinary order is
**defaults -> base repository -> bound knowledge repository -> machine ->
exact repository+machine**. Base and knowledge are the two contributions to
one repository tier. A missing optional contribution adds no values; an
explicit unresolved binding is not an absent contribution. Native owner
resolution supplies repository, logical machine and installation-cell
identity. Each linked reference defines its actual source paths, schema and
application boundary.

| Plugin | Composed preference surface | Separate owner or exception |
|--------|-----------------------------|-----------------------------|
| [agent-worktrees](../plugins/agent-worktrees/docs/config-reference.md) | Eligible workflow preferences and related-repository configuration | Repository hosting, publication and contribution policy are not overrideable preferences. |
| [agent-pull-requests](../plugins/agent-pull-requests/docs/configuration.md) | Worktrees-owned PR configuration, through its native target resolver | No second PR configuration file or independent precedence engine. |
| [agent-logger](../plugins/agent-logger/docs/config-reference.md) | Logging configuration through the selected origin's reader | Source/sink access, logging opt-in and scheduled-process ownership remain separate. |
| [agent-index](../plugins/agent-index/docs/configuration.md) | Repository corpus and ordinary index configuration | Installation role, ready runtime selection and authorized corpus access remain separate. |
| [agent-conduct-guidance](../plugins/agent-conduct-guidance/docs/configuration.md) | Model-routing preferences | Repository trust and demonstrated model eligibility remain separate. |
| [agent-machines](../plugins/agent-machines/docs/configuration.md) | Eligible scalar `ensure-present` Copilot model/effort defaults | Live values win; collection rules, `enforce` constraints and resource authority retain their native semantics. |
| [agent-ssh](../plugins/agent-ssh/docs/configuration.md) | Mesh configuration through the existing topology reader | Transport admission, logical execution identity and credential access remain separate. |
| [agent-bridge](../plugins/agent-bridge/docs/configuration.md) | Ordinary machine/target configuration | Explicit configuration selection, adopted profiles, execution targets and live service custody retain their own rules. |
| [agent-mcp](../plugins/agent-mcp/docs/configuration.md) | Ordinary bridge configuration | Explicit file/server selection, backend scope and credential-provider custody retain their own rules. |
| [agent-vault](../plugins/agent-vault/docs/configuration.md) | Non-secret vault selection/configuration | Vault contents, secret reads, explicit selectors and runtime rendezvous remain owner-scoped. |
| [agent-remote-driver](../plugins/agent-remote-driver/docs/configuration.md) | None: no ordinary authored preference schema | Its environment-selected discovery directory and generated descriptors are not four-tier configuration. |
| [agent-containers](../plugins/agent-containers/docs/configuration.md) | Fleet configuration through the existing fleet reader | Explicit selection, security validation, credential relay and resource acquisition remain separate. |
| [agent-codespaces](../plugins/agent-codespaces/docs/configuration.md) | Repository/provisioning preferences through the existing reader | Explicit selection, credentials, adoption and venue acquisition remain separate. |
| [agent-dispatch](../plugins/agent-dispatch/docs/configuration.md) | Declarative registrar and worker-identity preferences | Coordinator invocation selectors, task authority, live registrations and claims are not portable overrides. |

This matrix describes configuration acquisition, not installed-host rollout.
Read-only peer discovery uses the selected owner's ready runtime; it does not
bootstrap that runtime merely to obtain an optional configuration contribution.
An explicit source selector retains its documented bypass of implicit discovery.

See [adoption](adoption.md) for integration choices and
[configuration](configuration.md) for file ownership.
