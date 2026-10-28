# agent-conduct-guidance

> Consolidated, generally-good agent conduct guidance for Copilot CLI — one
> plugin, multiple independent ambient modules.

Several small, generally-applicable behavioral reminders (not domain
capabilities, not runtimes) share the same shape: a concise checked-in
instruction projection for the always-on reminder, plus a paired skill where
on-demand depth is needed. Rather than shipping each as its own single-purpose plugin,
this plugin is the single roof they group under. Each module is independent —
enabling the plugin does not entangle one module's guidance with another's —
but they share one README, one version, one install line, and one place to
look for "is there ambient guidance for X."

## Modules

| Module | Instruction projection | Skill | What it covers |
|--------|------------------------|-------|-----------------|
| Per-machine maintenance standby | [`maintenance-standby`](instructions/maintenance-standby.instructions.md) | Projection only | Read an owner-attributed local maintenance notice and wait using native Copilot 30-minute scheduled prompts when supporting agent tools are intentionally unavailable |
| Consented Windows elevation | [`elevation-fallback`](instructions/elevation-fallback.instructions.md) | [`requesting-windows-elevation`](skills/requesting-windows-elevation/SKILL.md) | One reviewed administrator operation through a temporary limited interactive task, genuine RunAs consent, specified private output log, matching terminal marker and scoped task cleanup; no persistent elevation grant |
| Multi-agent rules of engagement | [`multi-agent-rules-of-engagement`](instructions/multi-agent-rules-of-engagement.instructions.md) | [`using-multi-agent-rules-of-engagement`](skills/using-multi-agent-rules-of-engagement/SKILL.md) | In-scope same-operator peer authority without redundant approval, scope attenuation, real custody and gates, collective decisions/handoff, authoritative countermand, external ingress and minimal provenance |
| Process-spawn hygiene | [`process-hygiene-fallback`](instructions/process-hygiene-fallback.instructions.md) | [`spawning-headless-processes`](skills/spawning-headless-processes/SKILL.md) | Spawn every ad hoc CMD/PowerShell/Python/Node/etc. child process headlessly — especially on Windows, where a naive spawn allocates a visible, focus-stealing console window per invocation |
| Coordinator-first delegation | [`delegation-fallback`](instructions/delegation-fallback.instructions.md) | [`delegating-work`](skills/delegating-work/SKILL.md) | Route broad separable research, comparisons, evaluations, domain-tool calls, and disjoint bulk edits into bounded sub-agent contexts while the coordinator retains synthesis, integration, cohesive implementation, and completion |
| Scratch-space hygiene | [`scratch-space-fallback`](instructions/scratch-space-fallback.instructions.md) | [`using-scratch-space`](skills/using-scratch-space/SKILL.md) | Resolve a portable scratch root and a dated, worktree-stamped task folder with an ownership breadcrumb; keep ad hoc outputs attributable without treating names, age or receipts as deletion permission |
| Secret-pattern masking avoidance | [`secret-masking-fallback`](instructions/secret-masking-fallback.instructions.md) | [`avoiding-secret-pattern-masking`](skills/avoiding-secret-pattern-masking/SKILL.md) | Never construct an auth header as one scheme-plus-secret literal or f-string (e.g. a bearer-scheme word concatenated or interpolated directly with a token variable) -- Copilot's own content pipeline can silently rewrite that shape into a masked placeholder, with no error and no diff marker; split the scheme word across its own concatenation instead, and verify with a non-rendering byte/boolean check, never a plain read-back |

New modules declare their instruction projection in
[`instruction-projections.json`](instruction-projections.json), adding a paired
skill only for a separate task-time procedure. Document both surfaces in the
table above and in **What's in this plugin** below.

> **Migration note:** the delegation module was previously shipped as the
> standalone `delegation-guidance` plugin. That plugin is now a deprecated
> pointer at this one — see its own README. Existing adopters should switch
> `enabledPlugins` from `delegation-guidance@copilot-extensions` to
> `agent-conduct-guidance@copilot-extensions` to keep receiving the guidance
> (and avoid a stale duplicate projection).

## What it does (and how to use it)

The on-demand [recovering-operational-locks](skills/recovering-operational-locks/SKILL.md)
procedure also authorizes bounded operational-state hand-edits and stale-guard
bypasses during an already-authorized repair. It preserves owner evidence,
quarantine and actual-operation verification without permitting installed-code
patches or weakened update gates.

