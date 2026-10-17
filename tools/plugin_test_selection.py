"""Validated, portable exact-node selections for the contained plugin runner."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


def validate_test_file(plugin_root: Path, value: str) -> Path:
    path = PurePosixPath(value)
    candidate = plugin_root / value
    if (
        not value or "\\" in value or "\x00" in value
        or path.is_absolute() or ".." in path.parts
        or path.as_posix() != value
        or not candidate.resolve().is_relative_to((plugin_root / "tests").resolve())
        or candidate.resolve() != candidate.absolute()
        or not candidate.is_file()
        or not candidate.name.startswith("test_") or candidate.suffix != ".py"
    ):
        raise ValueError(f"test file must be an existing, unlinked tests/test_*.py: {value}")
    return candidate


@dataclass(frozen=True)
class TestSelection:
    plugin: str
    nodes: tuple[str, ...]
    files: tuple[str, ...]
    include_guards: bool

    def as_dict(self) -> dict:
        return {
            "version": 1, "plugin": self.plugin, "nodes": list(self.nodes),
            "files": list(self.files), "include_guards": self.include_guards,
        }

    def collection_files(self, plugin_root: Path) -> tuple[Path, ...]:
        return tuple(sorted({
            validate_test_file(plugin_root, value)
            for value in (*self.files, *(node.split("::", 1)[0] for node in self.nodes))
        }))


def load_selection(path: Path, plugin: str, plugin_root: Path) -> TestSelection:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(payload, dict)
        or set(payload) != {"version", "plugin", "nodes", "files", "include_guards"}
        or type(payload["version"]) is not int or payload["version"] != 1
        or payload["plugin"] != plugin
        or type(payload["include_guards"]) is not bool
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
            raise ValueError(f"selection requires exact test node IDs, not a file: {node}")
    selection = TestSelection(
        plugin, tuple(payload["nodes"]), tuple(payload["files"]), payload["include_guards"],
    )
    if not selection.nodes and not selection.files:
        raise ValueError("exact test selection must contain evidence, not only guards")
    selection.collection_files(plugin_root)
    return selection
