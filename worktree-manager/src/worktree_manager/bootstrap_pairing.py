"""Select knowledge for onboarding; portable choices never replace local bindings."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Literal

from . import bootstrap_harness, harness_state, runtime_source_repository
from .config_write_lock import config_write_lock


def _portable_file(onedrive: Path, harness: str) -> Path:
    if not onedrive.is_absolute() or not onedrive.is_dir():
        raise ValueError("Portable pairing directory must be an existing absolute directory")
    key = hashlib.sha256(harness.encode("utf-8")).hexdigest()
    path = onedrive / "Apps" / ".copilot-extensions" / "pairings" / (key + ".json")
    runtime_source_repository._ordinary(path)
    return path


def remember_bound(local_config: Path, harness_remote: str, knowledge_remote: str, onedrive: Path,
                   *, registered_knowledge: Path) -> None:
    """Persist only repo identities; never replace a different saved preference implicitly."""
    harness = bootstrap_harness.repository_remote(harness_remote)[1]
    name, knowledge = bootstrap_harness.repository_remote(knowledge_remote)
    if select(local_config, harness, None, None) != ("local", name):
        raise ValueError("Cannot remember a pairing without a matching local binding")
    checkout = bootstrap_harness._anchor(registered_knowledge)
    origin = bootstrap_harness._git(checkout, "remote", "get-url", "origin")
    if bootstrap_harness.repository_remote(origin)[1] != knowledge:
        raise ValueError("Registered knowledge checkout has a different origin")
    path = _portable_file(onedrive, harness)
    with config_write_lock(path):
        if path.exists():
            saved = _read_portable(path, harness)
            if saved != knowledge:
                raise ValueError("Portable knowledge preference differs; explicit replacement required")
            return
        document = {"version": 1, "harness": harness, "knowledge": knowledge}
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as out:
            temp = Path(out.name)
            json.dump(document, out, sort_keys=True)
            out.write("\n")
        try:
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)


def select(
    local_config: Path, harness_remote: str, explicit: str | None, onedrive: Path | None,
) -> tuple[Literal["local", "explicit", "portable", "absent"], str | None]:
    runtime_source_repository._ordinary(local_config)
    config = harness_state._read_yaml(local_config, strict=True)
    if "knowledge_repo" in config:
        value = config["knowledge_repo"]
        if not isinstance(value, str) or not bootstrap_harness._NAME.fullmatch(value):
            raise ValueError("Invalid local knowledge binding")
        return "local", value
    if explicit is not None:
        return "explicit", bootstrap_harness.repository_remote(explicit)[1]
    if onedrive is None or not harness_remote:
        return "absent", None
    normalized = bootstrap_harness.repository_remote(harness_remote)[1]
    path = _portable_file(onedrive, normalized)
    saved = _read_portable(path, normalized)
    return ("portable", saved) if saved else ("absent", None)


def _read_portable(path: Path, normalized: str) -> str | None:
    try:
        with path.open("rb") as stream:
            content = stream.read(8193)
    except FileNotFoundError:
        return None
    if len(content) > 8192:
        raise ValueError("Portable pairing exceeds size limit")
    data = json.loads(content)
    if (not isinstance(data, dict) or set(data) != {"version", "harness", "knowledge"}
            or type(data["version"]) is not int or data["version"] != 1
            or data["harness"] != normalized or not isinstance(data["knowledge"], str)):
        raise ValueError("Invalid portable pairing for this harness")
    return bootstrap_harness.repository_remote(data["knowledge"])[1]
