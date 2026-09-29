"""Spawn a genuinely NEW, VISIBLE terminal window attached to a worktree's
mux session -- the "headed" counterpart to :func:`handoff_cli.cmd_embody`'s
otherwise-always-detached embodiment (#embody-headed).

Every other process-spawning helper in this codebase (``agent_procutil``'s
``windowless_python``/``detached_kwargs``/``no_window_kwargs``) exists
specifically to SUPPRESS a console window -- this module is the deliberate,
narrow exception: an operator explicitly asked for a real, visible terminal
window (e.g. from the Worktree Manager Picker's "Launch in new window"
action), so ``embody --headed`` must open one, not silently degrade to
another headless/detached session.

Design: this does NOT reimplement mux attach semantics. It shells out to the
platform's own terminal-spawning mechanism to run a single, simple
``<mux_bin> attach-session -t <session>`` command in a brand-new window --
nothing more. Any load-bearing attach-time behavior (seeding, Ctrl+C
forwarding, environment) belongs to the session the worktree already embodied
before this is called; this module only makes that existing session visible.
"""

from __future__ import annotations

import platform
import re
import shutil
import subprocess

# Mirrors psmux-path.ps1's Test-AwPsmuxVersionCompatible policy (minimum
# 3.3.5, blocked 3.3.6 -- the `attach-session -t` ignoring `-t` and attaching
# to whatever's in `~/.psmux/last_session` regression). Kept here rather than
# calling into PowerShell so this module works standalone from Python on any
# platform; the two independently-hashed/versioned checks (this one PLUS the
# installer's own) mirror the existing fingerprint()/Get-PayloadHash pattern
# of parallel, per-language checks that don't need to invoke each other.
_PSMUX_MINIMUM_VERSION = (3, 3, 5)
_PSMUX_BLOCKED_VERSIONS = {(3, 3, 6)}

# `subprocess.CREATE_NEW_CONSOLE` only exists on the `subprocess` module when
# Python itself is running on Windows -- it is not an OS-detection-gated
# constant. `_windows_spawn` below only ever runs on a real Windows host, so
# the attribute is always present there; the `getattr` fallback exists solely
# so this module -- and its cross-platform test suite -- can be imported and
# exercised (with the Windows branch monkeypatched in) on non-Windows CI
# without an `AttributeError`. `0x00000010` is the documented Win32
# `CREATE_NEW_CONSOLE` creation-flag value.
_CREATE_NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010)  # headless-guard: allow this is the deliberate --headed exception (module docstring); the whole point is a visible window


class HeadedLaunchError(RuntimeError):
    """Raised when a headed attach cannot be started -- callers must
    surface this to the operator, never silently fall back to a detached
    session (that would defeat the entire point of ``--headed``)."""


def _parse_version(text: str) -> tuple[int, ...] | None:
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", text)
    if not m:
        return None
    return tuple(int(g) for g in m.groups())


