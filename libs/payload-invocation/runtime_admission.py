"""Package the canonical admission toolkit, not a second installer or resolver."""
from __future__ import annotations

import ast
import json
from pathlib import Path
import stat
import sys

ENTRY_MODULES = (
    "runtime_profile_policy", "runtime_source_build", "runtime_source_reuse",
    "runtime_marketplace_source",
)
REPOSITORY_ADAPTERS = {
    "runtime_source_manager", "worktree_manager.self_install",
    "worktree_manager.__main__", "promotion_python_artifacts",
    "publish_python_artifacts", "promote_release",
}
_IGNORED = {"tests", "__pycache__", ".venv", ".test-runs", "build", "dist"}
_RESOURCE_SUFFIXES = {".py", ".sh", ".ps1", ".c", ".h", ".json", ".typed"}
_RESOURCES = (
    "libs/agent-procutil/setup.py",
    "libs/versioned-runtime/standalone_build.py",
    "plugins/agent-containers/setup.py",
)


def _ordinary(path: Path, root: Path) -> None:
    if not path.is_relative_to(root):
        raise ValueError(f"toolkit source escapes its canonical root: {path}")
    for part in (path, *path.parents):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError(f"toolkit source is redirected: {part}")
        if part == root:
            break
    if not path.is_file():
        raise ValueError(f"toolkit source is not a file: {path}")


def _sources(directory: Path):
    for path in sorted(directory.rglob("*")):
        if (
            any(part in _IGNORED for part in path.relative_to(directory).parts)
            or path.name.startswith("test_")
        ):
            continue
        if path.suffix in _RESOURCE_SUFFIXES:
            yield path


def _imports(name: str, path: Path, content: str):
    for node in ast.walk(ast.parse(content, filename=str(path))):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            package = name.split(".") if path.name == "__init__.py" else name.split(".")[:-1]
            if node.level:
                package = package[:len(package) - node.level + 1]
            else:
                package = []
            module = ".".join([*package, *([node.module] if node.module else [])])
            yield module
            yield from (f"{module}.{alias.name}" for alias in node.names)


def expected_files(repo: Path) -> dict[Path, str]:
    """Return first-party bytes with their own complete internal resource layout.

    Static imports close ordinary module edges. Whole selected libraries also
    retain dynamic sibling modules and resources (notably the constructor and
    process owner). Explicit trusted build resources are data for existing
    owners; fetched source is never executed to discover this closure.
    """
    repo = repo.absolute()
    roots = [repo / "tools", repo / "worktree-manager" / "src"]
    roots.extend(
        library / "src" if (library / "src").is_dir() else library
        for library in sorted((repo / "libs").iterdir()) if library.is_dir()
    )
    modules, contents = {}, {}
    for root in roots:
        for path in _sources(root):
            if path.suffix != ".py":
                continue
            name = ".".join(path.relative_to(root).with_suffix("").parts)
            if name.endswith(".__init__"):
                name = name.removesuffix(".__init__")
            modules.setdefault(name, (path, root))
    selected, selected_roots = {}, set()
    pending = list(ENTRY_MODULES)
    expanded_libraries = set()
    known_names = {name.split(".")[0] for name in modules}
    while pending:
        name = pending.pop()
        if name in REPOSITORY_ADAPTERS:
            raise ValueError(f"admission toolkit imports repository adapter: {name}")
        if name in selected or name not in modules:
            continue
        path, root = modules[name]
        _ordinary(path, repo)
        content = path.read_text(encoding="utf-8")
        selected[name] = path
        selected_roots.add(root)
        contents[path.relative_to(repo)] = content
        for imported in _imports(name, path, content):
            parts = imported.split(".")
            if imported in REPOSITORY_ADAPTERS:
                raise ValueError(f"admission toolkit imports repository adapter: {imported}")
            if parts[0] not in known_names | sys.stdlib_module_names | {
                "packaging", "tomli", "setuptools",
            }:
                raise ValueError(f"undeclared toolkit dependency: {imported} in {path}")
            pending.extend(".".join(parts[:end]) for end in range(1, len(parts) + 1))
        if root.is_relative_to(repo / "libs") and root not in expanded_libraries:
            expanded_libraries.add(root)
            # Dynamic components execute with their original sibling paths.
            for resource in _sources(root):
                _ordinary(resource, repo)
                contents[resource.relative_to(repo)] = resource.read_text(encoding="utf-8")
                if resource.suffix == ".py":
                    sibling = ".".join(resource.relative_to(root).with_suffix("").parts)
                    pending.append(sibling.removesuffix(".__init__"))
    for name in ENTRY_MODULES:
        if name not in selected:
            raise ValueError(f"missing admission toolkit entry module: {name}")
    for relative in _RESOURCES:
        resource = repo / relative
        _ordinary(resource, repo)
        contents[resource.relative_to(repo)] = resource.read_text(encoding="utf-8")
    import_roots = [
        root.relative_to(repo).as_posix()
        for root in roots if root in selected_roots
    ]
    inventory = {
        "schema": "copilot-extensions.runtime-admission-toolkit", "version": 1,
        "python": ">=3.11", "requirements": ["packaging>=23"],
        "entryModules": list(ENTRY_MODULES), "importRoots": import_roots,
        "modules": sorted(selected), "files": sorted(path.as_posix() for path in contents),
    }
    contents[Path("toolkit.json")] = json.dumps(inventory, indent=2, sort_keys=True) + "\n"
    contents[Path("__init__.py")] = '''"""Canonical admission tooling; activation is not source or feed approval."""
from pathlib import Path
import json
import sys


def activate():
    """Make only this self-contained toolkit's first-party owners importable.

    Call in the installation authority's admitted tooling interpreter, never in
    the runtime under construction. Packaging is supplied by that locked
    tooling environment; this payload contains no third-party dependencies.
    """
    root = Path(__file__).resolve().parent
    record = json.loads((root / "toolkit.json").read_text(encoding="utf-8"))
    paths = [str(root / relative) for relative in record["importRoots"]]
    sys.path[:0] = [path for path in paths if path not in sys.path]
'''
    return contents
