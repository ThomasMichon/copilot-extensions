"""Explicit local replicas of an opted-in carrier; no provider discovery or API."""

from __future__ import annotations

import os
from pathlib import Path

from . import preferences as codec

MAX_ENTRIES = 128
DOCUMENT = "preferences.json"


def _validate(value) -> dict:
    codec._object(
        value, required=("schema", "version", "provider", "account", "directory", "participants"),
    )
    if (
        value["schema"] != "agent-machines.preferences.carrier"
        or type(value["version"]) is not int or value["version"] != 1
        or value["provider"] != "onedrive"
    ):
        raise codec.PreferenceError("unsupported-carrier")
    codec._identifier(value["account"])
    directory = value["directory"]
    if (
        not isinstance(directory, str) or not 1 <= len(directory) <= 4096
        or "\x00" in directory or not Path(directory).is_absolute()
        or ".." in Path(directory).parts
    ):
        raise codec.PreferenceError("invalid-carrier-directory")
    participants = value["participants"]
    if not isinstance(participants, list) or not 1 <= len(participants) <= codec.MAX_MACHINES:
        raise codec.PreferenceError("invalid-carrier-participants")
    seen = set()
    for participant in participants:
        identity = codec._identifier(participant).casefold()
        if identity in seen:
            raise codec.PreferenceError("duplicate-carrier-participant")
        seen.add(identity)
    return value


def _within(path: Path, directory: Path) -> bool:
    return path == directory or directory in path.parents


def select(state: Path, policy: dict, local_paths: list[Path]) -> dict:
    """Read only one configured replica and a bounded, nonrecursive name census."""
    from .preference_exchange import _plain_path

    config_path = _plain_path(state / "carrier.json")
    config = codec.read(config_path, validator=_validate)
    if not {policy["publisher"], policy["participant"]} <= set(config["participants"]):
        raise codec.PreferenceError("carrier-participant-not-permitted")
    directory = Path(os.path.abspath(config["directory"]))
    document = _plain_path(directory / DOCUMENT, missing_parents=True)
    if (
        _within(state, directory) or _within(directory, state)
        or any(_within(_plain_path(path, missing_parents=True), directory) for path in local_paths)
    ):
        raise codec.PreferenceError("carrier-local-state-overlap")
    names = []
    try:
        _plain_path(directory, directory=True, missing_parents=True)
    except codec.PreferenceError as error:
        if str(error) != "state-directory-unavailable":
            raise
        available = False
    else:
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if len(names) >= MAX_ENTRIES:
                        raise codec.PreferenceError("carrier-namespace-too-large")
                    names.append(entry.name)
        except FileNotFoundError:
            available = False
        else:
            available = True
    return {
        "config_path": config_path, "document": document,
        "inputs": {"config": config, "entries": sorted(names), "available": available},
        "available": available, "conflict": any(name != DOCUMENT for name in names),
    }


def exchange(operation: str, *, state: Path, source: Path | None = None, **arguments) -> dict:
    """Preview or confirm the existing exchange against the selected local replica."""
    from .preference_exchange import exchange as local_exchange

    if operation not in {"receive", "publish"}:
        raise codec.PreferenceError("unknown-operation")
    if operation == "publish" and source is None:
        raise codec.PreferenceError("input-required")
    return local_exchange(
        "accept" if operation == "receive" else "publish",
        state=state, source=source, carrier=True, **arguments,
    )
