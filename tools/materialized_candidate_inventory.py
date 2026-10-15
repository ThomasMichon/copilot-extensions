"""Fail-closed generated consumer inventory against validated development suites."""

from __future__ import annotations

import ast
import re
from pathlib import Path

PLUGIN = re.compile(r"[a-z][a-z0-9-]{0,63}")
STANDALONE = {
    "dispatch": Path("services") / "dispatch", "index": Path("services") / "index",
    "fleet-manager": Path("fleet-manager"),
}


class CandidateValidationError(RuntimeError):
    pass


def regular(root: Path, relative: Path) -> Path:
    target = root / relative
    cursor = root
    for part in relative.parts:
        cursor /= part
        if cursor.is_symlink():
            raise CandidateValidationError("candidate consumer path must not be a symlink")
    if not target.is_file():
        raise CandidateValidationError(f"required candidate consumer file is absent: {relative}")
    return target


def python_suites(root: Path) -> set[str]:
    suites: set[str] = set()
    plugins = root / "plugins"
    if plugins.is_symlink() or not plugins.is_dir():
        raise CandidateValidationError("candidate plugin directory is unavailable")
    for plugin in sorted(plugins.iterdir()):
        if plugin.is_symlink() or (plugin / "tests").is_symlink():
            raise CandidateValidationError("candidate plugin test root must not be a symlink")
        if not (plugin / "tests").exists():
            continue
        if not PLUGIN.fullmatch(plugin.name) or not (plugin / "tests").is_dir():
            raise CandidateValidationError("invalid candidate plugin test root")
        if any((plugin / "tests").glob("test_*.py")):
            regular(root, plugin.relative_to(root) / "plugin.json")
            suites.add(plugin.name)
    return suites


def standalone_roster(root: Path) -> set[str]:
    try:
        module = ast.parse(regular(root, Path("tools/standalone_consumers.py")).read_text("utf-8"))
        declarations = [
            node.value for node in module.body if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "STANDALONE_CONSUMERS"
                    for target in node.targets)
        ]
        value = ast.literal_eval(declarations[0]) if len(declarations) == 1 else None
    except (OSError, UnicodeError, SyntaxError, ValueError, TypeError) as error:
        raise CandidateValidationError("validated standalone roster is unreadable") from error
    if not isinstance(value, tuple) or not value or any(not isinstance(item, str) for item in value):
        raise CandidateValidationError("validated standalone roster must contain literal component IDs")
    if len(set(value)) != len(value):
        raise CandidateValidationError("validated standalone roster contains duplicate consumers")
    return set(value)


def matrix(
    root: Path, expected: set[str], standalone: set[str],
) -> dict[str, list[dict[str, str | int]]]:
    supported = {"worktree-manager", *(path.as_posix() for path in STANDALONE.values())}
    if standalone != supported:
        raise CandidateValidationError("standalone recipes do not cover the validated canonical roster")
    actual = python_suites(root)
    if not expected or len(expected) > 64 or "agent-worktrees" not in expected:
        raise CandidateValidationError("validated development consumer inventory is incomplete")
    if actual != expected:
        missing, extra = sorted(expected - actual), sorted(actual - expected)
        raise CandidateValidationError(
            f"candidate consumer inventory differs from validated development: "
            f"missing={missing}, unexpected={extra}"
        )
    rows: list[dict[str, str | int]] = [{
        "os": "ubuntu-latest", "component": f"plugin:{name}",
        "minutes": 12 if name in ("agent-dispatch", "agent-index", "agent-worktrees") else 8,
    } for name in sorted(actual)]
    for component, platforms in (
        ("manager", ("ubuntu-latest",)),
        ("dispatch", ("ubuntu-latest",)),
        ("index", ("ubuntu-latest", "windows-latest")),
        ("fleet-manager", ("ubuntu-latest", "windows-latest")),
    ):
        relative = Path("worktree-manager") if component == "manager" else STANDALONE[component]
        regular(root, relative / "pyproject.toml")
        if component == "fleet-manager":
            for name in ("setup.py", "README.md"):
                regular(root, relative / name)
            for name in ("seasoned_uv.py", "test_fleet_ci_scope.py"):
                regular(root, Path("tools") / name)
        rows.extend({
            "os": platform, "component": component, "minutes": 5 if component == "index" else 6,
        } for platform in platforms)
    return {"include": rows}
