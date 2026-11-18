"""Emit self-contained application source projects after canonical materialization."""
from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import sys
from collections.abc import Callable
from typing import Any, cast

import materialize_installer_engine as assets
from promotion_complete_source import _path as source_path

_TOOLING = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_TOOLING / "libs/installer-engine"))
from runtime_application_binstubs import setup_binstubs, slot_binstubs  # noqa: E402

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

_DECLARATION = "application-source.json"


def retain_outputs(root: Path, selected: tuple[str, ...]) -> tuple[str, ...]:
    """Keep generated outputs with their selected source at local promotion."""
    if "worktree-manager" in selected and (root / "apps/worktree-manager").is_dir():
        return tuple(sorted({*selected, "apps/worktree-manager"}))
    return selected


def _table(text: str, name: str, entries: dict[str, Any]) -> str:
    pattern = re.compile(
        r"^\[" + re.escape(name) + r"\][^\n]*\n.*?(?=^\[|\Z)", re.MULTILINE | re.DOTALL,
    )

    def value(item: Any) -> str:
        if isinstance(item, dict):
            return "{ " + ", ".join(
                json.dumps(key) + " = " + value(field) for key, field in item.items()
            ) + " }"
        if isinstance(item, list):
            return "[" + ", ".join(value(field) for field in item) + "]"
        if isinstance(item, (str, bool)):
            return json.dumps(item, ensure_ascii=True)
        raise ValueError("unsupported application project table value")

    replacement = (
        "[" + name + "]\n"
        + "".join(json.dumps(key) + " = " + value(item) + "\n" for key, item in entries.items())
        + "\n"
    ) if entries else ""
    result, count = pattern.subn(lambda _match: replacement, text)
    if count > 1:
        raise ValueError("ambiguous application project table")
    return result if count else text.rstrip() + "\n\n" + replacement


def _project_manifest(source: Path) -> str:
    from standalone_build import embedded_projects as select_embedded

    text = (source / "pyproject.toml").read_text(encoding="utf-8")
    data = tomllib.loads(text)
    embedded = cast(Callable[[Path], dict[str, tuple[Path, dict[str, Any]]]], select_embedded)(source)
    groups = dict(data.get("dependency-groups", {}))
    groups.pop("first-party-core", None)
    text = _table(text, "dependency-groups", groups)
    mappings = dict(data.get("tool", {}).get("uv", {}).get("sources", {}))
    for name in embedded:
        mappings.pop(name)
    text = _table(text, "tool.uv.sources", mappings)
    package_data = dict(data.get("tool", {}).get("setuptools", {}).get("package-data", {}))
    for directory, metadata in embedded.values():
        for package, patterns in metadata.get("tool", {}).get("setuptools", {}).get("package-data", {}).items():
            package_data[package] = sorted(set(package_data.get(package, ())) | set(patterns))
        roots = [path for path in (directory / "src").iterdir() if (path / "__init__.py").is_file()]
        if len(roots) != 1:
            raise ValueError("embedded application library has no unique package")
        package = roots[0].name
        package_data[package] = sorted(set(package_data.get(package, ())) | {"LICENSE"})
    text = _table(text, "tool.setuptools.package-data", package_data)
    tomllib.loads(text)
    return text


