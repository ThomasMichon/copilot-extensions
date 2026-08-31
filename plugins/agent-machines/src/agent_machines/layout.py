"""Diagnose and migrate agent-machines requirement-package layouts."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import discover
from .manifest import ManifestError, load_package


@dataclass
class LayoutFinding:
    level: str
    code: str
    message: str


@dataclass
class LayoutReport:
    repo: str
    path: str
    status: str
    package_count: int = 0
    findings: list[LayoutFinding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(finding.level == "error" for finding in self.findings)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["ok"] = self.ok
        return payload


@dataclass
class MigrationMove:
    source: str
    target: str


@dataclass
class MigrationResult:
    repo: str
    path: str
    status: str
    dry_run: bool
    changed: bool
    moves: list[MigrationMove] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _legacy_moves(repo_path: Path, repo_name: str) -> list[tuple[Path, Path]]:
    legacy = repo_path / discover.LEGACY_MACHINE_STATE_DIR
    if legacy.exists() and not legacy.is_dir():
        raise ManifestError(f"{legacy}: legacy package path is not a directory")
    if not legacy.exists():
        return []

    root = repo_path / discover.MACHINE_STATE_ROOT
    moves: list[tuple[Path, Path]] = []
    target_names: dict[str, Path] = {}
    package_names: dict[str, Path] = {}
    for entry in sorted(legacy.iterdir()):
        if not entry.is_file():
            raise ManifestError(
                f"{entry}: legacy migration supports direct YAML files and README.md only"
            )
        if entry.suffix.casefold() in {".yaml", ".yml"}:
            package = load_package(entry, source_repo=repo_name)
            if package.name in package_names:
                raise ManifestError(
                    f"{entry}: package {package.name!r} duplicates "
                    f"{package_names[package.name]}; canonical packages require "
                    "unique names"
                )
            package_names[package.name] = entry
            target = root / discover.ALL_PACKAGES_DIR / entry.name
        elif entry.name.casefold() == "readme.md":
            target = root / "README.md"
        else:
            raise ManifestError(
                f"{entry}: unsupported legacy entry; move or remove it before migration"
            )
        folded = str(target).casefold()
        if folded in target_names:
            raise ManifestError(
                f"{entry}: migration target collides with {target_names[folded]}"
            )
        if target.exists():
            raise ManifestError(f"{target}: migration target already exists")
        target_names[folded] = entry
        moves.append((entry, target))
    return moves


def inspect_repo_layout(repo_path: Path, repo_name: str, machine: str) -> LayoutReport:
    repo_path = repo_path.expanduser().absolute()
    root = repo_path / discover.MACHINE_STATE_ROOT
    legacy = repo_path / discover.LEGACY_MACHINE_STATE_DIR
    findings: list[LayoutFinding] = []

    if root.exists() and not root.is_dir():
        return LayoutReport(
            repo_name,
            str(repo_path),
            "malformed",
            findings=[LayoutFinding(
                "error", "invalid-layout", f"{root}: canonical package path is not a directory"
            )],
        )
    if legacy.exists() and not legacy.is_dir():
        return LayoutReport(
            repo_name,
            str(repo_path),
            "malformed",
            findings=[LayoutFinding(
                "error", "invalid-layout", f"{legacy}: legacy package path is not a directory"
            )],
        )

    if root.is_dir():
        mixed = legacy.is_dir()
        if mixed:
            findings.append(LayoutFinding(
                "error",
                "mixed-layout",
                f"{legacy} is ignored because {root} exists; finish or revert the migration",
            ))
        try:
            packages = discover.packages_in_repo(repo_path, repo_name, machine)
        except ManifestError as exc:
            findings.append(LayoutFinding("error", "invalid-layout", str(exc)))
            packages = []
        if mixed:
            status = "mixed"
        else:
            status = (
                "canonical"
                if not any(f.level == "error" for f in findings)
                else "malformed"
            )
        return LayoutReport(
            repo_name, str(repo_path), status, len(packages), findings
        )

    if legacy.is_dir():
        try:
            packages = discover.packages_in_repo(repo_path, repo_name, machine)
        except ManifestError as exc:
            return LayoutReport(
                repo_name,
                str(repo_path),
                "malformed",
                0,
                [LayoutFinding("error", "legacy-not-migratable", str(exc))],
            )
        try:
            moves = _legacy_moves(repo_path, repo_name)
        except ManifestError as exc:
            findings.append(LayoutFinding(
                "advisory",
                "legacy-not-migratable",
                f"{exc}; migrate this repo manually",
            ))
        else:
            code = "legacy-layout" if moves else "empty-legacy-layout"
            message = (
                f"run `agent-machines migrate --repo {repo_name}` to preview migration"
                if moves
                else "legacy directory is empty; remove it or add canonical packages"
            )
            findings.append(LayoutFinding("advisory", code, message))
        return LayoutReport(
            repo_name, str(repo_path), "legacy", len(packages), findings
        )

    return LayoutReport(repo_name, str(repo_path), "absent")


def _adopted_repos(
    registry: dict | None = None,
    projects: dict | None = None,
) -> list[discover.RepoCandidate]:
    return discover.candidate_repos(registry, projects)


def resolve_repo(
    value: str,
    registry: dict | None = None,
    projects: dict | None = None,
) -> tuple[str, Path]:
    for candidate in _adopted_repos(registry, projects):
        if candidate.name.casefold() == value.casefold():
            name, path = candidate.name, candidate.path
            if not path.is_dir():
                raise ManifestError(f"registered repo {name!r} is unavailable at {path}")
            return name, path
    candidate = Path(value).expanduser()
    if candidate.is_dir():
        return candidate.resolve().name, candidate.resolve()
    raise ManifestError(
        f"repo {value!r} is neither a directory nor an adopted project name"
    )


def _git_path(repo_path: Path, argument: str) -> Path | None:
    git = shutil.which("git")
    if git is None:
        return None
    try:
        result = subprocess.run(  # noqa: S603 - resolved executable, fixed argv
            [git, "-C", str(repo_path), "rev-parse", argument],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    path = Path(result.stdout.strip())
    if not path.is_absolute():
        path = repo_path / path
    return path.resolve()


def resolve_cwd_repo(
    cwd: Path | None = None,
    registry: dict | None = None,
    projects: dict | None = None,
) -> tuple[str, Path, Path]:
    """Resolve the package-owning repository containing ``cwd``.

    Registered linked worktrees match their anchor by Git common-directory
    identity, while a standalone repository falls back to its Git top-level
    directory and basename.
    """
    current = (cwd or Path.cwd()).resolve()
    if shutil.which("git") is None:
        raise ManifestError(
            "git is required to resolve repository scope; install Git or pass "
            "--all-projects"
        )
    top_level = _git_path(current, "--show-toplevel")
    if top_level is None:
        raise ManifestError(
            f"{current} is not inside a Git repository; pass --repo "
            "<name-or-path> or --all-projects"
        )
    current_common = _git_path(top_level, "--git-common-dir")
    for candidate in discover.registered_repos(registry):
        candidate_common = _git_path(candidate.path, "--git-common-dir")
        if current_common is not None and candidate_common == current_common:
            return candidate.name, top_level, candidate.path.resolve()
        if candidate.path.resolve() == top_level:
            return candidate.name, top_level, candidate.path.resolve()
    return top_level.name, top_level, top_level


def inspect_layouts(
    machine: str,
    repo: str | None = None,
    registry: dict | None = None,
    projects: dict | None = None,
) -> list[LayoutReport]:
    if repo:
        name, path = resolve_repo(repo, registry, projects)
        return [inspect_repo_layout(path, name, machine)]

    reports: list[LayoutReport] = []
    for candidate in _adopted_repos(registry, projects):
        name, path = candidate.name, candidate.path
        if not path.is_dir():
            required = candidate.required
            owners = ", ".join(candidate.required_by)
            reports.append(LayoutReport(
                name,
                str(path),
                "unavailable",
                findings=[LayoutFinding(
                    "error" if required else "advisory",
                    "supplemental-repo-unavailable" if required else "repo-unavailable",
                    (
                        f"supplemental repo required by {owners} does not exist: {path}"
                        if required
                        else f"registered repo path does not exist: {path}"
                    ),
                )],
            ))
            continue
        reports.append(inspect_repo_layout(path, name, machine))
    return reports


def migrate_repo_layout(
    repo_path: Path,
    repo_name: str,
    *,
    apply: bool = False,
) -> MigrationResult:
    repo_path = repo_path.expanduser().absolute()
    root = repo_path / discover.MACHINE_STATE_ROOT
    legacy = repo_path / discover.LEGACY_MACHINE_STATE_DIR

    if root.exists() and not root.is_dir():
        raise ManifestError(f"{root}: canonical package path is not a directory")
    if root.exists():
        if legacy.exists():
            raise ManifestError(
                f"refusing mixed-layout migration: both {root} and {legacy} exist"
            )
        return MigrationResult(
            repo_name, str(repo_path), "already-canonical", not apply, False
        )
    if not legacy.exists():
        return MigrationResult(
            repo_name, str(repo_path), "no-layout", not apply, False
        )

    planned = _legacy_moves(repo_path, repo_name)
    moves = [MigrationMove(str(source), str(target)) for source, target in planned]
    if not planned:
        return MigrationResult(
            repo_name, str(repo_path), "no-layout", not apply, False
        )
    if not apply:
        return MigrationResult(
            repo_name, str(repo_path), "would-migrate", True, True, moves
        )

    completed: list[tuple[Path, Path]] = []
    try:
        (root / discover.ALL_PACKAGES_DIR).mkdir(parents=True, exist_ok=False)
        for source, target in planned:
            if target.exists():
                raise FileExistsError(f"migration target appeared during apply: {target}")
            source.replace(target)
            completed.append((source, target))
        legacy.rmdir()
    except OSError as exc:
        rollback_errors: list[str] = []
        for source, target in reversed(completed):
            try:
                source.parent.mkdir(parents=True, exist_ok=True)
                target.replace(source)
            except OSError as rollback_exc:
                rollback_errors.append(str(rollback_exc))
        for directory in (root / discover.ALL_PACKAGES_DIR, root):
            try:
                if directory.exists() and not any(directory.iterdir()):
                    directory.rmdir()
            except OSError as rollback_exc:
                rollback_errors.append(str(rollback_exc))
        detail = f"; rollback failures: {'; '.join(rollback_errors)}" if rollback_errors else ""
        raise ManifestError(f"migration failed: {exc}{detail}") from exc

    return MigrationResult(
        repo_name, str(repo_path), "migrated", False, True, moves
    )
