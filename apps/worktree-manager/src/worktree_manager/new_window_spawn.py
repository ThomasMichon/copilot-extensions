"""Spawn a genuinely NEW, VISIBLE terminal window running an arbitrary argv --
the mechanics behind the Picker's "Launch in new window" action.

This is the Worktree Manager's own terminal-presentation mechanic (see the
``session-hosting`` vision's Concepts/*Session-host provider*: a GUI/plain
terminal window is a presentation concern owned here, not by agent-worktrees).
It wraps an arbitrary argv so a caller can run the SAME launch-plan execution
every other launch uses, inside a new window, instead of a separate code
path that bypasses it.
"""

from __future__ import annotations

import platform
import shutil
import subprocess

# `subprocess.CREATE_NEW_CONSOLE` only exists on the `subprocess` module when
# Python itself is running on Windows -- it is not an OS-detection-gated
# constant. `_windows_spawn` below only ever runs on a real Windows host, so
# the attribute is always present there; the `getattr` fallback exists solely
# so this module -- and its cross-platform test suite -- can be imported and
# exercised (with the Windows branch monkeypatched in) on non-Windows CI
# without an `AttributeError`. `0x00000010` is the documented Win32
# `CREATE_NEW_CONSOLE` creation-flag value.
_CREATE_NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010)  # headless-guard: allow this is the deliberate new-window exception (module docstring); the whole point is a visible window


class NewWindowSpawnError(RuntimeError):
    """Raised when a new, visible window cannot be opened -- callers must
    surface this to the operator, never silently fall back to running the
    argv in the caller's own (headless/TUI) process."""


def _windows_spawn(argv: list[str], *, title: str, env: dict[str, str] | None) -> dict:
    wt_bin = shutil.which("wt.exe") or shutil.which("wt")
    if wt_bin:
        # -w -1: always a brand-new window (never reuse/attach to an
        # existing Windows Terminal window that may not even be ours).
        wt_argv = [wt_bin, "-w", "-1", "new-tab", "--title", title, "--", *argv]
        proc = subprocess.Popen(wt_argv, env=env)
        return {"spawner": "wt.exe", "pid": proc.pid}
    # No Windows Terminal on PATH: fall back to a plain new console host.
    # CREATE_NEW_CONSOLE always pops a REAL, visible window (conhost, or
    # whatever the OS's own "default terminal application" setting -- a
    # Windows-11-only feature -- redirects it to); it is the platform's
    # only universal "give me a new window" primitive absent wt.exe.
    proc = subprocess.Popen(
        argv, creationflags=_CREATE_NEW_CONSOLE, env=env,  # headless-guard: allow this is the deliberate new-window exception (module docstring); the whole point is a visible window
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


def _applescript_quote(text: str) -> str:
    """Escape ``text`` for safe interpolation inside an AppleScript
    double-quoted string literal (backslash, then double-quote)."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _posix_spawn(argv: list[str], *, title: str, env: dict[str, str] | None) -> dict:
    if platform.system() == "Darwin":
        # osascript can't easily accept an argv list; quote for a shell
        # command string, THEN escape the result for the AppleScript string
        # literal it is embedded in -- shell-quoting alone leaves `"` and
        # `\` free to terminate that literal early or inject AppleScript
        # source (e.g. an argv element naming an installation path).
        import shlex

        cmd = " ".join(shlex.quote(a) for a in argv)
        script = f'tell application "Terminal" to do script "{_applescript_quote(cmd)}"'
        proc = subprocess.Popen(["osascript", "-e", script], env=env)
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
        term_argv = [term_bin, "--title", title, sep, *argv] if sep == "--" else [
            term_bin, "-T", title, sep, *argv
        ]
        proc = subprocess.Popen(term_argv, env=env)
        return {"spawner": term, "pid": proc.pid}
    raise NewWindowSpawnError(
        "no visible terminal spawner found (tried: "
        f"{', '.join(_POSIX_TERMINALS)}) -- install one of these, or run "
        f"manually: {' '.join(argv)}"
    )


def spawn_detached_new_window(
    argv: list[str], *, title: str, env: dict[str, str] | None = None,
) -> dict:
    """Open a brand-new, visible terminal window running ``argv``.

    ``env``, when given, is the exact environment the spawned process
    receives (default ``None``: inherit this process's current environment,
    matching ``subprocess.Popen``'s own default) -- callers that need a
    one-off override (e.g. a no-mux launch's ``WORKTREE_NO_MUX=1``) pass an
    explicit copy instead of mutating ``os.environ`` globally, which would
    leak into every other concurrent or later spawn in this process.

    Returns ``{"spawner": <str>, "pid": <int>}`` on success. Raises
    :class:`NewWindowSpawnError` -- never silently degrades to running
    ``argv`` in the caller's own process -- when no visible-window spawner
    is available on this platform.
    """
    if platform.system() == "Windows":
        return _windows_spawn(argv, title=title, env=env)
    return _posix_spawn(argv, title=title, env=env)
