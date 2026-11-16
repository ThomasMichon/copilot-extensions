"""Path-inspection failure remains unknown writer evidence."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import NoReturn

import pytest

from agent_bridge import session_lifecycle_cli as lifecycle
from agent_bridge import worktree_writer_observation as observation


def test_unreadable_slot_resolution_keeps_generation_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "worktrees"
    slot = root / "versions" / "1.0.0"
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.write_bytes(b"interpreter fixture")
    bootstrap = slot / ".runtime-bootstrap"
    bootstrap.mkdir()
    (bootstrap / "runtime-generation").write_text("a" * 32, encoding="ascii")
    assert observation._slot_generation(root, python) == "a" * 32

    original = Path.resolve

    def resolve(path: Path, strict: bool = False) -> Path:
        if path == python.parent:
            raise OSError("fixture path inspection refused")
        return original(path, strict=strict)

    def no_child(*_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError("observation launched a child")

    monkeypatch.setattr(Path, "resolve", resolve)
    monkeypatch.setattr(
        lifecycle, "_agent_worktrees_runtime_selection",
        lambda **_kwargs: (["bridge-owner"], root, python),
    )
    monkeypatch.setattr(lifecycle, "_agent_bridge_installation_context", lambda: None)
    monkeypatch.setattr(subprocess, "run", no_child)
    assert observation.observe() == {
        "installation_root": str(root),
        "runtime_generation": None,
    }
