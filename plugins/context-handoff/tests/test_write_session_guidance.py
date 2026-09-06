"""Tests for the context-handoff session guidance writer."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_PLUGIN = Path(__file__).resolve().parents[1]
_SCRIPT = _PLUGIN / "scripts" / "write_session_guidance.py"
_SPEC = importlib.util.spec_from_file_location(
    "handoff_guidance_writer_under_test", _SCRIPT
)
assert _SPEC and _SPEC.loader
writer = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = writer
_SPEC.loader.exec_module(writer)


def _target(home: Path) -> Path:
    return (
        home
        / ".copilot"
        / "session-state"
        / "session-1"
        / "instructions"
        / "context-handoff"
        / "session-guidance.instructions.md"
    )


def test_writes_full_continuity_guidance(monkeypatch, tmp_path):
    monkeypatch.setattr(
        writer,
        "_run_contributor",
        lambda root, payload: "[owner: context-handoff@1.0.0]\ncontinuity",
    )
    assert writer.write_session_guidance({"sessionId": "session-1"}, home=tmp_path)
    assert _target(tmp_path).read_text(encoding="utf-8") == (
        "# Context handoff session guidance\n\n"
        "[owner: context-handoff@1.0.0]\ncontinuity\n"
    )


def test_no_content_replaces_stale_guidance(monkeypatch, tmp_path):
    monkeypatch.setattr(writer, "_run_contributor", lambda *args: "")
    target = _target(tmp_path)
    target.parent.mkdir(parents=True)
    target.write_text("stale guidance", encoding="utf-8")
    assert writer.write_session_guidance({"sessionId": "session-1"}, home=tmp_path)
    content = target.read_text(encoding="utf-8")
    assert "stale guidance" not in content
    assert "unavailable" in content


def test_over_budget_content_writes_explicit_status(monkeypatch, tmp_path):
    monkeypatch.setattr(writer, "_run_contributor", lambda *args: "x" * 5000)
    assert writer.write_session_guidance({"sessionId": "session-1"}, home=tmp_path)
    assert "omitted because" in _target(tmp_path).read_text(encoding="utf-8")


@pytest.mark.parametrize("session_id", ("", "../escape", "slash/value", "a" * 129))
def test_rejects_unsafe_session_ids(monkeypatch, tmp_path, session_id):
    monkeypatch.setattr(
        writer,
        "_run_contributor",
        lambda *args: pytest.fail("must not run for an unsafe session id"),
    )
    assert not writer.write_session_guidance({"sessionId": session_id}, home=tmp_path)
    assert not (tmp_path / ".copilot").exists()


@pytest.mark.parametrize("component", (".copilot", "session-state", "session-1"))
def test_rejects_ancestor_escape(monkeypatch, tmp_path, component):
    monkeypatch.setattr(writer, "_run_contributor", lambda *args: "guidance")
    outside = tmp_path / "outside"
    outside.mkdir()
    if component == ".copilot":
        link = tmp_path / ".copilot"
    elif component == "session-state":
        copilot = tmp_path / ".copilot"
        copilot.mkdir()
        link = copilot / "session-state"
    else:
        state = tmp_path / ".copilot" / "session-state"
        state.mkdir(parents=True)
        link = state / "session-1"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")
    assert not writer.write_session_guidance(
        {"sessionId": "session-1"}, home=tmp_path
    )


def test_resolve_runtime_error_fails_open(monkeypatch, tmp_path):
    monkeypatch.setattr(writer, "_run_contributor", lambda *args: "guidance")
    original_resolve = Path.resolve

    def resolve(path, *args, **kwargs):
        if path.name == "session-state":
            raise RuntimeError("symlink loop")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    assert not writer.write_session_guidance(
        {"sessionId": "session-1"}, home=tmp_path
    )


def test_reparse_detection_without_link_creation():
    path = SimpleNamespace(
        lstat=lambda: SimpleNamespace(
            st_mode=stat.S_IFDIR,
            st_file_attributes=getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400),
        )
    )
    assert writer._is_link_or_reparse(path)


def test_main_always_emits_empty_object(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO("not json"))
    assert writer.main() == 0
    assert capsys.readouterr().out == "{}"


def test_powershell_wrapper_writes_bounded_session_file(tmp_path):
    shell = shutil.which("pwsh") or shutil.which("powershell.exe")
    if not shell:
        pytest.skip("PowerShell is unavailable")
    home = tmp_path / "home"
    home.mkdir()
    payload = json.dumps(
        {
            "sessionId": "session-1",
            "cwd": str(tmp_path),
            "source": "copilot-cli",
        }
    )
    env = {
        **os.environ,
        "HOME": str(home),
        "USERPROFILE": str(home),
        "COPILOT_PLUGIN_ROOT": str(_PLUGIN),
    }
    for name in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
        env.pop(name, None)
    result = subprocess.run(
        [
            shell,
            "-NoProfile",
            "-File",
            str(_PLUGIN / "scripts" / "write-session-guidance.ps1"),
        ],
        cwd=tmp_path,
        env=env,
        input=payload,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout == "{}"
    content = _target(home).read_text(encoding="utf-8")
    assert content.startswith("# Context handoff session guidance\n\n")
    assert "[owner: context-handoff@" in content
    assert "When you own the active objective" in content
    assert "agent-worktrees session command catalog" not in content
    assert len(content.encode("utf-8")) <= writer._GUIDANCE_MAX_BYTES


def test_hook_and_projection_contracts():
    hooks = json.loads((_PLUGIN / "hooks.json").read_text(encoding="utf-8"))
    entries = hooks["hooks"]["sessionStart"]
    writer_entries = [
        entry
        for entry in entries
        if "write-session-guidance" in entry.get("bash", "")
    ]
    assert len(writer_entries) == 1
    writer_entry = writer_entries[0]
    assert "write-session-guidance" in writer_entry["powershell"]
    assert writer_entry["timeoutSec"] == 30
    for shell in ("bash", "powershell"):
        assert "COPILOT_PLUGIN_ROOT" in writer_entry[shell]
        assert "PLUGIN_ROOT" in writer_entry[shell]
        assert "CLAUDE_PLUGIN_ROOT" in writer_entry[shell]

    declaration = json.loads(
        (_PLUGIN / "instruction-projections.json").read_text(encoding="utf-8")
    )
    assert declaration["projections"][0]["legacyMarkers"] == []
    pointer = (
        _PLUGIN / "instructions" / "session-guidance.instructions.md"
    ).read_text(encoding="utf-8")
    assert "COPILOT_AGENT_SESSION_ID" in pointer
    assert "instructions/context-handoff/session-guidance.instructions.md" in pointer
    assert "~/.copilot/session-state" not in pointer
