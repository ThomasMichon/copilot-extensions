"""Resolve a cmd.exe-free way to invoke this plugin as a module.

On Windows, agent-bridge spawns provider commands via ``cmd.exe /d /s /c``
whenever the executable is a ``.cmd`` (see
``agent_bridge.transport._wrap_batch_for_windows``). ``cmd.exe`` expands
``%VAR%`` tokens in the forwarded arguments -- e.g. inside the
``--remote-cmd`` payload -- which mangles them before the Python CLI ever
sees ``argv``. To avoid that layer entirely, callers invoke the venv
interpreter directly with ``-m agent_codespaces`` rather than the
``~/.local/bin/agent-codespaces.cmd`` binstub. ``CreateProcess`` runs the
signed ``python.exe`` directly (no cmd.exe), so arguments are parsed with
the same MSVCRT rules the caller used to quote them -- verbatim.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PACKAGE = "agent_codespaces"
_VENV_DIR = Path.home() / ".agent-codespaces" / ".venv"
_BIN_DIR = Path.home() / ".local" / "bin"


def _venv_python() -> str:
    """Return the interpreter that has ``agent_codespaces`` installed.

    Prefers the plugin's dedicated venv (the same interpreter the ``.cmd``
    binstub targets); falls back to the current interpreter -- e.g. the
    agent-bridge daemon venv, which carries the provider plugins as
    siblings -- when the dedicated venv is absent.
    """
    if sys.platform == "win32":
        cand = _VENV_DIR / "Scripts" / "python.exe"
    else:
        cand = _VENV_DIR / "bin" / "python"
    if cand.exists():
        return str(cand)
    return sys.executable


def module_argv() -> list[str]:
    """Return the argv prefix to run agent-codespaces as a module.

    Always ``[<python>, "-m", "agent_codespaces"]`` -- never the ``.cmd``
    binstub -- so forwarded arguments are not subject to cmd.exe parsing.
    """
    return [_venv_python(), "-m", _PACKAGE]


def binstub() -> str | None:
    """Absolute path to the version-stable agent-codespaces binstub, or None.

    The binstub (``~/.local/bin/agent-codespaces[.cmd]``) resolves the CURRENT
    versioned runtime at each launch, so a persisted command that routes through
    it survives a runtime upgrade that prunes ``versions/<ver>/`` -- unlike the
    versioned interpreter :func:`_venv_python` pins. Only safe to spawn when no
    shell-mangling-prone payload rides in argv (see :func:`dispatch_argv`).
    """
    name = "agent-codespaces.cmd" if sys.platform == "win32" else "agent-codespaces"
    cand = _BIN_DIR / name
    return str(cand) if cand.exists() else None


def dispatch_argv() -> list[str]:
    """Argv prefix for a *persisted* spawn that must survive a runtime upgrade.

    Prefers the version-stable :func:`binstub` over the pinned versioned
    interpreter of :func:`module_argv`, so a resume never launches a python.exe
    under a ``versions/<ver>/`` slot a later upgrade pruned. Callers MUST keep
    argv free of shell-mangling-prone tokens -- route any complex payload through
    a file (e.g. ``ssh --remote-cmd-file``) -- because the binstub is invoked via
    cmd.exe on Windows. Falls back to :func:`module_argv` when the binstub is
    absent.
    """
    stub = binstub()
    return [stub] if stub is not None else module_argv()
