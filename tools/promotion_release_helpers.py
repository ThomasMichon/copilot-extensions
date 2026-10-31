"""Bind release implementations and executable build inputs to qualified units."""
from __future__ import annotations

import ast
from pathlib import Path

from promotion_unit_contract import UnitEvidenceError, verify_sources
from release_controller_contract import helper_implementations
from release_helper_loaders import dynamic_loads

ENTRYPOINTS = (
    "promotion_frozen_candidate", "promote_release", "publish_python_artifacts",
    "qualify_promotion_candidate", "promotion_source_archive", "promotion_lifecycle",
    "tag_promotion", "seasoned_uv", "promotion_release_helpers",
)
BUILD_INPUTS = (
    "libs/agent-procutil/setup.py",
    "plugins/agent-containers/setup.py",
    "plugins/agent-containers/scripts/standalone_build.py",
    "libs/versioned-runtime/standalone_build.py",
)
DYNAMIC_INPUTS = {
    "installer_dependency_policy": ("libs/installer-engine/installer_dependency_config.py",),
}
BUILD_DECISIONS = {
    "libs/versioned-runtime/standalone_build.py",
} | {path for paths in DYNAMIC_INPUTS.values() for path in paths}
BUILD_ORIGINS = {
    "plugins/agent-containers/scripts/standalone_build.py": "libs/versioned-runtime/standalone_build.py",
}


def _registered_execs(tree: ast.Module, relative: str) -> set[ast.Call]:
    """No release helper has authority to execute a dynamically loaded builder."""
    return set()


def _modules(root: Path) -> dict[str, Path]:
    result = {}

    def add(name: str, path: Path) -> None:
        if name in result and result[name] != path:
            raise UnitEvidenceError(f"ambiguous repository helper module: {name}")
        result[name] = path

    for path in sorted((root / "tools").glob("*.py")):
        add(path.stem, path)
    for initializer in sorted((root / "tools").glob("*/__init__.py")):
        for path in sorted(initializer.parent.rglob("*.py")):
            parts = path.relative_to(root / "tools").parts
            module = ".".join((*parts[:-1], path.stem))
            add(module.removesuffix(".__init__"), path)
    for path in sorted((root / "libs").glob("*/*.py")):
        if not path.name.startswith("test_") and path.stem not in ("setup", "__init__"):
            add(path.stem, path)
    for path in sorted((root / "libs").glob("*/src/**/*.py")):
        parts = path.relative_to(root / "libs").parts[2:]
        module = ".".join((*parts[:-1], path.stem))
        if path.stem == "__init__":
            module = ".".join(parts[:-1])
        add(module, path)
    return result


