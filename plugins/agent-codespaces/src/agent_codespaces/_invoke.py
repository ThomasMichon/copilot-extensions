"""Resolve payload-local and module launch paths for agent-codespaces."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_PACKAGE = "agent_codespaces"


def _runtime_root() -> Path:
    override = os.environ.get("AGENT_CODESPACES_HOME", "").strip()
    if override:
        return Path(override).expanduser()
    sandbox = os.environ.get("AGENT_HOME", "").strip()
    if sandbox:
        return Path(sandbox).expanduser() / ".agent-codespaces"
    return Path.home() / ".agent-codespaces"


_VENV_DIR = _runtime_root() / ".venv"


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


def _payload_root() -> Path:
    return Path(__file__).resolve().parents[2]


def binstub() -> str | None:
    """Absolute path to this payload's shim, or None when it is unavailable."""
    name = (
        "agent-codespaces.cmd"
        if sys.platform == "win32"
        else "agent-codespaces"
    )
    cand = _payload_root() / "bin" / name
    return str(cand) if cand.exists() else None


def dispatch_argv() -> list[str]:
    """Argv prefix for a persisted spawn pinned to this payload root."""
    stub = binstub()
    return [stub] if stub is not None else module_argv()
