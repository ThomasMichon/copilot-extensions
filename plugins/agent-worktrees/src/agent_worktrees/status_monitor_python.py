"""Direct console-interpreter selection for monitor process identity."""

from __future__ import annotations

import os

from agent_procutil import windowless_python, windowless_python_env


def direct_monitor_python(executable: str) -> tuple[str, dict[str, str]]:
    """Bypass Windows venv redirectors without losing the selected venv."""
    if os.name != "nt":
        return executable, {}
    python = windowless_python(executable)
    extra_env = windowless_python_env(executable)
    config = os.path.join(os.path.dirname(os.path.dirname(executable)), "pyvenv.cfg")
    if os.path.isfile(config) and not extra_env:
        raise RuntimeError(
            "Cannot start status-monitor directly: the Windows venv's base "
            "pythonw.exe is unavailable; repair the Python installation."
        )
    if os.path.basename(python).lower() == "pythonw.exe":
        python = os.path.join(os.path.dirname(python), "python.exe")
        if not os.path.isfile(python):
            raise RuntimeError(
                "Cannot start status-monitor directly: the base python.exe "
                "is unavailable; repair the Python installation."
            )
    return python, extra_env
