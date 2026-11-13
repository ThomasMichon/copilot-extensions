"""Portable exact-node admission and sequential cohorts for the contained runner."""
from __future__ import annotations

import json
from collections.abc import Iterable
import fnmatch
import shlex
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

EXTERNAL_TEST_ROOTS = {
    "worktree-manager": Path("worktree-manager"),
    "payload-invocation": Path("libs/payload-invocation"),
    "installation-context": Path("libs/installation-context"),
    "runtime-source": Path("libs/versioned-runtime"),
    "worktree-identity": Path("libs/worktree-identity"),
    "repository-policy": Path("libs/repository-policy"),
    "update-driver": Path("libs/update-driver"),
    "agent-procutil": Path("libs/agent-procutil"),
    "plugin-activation": Path("libs/plugin-activation"),
    "ssh-manager": Path("libs/ssh-manager"),
    "venue-copilot": Path("libs/venue-copilot"),
    "pr-filters": Path("libs/pr-filters"),
    "zdd": Path("libs/zdd"),
}
SOURCE_ONLY_TEST_TARGETS = frozenset({"installation-context"})
_CODESPACES_NATIVE_FILE = Path("tests") / "test_installer_engine_adoption.py"
_CODESPACES_ADMISSION = (
    "codespaces_library_install_order_retry_and_errors or "
    "codespaces_real_admission_replays_captured_source"
)
_CODESPACES_FALSE = f"({_CODESPACES_ADMISSION}) and False"
_CODESPACES_TRUE = f"({_CODESPACES_ADMISSION}) and True"
_CODESPACES_NEGATIVE = "codespaces_admission_spy_refuses_incorrect_or_bypassed_calls"
_CODESPACES_LIFECYCLE = "stamp_snapshot or native_dev_generation_dependency_failure"
_CODESPACES_WINDOWS_PARTITIONS = (
    f"({_CODESPACES_FALSE}) and healthy",
    f"({_CODESPACES_FALSE}) and not healthy",
    f"not ({_CODESPACES_FALSE}) and ({_CODESPACES_TRUE}) and healthy",
    f"not ({_CODESPACES_FALSE}) and "
    f"((({_CODESPACES_TRUE}) and not healthy) or {_CODESPACES_NEGATIVE})",
    f"not (({_CODESPACES_FALSE}) or ({_CODESPACES_TRUE}) or {_CODESPACES_NEGATIVE}) "
    f"and ({_CODESPACES_LIFECYCLE})",
    f"not (({_CODESPACES_FALSE}) or ({_CODESPACES_TRUE}) or {_CODESPACES_NEGATIVE} "
    f"or {_CODESPACES_LIFECYCLE})",
)
_BRIDGE_HISTORICAL_FILE = Path("tests") / "test_http48_capture_guards.py"
_BRIDGE_HISTORICAL_PARTITIONS = (
    "not (http49 or http50 or http51)",
    "http49",
    "not http49 and http50",
    "not (http49 or http50) and http51",
)


def plan_test_groups(
    name: str, plugin_root: Path, groups: list[list[Path]], *, windows: bool,
) -> list[tuple[list[Path], str | None]]:
    """Partition expensive owned cohorts without widening their shared deadline."""
    target = plugin_root / _CODESPACES_NATIVE_FILE
    historical = plugin_root / _BRIDGE_HISTORICAL_FILE
    plan = []
    for group in groups:
        if name == "agent-bridge" and historical in group:
            other_files = [path for path in group if path != historical]
            if other_files:
                plan.append((other_files, None))
            plan.extend(([historical], selector) for selector in _BRIDGE_HISTORICAL_PARTITIONS)
            continue
        if name != "agent-codespaces" or not windows or target not in group:
            plan.append((group, None))
            continue
        other_files = [path for path in group if path != target]
        if other_files:
            plan.append((other_files, None))
        plan.extend(([target], selector) for selector in _CODESPACES_WINDOWS_PARTITIONS)
    return plan


def resolve_plugin_root(repo: Path, name: str, *, plugins_root: Path | None = None) -> Path:
    if name in EXTERNAL_TEST_ROOTS:
        return repo / EXTERNAL_TEST_ROOTS[name]
    return (repo / "plugins" if plugins_root is None else plugins_root) / name


def selection_diagnostic(label: str, values: Iterable[str]) -> str:
    ordered = sorted(values)
    sample = []
    for value in ordered[:10]:
        text = value.replace("\r", "\\r").replace("\n", "\\n")
        sample.append(text if len(text) <= 200 else text[:200] + "...")
    if len(ordered) > len(sample):
        sample.append(f"... {len(ordered) - len(sample)} more")
    return (
        f"{label} ({len(ordered)}):\n" + "\n".join(sample)
        + "\nSee the original arguments or selection manifest / decision artifact for the complete requested selection."
    )


