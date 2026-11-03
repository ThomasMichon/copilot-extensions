"""Validated, portable exact-node selections for the contained plugin runner."""
from __future__ import annotations

import json
from collections.abc import Iterable
import fnmatch
import shlex
import tomllib
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
}
SOURCE_ONLY_TEST_TARGETS = frozenset({"installation-context"})


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
