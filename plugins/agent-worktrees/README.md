# Agent Worktrees

Copilot CLI plugin for worktree-isolated sessions. Every Copilot CLI
session gets its own git worktree -- no branch conflicts, no stale state,
no stepping on parallel sessions.

agent-worktrees is **standalone-first**: enable this plugin, bootstrap the
runtime, and register any git repo you want to manage. You do **not** need a
separate "control harness", agent-bridge, CodeSpaces, containers, or any other
sibling plugin to use local worktree isolation. Those sibling plugins compose
gracefully when installed (extra picker pivots, cross-machine delegation,
CodeSpace/container resolvers), but their absence only disables those features.

## How It Works

Agent Worktrees has three shipped pieces:

- **Plugin payload** (skills, hooks, live-pulse extension) -- loaded by Copilot
  CLI when the plugin is enabled
- **Runtime** (versioned Python CLI) -- manages worktrees, launches sessions,
  handles finalization, and self-provisions on first CLI use when possible
- **Project binstubs** -- one launcher per registered project, plus the global
  `agent-worktrees` CLI

Project launchers are attributable global entry points. Each launcher invokes
the absolute payload-local agent-worktrees command that created it and carries
an ownership receipt under `~/.agent-worktrees/binstub-receipts/`. Routine
install, update, repair, and reconciliation refuse to replace a command owned
by another payload, replace any unreceipted command, or remove a receipt-owned
command whose bytes changed. Legacy launchers require the same explicit
transfer as every other unreceipted file. Windows command names are compared
case-insensitively. An operator can explicitly transfer a registered project
command with:

```bash
agent-worktrees reconcile-binstubs --transfer <project>
```

The plugin installs via the Copilot CLI marketplace. The runtime installs
via init/install scripts (or first-use provisioning from the global binstub)
and provides the `agent-worktrees` CLI and per-project binstubs.

## Same-Machine AHP Sessions

Interactive worktree sessions can optionally be hosted by one same-machine
`copilotd` over Agent Host Protocol (AHP). Agent-worktrees remains authoritative
for repository and worktree lifecycle; the host owns the durable Copilot
session. A mux pane is then only an attachable client, so closing it does not
destroy the hosted transcript.

