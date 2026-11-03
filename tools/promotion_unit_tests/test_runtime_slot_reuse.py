"""Single-process slot evidence; native leases and live updates are not exercised."""

from __future__ import annotations

import importlib.util
import os
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "libs" / "versioned-runtime" / "versioned_runtime.py"
VERSION = "1.2.3"
PAYLOAD_HASH = "a" * 64


@contextmanager
def _fake_lease(*_args, **_kwargs):
    yield


def _identity(path: Path):
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_mtime_ns


def _slot_snapshot(slot: Path):
    return (
        _identity(slot),
        {
            entry.name: (_identity(entry), entry.read_bytes())
            for entry in slot.iterdir()
        },
    )


@pytest.fixture
def completed_slot(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("promotion_slot_runtime", SOURCE)
    assert spec is not None and spec.loader is not None
    runtime = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, runtime)
    spec.loader.exec_module(runtime)
    monkeypatch.setattr(runtime, "_kernel_lease", _fake_lease)
    monkeypatch.setattr(runtime, "_publication_lease", _fake_lease)

    root = tmp_path / "runtime"
    slot = runtime.version_dir(root, VERSION)
    slot.mkdir(parents=True)
    payload = slot / "payload.py"
    payload.write_bytes(b"immutable payload\n")
    marker = runtime.mark_complete(root, VERSION, payload_hash=PAYLOAD_HASH, pid=11)
    old_mtime = 1_600_000_000_000_000_000
    for path in (slot, payload, marker):
        os.utime(path, ns=(old_mtime, old_mtime))
    return runtime, root, slot, marker, _slot_snapshot(slot)


def test_same_version_reuses_without_rewriting_slot(completed_slot):
    runtime, root, slot, marker, before = completed_slot
    assert (
        runtime.check_admission(root, VERSION, payload_hash=PAYLOAD_HASH)
        == runtime.ADMIT_REUSE
    )
    assert (
        runtime.mark_complete(root, VERSION, payload_hash=PAYLOAD_HASH, pid=22)
        == marker
    )
    assert _slot_snapshot(slot) == before


def test_same_version_conflict_refuses_without_rewriting_slot(completed_slot):
    runtime, root, slot, _marker, before = completed_slot
    other_hash = "b" * 64
    assert (
        runtime.check_admission(root, VERSION, payload_hash=other_hash)
        == runtime.ADMIT_CONTENT_CONFLICT
    )
    with pytest.raises(RuntimeError, match="content-conflict"):
        runtime.mark_complete(root, VERSION, payload_hash=other_hash, pid=22)
    assert _slot_snapshot(slot) == before
