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
    if not isinstance(roots, list) or len(roots) > (256 if core is not None else 32):
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


def from_profile(profile: dict[str, object], *, core: str) -> list[str]:
    """Derive unconditional provider intent from inspected distribution roots."""
    declared = profile.get("roots")
    if not isinstance(declared, list) or len(declared) > 256:
        raise ValueError("Runtime provider roots require a bounded list")
    roots: list[str] = []
    for value in declared:
        parsed = normalize([value], core=core)
        if isinstance(value, str) and ";" not in value:
            roots.extend(parsed)
    wheels = profile.get("wheels")
    if not isinstance(wheels, list):
        raise ValueError("Provider selection requires the inspected wheel closure")
    installed = {}
    for row in wheels:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str):
            raise ValueError("Provider selection requires inspected distribution names")
        installed[_name(row["name"])] = row.get("selected_extras", [])
    selected: dict[str, set[str]] = {}
    for root in roots:
        match = _ROOT.fullmatch(root)
        assert match is not None
        if match[1] not in installed:
            continue
        extras = installed[match[1]]
        if not isinstance(extras, list) or not all(isinstance(extra, str) for extra in extras):
            raise ValueError("Provider selection requires inspected extras")
        requested = set((match[2] or "").split(",")) - {""}
        if not requested <= {_name(extra) for extra in extras}:
            continue
        selected.setdefault(match[1], set()).update(requested)
    return [
        name + ("[" + ",".join(sorted(extras)) + "]" if extras else "")
        for name, extras in sorted(selected.items())
    ]


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
    if len(body.encode("utf-8")) > 16_384:
        raise ValueError("Runtime provider roots exceed the persisted byte bound")
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


def select(root: Path, provider_roots: object, *, write: bool = False) -> list[str]:
    incoming = normalize(provider_roots)
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
        select(root, from_profile(profile, core=core))
