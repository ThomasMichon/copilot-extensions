"""Explicit real bounded-copy lane; synthetic runtimes, not a guest install."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import guest_runtime

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="explicit local Linux copy lane")


def test_actual_copy_retains_executables_links_and_private_root(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    from single_instance_lease import OperationLock

    seed, state = tmp_path / "seed", tmp_path / "state"
    seed.mkdir()
    state.mkdir(mode=0o700)
    outside = tmp_path / "not-a-runtime"
    outside.write_bytes(b"untouched external target")
    for plugin in ("bridge", "dispatch", "worktrees"):
        binaries = seed / plugin / "bin"
        binaries.mkdir(parents=True)
        command = binaries / ("agent-" + plugin)
        command.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        command.chmod(0o555)
        (binaries / "python").symlink_to(sys.executable)
    (seed / ".retained").write_bytes(b"synthetic immutable seed metadata")
    (seed / "external").symlink_to(outside)
    runtime = state / "runtime"
    monkeypatch.setattr(guest_runtime, "SEED", seed)
    monkeypatch.setattr(guest_runtime, "STATE", state)
    monkeypatch.setattr(guest_runtime, "RUNTIME", runtime)
    # Actual cp/process/lock, but local files are not a root-owned read-only image.
    monkeypatch.setattr(
        guest_runtime, "_seed_descriptor",
        lambda: os.open(seed, os.O_RDONLY | os.O_DIRECTORY),
    )
    monkeypatch.setattr(guest_runtime, "_runtime_identity", runtime.lstat)
    with OperationLock(state / guest_runtime.LOCK.name) as holder:
        guest_runtime.prepare_runtime(SimpleNamespace(assert_owned=holder.assert_owned))
        with pytest.raises(ValueError, match="retained guest state"):
            guest_runtime.prepare_runtime(SimpleNamespace(assert_owned=holder.assert_owned))
    assert stat.S_IMODE(runtime.stat().st_mode) == 0o700
    for plugin in ("bridge", "dispatch", "worktrees"):
        command = runtime / plugin / "bin" / ("agent-" + plugin)
        assert command.read_bytes() == (seed / plugin / "bin" / command.name).read_bytes()
        assert command.stat().st_mode & 0o111 == 0o111
        assert (runtime / plugin / "bin/python").is_symlink()
        assert os.readlink(runtime / plugin / "bin/python") == sys.executable
    assert (runtime / "external").is_symlink()
    assert outside.read_bytes() == b"untouched external target"
    assert (runtime / ".retained").read_bytes() == (seed / ".retained").read_bytes()
    assert not (runtime / ".install-complete.json").exists()
