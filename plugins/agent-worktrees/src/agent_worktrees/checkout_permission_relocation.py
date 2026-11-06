"""Narrow, retry-safe path rebinding through the existing permission-file owners."""

from __future__ import annotations

import json
from contextlib import ExitStack
from pathlib import Path

from . import permissions, tracking


def _under(value: str, root: str) -> bool:
    return Path(value).is_relative_to(Path(root))


def capture(source: str, destination: str) -> dict:
    captured = {}
    for kind, path in (("trust", permissions._config_path()), ("permissions", permissions._permissions_path())):
        if not path.exists():
            captured[kind] = None
            continue
        if path.is_symlink():
            raise ValueError("Copilot permission file is redirected")
        _, data = permissions._read_config_jsonc(path) if kind == "trust" else (
            "", json.loads(path.read_text(encoding="utf-8")),
        )
        entries = data.get("trustedFolders", []) if kind == "trust" else data.get("locations", {})
        if isinstance(entries, (dict, list)) and any(
            isinstance(value, str) and (
                Path(value) == Path(source) and value != source
                or Path(value) == Path(destination) and value != destination
            )
            for value in entries
        ):
            raise ValueError("permission aliases changed during relocation")
        if not isinstance(entries, list if kind == "trust" else dict):
            raise ValueError("unsupported Copilot permission schema")
        for value in entries:
            if not isinstance(value, str):
                raise ValueError("unsupported Copilot permission path")
            if Path(value) == Path(source) and value != source:
                raise ValueError("relocation v1 refuses non-exact source permission aliases")
            if _under(value, destination) or _under(value, source) and Path(value) != Path(source):
                raise ValueError("relocation v1 refuses existing destination or nested permission entries")
        value = source in entries if kind == "trust" else entries.get(source)
        if kind == "permissions" and source in entries and not isinstance(value, dict):
            raise ValueError("unsupported source permission entry")
        captured[kind] = value
    return captured


def apply(source: str, destination: str, captured: dict) -> None:
    selected = (("trust", permissions._config_path()), ("permissions", permissions._permissions_path()))
    with ExitStack() as locks:
        for _, path in selected:
            if path.parent.exists():
                locks.enter_context(tracking._RecordLock(path, require_sidecar=True))
        for kind, path in selected:
            expected = captured[kind]
            if not path.exists():
                if expected is not None:
                    raise ValueError("Copilot permission file disappeared during relocation")
                continue
            header, data = permissions._read_config_jsonc(path) if kind == "trust" else (
                "", json.loads(path.read_text(encoding="utf-8")),
            )
            entries = data.get("trustedFolders", []) if kind == "trust" else data.get("locations", {})
            if kind == "trust":
                if not isinstance(entries, list):
                    raise ValueError("Copilot trust schema changed during relocation")
                if expected:
                    if source in entries:
                        data["trustedFolders"] = [destination if item == source else item for item in entries]
                    elif destination not in entries:
                        raise ValueError("source trust changed during relocation")
                elif source in entries or destination in entries:
                    raise ValueError("source trust changed during relocation")
            else:
                if not isinstance(entries, dict):
                    raise ValueError("Copilot permission schema changed during relocation")
                if expected is not None:
                    if entries.get(source) == expected and destination not in entries:
                        entries[destination] = entries.pop(source)
                    elif source in entries or entries.get(destination) != expected:
                        raise ValueError("source permissions changed during relocation")
                elif source in entries or destination in entries:
                    raise ValueError("source permissions changed during relocation")
            permissions._atomic_json_write(path, data, header=header)