| Entry point | When it applies | What it does |
|-------------|-----------------|--------------|
| [`requesting-windows-elevation`](skills/requesting-windows-elevation/SKILL.md) skill | Windows UAC, run as administrator, or an approved headless operation that cannot reach the user's consent desktop | Uses Windows and PowerShell 7.2+ only; stages a limited interactive task, requests RunAs, streams both outputs, waits for the actual result and removes the confirmed owned task |
| checked-in instruction projections | Every adopting repository | Loads each module's bounded owner-marked reminder without a startup output hook |
| [`using-multi-agent-rules-of-engagement`](skills/using-multi-agent-rules-of-engagement/SKILL.md) skill | peer work directives, repeated-approval questions, conflicting directives, leadership handoff or external-domain instructions | Resolves the existing domain/scope/custody evidence and applies bounded authority rules without adding an authorization service |
| [`spawning-headless-processes`](skills/spawning-headless-processes/SKILL.md) skill | "run this in the background", authoring a script/service that shells out, "why does a window keep flashing", any CMD/PowerShell/Python/Node child-process spawn | Selects the correct windowless/headless mechanism for the target language and OS before the spawn happens |
| [`delegating-work`](skills/delegating-work/SKILL.md) skill | "delegate this research", "use agents to compare", "parallelize the investigation", "split these bulk edits" | Chooses direct versus delegated work, defines bounded contracts, and preserves coordinator ownership |
| [`using-scratch-space`](skills/using-scratch-space/SKILL.md) skill | writing a draft PR/issue body, a captured log, a one-off JSON dump, or any other ad hoc file outside a repository checkout | Resolves a portable scratch root, owner-stamped dated task folders, ownership breadcrumbs and scoped temporary-output placement |
| [`avoiding-secret-pattern-masking`](skills/avoiding-secret-pattern-masking/SKILL.md) skill | writing or reviewing an `Authorization` header, or any auth-scheme-plus-secret string construction; an auth header that still fails despite a reviewed, seemingly-correct diff | Confirms whether a credential-shaped string got silently masked, and applies the split-literal construction that avoids the rewrite |

Enable it in Copilot settings:

```json
{
  "extraKnownMarketplaces": {
    "copilot-extensions": {
      "source": {
        "source": "github",
        "repo": "ThomasMichon/copilot-extensions"
      }
    }
  },
  "enabledPlugins": {
    "agent-conduct-guidance@copilot-extensions": true
  }
}
```

Then, for example:

> Write a PowerShell script that starts a local dev server in the background
> and polls its health endpoint.

The agent should launch the server headlessly (no visible console window,
no stolen focus) and redirect its stdio, per the process-hygiene skill's
PowerShell route. Or:

> Use agents to compare the three independent storage implementations, then
> synthesize a recommendation and implement the chosen integration.

The coordinator should assign bounded evidence tracks, continue any
independent coordinator-owned work, synthesize the reports, and retain the
implementation and completion decision, per the delegation skill. Or:

> Fetch that issue's full comment thread and draft a reply -- save the draft
> somewhere so I can review it before you post.

The agent should resolve a scratch root (an `AGENT_SCRATCH_ROOT` override, an
operator-configured default, or the operating system's own preferred
temporary-folder system as the final fallback) and write the draft into a
fresh, timestamped, worktree-stamped task folder with an ownership breadcrumb
there -- never as a loose file at a drive root, and never inside a
session-managed state folder -- per the scratch-space skill. That breadcrumb
helps find the existing owner; it does not authorize deletion. Or:

> Add an Authorization header carrying our bearer token to this HTTP client.

The agent should bind the scheme word to its own variable, split across its
own concatenation, and combine it with the token in a separate statement --
never a single scheme-plus-secret literal or f-string -- then verify the
real header value landed with a non-rendering byte/boolean check rather
than a plain read-back, per the secret-masking skill.

## Per-machine maintenance notice

The [standby instruction](instructions/maintenance-standby.instructions.md) is
static and normally projected into adopting sessions; it needs no live plugin,
Bridge, MCP, Vault or runtime helper to interpret a notice. Its normal projection
must be delivered before maintenance; adding this source file does not hot-load
it into an existing session or prove native scheduled-prompt availability.

