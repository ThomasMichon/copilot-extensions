"""Core install driver (Phase 2 — drive the harness's OWN install flow).

The Configurator does **not** reimplement the agent-worktrees install — it
locates and *calls* the plugin's own ``scripts/install.{ps1,sh} install``, which
builds the shared runtime (``~/.agent-worktrees/``), the venv, and the
``~/.local/bin`` binstubs. This keeps the dependency-free boundary: the installer
knows *how to invoke* the core, not how the core works.

Detection is idempotent and heals a partial install: it inspects the runtime dir
and the global binstub independently, so a half-finished state (runtime present
but binstub missing, or vice-versa) is reported as ``partial`` and a re-run of the
real installer repairs it.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .catalog import find_repo_root


def _home() -> Path:
    return Path(os.environ.get("USERPROFILE") or os.path.expanduser("~"))


def runtime_dir(home: Path | None = None) -> Path:
    return (home or _home()) / ".agent-worktrees"


def _binstub_names() -> tuple[str, ...]:
    return ("agent-worktrees", "agent-worktrees.cmd", "agent-worktrees.ps1")


def global_binstub(home: Path | None = None) -> Path | None:
    """The installed global agent-worktrees binstub in ~/.local/bin, if present."""
    local_bin = (home or _home()) / ".local" / "bin"
    for name in _binstub_names():
        p = local_bin / name
        if p.exists():
            return p
    return None


@dataclass(frozen=True)
class CoreStatus:
    """Whether the agent-worktrees core is installed on this machine."""

    #: "installed" | "partial" | "absent"
    state: str
    runtime_present: bool
    venv_present: bool
    binstub: str | None
    runtime_dir: str

    @property
    def installed(self) -> bool:
        return self.state == "installed"


def core_status(home: Path | None = None) -> CoreStatus:
    rt = runtime_dir(home)
    venv = (rt / ".venv").exists() or (rt / "versions").exists()
    runtime_present = rt.is_dir()
    stub = global_binstub(home)
    have_stub = stub is not None
    if runtime_present and venv and have_stub:
        state = "installed"
    elif runtime_present or have_stub:
        state = "partial"
    else:
        state = "absent"
    return CoreStatus(
        state=state,
        runtime_present=runtime_present,
        venv_present=venv,
        binstub=str(stub) if stub else None,
        runtime_dir=str(rt),
    )


def install_script(repo_root: Path | None = None) -> Path | None:
    """Locate agent-worktrees' own installer for this OS. Requires a checkout."""
    root = repo_root or find_repo_root()
    if root is None:
        return None
    scripts = root / "plugins" / "agent-worktrees" / "scripts"
    name = "install.ps1" if os.name == "nt" else "install.sh"
    p = scripts / name
    return p if p.is_file() else None


def install_command(repo_root: Path | None = None) -> list[str] | None:
    """The concrete command that drives the core install, or None if the real
    installer can't be located (no checkout)."""
    script = install_script(repo_root)
    if script is None:
        return None
    if script.suffix == ".ps1":
        return ["pwsh", "-NoProfile", "-File", str(script), "install"]
    return ["bash", str(script), "install"]


@dataclass(frozen=True)
class CoreInstallResult:
    planned_command: list[str] | None
    ran: bool
    returncode: int | None = None
    reason: str | None = None


def install_core(
    *,
    repo_root: Path | None = None,
    dry_run: bool = True,
    home: Path | None = None,
) -> CoreInstallResult:
    """Drive the harness's own core install.

    Idempotent: if the core is already fully installed this is a no-op (unless a
    re-run is forced by a ``partial`` state, which the caller decides). Dry-run by
    default — nothing executes unless ``dry_run=False``.
    """
    status = core_status(home)
    cmd = install_command(repo_root)
    if status.installed:
        return CoreInstallResult(planned_command=cmd, ran=False, reason="already-installed")
    if cmd is None:
        return CoreInstallResult(
            planned_command=None, ran=False,
            reason="no-installer (need a copilot-extensions checkout to run the real install)",
        )
    if dry_run:
        return CoreInstallResult(planned_command=cmd, ran=False, reason="dry-run")
    try:
        r = subprocess.run(cmd)  # noqa: S603 (installer-owned, no shell)
        return CoreInstallResult(planned_command=cmd, ran=True, returncode=r.returncode)
    except OSError as exc:  # pragma: no cover - environment dependent
        return CoreInstallResult(planned_command=cmd, ran=False, reason=str(exc))