def helper_closure(
    root: Path, *, entrypoints: tuple[str, ...] = ENTRYPOINTS,
    controllers: tuple[str, ...] = (),
) -> tuple[set[str], set[str]]:
    """Resolve repository imports as data, including registered dynamic loaders."""
    modules = _modules(root)
    commands = {
        name.removesuffix(".py") for controller in controllers
        for name in helper_implementations(controller)
    }
    roots = set(entrypoints) | commands
    pending = list(roots)
    visited, sources, decisions = set(), set(), set()
    file_loaded, decision_bodies = set(), set()
    by_path = {path.relative_to(root).as_posix(): name for name, path in modules.items()}

    def enqueue_file(relative: str) -> None:
        file_loaded.add(relative)
        key = by_path.get(relative)
        if key is None:
            key = "@" + relative
            modules[key] = root / relative
            by_path[relative] = key
        pending.append(key)

    for name in roots:
        if name not in modules:
            raise UnitEvidenceError(f"missing release implementation: {name}")
    while pending:
        name = pending.pop()
        if name.startswith("tools.") and name.removeprefix("tools.") in modules:
            name = name.removeprefix("tools.")
        if name in visited or name not in modules:
            continue
        visited.add(name)
        parts = name.split(".")
        pending.extend(".".join(parts[:index]) for index in range(1, len(parts)))
        path = modules[name]
        relative = path.relative_to(root).as_posix()
        if (
            path.is_symlink() or not path.is_file()
            or not path.resolve().is_relative_to(root.resolve())
        ):
            raise UnitEvidenceError(f"missing or indirect release implementation: {relative}")
        sources.add(relative)
        try:
            tree = ast.parse(path.read_bytes(), filename=relative)
        except (SyntaxError, UnicodeError) as error:
            raise UnitEvidenceError(f"invalid release implementation: {relative}") from error
        if any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) for node in ast.walk(tree)
        ):
            decision_bodies.add(relative)
            if relative.startswith("tools/"):
                decisions.add(relative)
        for loaded in dynamic_loads(tree, relative):
            enqueue_file(loaded)
        registered_execs = _registered_execs(tree, relative)
        package = "" if name.startswith("@") else (
            name if path.name == "__init__.py" else name.rpartition(".")[0]
        )
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                pending.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                target = node.module or ""
                if node.level:
                    parts = package.split(".")
                    if not package or node.level > len(parts):
                        raise UnitEvidenceError(f"unresolved relative release import: {relative}")
                    target = ".".join(
                        parts[:len(parts) - node.level + 1] + ([target] if target else [])
                    )
                pending.append(target)
                pending.extend(f"{target}.{alias.name}" for alias in node.names)
            elif isinstance(node, ast.Call):
                operation = getattr(node.func, "id", getattr(node.func, "attr", ""))
                if relative.startswith("tools/") and operation in (
                    "exec", "eval", "import_module", "run_path",
                ):
                    registered = node in registered_execs
                    if not registered:
                        raise UnitEvidenceError(f"unregistered dynamic release implementation: {relative}")
        for dynamic in DYNAMIC_INPUTS.get(name, ()):
            if dynamic not in sources:
                match = next((key for key, value in modules.items()
                              if value.relative_to(root).as_posix() == dynamic), None)
                if match is None:
                    raise UnitEvidenceError(f"missing dynamic release implementation: {dynamic}")
                pending.append(match)
    decisions.update(file_loaded & decision_bodies)
    return sources, decisions


def _controller(root: Path) -> str:
    path = root / ".github/workflows/validate-and-promote.yml"
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise UnitEvidenceError("release controller is indirect")
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise UnitEvidenceError(f"release controller is unavailable: {path}") from error


def verify_helper_contract(
    root: Path, manifest: dict, *, development_root: Path | None = None,
    controllers: tuple[str, ...] = (),
) -> set[str]:
    """Require byte admission and positive decision coverage before privileges."""
    controls = (_controller(root), *controllers)
    if development_root is not None:
        controls += (_controller(development_root),)
    sources, decisions = helper_closure(root, controllers=controls)
    sources.update(BUILD_INPUTS)
    sources.update(BUILD_DECISIONS)
    decisions.update(BUILD_DECISIONS)
    unbound = sources - set(manifest["sources"])
    uncovered = decisions - set(manifest["coverage_targets"])
    if unbound or uncovered:
        raise UnitEvidenceError(
            "release helper qualification is incomplete; unbound="
            + ",".join(sorted(unbound)) + "; uncovered=" + ",".join(sorted(uncovered))
        )
    verify_sources(root.resolve(), {name: manifest["sources"][name] for name in sources})
    for copied, canonical in BUILD_ORIGINS.items():
        if (root / copied).read_bytes() != (root / canonical).read_bytes():
            raise UnitEvidenceError(f"executable build input differs from its qualified origin: {copied}")
    return decisions


def _decision_lines(path: Path) -> set[int]:
    tree = ast.parse(path.read_bytes())
    definitions = {
        node.lineno for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    return {
        statement.lineno for function in ast.walk(tree)
        if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
        for statement in ast.walk(function)
        if isinstance(statement, ast.stmt)
        and not isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and not (
            isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant)
            and isinstance(statement.value.value, str)
        )
        and statement.lineno not in definitions
    }


def verify_helper_coverage(
    root: Path, manifest: dict, coverage: dict, *, development_root: Path | None = None,
) -> None:
    """An import-only observation is not a test of a helper's decision rules."""
    decisions = verify_helper_contract(root, manifest, development_root=development_root)
    required = {
        f"{node}|call" for node in manifest["nodes"]
    }
    files = coverage["files"]
    missing = [
        name for name in sorted(decisions)
        if name not in files or not any(
            required.intersection(files[name]["contexts"].get(str(line), ()))
            for line in _decision_lines(root / name)
        )
    ]
    if missing:
        raise UnitEvidenceError("release helper has no positive call coverage: " + ",".join(missing))
