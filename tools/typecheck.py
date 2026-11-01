"""Run explicit, reproducible MyPy checks for the repository's admitted projects."""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
from typing import Literal, cast

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "libs" / "installer-engine"))
from tools._admission_protocol import acquire as acquire_environment  # noqa: E402
from tools.cache_directory_paths import ordinary_cache_root  # noqa: E402
from installer_dependency_policy import DependencyPolicyError, run_uv_install  # noqa: E402
from single_instance_lease import AlreadyRunningError  # noqa: E402

Level = Literal["pending", "basic", "strict"]


class TypecheckError(ValueError):
    """The checker cannot faithfully qualify its selected input."""


@dataclass(frozen=True)
class Project:
    name: str
    include: tuple[str, ...]
    exclude: tuple[str, ...]
    level: Level
    python_version: str
    platforms: tuple[str, ...]
    requirements: str

    def owns(self, path: str) -> bool:
        """Match exact files or complete directory prefixes, not filename globs."""
        def matches(prefix: str) -> bool:
            return path == prefix or path.startswith(prefix + "/")
        return any(map(matches, self.include)) and not any(map(matches, self.exclude))


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise TypecheckError("Expected a JSON object with string keys")
    return cast(dict[str, object], value)


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise TypecheckError(f"Duplicate manifest field: {key}")
        result[key] = value
    return result


