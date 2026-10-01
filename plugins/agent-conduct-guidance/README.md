# agent-conduct-guidance

> Consolidated, generally-good agent conduct guidance for Copilot CLI — one
> plugin, multiple independent ambient modules.

Several small, generally-applicable behavioral reminders (not domain
capabilities, not runtimes) share the same shape: a concise checked-in
instruction projection for the always-on reminder, plus a paired skill for
on-demand depth. Rather than shipping each as its own single-purpose plugin,
this plugin is the single roof they group under. Each module is independent —
enabling the plugin does not entangle one module's guidance with another's —
but they share one README, one version, one install line, and one place to
look for "is there ambient guidance for X."

## Modules

| Module | Instruction projection | Skill | What it covers |
|--------|------------------------|-------|-----------------|
| Process-spawn hygiene | [`process-hygiene-fallback`](instructions/process-hygiene-fallback.instructions.md) | [`spawning-headless-processes`](skills/spawning-headless-processes/SKILL.md) | Spawn every ad hoc CMD/PowerShell/Python/Node/etc. child process headlessly — especially on Windows, where a naive spawn allocates a visible, focus-stealing console window per invocation |
| Coordinator-first delegation | [`delegation-fallback`](instructions/delegation-fallback.instructions.md) | [`delegating-work`](skills/delegating-work/SKILL.md) | Route broad separable research, comparisons, evaluations, domain-tool calls, and disjoint bulk edits into bounded sub-agent contexts while the coordinator retains synthesis, integration, cohesive implementation, and completion |
| Scratch-space hygiene | [`scratch-space-fallback`](instructions/scratch-space-fallback.instructions.md) | [`using-scratch-space`](skills/using-scratch-space/SKILL.md) | Resolve a portable scratch root and a timestamped per-task subfolder before writing an ad hoc working file (a draft PR body, a log, a one-off dump) outside a repository, instead of littering a drive root with undated loose files |

New modules are added the same way: one instruction projection entry in
[`instruction-projections.json`](instruction-projections.json) plus one paired
skill, documented in the table above and in **What's in this plugin** below.

> **Migration note:** the delegation module was previously shipped as the
> standalone `delegation-guidance` plugin. That plugin is now a deprecated
> pointer at this one — see its own README. Existing adopters should switch
> `enabledPlugins` from `delegation-guidance@copilot-extensions` to
> `agent-conduct-guidance@copilot-extensions` to keep receiving the guidance
> (and avoid a stale duplicate projection).

## What it does (and how to use it)

| Entry point | When it applies | What it does |
|-------------|-----------------|--------------|
| checked-in instruction projections | Every adopting repository | Loads each module's bounded owner-marked reminder without a startup output hook |
| [`spawning-headless-processes`](skills/spawning-headless-processes/SKILL.md) skill | "run this in the background", authoring a script/service that shells out, "why does a window keep flashing", any CMD/PowerShell/Python/Node child-process spawn | Selects the correct windowless/headless mechanism for the target language and OS before the spawn happens |
| [`delegating-work`](skills/delegating-work/SKILL.md) skill | "delegate this research", "use agents to compare", "parallelize the investigation", "split these bulk edits" | Chooses direct versus delegated work, defines bounded contracts, and preserves coordinator ownership |
| [`using-scratch-space`](skills/using-scratch-space/SKILL.md) skill | writing a draft PR/issue body, a captured log, a one-off JSON dump, or any other ad hoc file outside a repository checkout | Resolves a portable scratch root (never hardcoded) and a timestamped per-task subfolder convention |

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
in-scope machine-local convention, or the OS temp directory as a last
resort) and write the draft into a fresh, timestamped per-task subfolder
there -- never as a loose file at a drive root -- per the scratch-space
skill.

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

Start from [`examples/model-routing.json`](examples/model-routing.json) and
validate against
[`schemas/model-routing.schema.json`](schemas/model-routing.schema.json). The
example model IDs are intentionally synthetic.

## What this plugin provides - and what it doesn't

**Provides**

- a concise ambient reminder per module, each independently scoped;
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
  override, in-scope machine-local convention, OS temp directory), a
  timestamped per-task subfolder naming convention, and reuse/cleanup
  guidance — with no hardcoded path for any operator or machine.

**Does NOT provide**

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

Plugin manifests do not install companion plugins transitively. Enable domain
agent, MCP, bridge, or dispatch plugins separately when a task needs them.

## What's in this plugin

| Path | Purpose |
|------|---------|
| [`skills/spawning-headless-processes/SKILL.md`](skills/spawning-headless-processes/SKILL.md) | Per-language/per-OS headless-spawn route table and verification checklist (process-hygiene module) |
| [`skills/delegating-work/SKILL.md`](skills/delegating-work/SKILL.md) | Detailed direct-versus-delegated routing procedure (delegation module) |
| [`skills/using-scratch-space/SKILL.md`](skills/using-scratch-space/SKILL.md) | Scratch-root resolution order and timestamped per-task subfolder convention (scratch-space module) |
| [`instruction-projections.json`](instruction-projections.json) | Declares every module's checked-in ambient fallback |
| [`instructions/process-hygiene-fallback.instructions.md`](instructions/process-hygiene-fallback.instructions.md) | The projected fallback content for the process-hygiene module |
| [`instructions/delegation-fallback.instructions.md`](instructions/delegation-fallback.instructions.md) | The projected fallback content for the delegation module |
| [`instructions/scratch-space-fallback.instructions.md`](instructions/scratch-space-fallback.instructions.md) | The projected fallback content for the scratch-space module |
| [`scripts/emit-guidance.ps1`](scripts/emit-guidance.ps1) / [`scripts/emit-guidance.sh`](scripts/emit-guidance.sh) | Reserved PowerShell/Bash policy producers for the delegation module (not currently wired to a `sessionStart` hook — see below) |
| [`scripts/resolve-model-routing.py`](scripts/resolve-model-routing.py) | Strict inert repository/operator registry resolver (delegation module) |
| [`schemas/model-routing.schema.json`](schemas/model-routing.schema.json) | Versioned purpose-to-model configuration contract |
| [`examples/model-routing.json`](examples/model-routing.json) | Synthetic configuration example |
| [`tests/`](tests/) | Contract tests for both modules' projections, the reserved policy producers, and the model-routing resolver |

Each module's skill file is the source of truth for that module's task-time
behavior.

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
