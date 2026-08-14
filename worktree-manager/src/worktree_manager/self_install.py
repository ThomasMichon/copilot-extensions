"""Versioned self-install for the Worktree Manager (matches the harness convention).

The Worktree Manager is delivered out-of-plugin, but its install flow follows the
**same versioning convention as the harness's other installers** (see
``plugins/agent-worktrees/scripts/versioned_runtime.py``):

* an immutable per-version slot at ``<root>/versions/<version>/``,
* a plain-text ``<root>/current-version`` **marker file** naming the active
  version (written atomically: temp + rename), and
* a **binstub** in ``~/.local/bin/`` (``worktree-manager`` + ``.cmd``/``.ps1`` on
  Windows) that resolves the marker and runs the active slot.

This is *convention* reuse, not code reuse: nothing here imports the plugin's
versioned-runtime helper, so the out-of-plugin, dependency-free boundary holds.
The install is idempotent and **version-gated** — re-running with the same
payload version is a no-op; a newer payload publishes a new slot + marker.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import worktree_manager

MARKER = "current-version"
VERSIONS_DIR = "versions"
STAGING_DIR = "staging"
_VERSION_RE = re.compile(r'__version__\s*=\s*"([^"]+)"')

#: The Worktree Manager's out-of-band source (same as the bootstrap one-liners).
_MANAGER_REPO = "https://github.com/ThomasMichon/copilot-extensions.git"


def manager_repo() -> str:
    """The git source :func:`self_update` fetches from.

    Defaults to the canonical GitHub repo (:data:`_MANAGER_REPO`) but honors a
    ``WORKTREE_MANAGER_REPO`` override, mirroring the existing
    ``WORKTREE_MANAGER_REF`` / ``WORKTREE_MANAGER_ROOT`` env knobs. This keeps the
    updater usable behind a **mirror / fork / air-gapped clone** (and lets the
    end-to-end delivery test drive it against a local remote), without weakening
    the out-of-plugin, dependency-free boundary.
    """
    return os.environ.get("WORKTREE_MANAGER_REPO") or _MANAGER_REPO


def default_root() -> Path:
    """Install root, mirroring ``~/.agent-worktrees`` for the core installer."""
    env = os.environ.get("WORKTREE_MANAGER_ROOT")
    if env:
        return Path(env)
    home = Path(os.environ.get("USERPROFILE") or os.path.expanduser("~"))
    return home / ".worktree-manager"


def local_bin() -> Path:
    home = Path(os.environ.get("USERPROFILE") or os.path.expanduser("~"))
    return home / ".local" / "bin"


def running_payload_dir() -> Path:
    """The project dir of the currently-running payload (has pyproject.toml)."""
    # .../worktree-manager/src/worktree_manager/self_install.py -> parents[2] == project.
    return Path(worktree_manager.__file__).resolve().parents[2]


def payload_version(payload_dir: Path | None = None) -> str | None:
    """Read ``__version__`` from a payload's src/worktree_manager/__init__.py."""
    pd = payload_dir or running_payload_dir()
    init = pd / "src" / "worktree_manager" / "__init__.py"
    try:
        m = _VERSION_RE.search(init.read_text("utf-8"))
    except OSError:
        return None
    return m.group(1) if m else None


def current_version(root: Path | None = None) -> str | None:
    """The active version from the marker file, or None if not installed."""
    marker = (root or default_root()) / MARKER
    try:
        return marker.read_text("utf-8").strip() or None
    except OSError:
        return None


def version_slot(version: str, root: Path | None = None) -> Path:
    return (root or default_root()) / VERSIONS_DIR / version


def _binstub_files() -> list[str]:
    if os.name == "nt":
        return ["worktree-manager.cmd", "worktree-manager.ps1", "worktree-manager"]
    return ["worktree-manager"]


def binstub_present() -> Path | None:
    lb = local_bin()
    for name in _binstub_files():
        p = lb / name
        if p.exists():
            return p
    return None