def _strings(value: object, field: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise TypecheckError(f"{field} must be a list of strings")
    result = tuple(item for item in value if isinstance(item, str))
    if (not result and not allow_empty) or any(not item for item in result):
        raise TypecheckError(f"{field} cannot be empty")
    if len(set(result)) != len(result):
        raise TypecheckError(f"{field} contains duplicate values")
    return result


def _relative(value: str) -> str:
    if (
        not value or "\\" in value or ":" in value or value.startswith("/")
        or any(ord(char) < 32 or 127 <= ord(char) < 160 or char in "\u2028\u2029" for char in value)
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise TypecheckError(f"Not a repository-relative path: {value!r}")
    return PurePosixPath(value).as_posix()


def load_projects(root: Path = ROOT) -> tuple[Project, ...]:
    """Read a versioned manifest without accepting arbitrary MyPy arguments."""
    value: object = json.loads(
        (root / "tools" / "typecheck-projects.json").read_text(encoding="utf-8"),
        object_pairs_hook=_pairs,
    )
    manifest = _object(value)
    fields = {"version", "projects", "python_version", "platforms", "requirements"}
    if set(manifest) != fields or type(manifest["version"]) is not int:
        raise TypecheckError("Invalid typecheck manifest fields")
    if manifest["version"] != 1 or not isinstance(manifest["projects"], list):
        raise TypecheckError("Unsupported typecheck manifest")
    projects: list[Project] = []
    rows: list[object] = manifest["projects"]
    required = {"name", "include", "exclude", "level"}
    defaults = {key: manifest[key] for key in ("python_version", "platforms", "requirements")}
    for value in rows:
        row = _object(value)
        if not required <= set(row) <= required | set(defaults):
            raise TypecheckError("Invalid project fields")
        row = {**defaults, **row}
        name, level, python_version, requirements = (
            row["name"], row["level"], row["python_version"], row["requirements"]
        )
        if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
            raise TypecheckError("Invalid project name")
        if level not in ("pending", "basic", "strict"):
            raise TypecheckError(f"{name}: invalid admission level")
        if not isinstance(python_version, str) or not re.fullmatch(r"3\.[0-9]+", python_version):
            raise TypecheckError(f"{name}: invalid Python version")
        if not isinstance(requirements, str):
            raise TypecheckError(f"{name}: invalid requirements path")
        include = tuple(_relative(path) for path in _strings(row["include"], "include"))
        exclude = tuple(_relative(path) for path in _strings(row["exclude"], "exclude", allow_empty=True))
        platforms = _strings(row["platforms"], "platforms")
        if any(platform not in ("linux", "win32", "darwin") for platform in platforms):
            raise TypecheckError(f"{name}: unsupported platform")
        projects.append(Project(
            name, include, exclude, cast(Level, level), python_version, platforms,
            _relative(requirements),
        ))
    if len({project.name for project in projects}) != len(projects) or not projects:
        raise TypecheckError("Project names must be unique and the portfolio nonempty")
    return tuple(projects)


def _run(argv: list[str], root: Path, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv, cwd=root, env=env, capture_output=True, text=True, encoding="utf-8",
        errors="strict", timeout=120, creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
    )


def python_paths(root: Path = ROOT) -> tuple[str, ...]:
    """Include staged, unstaged and untracked non-ignored developer files."""
    selectors = {
        "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
        "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    }
    env = {
        key: value for key, value in os.environ.items()
        if key.upper() not in selectors and not key.upper().startswith("GIT_CONFIG")
    }
    env.update(GIT_TERMINAL_PROMPT="0", GIT_NO_LAZY_FETCH="1", GIT_OPTIONAL_LOCKS="0")
    result = _run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], root, env=env)
    if result.returncode:
        raise TypecheckError(result.stderr.strip() or "Git inventory failed")
    paths = sorted({path for path in result.stdout.split("\0") if path.endswith(".py")})
    for path in paths:
        _relative(path)
        source = root.joinpath(*path.split("/"))
        if not source.is_file() or not source.resolve().is_relative_to(root.resolve()):
            raise TypecheckError(f"Python source is missing or outside the repository: {path}")
    return tuple(paths)


def inventory(paths: tuple[str, ...], projects: tuple[Project, ...]) -> dict[str, Project]:
    """Account for each physical Python file exactly once, even while pending."""
    owners: dict[str, Project] = {}
    for path in paths:
        matches = [project for project in projects if project.owns(path)]
        if len(matches) != 1:
            names = ", ".join(project.name for project in matches) or "unclassified"
            raise TypecheckError(f"{path}: expected one owner, found {names}")
        owners[path] = matches[0]
    if not owners:
        raise TypecheckError("Empty Python inventory cannot qualify the portfolio")
    return owners


def requirements_bytes(root: Path, project: Project) -> bytes:
    """Capture one flat lock; includes need a separately qualified graph snapshot."""
    requirements = root.joinpath(*project.requirements.split("/"))
    if not requirements.is_file() or not requirements.resolve().is_relative_to(root.resolve()):
        raise TypecheckError(f"{project.name}: requirements must exist inside the repository")
    contents = requirements.read_bytes()
    for line in contents.splitlines():
        option = line.strip()
        if option.startswith(b"-") and not option.startswith(b"--hash=sha256:"):
            raise TypecheckError(f"{project.name}: checker requirements must be a flat hash-locked export")
    return contents


def environment_path(
    root: Path, project: Project, *, captured_lock: bytes | None = None,
) -> tuple[Path, str]:
    contents = requirements_bytes(root, project) if captured_lock is None else captured_lock
    digest = hashlib.sha256(
        contents
        + f"\n{sys.version_info[:2]!r}\n{sys.platform}".encode("ascii")
    ).hexdigest()
    path = root / ".test-venvs" / "typecheck" / sys.platform / digest[:20]
    return ordinary_cache_root(path), digest


def environment_python(path: Path) -> Path:
    """Select only the explicitly prepared environment's interpreter."""
    return path / "Scripts" / "python.exe" if os.name == "nt" else path / "bin" / "python"


def prepare(root: Path, project: Project) -> None:
    """Explicitly create a hash-locked checker environment under its normal lease."""
    uv = shutil.which("uv")
    if uv is None:
        raise TypecheckError("uv is required for --prepare")
    contents = requirements_bytes(root, project)
    path, digest = environment_path(root, project, captured_lock=contents)
    path.mkdir(parents=True, exist_ok=True)
    lease = acquire_environment(0, path)
    try:
        marker = path / ".typecheck-prepared"
        if marker.is_file() and marker.read_text(encoding="utf-8").strip() == digest:
            if environment_python(path).is_file():
                return
        before = path.stat()
        result = _run([uv, "venv", "--allow-existing", "--python", sys.executable, str(path)], root)
        if result.returncode:
            raise TypecheckError(result.stderr.strip() or "Checker environment creation failed")
        after = path.stat()
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise TypecheckError("uv replaced the leased checker environment directory")
        snapshot = path / "typecheck-requirements.txt"
        snapshot.write_bytes(contents)
        result = run_uv_install(
            uv, ["--python", str(environment_python(path)), "--require-hashes",
                 "-r", str(snapshot)],
            cwd=root, trusted_source_roots=(root,), check=True, timeout=300,
        )
        if result.returncode:
            raise TypecheckError("Checker dependency preparation failed")
        marker.write_text(digest + "\n", encoding="utf-8")
    finally:
        lease.release()


def check_project(
    root: Path, project: Project, files: tuple[str, ...], *,
    all_paths: tuple[str, ...], strict: bool = False,
) -> int:
    """Check one declared scope; never execute product or test code."""
    if not files:
        raise TypecheckError(f"{project.name}: selected scope has no Python files")
    path, digest = environment_path(root, project)
    python = environment_python(path)
    marker = path / ".typecheck-prepared"
    if not python.is_file() or not marker.is_file() or marker.read_text(encoding="utf-8").strip() != digest:
        raise TypecheckError(f"{project.name}: run python tools/typecheck.py {project.name} --prepare")
    lease = acquire_environment(0, path)
    try:
        imports = {str(root), str(root / "libs" / "installer-engine")}
        imports.update(
            str(root.joinpath(*file.split("/")[:file.split("/").index("src") + 1]))
            for file in all_paths if "src" in file.split("/")
        )
        env = {
            key: value for key, value in os.environ.items()
            if key not in {"PYTHONPATH", "PYTHONHOME", "MYPYPATH"}
        }
        env["MYPYPATH"] = os.pathsep.join(sorted(imports))
        targets = path / "typecheck-targets.txt"
        targets.write_text("\n".join(files) + "\n", encoding="utf-8")
        config = path / "typecheck-mypy.ini"
        config.write_text("[mypy]\n", encoding="utf-8")
        for platform in project.platforms:
            argv = [
                str(python), "-I", "-m", "mypy", "--config-file", str(config),
                "--python-version", project.python_version, "--platform", platform,
                "--explicit-package-bases", "--follow-imports", "silent",
                "--check-untyped-defs", "--show-error-codes", "--no-pretty",
                "--cache-dir", str(path / "mypy-cache" / project.name / platform),
            ]
            if strict or project.level == "strict":
                argv.append("--strict")
            argv.append("@" + str(targets))
            result = _run(argv, root, env=env)
            print(f"[{project.level}] {project.name} ({platform}, {len(files)} files)")
            print(result.stdout, end="")
            print(result.stderr, end="", file=sys.stderr)
            if result.returncode:
                return result.returncode
        return 0
    finally:
        try:
            (path / "typecheck-targets.txt").unlink(missing_ok=True)
            (path / "typecheck-mypy.ini").unlink(missing_ok=True)
        finally:
            lease.release()


def main(argv: list[str] | None = None) -> int:
    """Provide the same explicit scope and policy selection locally and in CI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("projects", nargs="*")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--all", action="store_true", help="probe all projects, including pending")
    selection.add_argument("--enforced", action="store_true", help="check admitted projects (default)")
    selection.add_argument("--inventory", action="store_true", help="classify Python without checking")
    parser.add_argument("--prepare", action="store_true", help="explicitly prepare selected environments")
    parser.add_argument("--strict", action="store_true", help="probe strictness without changing admission")
    parser.add_argument("--require-complete", action="store_true", help="require every project to be strict")
    parser.add_argument("--json", action="store_true", help="machine-readable --inventory output")
    args = parser.parse_args(argv)
    if args.projects and (args.all or args.enforced or args.inventory):
        parser.error("choose project names or a portfolio selector, not both")
    if args.inventory and (args.prepare or args.strict):
        parser.error("--inventory does not prepare or run a checker")
    if args.json and not args.inventory:
        parser.error("--json requires --inventory")
    try:
        projects = load_projects()
        owners = inventory(python_paths(), projects)
        counts = Counter(project.level for project in projects)
        if not args.json:
            print(
                f"Physical Python inventory: {len(owners)} files; "
                f"{counts['pending']} pending / {counts['basic']} basic / {counts['strict']} strict projects"
            )
        if args.require_complete and any(project.level != "strict" for project in projects):
            raise TypecheckError("Portfolio is not complete: pending/basic projects remain")
        if args.inventory:
            if args.json:
                print(json.dumps({
                    "version": 1, "kind": "inventory-only",
                    "project_levels": dict(counts),
                    "files": [
                        {"path": path, "project": owner.name, "level": owner.level}
                        for path, owner in owners.items()
                    ],
                }))
                return 0
            for project in projects:
                count = sum(owner.name == project.name for owner in owners.values())
                print(f"{project.name}: {project.level}, {count} files")
            return 0
        names: list[str] = args.projects
        unknown = set(names) - {project.name for project in projects}
        if unknown:
            raise TypecheckError("Unknown projects: " + ", ".join(sorted(unknown)))
        selected = [
            project for project in projects
            if project.name in names or (not names and (args.all or project.level != "pending"))
        ]
        if not selected:
            raise TypecheckError("No projects selected")
        failed = False
        for project in selected:
            if args.prepare:
                prepare(ROOT, project)
                path, _digest = environment_path(ROOT, project)
                print(f"Prepared {project.name}: {environment_python(path)}")
            else:
                files = tuple(path for path, owner in owners.items() if owner.name == project.name)
                failed = check_project(
                    ROOT, project, files, all_paths=tuple(owners), strict=args.strict,
                ) != 0 or failed
        if not args.prepare and any(project.level == "pending" for project in selected):
            print("Pending project probes do not certify admission.", file=sys.stderr)
            failed = True
        return int(failed)
    except AlreadyRunningError as exc:
        print(f"typecheck: checker environment is busy: {exc}", file=sys.stderr)
        return 3
    except (OSError, ValueError, DependencyPolicyError, subprocess.SubprocessError) as exc:
        print(f"typecheck: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