The backend is machine-local and off by default. Configure an explicit loopback
WebSocket endpoint and GitHub account under `session_backend`, then use the
normal picker/create/resume flow. The launcher creates or verifies one exact
AHP session for the worktree and starts Copilot hard-bound to that session.
Finalization refuses while the binding is active or unknown; explicitly run
`agent-worktrees session-backend dispose` after the hosted session is no longer
needed. See the
[Configuration Reference](docs/config-reference.md#same-machine-ahp-session-backend--session_backend)
and [CLI Reference](docs/cli-reference.md#session-lifecycle).

## Balanced Profile Assignment

Agent-worktrees can optionally assign eligible new interactive session
generations from a user-owned pool of existing `copilot_profiles` using a
balanced shuffled bag. The feature is off by default. Once the policy is valid,
explicit `--profile` selection remains authoritative; recovery, ACP/bridge,
system/delegated, and base-repo launches remain outside assignment. Invalid
armed user configuration fails before any worktree mutation even on those
excluded launch paths. An excluded launch, unassigned older session, replay
whose saved profile is unavailable, or optional assignment-state failure keeps
the concrete ordinary default/manual profile the launch path already selected.

Assignments are persisted before launch, reused by launch retries, bound to the
actual Copilot session id at registration, and replayed on ordinary resume.
Handoff cutover successors draw a new generation only while the policy is
armed. See the
[Configuration Reference](docs/config-reference.md#balanced-profile-assignment--profile_assignment)
for the trust boundary, generic example, supported lanes, one-shot private
token handling, and graceful fallback behavior.

## Status Bar at a Glance

Every worktree session runs inside a multiplexer (psmux on Windows, tmux on
Linux/WSL) with a status bar that reads the worktree's identity and git
disposition **live, per pane** -- the `#()` jobs run in each pane's own
directory, so a split or second window reports its own worktree, not the
session's.

**Left segment** (`status-context`) -- who and where you are:

```
 anomalous-potato  [ win ]  test-chamber:8e45
```

- **Machine** -- the host designation (black)
- **Environment** -- platform as a color-coded badge keyed on OS type
  (win = blue, wsl = purple, linux = orange), so a Windows pane and a WSL
  pane are distinguishable at a glance
- **Repo : id4** -- repo name plus the worktree id's last 4 hex (bold), so
  you always know which of several parallel worktrees a pane belongs to

**Right segment** (`status-segment`) -- what state the work is in:

| Block | Meaning |
|-------|---------|
| `DIRTY` (red) | Uncommitted changes in the working tree |
| `WIP` (amber) | Clean; committed work not yet on the default branch |
| `FINAL` (green) | Clean; work landed / fast-forwardable upstream |
| `CONVO N💬` (teal) | No commits, but the session held *N* conversation turns -- real work that an `UNUSED` label would hide |
| `UNUSED` (grey) | No commits **and** no conversation since the fork point |
| `ORPHAN` (magenta) | No merge base with upstream |

The state is classified content-aware (squash-merged work reads `FINAL`, not
`WIP`) and is annotated with a `↑ahead`/`↓behind` sync tag. The `CONVO` state
draws on the same turn-count detection that keeps `cleanup` from reaping a
worktree whose session held conversation but no commits -- so an
idle-*looking* tree that actually holds work is never mistaken for unused.

See the [CLI Reference](docs/cli-reference.md#status-bar-segment-tmux--psmux)
for the full state table and flags.

On Linux/WSL the bar is applied **per tmux session** by the launcher --
agent-worktrees does not deploy, overwrite, or delete your global
`~/.tmux.conf`. Server-global tuning that can't be session-scoped (keystroke
passthrough, `escape-time`) is an **opt-in** `apply-mux-keybinds.sh` you run
yourself; it persists a clearly-marked managed block in `~/.tmux.conf` (so it
survives restarts) and applies to any running server. (Windows/psmux works the
same way: per-session `session-options.ps1` + opt-in `apply-mux-keybinds.ps1`;
agent-worktrees no longer owns `~/.psmux.conf`.) See the CLI Reference's
*Per-session, not global* note for details.

## Getting Started

See [Getting Started](docs/getting-started.md) for the minimal standalone path:
install, runtime bootstrap, project registration, and first launch. Then use
the [CLI Reference](docs/cli-reference.md) for headless/scripted commands and
the skills below for in-session guidance.

## Docs

| Document | Description |
|----------|-------------|
| [Getting Started](docs/getting-started.md) | Install, adopt a repo, launch sessions |
| [The Worktree Picker](docs/picker.md) | The interactive launcher — screen anatomy, navigation, resume/create/clean/sync, launch-time freshness |
| [Multiplexed Sessions](docs/mux.md) | Why sessions run in tmux/psmux — persistence, detach/rejoin, and muxed-vs-programmatic launch |
| [Worktree Lifecycle & Change Management](docs/worktree-lifecycle.md) | The full landing flow — states, direct-push and PR mode, held/follow-up and serial-vs-parallel PRs |
| [Architecture](docs/architecture.md) | Plugin/runtime layers, installed layout, session lifecycle |
| [CLI Reference](docs/cli-reference.md) | Commands, installer actions, config format |

## Validation

Fresh-machine install/bootstrap behavior is covered by the repo's clean-room
rig. The
[`agent-worktrees-solo`](../../tools/clean-room/scenarios/agent-worktrees-solo)
scenario installs **only** agent-worktrees, verifies first-use runtime
provisioning and the versioned runtime slot, then round-trips register → create
→ finalize. Run or extend that scenario for installer, bootstrap, or
standalone-behavior changes.

## Skills

| Skill | Description |
|-------|-------------|
| `worktree` | Worktree lifecycle -- creation, finalization, cleanup, safety rules |
| `git-collaboration` | Pull-forward and shared feature-branch git primitives |
| `service-lifecycle` | Service installer patterns -- deploy, update, status |
| `copilot-extensions-setup` | Bootstrap agent-worktrees and optional sibling plugins |
| `agent-worktrees-wsl-provision` | Provision the current project in WSL |
| `agent-worktrees-repos` | Repos registry -- known repos and source roots |
| `agent-worktrees-related` | Directional related-repo index and locus/delegation plan |
| `resolving-state-home` | Native stateless-harness state routing and paired knowledge-worktree resolution |
| `repairing-worktrees` | Diagnose/repair worktree+session health via `doctor` |
| `create-setup-script` | Generate repo-specific session setup scripts |
| `working-cross-repo` | Good-citizen workflow for work in another registered repo |

## Hooks

| Hook | Trigger | What it does |
|------|---------|--------------|
| `preToolUse` | Tool calls | Runs statelessness, cross-repo, and anchor-write guards from `~/.agent-worktrees/bin/` when deployed |
| `sessionStart` | Every session | Emits worktree/account/machine context, reload guidance, runtime bootstrap hints, repo-plugin provisioning, project hooks, session registration, anchor hygiene, and provision checks. When cwd/env identity is absent, registration recovers the exact binding from the session lock PID -> process ancestry -> owning `wt-<id>` mux pane/session |
| `sessionEnd` | Session end | Reads the authoritative hook payload, resolves the exact prior association, and closes the latest activation interval without concluding the resumable session |

The bundled live-pulse extension writes `substatus.json` beside Copilot session
state so the picker can show live intent/rest state. It is passive; durable
worktree disposition still comes only from `agent-worktrees status`.

## Platforms

| Platform | Installer | Terminal integration |
|----------|-----------|---------------------|
| Windows | `install.ps1` | Windows Terminal fragments, psmux |
| Linux/WSL | `install.sh` | tmux, Tabby profiles |
| macOS | `install.sh` (POSIX path; reported as `linux` internally) | tmux where available |