@dataclass(frozen=True)
class SelfInstallStatus:
    installed_version: str | None
    binstub: str | None
    root: str

    @property
    def installed(self) -> bool:
        return self.installed_version is not None and self.binstub is not None


def status(root: Path | None = None) -> SelfInstallStatus:
    r = root or default_root()
    stub = binstub_present()
    return SelfInstallStatus(
        installed_version=current_version(r),
        binstub=str(stub) if stub else None,
        root=str(r),
    )


def needs_install(version: str, root: Path | None = None) -> bool:
    r = root or default_root()
    return not (
        current_version(r) == version
        and version_slot(version, r).is_dir()
        and binstub_present() is not None
    )


# ── binstub content (resolves the marker each run; matches ~/.local/bin) ─────

def _sh_binstub() -> str:
    return (
        "#!/usr/bin/env bash\n"
        "# worktree-manager binstub (versioned) — resolves the current-version marker.\n"
        'set -euo pipefail\n'
        'root="${WORKTREE_MANAGER_ROOT:-$HOME/.worktree-manager}"\n'
        'ver="$(cat "$root/current-version")"\n'
        'exec uv run --quiet --project "$root/versions/$ver" python -m worktree_manager "$@"\n'
    )


def _cmd_binstub() -> str:
    return (
        "@echo off\r\n"
        "setlocal\r\n"
        'if "%WORKTREE_MANAGER_ROOT%"=="" set "WORKTREE_MANAGER_ROOT=%USERPROFILE%\\.worktree-manager"\r\n'
        'set /p VER=<"%WORKTREE_MANAGER_ROOT%\\current-version"\r\n'
        'uv run --quiet --project "%WORKTREE_MANAGER_ROOT%\\versions\\%VER%" '
        "python -m worktree_manager %*\r\n"
    )


def _ps1_binstub() -> str:
    return (
        "# worktree-manager binstub (versioned) — resolves the current-version marker.\n"
        '$root = if ($env:WORKTREE_MANAGER_ROOT) { $env:WORKTREE_MANAGER_ROOT } '
        'else { Join-Path $env:USERPROFILE ".worktree-manager" }\n'
        '$ver = (Get-Content (Join-Path $root "current-version") -Raw).Trim()\n'
        '$slot = Join-Path (Join-Path $root "versions") $ver\n'
        "uv run --quiet --project $slot python -m worktree_manager @args\n"
    )


def _deploy_binstubs() -> list[Path]:
    lb = local_bin()
    lb.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    contents = {"worktree-manager": _sh_binstub()}
    if os.name == "nt":
        contents = {
            "worktree-manager.cmd": _cmd_binstub(),
            "worktree-manager.ps1": _ps1_binstub(),
            "worktree-manager": _sh_binstub(),  # for git-bash on Windows
        }
    for name, body in contents.items():
        p = lb / name
        p.write_text(body, encoding="utf-8", newline="")
        if os.name != "nt":
            p.chmod(0o755)
        written.append(p)
    return written