An operator or already-authorized maintenance session can leave the inert
[`examples/maintenance.json`](examples/maintenance.json) format at
`~/.copilot-extensions/maintenance.json` in the current OS user's home. Replace
the example owner with the **durable Copilot session ID**, not a Bridge-internal
handle. The file is per machine/user environment, not repository or fleet state.

| Field | Meaning |
|-------|---------|
| `version` | `1` |
| `status` | `active` for an intentional outage; `complete` after restoration |
| `ownerSessionId` | Nonempty durable ID of the responsible session, retained for operator reference; not a liveness assertion |
| `summary` | Nonempty explanation of what is happening, not executable instructions |
| `startedAt` | Nonempty RFC3339 start timestamp; together with the owner ID identifies this maintenance window |
| `updatedAt` | Optional RFC3339 informational update time; age never clears a hold |

The maintainer writes the notice before making tools unavailable, preserves it
and durable user state outside the removal inventory, and marks that same window
complete after restoration. Removing the file is not an all-clear. Only the
maintenance owner or operator updates it; other sessions read it and follow the
standby instruction. It is not an authorization grant, lock, election or repair
API. An owner change or another start time describes a different window.

This plugin does not create notices, schedule prompts, activate a hold, uninstall
anything or add a scheduler/daemon. The projection tests certify declarative
delivery and retained offline bytes, not model compliance, live polling or
Mux/Copilot survival during teardown.

## Consented Windows elevation

The Windows elevation helper is task-time and self-contained: it needs an
active same-user interactive desktop and PowerShell 7.2+, but no Python,
sibling plugin, password store, daemon or preinstalled elevated task.
Normal execution requires same-account administrator or linked split-token
elevation. Standard users supplying another administrator account are rejected
before UAC rather than widening private staging permissions.
`-InteractiveProbe` checks the desktop route without requesting administration;
only a separately approved RunAs invocation can qualify administrator execution.
Cancellation, missing completion and nonzero command results remain failures,
not successful setup. See the skill for bounded waiting, resumption and cleanup.

## Model-routing configuration (delegation module)

The delegation module ships the portable strategy and schema, but no real
preferred models. A trusted repository may provide
`.github/copilot/model-routing.json`; the operator may provide
`~/.copilot/model-routing.json`. The operator layer overrides the same
purpose/model entry.

Use the bundled stdlib-only resolver when Python is available:

```text
python <plugin-root>/scripts/resolve-model-routing.py --repo <repository-root>
```

The resolver validates both layers as inert JSON, ignores malformed or
unsupported layers with bounded diagnostics, and emits one deterministic merged
registry. Repository configuration is ignored unless that exact repository is
listed in Copilot's `trustedFolders`. If an operator config exists but is
invalid, lower-precedence repository choices are suppressed so an intended
operator hold cannot disappear because of a typo.

Pass `--purpose`, `--surface`, repeated `--available-model` values, and optional
context/reasoning/constraint facts to receive a deterministic `decision`. An
ordinary selection can return only a demonstrated eligible model. Selecting a
candidate requires both its exact `--trial-model` and a non-empty `--trial-id`.
An explicit `--operator-model` selects that exact demonstrated eligible model
instead of the lowest-cost default. It does not qualify a held, failed,
unavailable, expired, or candidate entry; candidate admission still requires the
separate trial pair. The operator and trial flags are mutually exclusive.

Start from [`examples/model-routing.json`](examples/model-routing.json) and
validate against
[`schemas/model-routing.schema.json`](schemas/model-routing.schema.json). The
example model IDs are intentionally synthetic.

### Optional budget advice

Pass `--budget-posture <status.json>` alongside selection arguments to attach
`decision.budgetAdvice`. The bounded regular JSON file must contain the
budget owner's [routing-advice contract](../budget-guidance/docs/configuration.md#optional-routing-advice);
the resolver never imports budget-guidance, acquires credentials, runs readers,
or refreshes a provider. Omit the flag for exactly the ordinary routing output.
`--budget-at <RFC3339>` supplies a deterministic advice instant; by default the
consumer uses current UTC independently of the evidence-expiry `--as-of` date.

The advice describes a recommendation, caution, escalation, or no-fit reason.
It retains the already-cheapest demonstrated eligible choice, or preserves an
explicit operator choice/trial without proposing an unproven replacement.
Unavailable, stale, future, malformed, and contradictory input cannot supply a
recommendation; malformed input also emits a diagnostic. No eligible choice
means no-fit, never promotion of a candidate or bypass of a hold.

