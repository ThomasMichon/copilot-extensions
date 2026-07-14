# Pattern: cross-platform-parity

**Serves:** *Vision plugin-services* — cross-platform parity underpins every
Feature/Behavior (a plugin behaves the same on Windows and Linux/WSL).
**Exemplars:** all runtime plugins (agent-worktrees, agent-bridge, agent-dispatch, …).

## Problem

Every runtime plugin ships on **Windows and Linux/WSL**. Users expect identical
*behavior*; the platforms differ in shells, encodings, service supervision, and —
under WSL — a shared network namespace. Parity must be engineered at the edges, not
left to leak into behavior.

## Standard approach

**Dual installers, one behavior.** Ship both `scripts/*.ps1` and `scripts/*.sh`
with the same lifecycle verbs and the same resulting runtime. Platform branches
live in the installer/binstub/supervisor — never in the plugin's user-visible
behavior.

**Shell version discipline.**

- A `.ps1` that must run during early bootstrap (before pwsh 7 exists) stays
  **Windows-PowerShell-5.1-safe** and ASCII-only.
- A `.ps1` that needs modern behavior (or UTF-8 output) declares
  `#Requires -Version 7.0`.
- Avoid PowerShell-7-only operators (`&&`, `||`, `??`, `?.`) in 5.1-compatible
  scripts.

**UTF-8 is established, not assumed.** Human-facing glyphs (`✓`, `→`, …) are fine
**only** in a stream proven UTF-8: Python that reconfigures **both** stdout *and*
stderr (or the shared `ensure_utf8_stdio()` shim); PowerShell that is `#Requires
-Version 7.0` (and ideally sets `[Console]::OutputEncoding`). Machine-parsed
output (JSON, manifest keys) stays ASCII regardless.

**The flag-dash rule.** Use the ASCII hyphen-minus (`-`) in command-line **flag
position** — never an em/en dash (`—`/`–`), which silently breaks argument
parsing. Dashes in prose/comments/strings are fine.

**Binstub shape.** On Windows, ship a single `.cmd` binstub (and remove any stale
same-named `.ps1` that would shadow it): a `.cmd` forwards `stdin` verbatim, which
a stdio MCP server requires, and launches the venv `python -m <pkg>`. On POSIX,
ship a bash stub doing the same.

**The WSL/Windows boundary.** Under WSL mirrored networking the guest and host
share one `127.0.0.1`, so any host+guest pair of the *same* service must deconflict
their endpoints — handled by the [local-endpoint-discovery](local-endpoint-discovery.md)
pattern, not by leaking a platform check into behavior. Note that AF_UNIX sockets do
**not** cross the WSL⇄Windows boundary; a cross-boundary endpoint is its own design
decision, not an accident.

## Rationale

Parity keeps the plugin's contract identical everywhere, so docs, skills, and
muscle memory transfer across machines. Pushing every platform difference to the
installer/binstub/supervisor edge keeps the behavioral core single-sourced.

## See Also

- Intent: [`visions/plugin-services/`](../../visions/plugin-services/README.md)
- Hub: [`docs/patterns/`](README.md) · Endpoint boundary: [local-endpoint-discovery](local-endpoint-discovery.md)
