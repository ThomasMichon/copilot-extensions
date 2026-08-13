# The engine ↔ Picker `--json` contract

The interactive front-end (the **Worktree Manager**'s Picker, Phase 6b — and the
still-bundled Picker until Phase 6c) reaches the `agent-worktrees` engine **only**
by shelling out to its machine-readable CLI verbs: `<project> <verb> --json`,
never `import agent_worktrees`. That process boundary is what keeps the coupling
one-way and dependency-free (the Picker owns no worktree logic or state), and it
is why the TUI framework (Textual) stays entirely out of the plugin engine.

Because the Manager self-updates **out of band** from the plugin (it wraps
Copilot launches; the plugin can't self-install mid-session), the two can be at
different versions on the same machine. So the `--json` verbs the Picker consumes
are a **stable, versioned contract**: the engine keeps them backward-compatible
within a contract window, and a newer Manager tolerates an older engine —
degrading a single feature rather than failing (the *version-skew-tolerant
contract* property).

This document **pins** that contract: the verbs, their required flags, and the
compatibility rules. Change a pinned verb's output shape only additively;
retiring or renaming one is a breaking change that must bump the contract
version and be coordinated with the Manager.

> **Contract version: `1`.** Bump on any breaking change (removed verb, removed
> or retyped field, changed flag semantics). Additive fields do **not** bump it.

## Invariants for every pinned verb

- **`--json` → stdout is JSON only; stderr is logs only.** No TTY prompts, no
  color, no picker. `--json` implies `--no-mux`.
- **Non-zero exit on error, with a JSON error envelope on stdout.**
- **Project scope is explicit.** The Picker always names the project — either the
  `<project>` binstub or `agent-worktrees --project <name>` — never relying on an
  ambient cwd.
- **Unknown flags degrade, not crash.** A verb must treat an unrecognized
  forward-compat flag it does not know as a no-op where practical, so a newer
  Manager passing a new flag to an older engine still gets a valid response
  (the Picker already falls back — e.g. dropping `--classify` — when an older
  engine rejects it).
- **Additive-only evolution.** New fields may appear; existing fields keep their
  name and type within a contract version.

## Pinned read verbs (the Picker's data plane)

| Verb (as invoked) | Purpose | Notes |
|---|---|---|
| `<project> list --json --classify --mux-details` | The core enumeration — every worktree with git-derived `state`, sync tags, and mux details. | `--include-other-platforms` (Windows), `--cache-only` (fast paint), `--stream` (incremental) are **optional** accelerators; the engine must still answer without them. An engine too old for `--classify` is tolerated by re-running without it. |
| `<project> list-sessions --worktree <id> --json` | Sessions belonging to a worktree. | |
| `<project> recent-messages --worktree <id> --limit N --json` | Last few conversation turns (the read-only Messages overlay). | |
| `<project> profiles get --json` | Current backend-profile grid. | |
| `<project> get <key>` | Scalar project value (e.g. `machine`, paths). | Plain value on stdout, **not** JSON — a deliberate exception for single-scalar reads. |

## Pinned action verbs (the Picker's control plane)

| Verb (as invoked) | Purpose |
|---|---|
| `<project> create [--json]` | Make a worktree, no launch (the programmatic "New worktree"). |
| `<project> resolve [--worktree-id <id>] [--new] [--bare-resume]` | Emit the JSON launch plan the front-end acts on (resume / create-and-launch / bare-resume). |
| `<project> restart <id> --json` | Restart a worktree's session. |
| `<project> reclaim --worktree-id <id> …` | Kill the exact bound orphan process so a session can be re-opened. |
| `<project> finalize <id> --json` | Finalize a merged/completed worktree. |
| `<project> sync --worktree-id <id> --json` | Fast-forward a clean, strictly-behind worktree. |
| `<project> cleanup [--worktree-id <id>] [--clean] [--bare-only --yes] --json` | Remove completed/gone worktrees (single or bulk). |
| `<project> profiles apply --json …` | Apply / reset the backend-profile grid. |

## What is *not* in the contract

- **Human-formatted (non-`--json`) output.** Only the machine-readable shapes
  above are pinned; the pretty CLI rendering may change freely.
- **The bare, no-args invocation itself.** `<project>` with no args is the
  **seam** (DQ7): it resolves to the front-end (Manager if on PATH, else the
  bundled Picker) — it is not a data verb and returns no contract payload.
- **Internal helpers** the engine does not expose as `--json` verbs.

## See also

- [picker.md](picker.md) — the operator walkthrough of the front-end.
- [cli-reference.md](cli-reference.md) — the full verb catalog.
- Effort: `efforts/active/copilot-extensions/installer-configurator/` — the
  Phase 6 design (the binstub seam, DQ7/DQ8/DQ9) this contract serves.
