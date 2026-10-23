"""Fail-closed generated consumer inventory against validated development suites."""

from __future__ import annotations

import ast
import math
import re
from pathlib import Path

PLUGIN = re.compile(r"[a-z][a-z0-9-]{0,63}")
STANDALONE = {
    "dispatch": Path("services") / "dispatch", "index": Path("services") / "index",
    "chat": Path("services") / "chat",
    "pr": Path("services") / "pr",
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


def plugin_minutes(root: Path, name: str) -> int:
    try:
        module = ast.parse(regular(root, Path("tools/run-plugin-tests.py")).read_text("utf-8"))
        declarations = [
            node.value for node in module.body
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
            and node.target.id == "_PLUGIN_TIMEOUT_OVERRIDES"
        ]
        defaults = [
            node.args[1] for node in ast.walk(module)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "_PLUGIN_TIMEOUT_OVERRIDES" and node.func.attr == "get"
            and len(node.args) == 2
        ]
        overrides = ast.literal_eval(declarations[0]) if len(declarations) == 1 else None
        default = ast.literal_eval(defaults[0]) if len(defaults) == 1 else None
        if not isinstance(overrides, dict) or any(
            not isinstance(key, str) or not PLUGIN.fullmatch(key) for key in overrides
        ):
            raise ValueError("expected literal plugin timeout policy")
        if any(type(value) not in (int, float) or not math.isfinite(value) or value <= 0
               for value in [default, *overrides.values()]):
            raise ValueError("expected finite positive recipe budgets")
    except (OSError, UnicodeError, SyntaxError, ValueError, TypeError, OverflowError) as error:
        raise CandidateValidationError("candidate plugin recipe budgets are unreadable") from error
    return math.ceil(overrides.get(name, default) / 60) + 3


def fleet_partitions(root: Path) -> dict[str, str]:
    try:
        text = regular(root, Path(".github/workflows/ci.yml")).read_text("utf-8")
    except (OSError, UnicodeError) as error:
        raise CandidateValidationError("candidate Fleet partition inventory is unreadable") from error
    sections = text.split("        partition: &fleet-native-partitions\n")
    if len(sections) != 2:
        raise CandidateValidationError("candidate Fleet partition declaration is ambiguous")
    body = sections[1].split("    steps: &fleet-native-steps\n", 1)
    windows = text.split("\n  fleet-native-windows:\n")
    windows_body = re.split(r"\n  [a-z][a-z0-9_-]*:\n", windows[1], maxsplit=1)[0] if len(windows) == 2 else ""
    if len(body) != 2 or "        partition: *fleet-native-partitions\n" not in windows_body:
        raise CandidateValidationError("candidate Fleet platforms do not share canonical partitions")
    entries = list(re.finditer(
        r"          - name: ([a-z][a-z0-9-]{0,63})\n"
        r"            selector: '([A-Za-z0-9_ ()]{1,512})'\n", body[0],
    ))
    partitions = {entry[1]: entry[2] for entry in entries}
    if (
        not 1 <= len(entries) <= 32 or len(partitions) != len(entries)
        or "".join(entry[0] for entry in entries) != body[0]
    ):
        raise CandidateValidationError("candidate Fleet partitions must be unique literal selectors")
    return partitions


def matrix(
    root: Path, expected: set[str], standalone: set[str],
) -> dict[str, list[dict[str, str | int]]]:
    supported = {"worktree-manager", *(path.as_posix() for path in STANDALONE.values())}
    optional = {"services/chat", "services/pr"}
    if not supported - optional <= standalone <= supported:
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
        "minutes": plugin_minutes(root, name),
    } for name in sorted(actual)]
    for name in ("seasoned_uv.py", "artifact_validation_requirements.txt"):
        regular(root, Path("tools") / name)
    rows.extend({
        "os": platform, "component": "validator-bootstrap", "minutes": 5,
    } for platform in ("ubuntu-latest", "windows-latest"))
    for component, platforms in (
        ("manager", ("ubuntu-latest",)),
        ("dispatch", ("ubuntu-latest",)),
        ("index", ("ubuntu-latest", "windows-latest")),
        ("chat", ("ubuntu-latest", "windows-latest")),
        ("pr", ("ubuntu-latest", "windows-latest")),
        ("fleet-manager", ("ubuntu-latest", "windows-latest")),
    ):
        if component in {"chat", "pr"} and f"services/{component}" not in standalone:
            continue
        relative = Path("worktree-manager") if component == "manager" else STANDALONE[component]
        regular(root, relative / "pyproject.toml")
        if component == "fleet-manager":
            for name in ("setup.py", "README.md"):
                regular(root, relative / name)
            for name in ("seasoned_uv.py", "test_fleet_ci_scope.py"):
                regular(root, Path("tools") / name)
        consumers = (
            [f"fleet-manager:{name}" for name in fleet_partitions(root)]
            if component == "fleet-manager" else [component]
        )
        rows.extend({
            "os": platform, "component": consumer, "minutes": 5 if component == "index" else 6,
        } for platform in platforms for consumer in consumers)
    for row in rows:
        component = row["component"]
        if component == "plugin:agent-worktrees":
            count = 2
        elif component == "validator-bootstrap" or (
            isinstance(component, str) and component.startswith("fleet-manager:")
        ):
            count = 3
        else:
            count = 1
        row["job_minutes"] = int(row["minutes"]) * count + 5
        if row["job_minutes"] > 360:
            raise CandidateValidationError("consumer recipe budget exceeds the hosted job limit")
    return {"include": rows}
