"""Retain the core's extension assets inside the caller's original launch scope."""

from __future__ import annotations

import hashlib
from importlib.resources import files
import json
import os
from pathlib import Path
import tempfile


def stage_extension(attempt_directory: Path) -> Path:
    source = files("agent_remote_driver.headed")
    assets = {
        "plugin.json": json.dumps({
            "name": "copilot-driver-headed", "version": "1.0.0",
            "description": "Launch-bound connection to the existing Copilot Driver Host",
        }, sort_keys=True).encode(),
        **{
            "extensions/copilot-driver-headed/" + name: source.joinpath(name).read_bytes()
            for name in ("extension.mjs", "acp-adapter.mjs", "host-client.mjs")
        },
    }
    digest = hashlib.sha256()
    for name, content in sorted(assets.items()):
        digest.update(name.encode() + b"\0" + content + b"\0")
    attempt_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = attempt_directory / ("headed-extension-" + digest.hexdigest()[:16])

    def verify() -> None:
        for name, content in assets.items():
            path = destination / name
            if path.is_symlink() or path.read_bytes() != content:
                raise ValueError("Retained headed extension assets changed")

    if destination.exists():
        verify()
        return destination
    with tempfile.TemporaryDirectory(prefix=".headed-stage-", dir=attempt_directory) as temporary:
        staged = Path(temporary) / "plugin"
        for name, content in assets.items():
            target = staged / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        try:
            os.replace(staged, destination)
        except OSError:
            if not destination.is_dir():
                raise
            verify()
    return destination
