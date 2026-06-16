"""Resolve a cmd.exe-free way to invoke this plugin as a module.

On Windows, agent-bridge spawns provider commands via ``cmd.exe /d /s /c``
whenever the executable is a ``.cmd`` (see
``agent_bridge.transport._wrap_batch_for_windows``). ``cmd.exe`` expands
``%VAR%`` tokens in the forwarded arguments -- e.g. inside the wrapped ACP
command -- which mangles them before the Python CLI ever sees ``argv``. To
avoid that layer entirely, callers invoke the venv interpreter directly
with ``-m agent_containers`` rather than the
``~/.local/bin/agent-containers.cmd`` binstub. ``CreateProcess`` runs the
signed ``python.exe`` directly (no cmd.exe), so arguments are parsed with
the same MSVCRT rules the caller used to quote them -- verbatim.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PACKAGE = "agent_containers"
_VENV_DIR = Path.home() / ".agent-containers" / ".venv"


def _venv_python() -> str:
    """Return the interpreter that has ``agent_containers`` installed.

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
    """Return the argv prefix to run agent-containers as a module.

    Always ``[<python>, "-m", "agent_containers"]`` -- never the ``.cmd``
    binstub -- so forwarded arguments are not subject to cmd.exe parsing.
    """
    return [_venv_python(), "-m", _PACKAGE]
