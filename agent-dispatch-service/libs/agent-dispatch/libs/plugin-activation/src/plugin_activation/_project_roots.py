"""Verify registered-project roots against their recorded Git identity.

``_verified_project_roots`` calls the shared ``_git`` helper that lives on
the ``resolver`` module (kept there deliberately -- see that module's
docstring on why it is a monkeypatch-sensitive compatibility surface). The
import below is deferred to call time, inside the function body, so that
``monkeypatch.setattr(resolver, "_git", ...)`` is observed no matter which
module's code path calls it -- a bare module-level ``from . import resolver``
would capture resolver's current attributes too early for a circular import
between this module and ``resolver.py``, and a bare `from .resolver import
_git`` would bind its own local name once, permanently bypassing any later
monkeypatch of ``resolver._git``.
"""

from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path

from dropin_registry import Finding, ScanAuthority

from ._config_io import _load_yaml_object
from ._findings import _finding
from ._remote_identity import normalize_remote
from .bare_anchor import git_root


def _platform_key() -> str:
    if platform.system() == "Windows":
        return "windows"
    if os.environ.get("WSL_DISTRO_NAME"):
        return "wsl"
    return "linux"


def _same_file(left: Path, right: Path) -> bool:
    try:
        return left.samefile(right)
    except OSError:
        return left == right


def _verified_project_roots(
    agent_worktrees_home: Path,
) -> tuple[list[tuple[str, Path]], list[Finding], ScanAuthority]:
    from . import resolver as _resolver  # deferred: see module docstring

    projects_path = agent_worktrees_home / "projects.yaml"
    repos_path = agent_worktrees_home / "repos.yaml"
    projects_authority, projects_data, findings = _load_yaml_object(projects_path)
    if projects_authority is ScanAuthority.ABSENT:
        return [], findings, ScanAuthority.ABSENT
    if projects_authority is ScanAuthority.INDETERMINATE:
        return [], findings, ScanAuthority.INDETERMINATE

    projects = projects_data.get("projects")
    if not isinstance(projects, dict):
        findings.append(
            _finding(
                projects_path,
                "invalid-entry",
                status="indeterminate",
                detail="projects must be a mapping",
            )
        )
        return [], findings, ScanAuthority.INDETERMINATE
    if not projects:
        return [], findings, ScanAuthority.COMPLETE

    repos_authority, repos_data, repos_findings = _load_yaml_object(repos_path)
    findings.extend(repos_findings)
    if repos_authority is not ScanAuthority.COMPLETE:
        if repos_authority is ScanAuthority.ABSENT:
            findings.append(
                _finding(
                    repos_path,
                    "registry-indeterminate",
                    status="indeterminate",
                    detail="repos registry is missing for adopted projects",
                )
            )
        return [], findings, ScanAuthority.INDETERMINATE
    repos = repos_data.get("repos")
    if not isinstance(repos, dict):
        findings.append(
            _finding(
                repos_path,
                "invalid-entry",
                status="indeterminate",
                detail="repos must be a mapping",
            )
        )
        return [], findings, ScanAuthority.INDETERMINATE

    plat = _platform_key()
    roots: list[tuple[str, Path]] = []
    registry_indeterminate = False
    for raw_name in sorted(projects, key=str):
        project = projects[raw_name]
        if not isinstance(raw_name, str) or not isinstance(project, dict):
            findings.append(
                _finding(
                    projects_path,
                    "invalid-entry",
                    status="indeterminate",
                    target=str(raw_name),
                    detail="project entries require string keys and mappings",
                )
            )
            registry_indeterminate = True
            continue
        name = raw_name
        repo = repos.get(name)
        if not isinstance(repo, dict):
            findings.append(
                _finding(projects_path, "identity-mismatch", owner=name)
            )
            continue
        raw_root = repo.get(plat)
        expected_remote = repo.get("remote")
        if (
            not isinstance(raw_root, str)
            or not raw_root.strip()
            or not isinstance(expected_remote, str)
            or not expected_remote.strip()
        ):
            findings.append(
                _finding(
                    repos_path,
                    "identity-mismatch",
                    owner=name,
                    detail=f"missing {plat} path or remote",
                )
            )
            continue
        root = Path(raw_root).expanduser()
        try:
            canonical = root.resolve(strict=True)
        except FileNotFoundError as exc:
            findings.append(
                _finding(
                    repos_path,
                    "identity-mismatch",
                    target=str(root),
                    owner=name,
                    detail=str(exc),
                )
            )
            continue
        except OSError as exc:
            findings.append(
                _finding(
                    repos_path,
                    "registry-indeterminate",
                    status="indeterminate",
                    target=str(root),
                    owner=name,
                    detail=str(exc),
                )
            )
            registry_indeterminate = True
            continue
        try:
            top = git_root(canonical, _resolver._git, _same_file)  # a bare anchor counts as its own root
            actual_remote = _resolver._git(canonical, "remote", "get-url", "origin")
        except subprocess.CalledProcessError as exc:
            findings.append(
                _finding(
                    repos_path,
                    "identity-mismatch",
                    target=str(canonical),
                    owner=name,
                    detail=str(exc),
                )
            )
            continue
        except (OSError, subprocess.TimeoutExpired) as exc:
            findings.append(
                _finding(
                    repos_path,
                    "registry-indeterminate",
                    status="indeterminate",
                    target=str(canonical),
                    owner=name,
                    detail=str(exc),
                )
            )
            registry_indeterminate = True
            continue
        actual_identity = normalize_remote(actual_remote)
        expected_identity = normalize_remote(expected_remote)
        if actual_identity is None or expected_identity is None:
            findings.append(
                _finding(
                    repos_path,
                    "registry-indeterminate",
                    status="indeterminate",
                    target=str(canonical),
                    owner=name,
                    detail="origin remote could not be normalized safely",
                )
            )
            registry_indeterminate = True
            continue
        if not _same_file(top, canonical) or actual_identity != expected_identity:
            findings.append(
                _finding(
                    repos_path,
                    "identity-mismatch",
                    target=str(canonical),
                    owner=name,
                    detail="Git top-level or origin remote differs from registry",
                )
            )
            continue
        roots.append((name, canonical))

    authority = (
        ScanAuthority.INDETERMINATE
        if registry_indeterminate
        else ScanAuthority.COMPLETE
    )
    return roots, findings, authority
