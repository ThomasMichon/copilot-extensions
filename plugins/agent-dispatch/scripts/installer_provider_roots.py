"""Retain installation-owned provider roots across complete runtime reconstruction."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
import uuid

FILE = "runtime-provider-roots.json"
SCHEMA = "copilot-extensions.runtime-provider-roots"
_NAME = r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?"
_ROOT = re.compile(rf"({_NAME})(?:\[({_NAME}(?:,{_NAME})*)\])?")


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate runtime provider-root key")
        result[key] = value
    return result


def _name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def normalize(roots: object, *, core: str | None = None) -> list[str]:
    if not isinstance(roots, list) or len(roots) > 32:
        raise ValueError("Runtime provider roots require a bounded list")
    selected: dict[str, str] = {}
    for value in roots:
        if not isinstance(value, str) or len(value) > 1024:
            raise ValueError("Runtime provider root must be bounded text")
        if core is not None:
            value = re.sub(r"\s+", "", value)
        match = _ROOT.match(value) if core is not None else _ROOT.fullmatch(value)
        if not match or (
            core is not None and value[match.end():] and value[match.end()] not in "<>=!~(;@"
        ):
            raise ValueError("Runtime provider root must name a distribution and optional extras")
        name = _name(match[1])
        if core is not None and name == _name(core):
            continue
        if name in selected:
            raise ValueError("Runtime provider roots must name distinct distributions")
        extras = [_name(extra) for extra in (match[2] or "").split(",") if extra]
        if len(set(extras)) != len(extras):
            raise ValueError("Duplicate runtime provider extra")
        selected[name] = name + ("[" + ",".join(sorted(extras)) + "]" if extras else "")
    return sorted(selected.values())


def read(root: Path) -> list[str] | None:
    path = root / FILE
    if not os.path.lexists(path):
        return None
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 16_384
        or getattr(info, "st_file_attributes", 0) & 0x400
    ):
        raise ValueError("Runtime provider roots must be a bounded ordinary file")
    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique)
    if (
        not isinstance(value, dict) or set(value) != {"schema", "version", "roots"}
        or value["schema"] != SCHEMA or type(value["version"]) is not int
        or value["version"] != 1
    ):
        raise ValueError("Unsupported runtime provider-root selection")
    return normalize(value["roots"])


def persist(root: Path, roots: object) -> list[str]:
    selected = normalize(roots)
    read(root)
    body = json.dumps({"schema": SCHEMA, "version": 1, "roots": selected}) + "\n"
    path = root / FILE
    if path.exists() and path.read_text(encoding="utf-8") == body:
        return selected
    root.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + FILE + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            os.chmod(temporary, 0o600)
            stream.write(body)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return selected


def require_inputs(root: Path, *, closed_wheels: bool) -> None:
    if read(root) and not closed_wheels:
        raise ValueError(
            "Selected runtime providers require a complete native wheel profile; "
            "supply the current core and retained provider roots together"
        )


def select(
    root: Path, profile_roots: object, *, core: str, write: bool = False,
) -> list[str]:
    incoming = normalize(profile_roots, core=core)
    desired = read(root)
    if desired is not None and incoming != desired:
        raise ValueError(
            "Native wheel profile changes selected runtime providers; "
            "declare the intended provider roots before reconstruction"
        )
    if write and (incoming or desired is not None):
        persist(root, incoming)
    return incoming


def check_construction(root: Path, receipt: dict[str, object] | None, *, core: str) -> None:
    """Apply explicit user selection before any candidate or current-state mutation."""
    desired = read(root)
    if desired is None:
        return
    require_inputs(root, closed_wheels=receipt is not None)
    if receipt is not None:
        binding = receipt.get("binding")
        if not isinstance(binding, dict):
            raise ValueError("Selected providers require an inspected complete wheel profile")
        profile = binding.get("profile", binding)
        if not isinstance(profile, dict):
            raise ValueError("Selected providers require an inspected complete wheel profile")
        select(root, profile.get("roots"), core=core)
