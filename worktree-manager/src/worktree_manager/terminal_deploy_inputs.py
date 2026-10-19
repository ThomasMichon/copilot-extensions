"""Validated Terminal documents retained through local publication."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .harness_state import _has_dangling_link


def _fragment_inventory(directory: Path) -> frozenset[Path]:
    try:
        directory.stat()
    except FileNotFoundError:
        if _has_dangling_link(directory):
            raise ValueError(f"Dangling Terminal fragment directory: {directory}")
        return frozenset()

    def fail(error: OSError) -> None:
        raise error

    return frozenset(
        Path(parent) / name
        for parent, _directories, files in os.walk(directory, onerror=fail)
        for name in files if name.lower().endswith(".json")
    )


def _jsonc(text: str) -> str:
    strings = r'"(?:\\.|[^"\\])*"'
    text = re.sub(
        strings + r"|//[^\r\n]*|/\*[\s\S]*?\*/",
        lambda match: match[0] if match[0].startswith('"') else " ",
        text,
    )
    return re.sub(
        strings + r"|,(?=\s*[}\]])",
        lambda match: "" if match[0] == "," else match[0],
        text,
    )


def _object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("Duplicate Terminal JSON key")
        result[name] = value
    return result


def _read_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        if _has_dangling_link(path):
            raise ValueError(f"Dangling Terminal input link: {path}")
        return None


def _parse(path: Path, raw: bytes | None, kind: str | None) -> dict | None:
    if raw is None:
        return None
    text = raw.decode("utf-8-sig")
    data = json.loads(_jsonc(text) if kind == "settings" else text,
                      object_pairs_hook=_object)
    if not isinstance(data, dict):
        raise ValueError(f"Terminal input must be a JSON object: {path}")
    if kind == "state":
        values = data.get("generatedProfiles", [])
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            raise ValueError(f"Terminal generatedProfiles must be a string list: {path}")
    elif kind in {"settings", "fragment"}:
        profiles = data.get("profiles", {} if kind == "settings" else [])
        if kind == "settings":
            if not isinstance(profiles, dict):
                raise ValueError(f"Terminal profiles must be a mapping: {path}")
            profiles = profiles.get("list", [])
        if not isinstance(profiles, list) or any(
            not isinstance(profile, dict)
            or ("guid" in profile and not isinstance(profile["guid"], str))
            for profile in profiles
        ):
            raise ValueError(f"Terminal profile list is invalid: {path}")
    return data


def read_optional_json(path: Path, *, kind: str | None = None) -> dict | None:
    """Tolerant read-only views never authorize publication."""
    try:
        return _parse(path, _read_bytes(path), kind)
    except (OSError, ValueError):
        return None


class TerminalInputs:
    def __init__(self) -> None:
        self.raw: dict[Path, bytes | None] = {}
        self.documents: dict[Path, dict | None] = {}
        self.fragment_sets: dict[Path, frozenset[Path]] = {}

    def read(self, path: Path, *, kind: str = "fragment") -> dict | None:
        if path not in self.raw:
            try:
                raw = _read_bytes(path)
                data = _parse(path, raw, kind)
            except (OSError, ValueError) as error:
                raise ValueError(f"Cannot read valid Terminal input: {path}") from error
            self.raw[path], self.documents[path] = raw, data
        return self.documents[path]

    def verify(self) -> None:
        for directory, paths in self.fragment_sets.items():
            if _fragment_inventory(directory) != paths:
                raise ValueError(f"Terminal fragment inventory changed before publication: {directory}")
        for path, raw in self.raw.items():
            if _read_bytes(path) != raw:
                raise ValueError(f"Terminal input changed before publication: {path}")

    def write(self, path: Path, text: str) -> None:
        self.verify()
        path.write_text(text, encoding="utf-8", newline="")
        self.raw[path] = text.encode("utf-8")
        for directory, paths in self.fragment_sets.items():
            if path.is_relative_to(directory):
                self.fragment_sets[directory] = paths | {path}

    def fragments(self, directory: Path) -> frozenset[Path]:
        paths = _fragment_inventory(directory)
        self.fragment_sets[directory] = paths
        return paths

    def backup(self, path: Path, destination: Path) -> None:
        self.verify()
        raw = self.raw[path]
        if raw is None:
            raise ValueError(f"Cannot back up an absent Terminal input: {path}")
        destination.write_bytes(raw)