def validate_test_file(plugin_root: Path, value: str) -> Path:
    path = PurePosixPath(value)
    candidate = plugin_root / value
    invalid = (
        not value or "\\" in value or "\x00" in value
        or path.is_absolute() or bool(PureWindowsPath(value).drive) or ".." in path.parts
        or path.as_posix() != value
    )
    if not invalid:
        resolved = candidate.resolve()
        tests_root = (plugin_root / "tests").resolve()
        invalid = (
            not resolved.is_relative_to(tests_root)
            or resolved != candidate.absolute() or not candidate.is_file()
            or not candidate.name.startswith("test_") or candidate.suffix != ".py"
        )
    if invalid:
        raise ValueError(selection_diagnostic(
            "test file must be an existing, unlinked tests/test_*.py", (value,),
        ))
    return candidate


@dataclass(frozen=True)
class TestSelection:
    plugin: str
    nodes: tuple[str, ...]
    files: tuple[str, ...]
    include_guards: bool
    coverage_profile: dict | None = None
    coverage_exclusions: dict | None = None

    def as_dict(self) -> dict:
        result = {
            "version": 1, "plugin": self.plugin, "nodes": list(self.nodes),
            "files": list(self.files), "include_guards": self.include_guards,
        }
        if self.coverage_profile is not None:
            result.update(
                version=2, coverage_profile=self.coverage_profile,
                coverage_exclusions=self.coverage_exclusions,
            )
        return result

    def collection_files(self, plugin_root: Path) -> tuple[Path, ...]:
        values = set(self.files) | {node.split("::", 1)[0] for node in self.nodes}
        return tuple(sorted(validate_test_file(plugin_root, value) for value in sorted(values)))


def load_selection(path: Path, plugin: str, plugin_root: Path) -> TestSelection:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return selection_from_payload(payload, plugin, plugin_root)


def selection_from_payload(payload: object, plugin: str, plugin_root: Path) -> TestSelection:
    fields = {"version", "plugin", "nodes", "files", "include_guards"}
    coverage_mode = isinstance(payload, dict) and payload.get("version") == 2
    if coverage_mode:
        fields |= {"coverage_profile", "coverage_exclusions"}
    if (
        not isinstance(payload, dict)
        or set(payload) != fields
        or type(payload["version"]) is not int or payload["version"] not in (1, 2)
        or payload["plugin"] != plugin
        or type(payload["include_guards"]) is not bool
        or (coverage_mode and (
            not isinstance(payload["coverage_profile"], dict)
            or not isinstance(payload["coverage_exclusions"], dict)
        ))
    ):
        raise ValueError("invalid exact test selection schema, version or plugin")
    for key in ("nodes", "files"):
        values = payload[key]
        if not isinstance(values, list) or any(
            not isinstance(value, str) or not value or any(c in value for c in "\x00\r\n")
            for value in values
        ) or len(values) != len(set(values)):
            raise ValueError(f"selection {key} must be a unique list of nonempty strings")
    for node in payload["nodes"]:
        file, separator, test = node.partition("::")
        if not separator or not test:
            raise ValueError(selection_diagnostic(
                "selection requires exact test node IDs, not a file", (node,),
            ))
    selection = TestSelection(
        plugin, tuple(payload["nodes"]), tuple(payload["files"]), payload["include_guards"],
        payload.get("coverage_profile"), payload.get("coverage_exclusions"),
    )
    if not selection.nodes and not selection.files:
        raise ValueError("exact test selection must contain evidence, not only guards")
    selection.collection_files(plugin_root)
    return selection
def default_collection_files(root: Path) -> tuple[Path, ...]:
    import tomllib

    project = root / "pyproject.toml"
    config = tomllib.loads(project.read_text(encoding="utf-8")) if project.is_file() else {}
    ignored = config.get("tool", {}).get("pytest", {}).get("ini_options", {}).get("norecursedirs", [])
    if isinstance(ignored, str):
        ignored = shlex.split(ignored)
    if not isinstance(ignored, list) or any(not isinstance(pattern, str) for pattern in ignored):
        raise ValueError("pytest norecursedirs must be directory patterns")
    return tuple(sorted(
        path for path in (root / "tests").rglob("test_*.py")
        if not any(
            fnmatch.fnmatch(parent.name, pattern) or parent.match(pattern)
            for parent in path.relative_to(root).parents if parent != Path(".")
            for pattern in ignored
        )
    ))