def materialize(root: Path) -> list[str]:
    """Produce the opted-in Manager source project without building or installing."""
    root = root.absolute()
    source = root / "worktree-manager"
    declaration = source / _DECLARATION
    if not declaration.exists() and not declaration.is_symlink():
        return []
    from governed_feed_trust import ArtifactBuildError
    from standalone_python_artifacts import injected_resources

    try:
        problem = assets.derived_path_problem(declaration, root, required=True)
        if problem:
            raise ValueError(problem)
        if json.loads(declaration.read_text(encoding="utf-8")) != {
            "schema": "copilot-extensions.application-source", "version": 1,
            "name": "worktree-manager",
        }:
            raise ValueError("unsupported application source declaration")
        destination = root / "apps" / "worktree-manager"
        if destination.exists() or destination.is_symlink():
            raise ValueError("application source output already exists")
        if problem := assets.derived_path_problem(destination / "pyproject.toml", root, required=False):
            raise ValueError(problem)
        from uv_editable_ref import materialized_local_source_problems

        if problems := materialized_local_source_problems(source):
            raise ValueError("; ".join(problems))
        if not (source / "src/worktree_manager/__init__.py").is_file():
            raise ValueError("application source package is missing")
        resources = injected_resources("worktree-manager", root)
        manifest = _project_manifest(source)
        metadata = tomllib.loads(manifest)["project"]
        commands = metadata.get("scripts")
        python = metadata.get("requires-python")
        if (
            not isinstance(commands, dict) or "worktree-manager" not in commands
            or any(not isinstance(entry, str) for entry in commands.values())
            or not isinstance(python, str) or not python.strip()
            or any(character in python for character in "\r\n\0")
        ):
            raise ValueError("application source requires Manager commands and declared Python")
        launchers = {}
        for name, entry in commands.items():
            for filename, text in slot_binstubs(name, entry, source_project=True).items():
                if filename in {"install", "install.cmd", "install.ps1", "install.sh"} and name != "worktree-manager":
                    continue
                if filename in launchers:
                    raise ValueError("application source commands collide")
                launchers[filename] = text
        members = {
            path.relative_to(source).as_posix(): path
            for path in (source / "src").rglob("*") if path.is_file()
        }
        members.update({
            name: source / name for name in ("README.md", "LICENSE", "setup.py")
        })
        from python_project_inventory import contained_path

        mappings = tomllib.loads(manifest).get("tool", {}).get("uv", {}).get("sources", {})
        for mapping in mappings.values():
            for entry in mapping if isinstance(mapping, list) else [mapping]:
                if isinstance(entry, dict) and "path" in entry:
                    local = contained_path(source / entry["path"], source)
                    for path in local.rglob("*") if local.is_dir() else [local]:
                        if path.is_file():
                            members[path.relative_to(source).as_posix()] = path
        for name, path in resources.items():
            relative = "src/" + name
            if relative in members and members[relative].read_bytes() != path.read_bytes():
                raise ValueError("included application source resource differs from its owner")
            members[relative] = path
        members["_standalone_runtime_build.py"] = root / "libs/versioned-runtime/standalone_build.py"
        members["install.py"] = root / "libs/installer-engine/runtime_application_install.py"
        reserved = {Path(name).parts[0].casefold() for name in members} | {
            "pyproject.toml", "manifest.in", ".python-version", ".gitattributes", "bin", "build", "dist",
        }
        if any(name.casefold() in reserved for name in launchers):
            raise ValueError("application source commands conflict with project inputs")
        for name in launchers:
            source_path(name)
        for path in members.values():
            if problem := assets.derived_path_problem(path, root, required=True):
                raise ValueError(problem)
        destination.mkdir(parents=True)
        for name, path in sorted(members.items()):
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
        (destination / "pyproject.toml").write_text(manifest, encoding="utf-8", newline="\n")
        (destination / "MANIFEST.in").write_text(
            "include _standalone_runtime_build.py\nrecursive-include src *\n",
            encoding="utf-8", newline="\n",
        )
        (destination / ".gitattributes").write_text("* -text\n", encoding="utf-8", newline="\n")
        (destination / ".python-version").write_text(
            python + "\n", encoding="utf-8", newline="\n",
        )
        for filename, text in launchers.items():
            path = destination / filename
            path.write_text(text, encoding="utf-8", newline="\r\n" if path.suffix == ".ps1" else "")
            if path.suffix == ".sh" or not path.suffix:
                path.chmod(0o700)
        directory = destination / "bin"
        directory.mkdir()
        for filename, text in setup_binstubs().items():
            path = directory / filename
            path.write_text(text, encoding="utf-8", newline="\r\n" if path.suffix == ".ps1" else "")
            if path.suffix == ".sh":
                path.chmod(0o700)
        if problems := materialized_local_source_problems(destination):
            raise ValueError("; ".join(problems))
        return [f"OK   {destination} <- {source} (embedded application source)"]
    except (OSError, ValueError, RuntimeError, ArtifactBuildError) as error:
        return [f"SKIP {declaration}: {error}"]


def bind_source(root: Path, *, source_commit: str, source_tree: str) -> list[str]:
    """Associate the emitted source inputs through the existing version owner."""
    root = root.absolute()
    application = root / "apps/worktree-manager"
    target = application / "runtime-source.json"
    try:
        if problem := assets.derived_path_problem(target, root, required=False):
            raise ValueError(problem)
        if target.exists():
            raise ValueError("application source association already exists")
        from standalone_python_artifacts import TRUSTED_ROOT

        previous = sys.path[:]
        try:
            sys.path.insert(0, str(TRUSTED_ROOT / "libs/versioned-runtime"))
            import versioned_runtime_identity as owner
            import versioned_runtime_fingerprint as fingerprints
        finally:
            sys.path[:] = previous
        metadata = tomllib.loads((application / "pyproject.toml").read_text(encoding="utf-8"))
        project = metadata.get("project")
        name = project.get("name") if isinstance(project, dict) else None
        version = project.get("version") if isinstance(project, dict) else None
        if (
            not isinstance(name, str)
            or re.sub(r"[-_.]+", "-", name).lower() != "copilot-extensions-worktree-manager"
            or not isinstance(version, str)
        ):
            raise ValueError("application source project identity is invalid")
        digest = owner.assembly_hash(
            application, native_path=fingerprints.native_path,
            fingerprint=fingerprints.fingerprint_source,
        )
        identity = owner.SourceIdentity(
            "worktree-manager", version, source_commit, source_tree, digest,
            owner.runtime_version(version, source_commit, digest),
        )
        document = identity.document()
        owner.parse_source_identity(document, package_version=version, plugin="worktree-manager")
        target.write_text(json.dumps(document, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        return [f"OK   {target} (source-qualified application identity)"]
    except (OSError, ValueError, RuntimeError) as error:
        return [f"SKIP {target}: {error}"]


def bind_promotion_source(root: Path, read_identity: Callable[[], str]) -> list[str]:
    """Keep opted-in source association with its producer, not Git discovery."""
    if not (root / "worktree-manager" / _DECLARATION).is_file():
        return []
    commit, tree = read_identity().splitlines()
    return bind_source(root, source_commit=commit, source_tree=tree)


def promotion_outputs(root: Path) -> tuple[str, ...]:
    """Identify complete emitted distributions that bypass development ignores."""
    application = "apps/worktree-manager"
    return (application,) if (
        (root / "worktree-manager" / _DECLARATION).is_file()
        and (root / application / "runtime-source.json").is_file()
    ) else ()
