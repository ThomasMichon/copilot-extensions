"""Independent checks for the committed-source current-authority capture."""
from __future__ import annotations

import asyncio
import copy
import importlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

from agent_bridge import preference_attestation as a
from agent_bridge import preference_exec as terminal
from agent_bridge.session_host import preference_spawn as host


@pytest.fixture
def capture_tool(monkeypatch: pytest.MonkeyPatch):
    repo = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(repo / "tools"))
    return importlib.import_module("capture_bridge_current_authority")


def test_preference_authority_capture_replays_terminal_and_host(capture_tool) -> None:
    path = Path(__file__).resolve().parents[1] / (
        "contract/fixtures/session-host/current/wrapper-authority-v1.json"
    )
    seed = json.loads(path.read_text(encoding="utf-8"))
    original = copy.deepcopy(seed)
    frame, status, executed = capture_tool.producer_probe(a, terminal, seed, b"\x01")
    rejected_frame, rejected_status, rejected_exec = capture_tool.producer_probe(
        a, terminal, seed, b"",
    )
    assert seed == original
    assert status == 0 and executed
    assert rejected_status == 78 and not rejected_exec
    assert rejected_frame == frame
    assert "nonce" not in frame["receipt"] and "proof" not in frame["receipt"]
    authority = frame["receipt"]["authority"]
    assert authority["digest"] == a.component_digest()
    expected = {
        **authority["space"], "nonce": frame["proof"]["nonce"],
        **{key: authority[key] for key in ("digest", "mode", "target")},
    }
    events, accepted = asyncio.run(capture_tool.host_probe(a, host, frame, expected))
    assert accepted
    assert events == ["spawn-private-channel", "read-frame", "verify-frame", "consent"]
    frame["proof"]["nonce"] = "wrong"
    events, accepted = asyncio.run(capture_tool.host_probe(a, host, frame, expected))
    assert not accepted and "consent" not in events


def test_preference_authority_capture_without_home_environment(
    capture_tool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = Path(__file__).resolve().parents[1] / (
        "contract/fixtures/session-host/current/wrapper-authority-v1.json"
    )
    seed = json.loads(path.read_text(encoding="utf-8"))
    home_variables = ("HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH")
    for key in home_variables:
        monkeypatch.delenv(key, raising=False)

    def unavailable_home(_path: str) -> str:
        assert not any(key in os.environ for key in home_variables)
        raise RuntimeError("Could not determine home directory.")

    monkeypatch.setattr(os.path, "expanduser", unavailable_home)
    actual_reader = terminal.execution_settings

    def read_without_home(*args: Any, **kwargs: Any) -> dict:
        assert not any(key in os.environ for key in home_variables)
        return actual_reader(*args, **kwargs)

    reader = Mock(side_effect=read_without_home)
    monkeypatch.setattr(terminal, "execution_settings", reader)
    frame, status, executed = capture_tool.producer_probe(a, terminal, seed, b"\x01")
    assert reader.call_count == 1
    assert status == 0 and executed
    assert frame["receipt"]["status"] == "resolved"
    assert frame["receipt"]["values"] == seed["receipt"]["values"]
    assert frame["receipt"]["sources"] == seed["receipt"]["sources"]
    assert frame["receipt"]["authority"]["space"] == seed["receipt"]["authority"]["space"]


def test_preference_authority_capture_refuses_dirty_source(
    capture_tool, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(capture_tool, "REPO", tmp_path)
    monkeypatch.setattr(capture_tool, "committed", lambda commit, path: "committed\n")
    source = tmp_path / "authority.py"
    source.write_text("changed\n", encoding="utf-8")
    with pytest.raises(ValueError, match="dirty capture source refused"):
        capture_tool.unchanged("a" * 40, "authority.py")


def test_preference_authority_capture_refuses_unknown_commit(capture_tool) -> None:
    with pytest.raises(ValueError, match="real locally available source commit"):
        capture_tool.capture("not-a-commit")


def test_preference_authority_capture_refuses_dirty_output(
    capture_tool, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(capture_tool, "REPO", tmp_path)
    output = {capture_tool.CURRENT + name: "expected\n" for name in capture_tool.FILES}
    output[capture_tool.REGISTRY] = "expected\n"
    monkeypatch.setattr(capture_tool, "capture", lambda commit: output)
    monkeypatch.setattr(capture_tool, "committed", lambda commit, path: "committed\n")
    monkeypatch.setattr("sys.argv", ["capture", "--source-commit", "a" * 40, "--write"])
    for relative in output:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("unexpected\n", encoding="utf-8")
    assert capture_tool.main() == 1
    assert all((tmp_path / path).read_text(encoding="utf-8") == "unexpected\n" for path in output)


@pytest.mark.parametrize("kind", ["file", "parent", "hardlink", "directory", "reparse"])
def test_preference_authority_capture_refuses_linked_outputs_before_any_write(
    capture_tool, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: str,
) -> None:
    root = tmp_path / "repo"
    output = {capture_tool.CURRENT + name: "captured\n" for name in capture_tool.FILES}
    output[capture_tool.REGISTRY] = "captured\n"
    for relative in output:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("committed\n", encoding="utf-8")
    registry = root / capture_tool.REGISTRY
    outside = tmp_path / "outside"
    if kind == "file":
        registry.rename(outside)
        registry.symlink_to(outside)
    elif kind == "parent":
        registry.parent.rename(outside)
        registry.parent.symlink_to(outside, target_is_directory=True)
    elif kind == "hardlink":
        os.link(registry, outside)
    elif kind == "directory":
        registry.rename(outside)
        registry.mkdir()
    else:
        lstat = Path.lstat

        def reparse(path: Path, *args: Any, **kwargs: Any) -> Any:
            info = lstat(path, *args, **kwargs)
            if path != registry.parent:
                return info
            return SimpleNamespace(
                st_mode=info.st_mode, st_nlink=info.st_nlink,
                st_dev=info.st_dev, st_ino=info.st_ino, st_file_attributes=0x400,
            )

        monkeypatch.setattr(Path, "lstat", reparse)
    monkeypatch.setattr(capture_tool, "REPO", root)
    monkeypatch.setattr(capture_tool, "capture", lambda commit: output)
    monkeypatch.setattr(capture_tool, "committed", lambda commit, path: "committed\n")
    monkeypatch.setattr("sys.argv", ["capture", "--source-commit", "a" * 40, "--write"])
    writes = []
    write_text = Path.write_text

    def tracked_write(path: Path, *args: Any, **kwargs: Any) -> int:
        writes.append(path)
        return write_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", tracked_write)
    assert capture_tool.main() == 1
    assert writes == []
    for relative in output:
        path = root / relative
        if path.is_file():
            assert path.read_text(encoding="utf-8") == "committed\n"
    if outside.is_file():
        assert outside.read_text(encoding="utf-8") == "committed\n"


@pytest.mark.parametrize("relative", ["../outside.json", "other.json", "/outside.json"])
def test_preference_authority_capture_refuses_unapproved_output_set(
    capture_tool, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, relative: str,
) -> None:
    monkeypatch.setattr(capture_tool, "REPO", tmp_path)
    with pytest.raises(ValueError, match="four approved files"):
        capture_tool.output_paths({relative: "captured\n"})
