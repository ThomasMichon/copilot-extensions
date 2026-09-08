"""Resolve payload-local and module launch paths for agent-containers."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_PACKAGE = "agent_containers"


def runtime_root() -> Path:
    override = os.environ.get("AGENT_CONTAINERS_HOME", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".agent-containers"


def payload_root() -> Path:
    return Path(__file__).resolve().parents[2]


def payload_binstub() -> Path | None:
    name = "agent-containers.cmd" if sys.platform == "win32" else "agent-containers"
    shim = payload_root() / "bin" / name
    return shim if shim.is_file() else None


def payload_command_argv() -> list[str]:
    shim = payload_binstub()
    if shim is not None:
        return [str(shim)]
    return module_argv()


_ROOT = runtime_root()
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

    # 3. Last resort: the legacy single-venv layout (may be stale). Only return
    #    it if it actually exists; otherwise fail fast with a clear error rather
    #    than handing back a bogus path that spawns with a confusing failure.
    legacy = _LEGACY_VENV_DIR / scripts / exe
    if legacy.exists():
        return str(legacy)
    raise RuntimeError(
        "Cannot resolve an agent_containers interpreter: no active versioned "
        "runtime (~/.agent-containers/current-version -> versions/<ver>), an "
        "empty sys.executable, and no legacy ~/.agent-containers/.venv."
    )


def module_argv() -> list[str]:
    """Return the argv prefix to run agent-containers as a module.

    Always ``[<python>, "-m", "agent_containers"]`` -- never the ``.cmd``
    binstub -- so forwarded arguments are not subject to cmd.exe parsing.
    """
    return [_venv_python(), "-m", _PACKAGE]
