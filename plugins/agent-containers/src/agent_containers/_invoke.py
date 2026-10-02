"""Resolve payload-local and module launch paths for agent-containers."""

from __future__ import annotations

import json
import os
import sys
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

_PACKAGE = "agent_containers"
_DIST_NAME = "agent-containers"


def runtime_root() -> Path:
    override = os.environ.get("AGENT_CONTAINERS_HOME", "").strip()
    if override:
        return Path(override).expanduser()
    _legacy = ".agent-containers"  # marketplace-isolation: allow legacy compatibility root
    return Path.home() / _legacy


def _deploy_manifest_source_root() -> Path | None:
    manifest = runtime_root() / "deploy-manifest.json"
    if not manifest.is_file():
        return None
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError):
        return None
    source_path = str((data or {}).get("source", {}).get("path") or "").strip()
    if not source_path:
        return None
    candidate = Path(source_path).expanduser()
    if candidate.is_dir() and (candidate / "plugin.json").is_file():
        return candidate.resolve()
    return None


def _direct_url_source_root() -> Path | None:
    try:
        payload_dist = distribution(_DIST_NAME)
    except PackageNotFoundError:
        return None
    try:
        payload = payload_dist.read_text("direct_url.json")
    except FileNotFoundError:
        return None
    if not payload:
        return None
    try:
        data = json.loads(payload)
    except (ValueError, TypeError):
        return None
    url = str((data or {}).get("url") or "").strip()
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.scheme != "file":
        return None
    candidate = Path(url2pathname(parsed.path))
    if candidate.is_dir() and (candidate / "plugin.json").is_file():
        return candidate.resolve()
    return None


def payload_root() -> Path:
    env_payload = os.environ.get("COPILOT_PLUGIN_ROOT", "").strip()
    if env_payload:
        candidate = Path(env_payload).expanduser()
        if candidate.is_dir() and (candidate / "plugin.json").is_file():
            return candidate.resolve()
    candidate = Path(__file__).resolve().parents[2]
    if candidate.is_dir() and (candidate / "plugin.json").is_file():
        return candidate
    manifest_root = _deploy_manifest_source_root()
    if manifest_root is not None:
        return manifest_root
    direct_url_root = _direct_url_source_root()
    if direct_url_root is not None:
        return direct_url_root
    return candidate


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
    _root_name = ".agent-containers"  # marketplace-isolation: allow deployed-runtime-diagnostics
    raise RuntimeError(
        "Cannot resolve an agent_containers interpreter: no active versioned "
        f"runtime (~/{_root_name}/current-version -> versions/<ver>), an "
        f"empty sys.executable, and no legacy ~/{_root_name}/.venv."
    )


def module_argv() -> list[str]:
    """Return the argv prefix to run agent-containers as a module.

    Always ``[<python>, "-m", "agent_containers"]`` -- never the ``.cmd``
    binstub -- so forwarded arguments are not subject to cmd.exe parsing.
    """
    return [_venv_python(), "-m", _PACKAGE]
