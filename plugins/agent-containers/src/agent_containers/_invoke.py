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
_ROOT = Path.home() / ".agent-containers"
#: Legacy single-venv layout (pre versioned-runtime). Kept only as a last-resort
#: fallback -- the versioned-runtime migration stopped updating it, so it goes
#: stale and must NOT be preferred over the active runtime (dotfiles #1631).
_LEGACY_VENV_DIR = _ROOT / ".venv"


def _venv_python() -> str:
    """Return the interpreter for the ACTIVE agent-containers runtime.

    The versioned-runtime layout installs each version under
    ``~/.agent-containers/versions/<current-version>`` and records the active one
    in ``~/.agent-containers/current-version`` -- the same resolution the ``.cmd``
    binstub's ``:_resolve`` performs. We must target that so a spawned
    ``agent-containers exec`` wrapper runs the **same code as the active runtime**.

    History / the bug this fixes (dotfiles #1631): this helper used to prefer a
    hardcoded ``~/.agent-containers/.venv``. After the versioned-runtime
    migration, updates land in ``versions/<ver>`` and that legacy ``.venv`` is
    never refreshed -- so preferring it made the daemon spawn the wrapper from
    **stale** code (e.g. injecting a stale credential-relay port). Resolution
    order now: active versioned runtime -> the current interpreter (which, when
    invoked via the binstub, already *is* the active runtime and always has
    ``agent_containers`` importable) -> the legacy ``.venv`` as a last resort.
    """
    scripts = "Scripts" if sys.platform == "win32" else "bin"
    exe = "python.exe" if sys.platform == "win32" else "python"

    # 1. The active versioned runtime (current-version -> versions/<ver>).
    try:
        ver = (_ROOT / "current-version").read_text(encoding="utf-8").strip()
    except OSError:
        ver = ""
    if ver:
        cand = _ROOT / "versions" / ver / scripts / exe
        if cand.exists():
            return str(cand)

    # 2. The running interpreter -- when spawned via the binstub this is the
    #    active runtime; in-process it is whatever imported us. It always has
    #    agent_containers importable, so it is a safe, non-stale default.
    if sys.executable:
        return sys.executable

    # 3. Last resort: the legacy single-venv layout (may be stale).
    legacy = _LEGACY_VENV_DIR / scripts / exe
    return str(legacy)


def module_argv() -> list[str]:
    """Return the argv prefix to run agent-containers as a module.

    Always ``[<python>, "-m", "agent_containers"]`` -- never the ``.cmd``
    binstub -- so forwarded arguments are not subject to cmd.exe parsing.
    """
    return [_venv_python(), "-m", _PACKAGE]
