# process-hygiene-guidance

> Ambient process-spawn hygiene for Copilot CLI: every ad hoc child process an
> agent starts should be headless.

This payload-only plugin keeps an agent from spamming visible, focus-stealing
console windows every time it runs a helper command, a build/watch/dev-server
step, or a self-authored background job. The risk is sharpest on Windows,
where a naive spawn of a console-subsystem program (`cmd.exe`, `powershell.exe`,
`pwsh.exe`, console `python.exe`, `node.exe`) allocates its own terminal window
even when the parent process has none, and `-WindowStyle Hidden` alone does not
suppress it. The ambient kernel is a concise, checked-in instruction projection
so the reminder does not depend on a startup hook; detailed per-language,
per-OS routes load only when the `spawning-headless-processes` skill is
invoked or matched.

## What it does (and how to use it)

| Entry point | When it applies | What it does |
|-------------|-----------------|--------------|
| checked-in instruction projection | Every adopting repository | Loads a bounded owner-marked reminder to headless every ad hoc process spawn, without a startup output hook |
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
    "process-hygiene-guidance@copilot-extensions": true
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

- a concise ambient reminder that every ad hoc process spawn must be headless;
- a per-language (PowerShell, Python, Node.js, CMD/batch), per-OS route table
  for the correct windowless/headless mechanism;
- guidance to prefer an existing local API/service over spawning a process at
  all;
- a verification checklist (real parent shape, multiple cycles, forced
  timeout) so a fix is confirmed rather than assumed.

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
is pure guidance: a static instruction projection plus one skill.

## What's in this plugin

| Path | Purpose |
|------|---------|
| [`skills/spawning-headless-processes/SKILL.md`](skills/spawning-headless-processes/SKILL.md) | Per-language/per-OS headless-spawn route table and verification checklist |
| [`instruction-projections.json`](instruction-projections.json) | Declares the checked-in ambient fallback |
| [`instructions/process-hygiene-fallback.instructions.md`](instructions/process-hygiene-fallback.instructions.md) | The projected fallback content itself |
| [`tests/test_process_hygiene_fallback_projection.py`](tests/test_process_hygiene_fallback_projection.py) | Contract test for the projection declaration and template |

The skill file is the source of truth for task-time routing behavior.

## Troubleshooting, contributing & issues

- **No ambient guidance:** confirm the plugin is enabled and that its declared
  instruction projection has synchronized into the adopting repository.
- **A window still flashes after following the skill's route:** confirm the
  actual parent process shape (windowless script vs. interactive terminal) --
  a launch that looks fixed from an interactive terminal can still leak from a
  scheduled task or detached daemon; re-verify from the real parent.
- **This repository already has its own process-spawn library/guard:** use
  that repository's canonical primitive first (e.g. copilot-extensions'
  `agent-procutil` + `windows-background-process-launch` pattern); this
  plugin's skill is the general fallback, not a replacement for an
  already-adopted one.

Contributions follow the repository's PR-required workflow in
[`CONTRIBUTING.md`](../../CONTRIBUTING.md). File issues in the
[`ThomasMichon/copilot-extensions`](https://github.com/ThomasMichon/copilot-extensions/issues)
repository.
