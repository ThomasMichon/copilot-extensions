"""Synthetic constructor for Manager orchestration/assembly unit tests.

Real governed construction, origin health and native execution are exercised by
the separate cold acceptance lane; these existing units never install packages.
"""

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import shutil


def apply_constructor_fixture(monkeypatch):
    from worktree_manager import self_install as si

    adapter = si._runtime_installer()
    runtime = adapter.runtime
    monkeypatch.setattr(si, "_runtime_installer", lambda: adapter)

    def healthy(slot):
        return runtime.is_complete(slot.parent.parent, slot.name) and all(
            (slot / "payload" / rel).is_file() for rel in si._SLOT_KEY_FILES
        )

    @contextmanager
    def candidate(payload, root, *, assemble):
        version = si.payload_version(payload)
        selected = si.current_version(root)
        slot = si.version_slot(selected or version, root)
        reused = healthy(slot) and si.payload_version(slot / "payload") == version
        if not reused:
            slot = si.version_slot(version, root)
            if slot.exists():
                slot = si.version_slot(version + "+repair.unit", root)
            slot.mkdir(parents=True)
            try:
                assemble(payload, slot / "payload")
            except BaseException:
                shutil.rmtree(slot, ignore_errors=True)
                raise
            python = slot / ("Scripts/python.exe" if si.os.name == "nt" else "bin/python")
            python.parent.mkdir(exist_ok=True)
            python.write_text("unit constructor", encoding="utf-8")
            runtime.mark_complete(root, slot.name, payload_hash="a" * 64)
        yield SimpleNamespace(slot=Path(slot), reused=reused)

    monkeypatch.setattr(adapter, "candidate", candidate)
