"""Installed worker modules may be uv-linked without relaxing wheel custody."""
from __future__ import annotations

import os
from pathlib import Path
import sys
from typing import BinaryIO

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libs" / "installer-engine"))
import installer_wheel_acquisition as acquisition
import installer_feed_policy as feed_policy
import installer_wheel_inputs as inspection
import installer_wheel_snapshot as snapshots


@pytest.fixture
def linked_modules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, bytes]:
    modules = (acquisition, inspection, snapshots, feed_policy)
    cache, installed = tmp_path / "cache", tmp_path / "installed"
    cache.mkdir()
    installed.mkdir()
    expected = {}
    for module in modules:
        assert module.__file__ is not None
        original = Path(module.__file__)
        content = original.read_bytes()
        cached = cache / original.name
        cached.write_bytes(content)
        linked = installed / original.name
        os.link(cached, linked)
        assert linked.stat().st_nlink == 2
        monkeypatch.setattr(module, "__file__", str(linked))
        expected[linked.name] = content
    return expected


def test_worker_sources_accept_uv_hardlinked_modules(linked_modules: dict[str, bytes]) -> None:
    assert acquisition._worker_sources() == linked_modules
    with pytest.raises(inspection.WheelError, match="unlinked"):
        inspection._ordinary(Path(acquisition.__file__))


def test_worker_sources_reject_replacement_before_open(
    linked_modules: dict[str, bytes], monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = Path(acquisition.__file__)
    original = snapshots._open

    def replaced(path: Path) -> BinaryIO:
        if path == source:
            replacement = path.with_suffix(".replacement")
            replacement.write_bytes(linked_modules[path.name])
            os.replace(replacement, path)
        return original(path)

    monkeypatch.setattr(snapshots, "_open", replaced)
    with pytest.raises(inspection.WheelError, match="changed"):
        acquisition._worker_sources()
