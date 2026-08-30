# The engine ↔ Picker `--json` contract

The interactive front-end (the **Worktree Manager**'s Picker, Phase 6b — and the
still-bundled Picker until Phase 6c) reaches the `agent-worktrees` engine **only**
by shelling out to its machine-readable CLI verbs: `<project> <verb> --json`,
never `import agent_worktrees`. That process boundary is what keeps the coupling
one-way and dependency-free (the Picker owns no worktree logic or state), and it
is why the TUI framework (Textual) stays entirely out of the plugin engine.

## Provider-backed source registry

Venue providers may add project-scoped Picker sources by writing JSON registry
documents under `~/.agent-worktrees/sources/` (or
`AGENT_WORKTREES_SOURCES_DIR`). Each file owns one provider namespace and uses:

```json
{
  "schema_version": 1,
  "provider": "example-provider",
  "sources": [
    {
      "kind": "provider-exec",
      "project": "example-project",
      "target_id": "target:stable-id",
      "instance_id": "replaceable-instance-id",
      "label": "Restricted target",
      "alias": "restricted-target",
      "shell": "bash",
      "resolve": ["/absolute/provider-command", "resolve", "target"],
      "connect": ["/absolute/provider-command", "connect", "target"],
      "venue": {
        "provider": "example-provider",
        "target_id": "target:stable-id",
        "instance_id": "replaceable-instance-id",
        "transport": "provider-exec",
        "ready": true,
        "posture_verified": true,
        "assignment": {
          "kind": "lease",
          "effort": "example-effort",
          "acquired_at": 1700000000.0
        }
      },
      "capabilities": {
        "list": true,
        "messages": true,
        "sessions": true,
        "refresh": true,
        "create": false,
        "resume": false,
        "cleanup": false
      }
    }
  ]
}
```

The provider and stable target ID form the canonical source ID. The replaceable
instance ID does not: when live resolution reports a different instance, the
Picker invalidates rows derived from the prior instance before fetching again.
The assignment identifies the provider-owned lease captured at registration.
Live resolution must return the same assignment; a released or reassigned target
fails closed even if its stable target name is reused.
Descriptors are filtered to the active project, validated independently of
provider packages, and rejected unless their provider/target/instance identity,
assignment, transport, readiness/trust fields, capabilities, exact SSH alias,
and absolute resolve and connection commands are structurally valid. On POSIX,
the registry directory and descriptor files must be owned by the current user
and must not be group/world-writable. Display labels are bounded and may not
contain control characters. Schema version 1 permits only `list`, `messages`,
`sessions`, and `refresh` to be enabled. Mutation dispatch also rejects
provider sources independently of the capability map. One malformed source
entry does not suppress valid siblings in the same provider document, while
every descriptor sharing an ambiguous canonical source ID is rejected.

Each Picker setup or refresh captures the machine roster and provider registry
once. Tabs and the live loader consume that same snapshot, so a concurrent
registration cannot create a tab without a matching loader source (or vice
versa).

Provider rows retain blank legacy `machine` and `env` fields; they are scoped by
`source_id` and display `source_label` rather than inventing a physical-machine
identity. A source whose descriptor is not ready and posture-verified remains a
disabled tab and is not contacted. Operations fail closed against the source
capability map. The first
provider-exec contract permits only listing, recent-message/session reads, and
source refresh; unsupported create, launch, lifecycle, maintenance, profile,
and contributed worktree actions are not rendered and cannot fall through to a
local execution path. Row selection uses canonical source identity plus the full
worktree ID for provider rows; the four-character suffix remains display-only.
Message and session reads re-resolve the venue immediately and require the
row's recorded instance and assignment before SSH dispatch. Provider reads use
the descriptor's absolute connection command directly as an explicit
`ProxyCommand`; the provider rechecks the stable target, instance, and lease
assignment while admitting that exact connection. Active provider reads block
lease release and expired-lease reassignment, so validation and transport cannot
straddle a reassignment. The row also records a fingerprint of the provider's
alias, shell, resolve argv, and connection argv; immediate reads reject a
registry rewrite that changes any transport field after the row was displayed.
Provider worktree IDs are quoted as one remote-shell argument before execution.
The provider command uses an owner-private stable launcher that resolves the
currently active runtime and starts it in Python isolated mode, so registrations
survive runtime-slot updates without admitting project-local module shadowing.

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
- **Provider invocation is attributable.** The agent-worktrees front door hands
  the Manager an exact immutable runtime argv. A directly-invoked Manager
  uses the deployment manifest as the provider identity attestation, then
  follows the provider's marker/fallback order to its immutable runtime. It never
  resolves the engine from an ambient same-named command on `PATH`.
- **Unknown flags degrade, not crash.** A verb must treat an unrecognized
  forward-compat flag it does not know as a no-op where practical, so a newer
  Manager passing a new flag to an older engine still gets a valid response
  (the Picker already falls back — e.g. dropping `--classify` — when an older
  engine rejects it).
- **Additive-only evolution.** New fields may appear; existing fields keep their
  name and type within a contract version.
- **Invoked off the render flow.** Because every read and action is a subprocess,
  the Picker calls these verbs from **background workers**, never on its Textual
  event-loop thread — reads via the `LiveLoader` threads, actions via
  `PickerScreen._run_bg`. A slow or hung verb must degrade a single row/action, not
  freeze the UI (see the render-flow invariant in
  [architecture.md](architecture.md#the-picker-render-flow----never-block-on-cross-processio-invariant)).

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
| `<project> resolve --json (--worktree-id <id> \| --new \| --base) [--bare-resume] [--machine <name> --environment <env> --target-no-mux]` | Emit the launch plan the front-end acts on: local resume/create/base-repo execution, or an environment-specific remote SSH handoff carrying the same selection. |
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
