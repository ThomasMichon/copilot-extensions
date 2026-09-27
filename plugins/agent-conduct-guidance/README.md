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

New modules are added the same way: one instruction projection entry in
[`instruction-projections.json`](instruction-projections.json) plus one paired
skill, documented in the table above and in **What's in this plugin** below.

## What it does (and how to use it)

| Entry point | When it applies | What it does |
|-------------|-----------------|--------------|
| checked-in instruction projections | Every adopting repository | Loads each module's bounded owner-marked reminder without a startup output hook |
| [`spawning-headless-processes`](skills/spawning-headless-processes/SKILL.md) skill | "run this in the background", authoring a script/service that shells out, "why does a window keep flashing", any CMD/PowerShell/Python/Node child-process spawn | Selects the correct windowless/headless mechanism for the target language and OS before the spawn happens |

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
no stolen focus) and redirect its stdio, per the skill's PowerShell route.

## What this plugin provides — and what it doesn't

**Provides**

- a concise ambient reminder per module, each independently scoped;
- (process-hygiene module) a per-language (PowerShell, Python, Node.js,
  CMD/batch), per-OS route table for the correct windowless/headless
  mechanism, guidance to prefer an existing local API/service over spawning a
  process at all, and a verification checklist (real parent shape, multiple
  cycles, forced timeout).

**Does NOT provide**

- a process-spawn library, runtime, or vendored helper module — it is
  guidance, not code;
- copilot-extensions' own internal enforcement (`tools/check-headless-launch.py`,
  the `agent-procutil` shared library, and
  [`docs/patterns/windows-background-process-launch.md`](../../docs/patterns/windows-background-process-launch.md))
  for that repo's own plugin runtimes — those remain owned by that repo's
  `windows-launch-hardening` effort and are the canonical primitive when
  already in scope; this plugin's skill explicitly defers to them;
- process supervision, restart policy, or orphaned-tree reaping.

**Assumes**

- the adopting agent runs ad hoc shell/script commands on behalf of the
  operator and may author short-lived helpers or background services;
- no companion runtime or MCP server is required — the plugin has zero
  dependencies.

## Dependencies & assumptions

The plugin has no runtime, service, network, or authentication dependency. It
is pure guidance: static instruction projections plus skills.

## What's in this plugin

| Path | Purpose |
|------|---------|
| [`skills/spawning-headless-processes/SKILL.md`](skills/spawning-headless-processes/SKILL.md) | Per-language/per-OS headless-spawn route table and verification checklist (process-hygiene module) |
| [`instruction-projections.json`](instruction-projections.json) | Declares every module's checked-in ambient fallback |
| [`instructions/process-hygiene-fallback.instructions.md`](instructions/process-hygiene-fallback.instructions.md) | The projected fallback content for the process-hygiene module |
| [`tests/test_process_hygiene_fallback_projection.py`](tests/test_process_hygiene_fallback_projection.py) | Contract test for the process-hygiene projection declaration and template |

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

Contributions follow the repository's PR-required workflow in
[`CONTRIBUTING.md`](../../CONTRIBUTING.md). File issues in the
[`ThomasMichon/copilot-extensions`](https://github.com/ThomasMichon/copilot-extensions/issues)
repository.
