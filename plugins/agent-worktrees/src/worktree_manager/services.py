"""Service discovery and staleness checks.

Walks the repo for ``service.yaml`` manifests, filters by deployment
environment, reads ``deploy-manifest.json`` for provenance, and compares
deployed commits against HEAD to detect staleness.

Discovery paths (relative to repo root):
    services/*/service.yaml              — shared services
    tools/*/service.yaml                 — tool services
    {machine}/services/*/service.yaml    — machine-scoped services

Pure Python with git via subprocess (no libgit2).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import git_ops
from . import config as cfg


# ── Data structures ─────────────────────────────────────────────────────


@dataclass
class ServiceInfo:
    """A service discovered from a ``service.yaml`` manifest."""

    name: str
    display_name: str
    description: str
    service_type: str  # "systemd", "scheduled-task", "installed-tools", ...
    level: str  # "user", "system"

    service_yaml_path: Path  # repo-relative path to service.yaml
    installer_path: str | None  # repo-relative path to install.sh/install.ps1
    install_dir: str | None  # expanded from deployments[env].install_dir
    deployment_type: str  # "full", "redirector", etc.
    source_dir: str  # repo-relative parent dir of service.yaml


@dataclass
class ServiceStatus:
    """A service with its deployment status resolved."""

    service: ServiceInfo
    staleness: str  # "current" | "stale:N" | "unknown"
    deployed_commit: str | None = None
    deployed_at: str | None = None
    deployed_branch: str | None = None
    dirty: bool = False
    source_paths: list[str] = field(default_factory=list)


# ── Discovery ───────────────────────────────────────────────────────────


def _expand_install_dir(raw: str) -> str:
    """Expand shell variables and ``~`` in an install_dir string.

    On Windows, ``$HOME`` is often unset.  Fall back to ``USERPROFILE``
    so ``${HOME}/...`` resolves correctly.
    """
    if not os.environ.get("HOME") and os.environ.get("USERPROFILE"):
        raw = raw.replace("${HOME}", os.environ["USERPROFILE"])
    return os.path.expanduser(os.path.expandvars(raw))


def _preferred_installer_order() -> tuple[str, str]:
    """Return installer filenames in platform-preferred order.

    ``.ps1`` first on Windows, ``.sh`` first on Linux/WSL.
    """
    if cfg.detect_platform() == "windows":
        return ("install.ps1", "install.sh")
    return ("install.sh", "install.ps1")


def _find_installer(service_dir: Path, repo_dir: Path) -> str | None:
    """Find install.sh or install.ps1 in *service_dir*.

    Prefers the installer matching the current platform (``.ps1`` on Windows,
    ``.sh`` on Linux/WSL).  Falls back to the other if only one exists.

    Args:
        service_dir: Repo-relative path to the service directory.
        repo_dir: Absolute path to repo root.

    Returns the repo-relative path as a string, or None.
    """
    for name in _preferred_installer_order():
        if (repo_dir / service_dir / name).exists():
            return (service_dir / name).as_posix()
    return None


def _machine_from_environment(
    environment: str,
    machine_keys: list[str],
) -> str | None:
    """Derive the machine key from an environment string.

    Tries exact match first (e.g. ``wheatley``), then checks if the
    environment starts with a known machine key (e.g. ``lambda-core-wsl``
    → ``lambda-core``).  Returns None if no match.
    """
    if environment in machine_keys:
        return environment
    # Longest prefix wins (so "tmichon-book2" beats "tmichon")
    candidates = [k for k in machine_keys if environment.startswith(k + "-")]
    if candidates:
        return max(candidates, key=len)
    return None


def _load_machine_keys(repo_dir: Path) -> list[str]:
    """Load machine keys from ``machines.yaml``."""
    try:
        entries = cfg.load_machines_yaml(repo_dir)
        return list(entries.keys())
    except (FileNotFoundError, ValueError):
        machine = cfg.detect_machine(repo_dir)
        return [machine] if machine else []


def _parse_service_yaml(
    yaml_path: Path,
    repo_dir: Path,
    environment: str,
) -> ServiceInfo | None:
    """Parse a service.yaml and return ServiceInfo if deployable to *environment*.

    Supports two patterns:
    - Modern: ``deployments:`` block with environment-keyed entries
    - Legacy: ``machines:`` + ``platform:`` + ``runtime.install_dir:``
    """
    try:
        with open(repo_dir / yaml_path, encoding="utf-8") as f:
            data: dict[str, Any] = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        return None

    name = data.get("name", "")
    if not name:
        return None

    service_dir = yaml_path.parent

    # --- Modern: explicit deployments block ---
    deployments = data.get("deployments")
    if deployments and isinstance(deployments, dict):
        dep = deployments.get(environment)
        if dep and isinstance(dep, dict):
            dep_type = dep.get("type", "full")
            raw_dir = dep.get("install_dir") or data.get("runtime", {}).get("install_dir")
            install_dir = _expand_install_dir(raw_dir) if raw_dir else None
            return ServiceInfo(
                name=name,
                display_name=data.get("display_name", name),
                description=data.get("description", ""),
                service_type=data.get("type", "unknown"),
                level=dep.get("level", data.get("level", "user")),
                service_yaml_path=yaml_path,
                installer_path=_find_installer(service_dir, repo_dir),
                install_dir=install_dir,
                deployment_type=dep_type,
                source_dir=str(service_dir),
            )
        # Has deployments but this env isn't listed → skip
        return None

    # --- Legacy fallback: machines + platform ---
    machines_list = data.get("machines", [])
    platform_str = data.get("platform", "")

    if not machines_list:
        return None

    # Derive machine key from environment
    machine_keys = [str(m) for m in machines_list]
    machine = _machine_from_environment(environment, machine_keys)
    if machine is None:
        return None

    # Check platform compatibility
    plat = cfg.detect_platform()
    if platform_str and platform_str != "linux":
        # "linux" covers both native linux and wsl for legacy services
        if platform_str == "windows" and plat != "windows":
            return None

    raw_dir = data.get("runtime", {}).get("install_dir")
    install_dir = _expand_install_dir(raw_dir) if raw_dir else None

    return ServiceInfo(
        name=name,
        display_name=data.get("display_name", name),
        description=data.get("description", ""),
        service_type=data.get("type", "unknown"),
        level=data.get("level", "user"),
        service_yaml_path=yaml_path,
        installer_path=_find_installer(service_dir, repo_dir),
        install_dir=install_dir,
        deployment_type="full",
        source_dir=str(service_dir),
    )


# Default service discovery globs used when no config is provided.
_DEFAULT_SERVICE_GLOBS: list[str] = [
    "services/*/service.yaml",
    "tools/*/service.yaml",
]


def discover_services(
    repo_dir: Path,
    environment: str,
    service_paths: list[str] | None = None,
) -> list[ServiceInfo]:
    """Walk the repo for ``service.yaml`` files deployable to *environment*.

    Args:
        repo_dir: Absolute path to the repo root.
        environment: Deployment environment key (e.g. ``lambda-core-wsl``).
        service_paths: Glob patterns for service discovery.  If None,
            uses the default patterns plus machine-scoped path.

    Returns:
        List of discovered services, sorted by name.
    """
    machine_keys = _load_machine_keys(repo_dir)
    machine = _machine_from_environment(environment, machine_keys)

    if service_paths:
        # Config-provided paths — expand {machine} placeholder
        scan_globs = [
            p.replace("{machine}", machine) if machine else p
            for p in service_paths
            if machine or "{machine}" not in p
        ]
    else:
        # Default discovery paths
        scan_globs = list(_DEFAULT_SERVICE_GLOBS)
        if machine:
            scan_globs.append(f"{machine}/services/*/service.yaml")

    seen: dict[str, ServiceInfo] = {}
    for pattern in scan_globs:
        for yaml_path in sorted(repo_dir.glob(pattern)):
            rel = yaml_path.relative_to(repo_dir)
            info = _parse_service_yaml(rel, repo_dir, environment)
            if info and info.name not in seen:
                seen[info.name] = info

    return sorted(seen.values(), key=lambda s: s.name)


# ── Staleness ───────────────────────────────────────────────────────────


def _read_manifest(manifest_path: Path) -> dict[str, Any] | None:
    """Read and parse a ``deploy-manifest.json``.  Returns None on failure."""
    try:
        with open(manifest_path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return None


def check_staleness(manifest_path: Path, repo_dir: Path) -> str:
    """Compare the deployed commit against HEAD for the service's source paths.

    Args:
        manifest_path: Absolute path to ``deploy-manifest.json``.
        repo_dir: Absolute path to the repo root.

    Returns:
        ``"current"`` — deployed commit is up-to-date for source paths.
        ``"stale:N"`` — N commits behind HEAD.
        ``"unknown"`` — cannot determine (missing manifest, no git, etc.).
    """
    manifest = _read_manifest(manifest_path)
    if manifest is None:
        return "unknown"

    commit = manifest.get("commit")
    if not commit or commit == "null":
        return "unknown"

    # Verify the deployed commit still exists in the repo
    try:
        git_ops.git("cat-file", "-e", f"{commit}^{{commit}}", cwd=repo_dir)
    except git_ops.GitError:
        return "unknown"

    source_paths = manifest.get("source_paths", [])
    if not source_paths:
        return "unknown"

    # Count commits between deployed commit and HEAD touching source paths
    try:
        result = git_ops.git(
            "log", "--oneline", f"{commit}..HEAD", "--",
            *source_paths,
            cwd=repo_dir,
            check=False,
        )
    except git_ops.GitError:
        return "unknown"

    if result.returncode != 0:
        return "unknown"

    lines = [l for l in result.stdout.strip().splitlines() if l.strip()]
    if lines:
        return f"stale:{len(lines)}"
    return "current"


def get_service_status(
    service: ServiceInfo,
    repo_dir: Path,
) -> ServiceStatus:
    """Resolve deployment status for a discovered service.

    Reads the ``deploy-manifest.json`` from the service's install_dir
    and checks staleness against HEAD.
    """
    if not service.install_dir:
        return ServiceStatus(service=service, staleness="unknown")

    manifest_path = Path(service.install_dir) / "deploy-manifest.json"
    manifest = _read_manifest(manifest_path)

    if manifest is None:
        return ServiceStatus(
            service=service,
            staleness="unknown",
            source_paths=[service.source_dir],
        )

    staleness = check_staleness(manifest_path, repo_dir)

    return ServiceStatus(
        service=service,
        staleness=staleness,
        deployed_commit=manifest.get("commit"),
        deployed_at=manifest.get("deployed_at"),
        deployed_branch=manifest.get("branch"),
        dirty=bool(manifest.get("dirty", False)),
        source_paths=manifest.get("source_paths", [service.source_dir]),
    )
