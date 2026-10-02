"""Resolve payload-local and module launch paths for agent-codespaces."""

from __future__ import annotations

import json
import os
import re
import sys
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

_PACKAGE = "agent_codespaces"
_DIST_NAME = "agent-codespaces"

#: Slot-interpreter subpaths, POSIX then Windows (matches agent-dispatch's
#: ``procutil._SLOT_PYTHON_SUBPATHS`` / ``resolve-runtime.ps1``).
_SLOT_PYTHON_SUBPATHS = ("bin/python", "Scripts/python.exe")


def _runtime_root() -> Path:
    _legacy_dirname = ".agent-codespaces"  # marketplace-isolation: allow legacy compatibility root
    override = os.environ.get("AGENT_CODESPACES_HOME", "").strip()
    if override:
        return Path(override).expanduser()
    sandbox = os.environ.get("AGENT_HOME", "").strip()
    if sandbox:
        return Path(sandbox).expanduser() / _legacy_dirname
    return Path.home() / _legacy_dirname


def _slot_python(root: Path, version: str) -> Path | None:
    """The interpreter inside ``<root>/versions/<version>``, or ``None``."""
    if not version:
        return None
    vdir = root / "versions" / version
    for sub in _SLOT_PYTHON_SUBPATHS:
        p = vdir / sub
        if p.is_file():
            return p
    return None


def _read_marker(root: Path, name: str) -> str | None:
    """Read a plain-text marker file (``current-version`` / ``last-known-good``)."""
    try:
        return (root / name).read_text(encoding="utf-8").strip() or None
    except (OSError, ValueError):
        return None


def _version_sort_key(version: str) -> str:
    """Version-aware sort key: zero-pad each numeric run (matches
    agent-dispatch's ``procutil._version_sort_key`` / ``resolve-runtime.ps1``)."""
    return re.sub(r"\d+", lambda m: m.group().zfill(10), version)


def _list_versions(root: Path) -> list[str]:
    """Installed version slot names, sorted oldest -> newest (newest last)."""
    try:
        names = [d.name for d in (root / "versions").iterdir() if d.is_dir()]
    except OSError:
        return []
    return sorted(names, key=_version_sort_key)


def _resolve_runtime_python(root: Path) -> Path | None:
    """Canonically resolve this plugin's own versioned-runtime interpreter.

    Mirrors agent-dispatch's ``procutil.resolve_runtime_python``: the
    ``current-version`` marker is authoritative, ``last-known-good`` is the
    fallback, then the newest complete installed slot, then any newest slot.
    Never falls back to a PATH python -- returns ``None`` when no runtime is
    installed so the caller degrades deliberately (to ``sys.executable``, the
    one legitimate last resort -- see ``_venv_python``).
    """
    p = _slot_python(root, _read_marker(root, "current-version") or "")
    if p is not None:
        return p
    p = _slot_python(root, _read_marker(root, "last-known-good") or "")
    if p is not None:
        return p
    versions = _list_versions(root)  # newest last
    for ver in reversed(versions):
        if (root / "versions" / ver / ".install-complete.json").is_file():
            p = _slot_python(root, ver)
            if p is not None:
                return p
    for ver in reversed(versions):
        p = _slot_python(root, ver)
        if p is not None:
            return p
    return None


def _venv_python() -> str:
    """Return the interpreter that has ``agent_codespaces`` installed.

    Resolves the installed versioned-runtime slot (``versions/<current-
    version>``, the same three-tier resolution the binstubs and service
    launchers use) -- NOT a hard-coded legacy ``.venv`` path, which no longer
    exists once a runtime migrates to the versioned-slot layout and would
    silently fall through to ``sys.executable`` (the current process's own
    interpreter) on every call. Trusting whatever interpreter happens to be
    running the *current* process is a footgun: it can diverge from the
    installed current-version slot and, because a detached self-relaunch
    child inherits whatever its parent resolved, that divergence propagates
    down the entire spawn tree -- the exact pattern already found and fixed
    in ``agent_dispatch.procutil.resolve_own_runtime_python`` (a live
    incident: a correctly-running coordinator/supervisor pair each spawning a
    full duplicate tree under the system Python install instead of the
    versioned slot). Falls back to ``sys.executable`` only when no installed
    runtime resolves at all (e.g. a dev/test environment with no real
    install).
    """
    resolved = _resolve_runtime_python(_runtime_root())
    return str(resolved) if resolved is not None else sys.executable


def module_argv() -> list[str]:
    """Return the argv prefix to run agent-codespaces as a module.

    Always ``[<python>, "-m", "agent_codespaces"]`` -- never the ``.cmd``
    binstub -- so forwarded arguments are not subject to cmd.exe parsing.
    """
    return [_venv_python(), "-m", _PACKAGE]


def _deploy_manifest_source_root() -> Path | None:
    manifest = _runtime_root() / "deploy-manifest.json"
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


def _payload_root() -> Path:
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
