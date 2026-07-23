# Agent Worktrees

Copilot CLI plugin for worktree-isolated sessions. Every Copilot CLI
session gets its own git worktree -- no branch conflicts, no stale state,
no stepping on parallel sessions.

## How It Works

Agent Worktrees has two layers:

- **Plugin** (skills, hooks) -- loaded into every Copilot CLI session
- **Runtime** (Python CLI) -- manages worktrees, launches sessions,
  handles finalization

The plugin installs via the Copilot CLI marketplace. The runtime installs
separately via init scripts and provides the `agent-worktrees` CLI and
per-project binstubs.

## Status Bar at a Glance

Every worktree session runs inside a multiplexer (psmux on Windows, tmux on
Linux/WSL) with a status bar that reads the worktree's identity and git
disposition **live, per pane** -- the `#()` jobs run in each pane's own
directory, so a split or second window reports its own worktree, not the
session's.

**Left segment** (`status-context`) -- who and where you are:

```
 lambda-core  [ win ]  aperture-labs:8e45
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

See [Getting Started](docs/getting-started.md) for install, repo
adoption, and session launch.

## Docs

| Document | Description |
|----------|-------------|
| [Getting Started](docs/getting-started.md) | Install, adopt a repo, launch sessions |
| [The Worktree Picker](docs/picker.md) | The interactive launcher — screen anatomy, navigation, resume/create/clean/sync, launch-time freshness |
| [Multiplexed Sessions](docs/mux.md) | Why sessions run in tmux/psmux — persistence, detach/rejoin, and muxed-vs-programmatic launch |
| [Worktree Lifecycle & Change Management](docs/worktree-lifecycle.md) | The full landing flow — states, direct-push and PR mode, held/follow-up and serial-vs-parallel PRs |
| [Architecture](docs/architecture.md) | Plugin/runtime layers, installed layout, session lifecycle |
| [CLI Reference](docs/cli-reference.md) | Commands, installer actions, config format |

## Skills

| Skill | Description |
|-------|-------------|
| `worktree` | Worktree lifecycle -- creation, finalization, cleanup, safety rules |
| `service-lifecycle` | Service installer patterns -- deploy, update, status |
| `copilot-extensions-setup` | Install and adopt for all three plugins |
| `agent-worktrees-wsl-provision` | Provision the current project in WSL |
| `agent-worktrees-repos` | Repos registry -- known repos and source roots |
| `repairing-worktrees` | Diagnose/repair worktree+session health via `doctor` |
| `create-setup-script` | Generate repo-specific session setup scripts |
| `agent-ssh` | SSH transport helpers |

## Hooks

| Hook | Trigger | What it does |
|------|---------|--------------|
| `sessionStart` | Every session | Verifies runtime is installed; prints setup hint if not |

## Platforms

| Platform | Installer | Terminal integration |
|----------|-----------|---------------------|
| Windows | `install.ps1` | Windows Terminal fragments, psmux |
| Linux/WSL | `install.sh` | tmux, Tabby profiles |
| macOS | Planned | -- |
