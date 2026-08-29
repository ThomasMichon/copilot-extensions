# Context Injection

**Serves:** Vision harness-guidance.

## Problem

An agent needs ambient policy throughout a session, but the policy can be owned
by several authorities. Repositories own local identity and invariants, plugins
own reusable capability policy, operators own portable personal policy, and
skills own detailed task-time procedures. Copying plugin policy into every
repository's `AGENTS.md` creates split ownership, stale copies, and an
unbounded always-on context payload.

## Standard approach

### Split ownership at the source

| Owner | Authoritative content | Delivery |
|-------|-----------------------|----------|
| Repository | Identity, configuration, irreducible local invariants and fail-safes | Lean `AGENTS.md` / repository custom instructions |
| Plugin | Generic ambient policy for the capability | Plugin `sessionStart` hook emitting `additionalContext` |
| Skill | Detailed task-time procedure | Triggered, on-demand skill |
| Operator | Portable personal policy | Operator configuration discovered by the owning plugin |

A static `AGENTS.md` rule remains valid when it is genuinely repository-owned,
and it is the right place for a minimal fallback that must survive launch paths
without plugin hooks. When setup writes plugin compatibility/fallback prose, it
must idempotently reconcile one stable marked region (for example,
`<!-- example-plugin:ambient-fallback:start -->` through the matching end
marker) or one dedicated rule file that names the owning plugin. Future setup
runs use that owner marker to update, shrink, or remove the fallback without
duplicating prose or disturbing neighboring repository-owned instructions.
Wholesale materialization of plugin-owned policy into adopting repositories is
a legacy compatibility path, not the target design.

### Inject a concise plugin-owned kernel

A plugin that owns ambient policy should register a `sessionStart` command hook.
The hook reads the launch payload and emits exactly one JSON object. The
injected kernel begins with a stable owner marker -- at minimum the plugin name,
and preferably the plugin name plus version -- so diagnostics and budget reports
can attribute the bytes:

```json
{"additionalContext":"[owner: example-plugin@1.2.3]\n<concise guidance kernel>"}
```

Until Copilot CLI issue #1234 deterministically aggregates multiple non-empty
results, registrations remain plugin-specific risk decisions. `context-handoff`
is the explicit best-effort exception: its continuity kernel registers now and
temporarily carries an adjacent agent-worktrees command catalog when its result
wins the runtime race. Plugins whose richer policy can remain in a static
fallback, including `efforts`, may continue to defer registration.

Locate a payload-owned producer from the plugin-root environment that Copilot
CLI supplies to plugin hooks (`COPILOT_PLUGIN_ROOT`, with `PLUGIN_ROOT` and
`CLAUDE_PLUGIN_ROOT` as compatibility aliases). Do not use the session cwd or
target repository to rediscover the plugin's own code. The start payload's
`cwd` is for applicability gating; the plugin root is for locating the
producer. A missing root or producer emits a diagnostic to stderr and fails
open with `{}`.

The kernel contains only policy that must remain active throughout the session.
Detailed mechanics stay in an on-demand skill or a dedicated file named by a
backtick faux-link so the agent can read it when needed. This preserves the
skill boundary and avoids Markdown auto-loading from always-on instructions.

All hooks share the aggregate 10 KB `additionalContext` cap. Treat that cap as a
shared budget, not a per-plugin allowance. Each plugin should minimize its
kernel and make its contribution attributable.

### Discover configuration without executing it

The owning plugin discovers configuration in a documented precedence order:

1. Target-repository overrides, but only for keys the plugin explicitly
   declares repo-delegable.
2. Operator policy, which follows the operator across repositories.
3. Plugin defaults, which remain generic and safe.

Each plugin defines an explicit allowlist of repo-delegable keys. Reject unknown
keys and reject a repository value for any known key not on that allowlist.
Operator/plugin-owned safety, publication, attribution, and sanitization policy
is never repo-delegable and cannot be weakened or replaced by target-repository
configuration. Repository overrides do not transfer ownership of generic plugin
policy to the repository.

Configuration is data only. Parse a constrained documented format and reject
unsupported shapes; never source a shell file, import arbitrary code, expand
command substitutions, or execute a command found in configuration.

Public repository configuration must not contain sensitive identifier lists.
Keep private target names, accounts, network values, and other identifying
policy inputs in operator-scoped configuration outside the public repository.

### Gate every emission