def _write_marker(root: Path, version: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    tmp = root / (MARKER + ".tmp")
    tmp.write_text(version, encoding="utf-8")
    tmp.replace(root / MARKER)  # atomic publish


def _copy_payload(payload_dir: Path, slot: Path) -> None:
    if slot.exists():
        shutil.rmtree(slot)
    ignore = shutil.ignore_patterns(".git", ".venv", "__pycache__", "*.pyc")
    shutil.copytree(payload_dir, slot, ignore=ignore)


@dataclass(frozen=True)
class SelfInstallResult:
    version: str | None
    action: str          # "installed" | "already-current" | "planned" | "error"
    root: str
    slot: str | None = None
    marker: str | None = None
    binstubs: tuple[str, ...] = ()
    reason: str | None = None


def self_install(
    payload_dir: Path | None = None,
    *,
    root: Path | None = None,
    dry_run: bool = True,
) -> SelfInstallResult:
    """Install the running (or given) payload into the versioned layout.

    Idempotent + version-gated: a no-op when the marker already names this
    version and its slot + binstub exist. Dry-run by default.
    """
    r = root or default_root()
    pd = payload_dir or running_payload_dir()
    version = payload_version(pd)
    if version is None:
        return SelfInstallResult(version=None, action="error", root=str(r),
                                 reason="could not read payload __version__")
    slot = version_slot(version, r)
    if not needs_install(version, r):
        return SelfInstallResult(version=version, action="already-current", root=str(r),
                                 slot=str(slot), marker=version)
    if dry_run:
        return SelfInstallResult(version=version, action="planned", root=str(r),
                                 slot=str(slot), reason="dry-run")
    _copy_payload(pd, slot)
    stubs = _deploy_binstubs()
    _write_marker(r, version)  # publish last, so the marker only names a ready slot
    return SelfInstallResult(
        version=version, action="installed", root=str(r), slot=str(slot),
        marker=version, binstubs=tuple(str(s) for s in stubs),
    )


@dataclass(frozen=True)
class SelfUpdateResult:
    action: str          # "updated" | "already-current" | "skipped" | "error"
    version: str | None = None
    previous: str | None = None
    reason: str | None = None


def self_update(
    *,
    ref: str | None = None,
    root: Path | None = None,
    dry_run: bool = False,
) -> SelfUpdateResult:
    """Fetch the latest Worktree Manager payload and version-install it.

    This is the "updater updates itself" step: it git-fetches the out-of-band
    ``worktree-manager`` payload (the same source as the bootstrap one-liners),
    then :func:`self_install`\\s it -- publishing a new ``versions/<ver>`` slot +
    ``current-version`` marker when the fetched payload is newer, a no-op when
    already current (version-gated).

    Deliberately **best-effort and non-fatal**: git/uv missing, offline, or a
    fetch error return an ``error``/``skipped`` result rather than raising, so a
    transient network problem never blocks the harness ``update`` this feeds. The
    currently-running process keeps running its existing code; the freshly
    installed slot takes effect on the *next* ``worktree-manager`` invocation
    (normal versioned-install semantics -- no in-process hot-swap).
    """
    r = root or default_root()
    ref = ref or os.environ.get("WORKTREE_MANAGER_REF") or "main"
    previous = current_version(r)

    if not shutil.which("git"):
        return SelfUpdateResult(action="skipped", previous=previous,
                                reason="git not found on PATH")

    staging = r / STAGING_DIR
    try:
        staging.mkdir(parents=True, exist_ok=True)
        if (staging / ".git").is_dir():
            subprocess.run(["git", "-C", str(staging), "fetch", "--depth", "1",
                            "origin", ref], check=True, capture_output=True,
                           text=True, timeout=180)
            subprocess.run(["git", "-C", str(staging), "checkout", "-q",
                            "FETCH_HEAD"], check=True, capture_output=True,
                           text=True, timeout=60)
        else:
            subprocess.run(["git", "clone", "--depth", "1", "--branch", ref,
                            manager_repo(), str(staging)], check=True,
                           capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.SubprocessError) as e:
        return SelfUpdateResult(action="error", previous=previous,
                                reason=f"fetch failed: {e}")

    payload = staging / "worktree-manager"
    if not (payload / "pyproject.toml").is_file():
        return SelfUpdateResult(action="error", previous=previous,
                                reason=f"fetched payload not found at {payload}")

    res = self_install(payload_dir=payload, root=r, dry_run=dry_run)
    if res.action == "error":
        return SelfUpdateResult(action="error", version=res.version,
                                previous=previous, reason=res.reason)
    if res.action == "already-current":
        return SelfUpdateResult(action="already-current", version=res.version,
                                previous=previous)
    # installed | planned (dry-run)
    return SelfUpdateResult(action="updated", version=res.version, previous=previous)
