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
owns their lifecycle — the two backends point at **different** likely layers,
because they don't share a server the same way:

- **tmux (Linux/WSL) runs one shared server per machine.** A single tmux
  server exit (crash, OOM-kill, a host reboot) takes every session on that
  host down together — this *is* a plausible explanation for simultaneous
  loss on Linux/WSL.
- **psmux (Windows) runs a separate server per `wt-<id>` session** (each
  session's keybind config is sourced into its own server, never a shared
  one). A single psmux server exiting only takes down **its own** session —
  it cannot by itself explain several unrelated Windows sessions dying at
  the same time. On Windows, simultaneous multi-session loss points instead
  at something **common to all of those independent server processes**: the
  console-host layer each one depends on, or a host-wide event (e.g. a
  Windows Update reboot, a shared binary crash-looping under every server).

Check the host's crash/application event log for the shell binary (`pwsh.exe`
on Windows, the Application log, Event ID 1000) **and** for `psmux.exe`/
`conhost.exe` themselves — on Windows a cluster of simultaneous session
deaths is most consistent with a shared console-host defect common to every
session's otherwise-independent server, not one server's exit explaining the
rest.

**An observed compatibility signature:** PowerShell 7.6.6 (ARM64)'s
`Console Host` (`Microsoft.PowerShell.ConsoleHost.dll`) has been seen
fast-failing repeatedly (`Environment.FailFast`, exception code
`0x80131623`, same binary fault offset across occurrences) when hosted
inside a `psmux`-managed redirected console — i.e. exactly the pattern every
muxed Windows worktree session runs under. Each crash kills the `pwsh.exe`
process backing that pane; since every independent psmux server on the host
hosts the same crash-prone binary, a burst of unrelated-looking "session
gone" reports across different `wt-<id>` sessions can land within the same
few minutes without any single shared process tying them together. Treat
this as a reproducible compatibility signature to rule in or out, not a
confirmed root cause: the same fault code has also been reported upstream as
a downstream symptom of a `conhost.exe` failure. Inspect `psmux.exe`/
`conhost.exe` events alongside `pwsh.exe` before concluding which layer
actually failed first.

**Mitigation, if this signature matches:** pin the host's PowerShell install
to the 7.4 LTS line instead of latest-stable 7.6.x as a **time-boxed**
compatibility workaround, not a permanent fix — 7.4 reaches end of support on
**2026-11-10**. Before that date, reassess whether a fixed 7.6.x (or later)
build resolves the crash and migrate off the pin; do not let a declarative
pin silently carry a host onto an unsupported PowerShell release past its
support window. If you manage the host through `agent-machines`, express the
pin as a declarative `package` resource (`manager: winget`, `id:
Microsoft.PowerShell`, an explicit `version`, and `pin: true`, optionally a
`process_guard` on `pwsh.exe` so an in-progress restore defers rather than
races a live session) so the pin survives machine re-provisioning **until
you deliberately remove it**. See `docs/resources.md` in the `agent-machines`
plugin for the resource schema.

**Known limitation — this declares the pin, it does not downgrade a newer
install for you.** `agent-machines`' winget package resource converges a
version mismatch with `winget upgrade --version <pinned>` (see
`resources.py`'s `update` command), which winget treats as a no-op when the
installed version is already *newer* than the pin target ("No available
upgrade found") — winget has no downgrade verb. If the host already has
7.6.x installed, `agent-machines restore --apply` will **not** actually
replace it; you must uninstall the newer package yourself first (for
example `winget uninstall --id Microsoft.PowerShell --exact --version
7.6.6.0`) before a restore can install and pin the 7.4.x target. A fresh
restore on a host that has never installed PowerShell before converges
correctly with no manual step.

## See also

- [The Worktree Picker](picker.md) — the launcher that creates/rejoins muxed
  sessions.
- [Worktree Lifecycle & Change Management](worktree-lifecycle.md) — the
  `resolve` / `resolve --new` / `create` modes in the lifecycle.
- [CLI Reference](cli-reference.md) — `status-segment`, `status-updater`, and the
  per-session vs opt-in mux configuration.
