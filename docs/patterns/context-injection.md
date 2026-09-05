# Context Injection

**Serves:** Vision harness-guidance.

> **Status update (2026-09-05): known-fragile, not the primary delivery
> channel.** Real-world and clean-room testing found `sessionStart`
> `additionalContext` composition across multiple hooks unreliable -- observed
> sessions where **no** contributor's `additionalContext` reached the model at
> all, beyond the previously tracked last-writer-wins defect
> ([github/copilot-cli#3589](https://github.com/github/copilot-cli/issues/3589)).
> **Use [`session-scoped-dynamic-guidance.md`](session-scoped-dynamic-guidance.md)
> as the primary delivery path for guidance a harness depends on.** This
> document's aggregation engine remains a documented, tested mechanism and its
> static-fail-safe-projection section (below) is still current and reused by
> that pattern, but its `additionalContext` composition machinery is now
> best-effort/supplementary only until an upstream Copilot CLI fix lands and
> reaches the supported version floor.

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

A plugin that owns ambient policy declares a pure payload-relative contributor
and registers an authority-aware `sessionStart` producer wrapper. The
contributor reads the launch payload and emits exactly one JSON object. The
injected kernel begins with a stable owner marker -- at minimum the plugin name,
and preferably the plugin name plus version -- so diagnostics and budget
reports can attribute the bytes:

```json
{"additionalContext":"[owner: example-plugin@1.2.3]\n<concise guidance kernel>"}
```

On affected Copilot CLI versions, the repository adopts the exact direct
`context-injection@copilot-extensions` marketplace authority and engine-v5
contract. Before exact authority proof, the wrapper invokes its own contributor
directly. After proof, it validates the current effective stack, derives a
generation from its attributable payload roots and context-producing contracts,
then joins the shared `(sessionId, canonical resolved cwd, stack generation)`
rendezvous and emits the same cached aggregate as the authority. Authority-first,
producer-first, and concurrent execution therefore return byte-identical output
without carrying cached guidance across plugin, declaration, hook, contributor,
or command-catalog changes. Missing,
malformed, ambiguous, inactive, or incompatible authority proof preserves
standalone output.

Store rendezvous state in a per-user runtime or cache directory. On POSIX,
require a current-user-owned `0700` root and `0600` lock/result files; reject
unsafe or symlinked paths.

Host settings only enable `context-injection@copilot-extensions`. Adoption is
plugin-owned repository configuration in `.context-injection/config.yaml`:

```yaml
schema: copilot-extensions.context-injection
version: 1
authority: context-injection@copilot-extensions
engine:
  schema: copilot-extensions.context-injection-engine
  version: 5
```

Read that file only after exact persisted repository-trust proof. Reject
unknown or duplicate keys, malformed or unsupported YAML shapes, path escape,
and incompatible schema/version values. A host-settings key must not duplicate
or override this authority declaration.

The plugin's `session-context.json` complete-declares its behavior:

- context-only producers use `sideEffects: none` and
  `context: authority-aware`;
- mixed plugins use `sideEffects: restart-safe-idempotent` and
  `context: authority-aware`, keep direct idempotent side-effect commands
  separate, and declare only pure contributor commands; and
- side-effect-only plugins use `context: none` with no contributors.

The authority never reruns direct hooks. Producer and authority hook
registrations use a 30-second host timeout, leaving wrapper overhead beyond the
engine's 25-second rendezvous deadline. Bash and PowerShell wrappers must have
the same bytes and behavior across every contributing payload.
If a pure contributor consumes output computed by a direct side-effect hook,
the direct hook atomically publishes an explicit completion snapshot and the
contributor waits for it within its own deadline. A missing snapshot never
authorizes the contributor to replay the side effect.

Locate the wrapper and payload-owned contributor from the plugin-root
environment that Copilot CLI supplies to plugin hooks (`COPILOT_PLUGIN_ROOT`,
with `PLUGIN_ROOT` and `CLAUDE_PLUGIN_ROOT` as compatibility aliases). Do not
use the session cwd or target repository to rediscover the plugin's own code.
The start payload's `cwd` is for applicability gating; the plugin root is for
locating the wrapper and contributor. A missing root or wrapper emits `{}`; a
present wrapper owns exact authority resolution and direct fallback.
Authority lookup is source-qualified rather than sibling-relative: the wrapper
resolves the repository-adopted `context-injection@copilot-extensions` payload
through the effective settings/staged-plugin inventory, validates its engine
contract, and only then executes it. An optional
`COPILOT_CONTEXT_INJECTION_ENGINE_ROOT` is a claimed exact root, not an
override; it is accepted only when it equals that resolved adopted payload.
Same-named payloads from another marketplace never become a fallback.

Before activation, validate the complete roster with the authority's reusable
scanner. It accepts either a directory-marketplace root or a trusted
repository's effective plugin stack, reports every violation in one result, and
supports structured JSON:

```text
python plugins/context-injection/scripts/aggregate_context.py --validate \
  --marketplace-root . --json
python plugins/context-injection/scripts/aggregate_context.py --validate \
  --repository /path/to/repository --json
python plugins/context-injection/scripts/aggregate_context.py --validate \
  --marketplace-root /path/to/producer-marketplace \
  --authority-root /exact/context-injection/payload --json
```

The scan covers payload availability, complete behavior declarations, bounded
pure contributors, payload-relative command containment, attributable wrapper
identity/argv, synchronized wrappers and hook timeouts, and runtime `agent-*`
command catalogs. A nonzero exit means the stack is not safe to activate.
Runtime aggregation reuses the same scanner and remains fail-closed.

The kernel contains only policy that must remain active throughout the session.
Detailed mechanics stay in an on-demand skill or a dedicated file named by a
backtick faux-link so the agent can read it when needed. This preserves the
skill boundary and avoids Markdown auto-loading from always-on instructions.

Treat injected context as a shared budget, not a per-plugin allowance.
Per-invocation hook stdout limits do not prove that repeated session-start
results will survive the host's composition budget. The compatibility engine
therefore keeps inline aggregate context below 8 KB, minimizes each kernel, and
makes every contribution attributable.

When the aggregate exceeds the engine's inline budget, write the complete
attributable context atomically under the exact session's
`~/.copilot/session-state/<sessionId>/files/` directory. Key the filename by a
96-bit SHA-256 prefix of the canonical CWD so two repository contexts in one
resumed session cannot replace each other's spill, without disclosing either
path or exceeding common Windows path limits.
Return one compact, byte-identical critical kernel from every participating
hook. It contains the absolute spill instruction, exact command catalogs when
they fit, complete highest-priority owner fragments selected by declared order,
and a bounded exact-excerpt index for deferred contributors. If the full index
cannot fit, retain its total, remaining count, and deterministic roster digest;
the spill remains the full attribution source. The kernel must support safe
first-turn decisions without requiring a file read, while the spill remains
authoritative for deferred detail. Validate the session identifier, contain the
write beneath the session-state root, reject symlink escapes, and use private
file permissions on POSIX.

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

When a launch reconciler generates marker-owned repository settings from a
paired configuration source, it refreshes or retires those exact managed values
before the host inventories plugins. It preserves unmanaged settings and fails
the launch preflight closed when ownership cannot be proven. The aggregation
authority only scans the resulting effective settings; it does not learn how a
particular reconciler or pairing system works.

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
producer exists, the wrapper buffers both engine and direct-contributor output,
requires a successful child plus exactly one JSON object, and otherwise emits
one `{}` with exit zero. It never appends a second fallback around a pipe whose
stdin writer can receive SIGPIPE. A POSIX host without Python cannot select an
authority or fully parse JSON; its compatibility fallback accepts only a
successful, structurally bounded local object result.
An early broken-pipe write from a non-reading child does not replace that
child's successful exit and valid buffered output.

PowerShell wrappers set explicit UTF-8 input and output encodings, write
redirected native stdin as UTF-8 bytes, and preserve arbitrary contributor
arguments under both `ArgumentList` and the Windows PowerShell 5.1 command-line
fallback.

Some headless, cloud, or other confined launch paths do not load plugin hooks.
Critical safety and publication rules therefore retain an irreducible static
fallback in repository or platform instructions on those paths. The fallback is
minimal and is not a second full copy of plugin policy. A repository-owned
invariant stays repository-owned; plugin compatibility prose remains
plugin-attributable through its stable owner marker.

#### Project plugin-owned static fail-safes declaratively

When a plugin owns that minimal fallback, it may ship one bounded
`instruction-projections.json` declaration at the payload root beside a
supported plugin manifest. The declaration is inert versioned data: each entry
names a stable source id, a canonical
payload-relative `.instructions.md` template, one repository-relative
destination under `.github/instructions/<plugin>/`, the customization kind,
`applyTo`, and any legacy owner markers used for migration. Templates carry
ordinary YAML frontmatter and useful human-readable policy; they contain no
session identity, installed path, environment interpolation, or live state.

`customizing-copilot:reviewing-customizations` owns the reference sync and scan
tooling. It resolves the plugin payloads explicitly enabled by repository
settings through the existing settings/marketplace rules. This explicit
repository mutation reads committed settings independently of interactive
folder trust, rejects malformed committed settings or unavailable enabled
payloads, reads only plugins with the declaration, and never executes
plugin-provided code. Personal plugin activation does not alter a shared
checked-in projection. Sync renders
deterministic UTF-8/LF bytes with an
HTML provenance record containing the projection format, marketplace-qualified
plugin identity and version, canonical template path and SHA-256, destination,
customization kind, `applyTo`, and byte counts. It updates the versioned
`.github/copilot/context-projections.json` lock in the same serialized,
rollback-protected transaction as every changed projection.
Compare-before-replace checks abort rather than overwrite a file changed after
validation, and the resulting diff remains subject to normal repository review.

Projection ownership is fail-closed even though ambient hook delivery remains
fail-open. Sync creates or updates only a declared destination and refuses an
unmarked file, another owner, a malformed marker or lock, local rendered-body
changes, nonportable or case-conflicting destinations, path escape, and
symlink/reparse indirection (including dangling lock links). It never deletes
repository-owned files; transaction rollback may remove a newly created
projection when a later write fails. The offline scan reports missing,
malformed, orphaned, conflicting, stale, or over-budget projections; when
current payloads are available it additionally advises that a source template
or plugin version has changed. Old marked
`AGENTS.md` regions remain actionable migration findings until reviewed and
removed manually.

The checked-in fallback budget is 4 KiB per projected file and 12 KiB in
aggregate. This path is only a static fail-safe for launch modes that miss
hooks; it does not aggregate dynamic context, inline or spill session content,
or compete with the `context-injection` authority.

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

- **[`session-scoped-dynamic-guidance.md`](session-scoped-dynamic-guidance.md)
  -- the primary delivery pattern for guidance a harness depends on.**
- Vision: `visions/harness-guidance/README.md`
- Harness adoption: `docs/harness-runbook.md`
- Hook authoring: `plugins/customizing-copilot/skills/authoring-skills/SKILL.md`