Budget composition does not change the selected model, fallbacks, eligibility,
direct-versus-delegate strategy, or trial admission. `fit` remains `unknown`
because account totals and ordinal model ranks do not establish per-task cost;
`launchBlocked` is always `false`. An escalation requests operator attention,
not a launch refusal or automatic worker mutation. Posture expiry is issued by
the budget owner, not reconstructed from billing fields in this plugin.

## What this plugin provides - and what it doesn't

**Provides**

- a concise ambient reminder per module, each independently scoped;
- (rules-of-engagement module) the task-time procedure and explicit decision
  examples for same-operator delegated work, scope/custody/review boundaries,
  collective decisions, countermand and external ingress. It implements the
  reviewed [Agent Chat scoped-authority intent](../../visions/plugins/agent-chat/README.md#scoped-peer-authority-and-countermand)
  as generic guidance, not a new Chat dependency or runtime API;
- (process-hygiene module) a per-language (PowerShell, Python, Node.js,
  CMD/batch), per-OS route table for the correct windowless/headless
  mechanism, guidance to prefer an existing local API/service over spawning a
  process at all, and a verification checklist (real parent shape, multiple
  cycles, forced timeout);
- (delegation module) early delegation guidance for broad research,
  comparisons, evaluations, domain-tool calls, and disjoint bulk edits;
  bounded, non-overlapping delegate contract guidance; limits on recursive
  delegation, duplicate investigation, and repeated review; a versioned
  purpose-to-model configuration contract and inert resolver;
  demonstrated/candidate/held/failed model eligibility guidance; deterministic
  ordinary, fallback, explicit-trial, and no-route decisions;
- (scratch-space module) a portable scratch-root resolution order (explicit
  override, operator-configured default, falling back to the operating
  system's own preferred temporary-folder system), owner-stamped dated task
  folders and discovery-only ownership breadcrumbs, a boundary against using session-managed
  state folders as a checkout/build/sensitive-data scratch root, and
  reuse/cleanup/durable-filing guidance — with no hardcoded path for any
  operator or machine.

**Does NOT provide**

- an operator-domain verifier, grant/revocation API, automatic deployment,
  leadership election or transcript collector. Existing explicit authenticated
  scope remains actionable; a new adapter's missing callable provenance seam
  stays with its host/client owner instead of becoming a universal approval hold;
- a process-spawn library, runtime, or vendored helper module — it is
  guidance, not code;
- copilot-extensions' own internal enforcement (`tools/check-headless-launch.py`,
  the `agent-procutil` shared library, and
  [`docs/patterns/windows-background-process-launch.md`](../../docs/patterns/windows-background-process-launch.md))
  for that repo's own plugin runtimes — those remain owned by that repo's
  `windows-launch-hardening` effort and are the canonical primitive when
  already in scope; this plugin's skill explicitly defers to them;
- process supervision, restart policy, or orphaned-tree reaping;
- a sub-agent runtime, task queue, cross-machine transport, or MCP server;
- named domain agents, real preferred models, or environment-specific
  defaults; automatic delegation enforcement or a replacement for coordinator
  judgment; custom-agent authoring or validation guidance.

**Assumes**

- the adopting agent runs ad hoc shell/script commands on behalf of the
  operator and may author short-lived helpers or background services;
- the active Copilot CLI exposes one or more sub-agent types when delegation
  is requested; separately enabled domain plugins provide any named agents or
  MCP tools; the coordinating agent remains responsible for integrating
  delegated work;
- no companion runtime or MCP server is required — the plugin has zero
  dependencies;
- (scratch-space module) the adopting environment has an available
  filesystem location for ad hoc working files distinct from any repository
  checkout; the operator or machine-local config may supply the actual root
  path, which this plugin never hardcodes.

## Dependencies & assumptions

The plugin has no runtime, service, network, or authentication dependency.
Model-routing configuration is optional; the stdlib-only resolver uses Python
when invoked, while the checked-in projections remain independent of it.
The optional Windows elevation helper requires PowerShell 7.2+, Task Scheduler,
an active same-user desktop and genuine operator UAC approval. It does not
provision these prerequisites or override policy when consent is unavailable.

Plugin manifests do not install companion plugins transitively. Enable domain
agent, MCP, bridge, or dispatch plugins separately when a task needs them.

## What's in this plugin

| Path | Purpose |
|------|---------|
| [`instructions/maintenance-standby.instructions.md`](instructions/maintenance-standby.instructions.md) | Static per-machine missing-tool standby policy, independent of removed payload helpers |
| [`examples/maintenance.json`](examples/maintenance.json) | Inert owner-attributed local maintenance-notice example; never automatically activated |
| [`skills/using-multi-agent-rules-of-engagement/SKILL.md`](skills/using-multi-agent-rules-of-engagement/SKILL.md) | Bounded peer-authority, conflict, handoff and external-ingress procedure with policy decision examples |
| [`instructions/multi-agent-rules-of-engagement.instructions.md`](instructions/multi-agent-rules-of-engagement.instructions.md) | Static ambient authority/custody boundaries between task-time skill invocations |
| [`skills/spawning-headless-processes/SKILL.md`](skills/spawning-headless-processes/SKILL.md) | Per-language/per-OS headless-spawn route table and verification checklist (process-hygiene module) |
| [`skills/delegating-work/SKILL.md`](skills/delegating-work/SKILL.md) | Detailed direct-versus-delegated routing procedure (delegation module) |
| [`skills/using-scratch-space/SKILL.md`](skills/using-scratch-space/SKILL.md) | Scratch-root resolution, owner-stamped dated task folders, discovery breadcrumbs and cleanup boundaries (scratch-space module) |
| [`instruction-projections.json`](instruction-projections.json) | Declares every module's checked-in ambient fallback |
| [`instructions/process-hygiene-fallback.instructions.md`](instructions/process-hygiene-fallback.instructions.md) | The projected fallback content for the process-hygiene module |
| [`instructions/delegation-fallback.instructions.md`](instructions/delegation-fallback.instructions.md) | The projected fallback content for the delegation module |
| [`instructions/scratch-space-fallback.instructions.md`](instructions/scratch-space-fallback.instructions.md) | The projected fallback content for the scratch-space module |
| [`scripts/emit-guidance.ps1`](scripts/emit-guidance.ps1) / [`scripts/emit-guidance.sh`](scripts/emit-guidance.sh) | Reserved PowerShell/Bash policy producers for the delegation module (not currently wired to a `sessionStart` hook — see below) |
| [`scripts/resolve-model-routing.py`](scripts/resolve-model-routing.py) | Strict inert repository/operator registry resolver (delegation module) |
| [`schemas/model-routing.schema.json`](schemas/model-routing.schema.json) | Versioned purpose-to-model configuration contract |
| [`examples/model-routing.json`](examples/model-routing.json) | Synthetic configuration example |
| [`tests/`](tests/) | Declarative policy, projection/discovery, reserved policy-producer and model-routing contracts; these do not certify model behavior or runtime authorization |

For modules with a paired skill, that skill owns the detailed task-time
procedure. Projection-only modules keep their complete bounded policy inline.

## Troubleshooting, contributing & issues

- **No ambient guidance:** confirm the plugin is enabled and that its declared
  instruction projections have synchronized into the adopting repository.
- **A window still flashes after following the process-hygiene skill's
  route:** confirm the actual parent process shape (windowless script vs.
  interactive terminal) — a launch that looks fixed from an interactive
  terminal can still leak from a scheduled task or detached daemon;
  re-verify from the real parent.
- **This repository already has its own process-spawn library/guard:** use
  that repository's canonical primitive first (e.g. copilot-extensions'
  `agent-procutil` + `windows-background-process-launch` pattern); this
  plugin's skill is the general fallback, not a replacement for an
  already-adopted one.
- **No suitable sub-agent is available (delegation module):** use the skill's
  unavailable-agent path; the plugin does not install agent types.
- **Repository routing config is ignored:** confirm the exact repository root
  is listed in Copilot `trustedFolders` and the config matches the bundled
  schema.
- **Still enabled `delegation-guidance` too:** disable it — its content now
  lives here, and running both leaves a stale, redundant projection consuming
  context budget for no benefit.

Direct plugin-owned `additionalContext` (the reserved `emit-guidance.ps1`/`.sh`
producers) is reserved for a future activation after native host composition
is proven across fresh, resume, non-interactive, and ACP launches at the
supported version floor.

Contributions follow the repository's PR-required workflow in
[`CONTRIBUTING.md`](../../CONTRIBUTING.md). File issues in the
[`ThomasMichon/copilot-extensions`](https://github.com/ThomasMichon/copilot-extensions/issues)
repository.