A plugin hook can load globally and therefore must hard-gate applicability:

- **`cwd` plus applicable configuration/capability**: the session is within a
  repository to which the policy applies, using resolved path boundaries rather
  than substring matching. Prefer a repository capability or marker that proves
  applicability. A configured-root list is a fallback for capabilities that
  have no durable repo marker.
- **`source` exclusions only when necessary**: source is allow-by-default.
  Exclude only source values whose behavior is explicitly documented as
  incompatible. Do not invent or maintain an allowlist of opaque runtime source
  values.

If cwd/config applicability fails, or a documented source exclusion matches,
emit `{}`. Do not leak guidance into unrelated repositories, and do not infer
applicability from repository names alone. Command-hook `additionalContext`
continues to apply on resumed sessions; do not exclude resume.

The dependency-free reference I/O shape is deliberately small and makes no
claim about the runtime's source vocabulary:

```json
{"cwd":"/path/to/repository","source":"<runtime-provided value>"}
```

```json
{"additionalContext":"[owner: example-plugin]\n<concise guidance kernel>"}
```

### Fail open, retain a static fail-safe

Hook errors, missing optional configuration, and explicitly excluded launch
sources must fail open: write diagnostics to stderr if useful, emit `{}`, and do
not block session startup. The hook writes exactly one final JSON object to
stdout.

The command wrapper treats an absent optional producer as a no-op. Once the
producer exists, it owns its internal fail-open behavior; the wrapper must not
hide a crashing producer, because partial stdout cannot be repaired by appending
another JSON object.

Some headless, cloud, or other confined launch paths do not load plugin hooks.
Critical safety and publication rules therefore retain an irreducible static
fallback in repository or platform instructions on those paths. The fallback is
minimal and is not a second full copy of plugin policy. A repository-owned
invariant stays repository-owned; plugin compatibility prose remains
plugin-attributable through its stable owner marker.

A distinct case is the **ACP transport** (used by host integrations such as an
ACP-mode bridge). It creates a normal session that loads plugin hooks, but ACP
sessions **do not run an interactive trust prompt** -- they honor only *persisted*
folder-trust. A plugin enabled at **repository scope** activates through a
folder-trust-gated repository settings file, so its ambient kernel fires over ACP
**iff** the working directory is already persisted-trusted. In practice a worktree
manager typically adds each worktree to the trusted-folders store on creation, so
repo-scoped plugin hooks **do** fire over ACP for those sessions; the gap bites
only an ACP working directory that was never trusted (enable at **user scope**,
not folder-trust-gated, and/or pre-trust the directory for that case). A
repository's own `.github/hooks`, by contrast, are deferred and **never load over
ACP** regardless of trust -- keep the static fail-safe above for anything that
must survive it.

### Keep cross-platform behavior equal

Provide equivalent Bash and PowerShell hook paths. They must implement the same
configuration precedence, resolved-path gating, documented source exclusions,
JSON shape, failure behavior, and guidance bytes. Platform-specific shell
behavior stays at the edge and must not change policy semantics.

### Inventory without execution

Context audits count static instruction files and enabled skill/agent
frontmatter directly. They enumerate hook registrations but report dynamic
payload size as unknown. Prompt-type hooks are inventoried separately because
their prompt payload is not an `additionalContext` contribution. Never execute
hooks merely to audit their size: a hook is arbitrary code, may depend on live
state, and may have side effects.

Report Unicode character count, UTF-8 byte count, word count, and an explicitly
documented fixed token estimate. Keep reports counts-only: paths and metrics,
never instruction contents, commands, secrets, or sensitive identifiers.

## Rationale

This split keeps policy versioned with its owner, lets operator policy travel,
preserves repository authority over local invariants, and makes context pressure
visible. A concise ambient kernel plus on-demand detail also avoids the decay of
one-shot ambient-guidance skills without forcing a repository to vendor a
plugin's full policy.

## Exemplars

- `customizing-copilot:authoring-skills` documents the `sessionStart`
  `additionalContext` mechanism and context-budget discipline.
- `customizing-copilot:reviewing-customizations` inventories known context
  payloads and enumerates dynamic hook registrations without running them.

## See Also

- Vision: `visions/harness-guidance/README.md`
- Harness adoption: `docs/harness-runbook.md`
- Hook authoring: `plugins/customizing-copilot/skills/authoring-skills/SKILL.md`
