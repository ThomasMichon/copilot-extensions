# Multiplexed Sessions — Why and When

agent-worktrees runs your interactive Copilot sessions inside a **terminal
multiplexer** — `tmux` on Linux/WSL, `psmux` on Windows. This page explains
*why* that matters and *when* you want a muxed session versus a plain,
non-muxed worktree. For the mechanics (status bar, per-session config, the
opt-in keybinds) see [cli-reference.md § Status bar segment](cli-reference.md#status-bar-segment-tmux--psmux);
for the launch surface see [The Worktree Picker](picker.md).

## Why a multiplexer

A worktree is meant to **outlive any one terminal**. The multiplexer is what
makes that true — the session survives things that would otherwise kill it:

- **The session persists past the terminal.** Each launched worktree gets a
  named mux session (`wt-<id>`). Close the terminal, drop the SSH connection, or
  reboot your local terminal app, and the session — and the running Copilot
  agent — keep going. Reconnect and **rejoin** exactly where you left off.
- **Copilot exiting ≠ the session ending.** When the Copilot process exits the
  mux session stays alive, so `/restart` and re-launch work without tearing down
  the worktree.
- **Detach and rejoin at will.** Detach to background a long-running agent and
  reattach later — from the same terminal or a different one (including over
  SSH).
- **Parallel panes.** Multiple worktree sessions (and multiple machines) run as
  independent mux sessions you can move between, without one blocking another.

This is the backbone of "a worktree can outlive any one terminal, shell, or
Copilot session."

## When you want a mux — and when you don't

There are three ways to enter a worktree; the difference is **who's driving** and
**whether a session launches**:

| You are… | Use | Muxed? | Why |
|----------|-----|--------|-----|
| A human at a terminal, picking or resuming | `my-project` (the **Picker**) / `agent-worktrees resolve` | **Yes** — creates or **rejoins** the `wt-<id>` session | Persistent, detachable interactive work |
| A human who wants a brand-new session now | `agent-worktrees resolve --new` | **Yes** — launches a fresh muxed session | Same, skipping the picker (refused without a TTY) |
| An agent, daemon, or script | `agent-worktrees create [--json]` | **No** — prints the worktree path, launches nothing | Programmatic callers edit in their *current* process; a mux would just get in the way |

Rule of thumb: **interactive human work → muxed** (persistence + detach/rejoin);
**automated/programmatic work → `create` (no mux)**, then operate on the printed
path in your existing session. `--new` is explicitly **refused without a TTY**,
so a tool call can never accidentally spawn an interactive mux — use `create`.

> **Windows over SSH:** the interactive TUI picker auto-falls back to a simpler
> flow (a ConPTY limitation), but the muxed-session model is the same. See
> [picker.md](picker.md).

## Detach and rejoin

- **Detach** with the multiplexer's own detach key (tmux default `Ctrl-b d`;
  psmux equivalent) — the `wt-<id>` session keeps running in the background.
- **Rejoin** by relaunching the project binstub and picking the worktree (the
  Picker **resumes** the existing session rather than starting a second one), or
  by attaching to the mux session directly.

The status bar of a muxed worktree session shows its identity and live git
state; that's the same `status-segment` / `status-updater` machinery documented
in [cli-reference.md](cli-reference.md#status-bar-segment-tmux--psmux).

## Two backends, one model

`tmux` (Linux/WSL) and `psmux` (Windows) are different implementations of the
**same** model — a named, detachable session with a status bar. agent-worktrees
configures them **per session** (`set -t <session>`, never a global `-g`), so it
**does not own or overwrite** your personal `~/.tmux.conf` / `~/.psmux.conf`, and
ad-hoc mux sessions you start yourself are untouched. The few server-global
settings it can't scope per-session (keystroke passthrough, `escape-time`) live
in an **opt-in** `apply-mux-keybinds.{sh,ps1}` you run only if you want them.
Full detail: [cli-reference.md § Status bar segment](cli-reference.md#status-bar-segment-tmux--psmux).

## See also

- [The Worktree Picker](picker.md) — the launcher that creates/rejoins muxed
  sessions.
- [Worktree Lifecycle & Change Management](worktree-lifecycle.md) — the
  `resolve` / `resolve --new` / `create` modes in the lifecycle.
- [CLI Reference](cli-reference.md) — `status-segment`, `status-updater`, and the
  per-session vs opt-in mux configuration.
