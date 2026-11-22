"""Real thread interleaving qualifies the guard outside the actor-free unit packet."""

from pathlib import Path
import importlib.util
import pytest
import os
import sys
import threading
from unittest.mock import Mock
from typing import Any, cast

from promotion_unit_contract import UnitEvidenceError
from promotion_unit_effects import UnitRun


@pytest.mark.skipif(os.name != "nt", reason="Windows cache-prefix path spelling")
@pytest.mark.parametrize("extended", [False, True])
@pytest.mark.parametrize("admitted", [False, True])
def test_redirected_bytecode_keeps_exact_source_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extended: bool, admitted: bool,
) -> None:
    root = tmp_path / "source"
    source = root / "example.py"
    cache = tmp_path / "cache"
    monkeypatch.setattr(sys, "pycache_prefix", str(cache))
    spelling = "\\\\?\\" + str(source) if extended else str(source)
    bytecode = importlib.util.cache_from_source(spelling)
    run = UnitRun(Mock(), root, {"example.py": "admitted"} if admitted else {}, cache)
    run.active = True
    if admitted:
        run.audit("open", (bytecode, "r", 0))
        assert run.opened_sources == {"example.py"}
        assert run.effects == []
    else:
        with pytest.raises(UnitEvidenceError, match="unadmitted source read: example.py"):
            run.audit("open", (bytecode, "r", 0))
        assert run.effects == ["unadmitted source read: example.py"]


@pytest.mark.skipif(os.name != "nt", reason="Windows cache-prefix path spelling")
def test_bytecode_mapping_does_not_authorize_an_outside_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, cache = tmp_path / "source", tmp_path / "cache"
    monkeypatch.setattr(sys, "pycache_prefix", str(cache))
    run = UnitRun(Mock(), root, {"example.py": "admitted"}, cache)
    run.active = True
    with pytest.raises(UnitEvidenceError, match="write outside synthetic"):
        run.audit("open", (str(root / "__pycache__" / "example.cpython-312.pyc"), "w", 0))
    with pytest.raises(UnitEvidenceError, match="unattributable Python bytecode"):
        run.audit("open", (str(cache / "unattributed.pyc"), "r", 0))


def test_interleaved_relative_opens_keep_thread_and_nested_path_custody(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    temporary = tmp_path / "synthetic"
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(
        os, "readlink",
        lambda path: str(temporary if str(path).endswith("/42") else tmp_path / "outside"),
    )
    run = UnitRun(Mock(), tmp_path, {}, temporary)
    run.active = True
    outside_ready, inside_ready, outside_done = (threading.Event() for _ in range(3))
    audit = run.audit
    executed: list[tuple[str, int | None]] = []
    outcomes: dict[str, int | BaseException] = {}
    settled: dict[str, bool] = {}

    def interleaved_audit(event: str, arguments: tuple[Any, ...]) -> None:
        if event == "open" and arguments[0] == "same.txt":
            if threading.current_thread() is outside:
                outside_ready.set()
                assert inside_ready.wait(5)
            else:
                inside_ready.set()
                assert outside_done.wait(5)
        audit(event, arguments)

    def original(name: str, flags: int, mode: int, *, dir_fd: int | None) -> int:
        audit("open", (name, None, flags))
        if name == "same.txt" and dir_fd == 42:
            run.guarded_open("nested.txt", flags, dir_fd=dir_fd)
            audit("open", (name, None, flags))
        executed.append((name, dir_fd))
        return 17

    def open_path(label: str, directory_fd: int) -> None:
        try:
            if label == "inside":
                assert outside_ready.wait(5)
            outcomes[label] = run.guarded_open(
                "same.txt", os.O_WRONLY | os.O_CREAT, dir_fd=directory_fd,
            )
        except BaseException as error:
            outcomes[label] = error
        finally:
            settled[label] = run._open_paths == []
            if label == "outside":
                outside_done.set()

    cast(Any, run).audit = interleaved_audit
    run._os_open = cast(Any, original)
    outside = threading.Thread(target=open_path, args=("outside", 41))
    inside = threading.Thread(target=open_path, args=("inside", 42))
    outside.start()
    inside.start()
    try:
        outside.join(10)
        inside.join(10)
    finally:
        outside_ready.set()
        inside_ready.set()
        outside_done.set()
        outside.join(5)
        inside.join(5)
    assert not outside.is_alive() and not inside.is_alive()
    assert isinstance(outcomes["outside"], UnitEvidenceError)
    assert outcomes["inside"] == 17
    assert executed == [("nested.txt", 42), ("same.txt", 42)]
    assert settled == {"outside": True, "inside": True}
    assert run.effects == ["write outside synthetic unit temporary storage"]
