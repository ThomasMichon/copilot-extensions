"""The native installation marker survives the actual daemon producer."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import agent_procutil
import pytest

from agent_dispatch import procutil

pytestmark = pytest.mark.portfolio_tier("T1")


@pytest.mark.parametrize(
    ("platform", "executable", "finite", "marked"),
    [
        ("nt", "python.exe", False, True),
        ("nt", "pythonw.exe", True, True),
        ("posix", "python.exe", False, False),
        ("nt", "tool.exe", False, False),
    ],
)
def test_daemon_producer_binds_native_root_without_changing_other_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    platform: str, executable: str, finite: bool, marked: bool,
) -> None:
    root = tmp_path / "runtime"
    monkeypatch.setattr(procutil, "runtime_root", lambda: root)
    monkeypatch.setattr(procutil, "os", SimpleNamespace(name=platform, path=os.path))
    calls: list[list[str]] = []
    displays: list[object] = []
    sentinel = object()

    def spawn(argv: list[str], **options: object) -> object:
        calls.append(list(argv))
        displays.append(options["display"])
        return sentinel

    monkeypatch.setattr(agent_procutil, "spawn_daemon", spawn)
    monkeypatch.setattr(procutil, "spawn_finite_worker", spawn)
    argv = [str(tmp_path / executable), "-m", "agent_dispatch", "serve"]
    original = list(argv)
    result = procutil.spawn_dispatch_daemon(
        argv, log_name="fixture.log", finite_worker=finite,
    )
    expected = [
        original[0], "-X", "agent_dispatch_install_dir=" + str(root), *original[1:],
    ] if marked else original
    assert calls == [expected]
    assert argv == original
    assert result is sentinel
    assert displays == ["agent_dispatch serve"]
