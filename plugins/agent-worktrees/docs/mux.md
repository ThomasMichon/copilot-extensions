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

An interactive launch retries multiplexer session creation up to three times
before treating it as failed. If all attempts fail in an interactive terminal,
the launcher offers an explicit retry; declining or exhausting recovery reports
the preserved worktree path and the command that retries that same worktree. A
non-interactive caller never blocks on the prompt. The launcher never silently
starts a bare Copilot process instead. Use `--no-mux` (or
`WORKTREE_NO_MUX=1`) only when you intentionally want the direct,
non-persistent diagnostic path.

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
in [cli-reference.md](cli-reference.md#status-bar-segment-tmux--psmux). A
`MERGED`/`FINAL` block there can carry markers of its own -- a compact
`C<N>`/`F<N>` for held claims / open follow-ups, and an independent `U*`/`OC*`
for an unconfirmed upstream-containment / claims fact (worktree-finality-and-
obligations Phase 9's per-fact freshness markers) -- see
[worktree-lifecycle.md § Decomposed sub-state facts](worktree-lifecycle.md#decomposed-sub-state-facts-phase-9)
for what each one means.

## Two backends, one model

`tmux` (Linux/WSL) and `psmux` (Windows) are different implementations of the
**same** model — a named, detachable session with a status bar. The launcher
(relocated to Worktree Manager's `bin/` in Phase 3b Sub-slice 2a Step 2)
configures them **per session** (`set -t <session>`, never a global `-g`), so
**neither agent-worktrees nor Worktree Manager owns or overwrites** your
personal `~/.tmux.conf` / `~/.psmux.conf`, and ad-hoc mux sessions you start
yourself are untouched. The few server-global settings it can't scope
per-session (keystroke passthrough, `escape-time`) live in an **opt-in**
`apply-mux-keybinds.{sh,ps1}` you run only if you want them.
Full detail: [cli-reference.md § Status bar segment](cli-reference.md#status-bar-segment-tmux--psmux).

## Troubleshooting: every mux session died at once

If every `wt-<id>` session on a host disappeared together — not one worktree,
**all of them**, with no `finalize`/`cleanup` run and no scheduled task that
owns their lifecycle — the multiplexer itself almost certainly didn't fail;
its **backing shell process did**, and every pane sharing that host lost its
process at the same time. Check the host's crash/application event log for
the actual shell binary (`pwsh.exe` on Windows, the Application log, Event ID
1000) before assuming a mux or agent-worktrees defect: a `tmux`/`psmux` server
only reports a session as gone once the process it was holding onto exits,
so a cluster of simultaneous session deaths reads as a mux problem but is
really a shell-runtime problem underneath it.

**A confirmed repeat offender:** PowerShell 7.6.6 (ARM64)'s `Console Host`
(`Microsoft.PowerShell.ConsoleHost.dll`) has fast-failed repeatedly
(`Environment.FailFast`, exception code `0x80131623`, same binary fault
offset across occurrences) specifically when hosted inside a `psmux`-managed
redirected console — i.e. exactly the pattern every muxed Windows worktree
session runs under. Each crash kills the `pwsh.exe` process backing that pane;
with several panes sharing the same crash-prone binary, a burst of unrelated-
looking "session gone" reports can land within the same few minutes. The
multiplexer, agent-worktrees, and the worktree lifecycle are all innocent here
— this is a host PowerShell packaging defect, not a mux defect.

**Mitigation:** pin the host's PowerShell install to the 7.4 LTS line instead
of latest-stable 7.6.x until the upstream regression is resolved (7.4.20 is
supported through 2026-11-10 at time of writing). If you manage the host
through `agent-machines`, express this as a declarative `package` resource
(`manager: winget`, `id: Microsoft.PowerShell`, an explicit `version`, and
`pin: true`, optionally a `process_guard` on `pwsh.exe` so a live session
defers the downgrade rather than racing it) rather than a one-off manual
`winget` command, so the pin survives machine re-provisioning. See
`docs/resources.md` in the `agent-machines` plugin for the resource schema.

## See also

- [The Worktree Picker](picker.md) — the launcher that creates/rejoins muxed
  sessions.
- [Worktree Lifecycle & Change Management](worktree-lifecycle.md) — the
  `resolve` / `resolve --new` / `create` modes in the lifecycle.
- [CLI Reference](cli-reference.md) — `status-segment`, `status-updater`, and the
  per-session vs opt-in mux configuration.