def check_psmux_version(mux_bin: str, *, timeout: float = 5.0) -> None:
    """Refuse a known-broken psmux version outright rather than working
    around it (dotfiles/agent-worktrees policy: ban, don't patch around, a
    specific bad dependency version). A no-op for tmux (POSIX) or when the
    version can't be determined -- this is a targeted block on one known
    regression, not a general compatibility gate; an unparsable/absent
    version is not itself grounds to refuse a headed launch.
    """
    import os

    if os.path.basename(mux_bin).lower() not in ("psmux", "psmux.exe"):
        return
    try:
        proc = subprocess.run(
            [mux_bin, "--version"], capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return
    out = (proc.stdout or "") + (proc.stderr or "")
    # Two lines are printed (tmux-compat identity, then psmux's own version);
    # only the psmux-prefixed line's version is the one this policy governs.
    psmux_line = next(
        (line for line in out.splitlines() if line.strip().lower().startswith("psmux")),
        "",
    )
    version = _parse_version(psmux_line)
    if version is None:
        return
    if version[:3] < _PSMUX_MINIMUM_VERSION or version[:3] in _PSMUX_BLOCKED_VERSIONS:
        raise HeadedLaunchError(
            f"psmux {'.'.join(map(str, version))} is blocked for headed attach "
            f"(minimum {'.'.join(map(str, _PSMUX_MINIMUM_VERSION))}, "
            f"blocked: {sorted('.'.join(map(str, v)) for v in _PSMUX_BLOCKED_VERSIONS)}"
            f") -- update psmux (the installer's Ensure-Psmux already refuses "
            "this version at provision/update time; a headed attach found it "
            "anyway, which means this machine's psmux was installed or "
            "replaced out-of-band)."
        )


def _windows_spawn(attach_argv: list[str], *, title: str) -> dict:
    wt_bin = shutil.which("wt.exe") or shutil.which("wt")
    if wt_bin:
        # -w -1: always a brand-new window (never reuse/attach to an
        # existing Windows Terminal window that may not even be ours).
        argv = [wt_bin, "-w", "-1", "new-tab", "--title", title, "--", *attach_argv]
        proc = subprocess.Popen(argv)
        return {"spawner": "wt.exe", "pid": proc.pid}
    # No Windows Terminal on PATH: fall back to a plain new console host.
    # CREATE_NEW_CONSOLE always pops a REAL, visible window (conhost, or
    # whatever the OS's own "default terminal application" setting -- a
    # Windows-11-only feature -- redirects it to); it is the platform's
    # only universal "give me a new window" primitive absent wt.exe.
    proc = subprocess.Popen(
        attach_argv, creationflags=_CREATE_NEW_CONSOLE,  # headless-guard: allow this is the deliberate --headed exception (module docstring); the whole point is a visible window
    )
    return {"spawner": "conhost (CREATE_NEW_CONSOLE)", "pid": proc.pid}


# Ordered by how likely each is to actually be installed/configured as the
# operator's real terminal; first one found on PATH wins. There is no POSIX
# standard for "the default terminal emulator" (see module docstring) --
# Debian/Ubuntu's `x-terminal-emulator` alternative comes closest and is
# tried first, then the common desktop-environment terminals.
_POSIX_TERMINALS = (
    "x-terminal-emulator",
    "gnome-terminal",
    "konsole",
    "xfce4-terminal",
    "terminator",
    "xterm",
)


def _posix_spawn(attach_argv: list[str], *, title: str) -> dict:
    if platform.system() == "Darwin":
        # osascript can't easily accept an argv list; quote for a shell
        # command string instead.
        import shlex

        cmd = " ".join(shlex.quote(a) for a in attach_argv)
        script = f'tell application "Terminal" to do script "{cmd}"'
        proc = subprocess.Popen(["osascript", "-e", script])
        return {"spawner": "osascript (Terminal.app)", "pid": proc.pid}
    for term in _POSIX_TERMINALS:
        term_bin = shutil.which(term)
        if not term_bin:
            continue
        # gnome-terminal/xfce4-terminal/terminator use `--`; the rest
        # (x-terminal-emulator, konsole, xterm) accept the xterm-compatible
        # `-e`. x-terminal-emulator itself is usually a symlink to an
        # xterm-compatible wrapper (Debian's update-alternatives system).
        sep = "--" if term in ("gnome-terminal", "xfce4-terminal", "terminator") else "-e"
        argv = [term_bin, "--title", title, sep, *attach_argv] if sep == "--" else [
            term_bin, "-T", title, sep, *attach_argv
        ]
        proc = subprocess.Popen(argv)
        return {"spawner": term, "pid": proc.pid}
    raise HeadedLaunchError(
        "no visible terminal spawner found (tried: "
        f"{', '.join(_POSIX_TERMINALS)}) -- install one of these, or attach "
        f"manually: {' '.join(attach_argv)}"
    )


def spawn_headed_attach(mux_bin: str, session_name: str, *, title: str | None = None) -> dict:
    """Open a brand-new, visible terminal window running
    ``<mux_bin> attach-session -t <session_name>``.

    Returns ``{"spawner": <str>, "pid": <int>}`` on success. Raises
    :class:`HeadedLaunchError` -- never silently degrades to a detached
    session -- when no visible-window spawner is available on this platform,
    or when ``mux_bin`` is a blocked psmux version.
    """
    check_psmux_version(mux_bin)
    attach_argv = [mux_bin, "attach-session", "-t", session_name]
    label = title or session_name
    if platform.system() == "Windows":
        return _windows_spawn(attach_argv, title=label)
    return _posix_spawn(attach_argv, title=label)
