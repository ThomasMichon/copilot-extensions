"""Tests for the agent-mcp session guidance writer."""

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

from .managed_hook_fixture import packaged_hooks as packaged_hooks
from .managed_hook_fixture import prepared_helper as prepared_helper

if sys.platform == "win32":
    HEADLESS = subprocess.CREATE_NO_WINDOW
else:
    HEADLESS = 0

_PLUGIN = Path(__file__).resolve().parents[1]
_SCRIPT = _PLUGIN / "scripts" / "write_session_guidance.py"
_SPEC = importlib.util.spec_from_file_location("agent_mcp_guidance_writer_under_test", _SCRIPT)
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
        / "agent-mcp"
        / "session-guidance.instructions.md"
    )


def test_writes_command_catalog(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        writer,
        "_run_contributor",
        lambda root, stem, payload: "## Agent MCP session command catalog\n\ncatalog",
    )
    assert writer.write_session_guidance({"sessionId": "session-1"}, home=tmp_path)
    assert _target(tmp_path).read_text(encoding="utf-8") == (
        "# Agent MCP session guidance\n\n"
        "## Agent MCP session command catalog\n\ncatalog\n"
    )


def test_no_content_replaces_stale_guidance(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(writer, "_run_contributor", lambda *args: "")
    target = _target(tmp_path)
    target.parent.mkdir(parents=True)
    target.write_text("stale guidance", encoding="utf-8")
    assert writer.write_session_guidance({"sessionId": "session-1"}, home=tmp_path)
    content = target.read_text(encoding="utf-8")
    assert "stale guidance" not in content
    assert "unavailable" in content


def test_over_budget_content_writes_explicit_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        writer, "_run_contributor", lambda root, stem, payload: "x" * 5000
    )
    assert writer.write_session_guidance({"sessionId": "session-1"}, home=tmp_path)
    assert "omitted because" in _target(tmp_path).read_text(encoding="utf-8")


@pytest.mark.parametrize("session_id", ("", "../escape", "slash/value", "a" * 129))
def test_rejects_unsafe_session_ids(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, session_id: str,
) -> None:
    monkeypatch.setattr(
        writer,
        "_run_contributor",
        lambda *args: pytest.fail("must not run for an unsafe session id"),
    )
    assert not writer.write_session_guidance({"sessionId": session_id}, home=tmp_path)
    assert not (tmp_path / ".copilot").exists()


@pytest.mark.parametrize("component", (".copilot", "session-state", "session-1"))
def test_rejects_ancestor_escape(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, component: str,
) -> None:
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


def test_resolve_runtime_error_fails_open(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(writer, "_run_contributor", lambda *args: "guidance")
    original_resolve = Path.resolve

    def resolve(path: Path, strict: bool = False) -> Path:
        if path.name == "session-state":
            raise RuntimeError("symlink loop")
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", resolve)
    assert not writer.write_session_guidance(
        {"sessionId": "session-1"}, home=tmp_path
    )


def test_reparse_detection_without_link_creation() -> None:
    path = SimpleNamespace(
        lstat=lambda: SimpleNamespace(
            st_mode=stat.S_IFDIR,
            st_file_attributes=getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400),
        )
    )
    assert writer._is_link_or_reparse(path)


def test_main_always_emits_empty_object(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        sys,
        "stdin",
        SimpleNamespace(buffer=__import__("io").BytesIO(b"not json")),
    )
    assert writer.main() == 0
    assert capsys.readouterr().out == "{}"


def test_powershell_wrapper_writes_bounded_session_file(
    tmp_path: Path, packaged_hooks: Path, prepared_helper: tuple[Path, Path, Path],
) -> None:
    shell = shutil.which("pwsh") or shutil.which("powershell.exe")
    if not shell:
        pytest.skip("PowerShell is unavailable")
    home = tmp_path / "home"
    home.mkdir()
    payload = json.dumps({"sessionId": "session-1", "source": "copilot-cli"})
    env = {
        **os.environ,
        "HOME": str(home),
        "USERPROFILE": str(home),
        "COPILOT_PLUGIN_ROOT": str(packaged_hooks),
        "UV_PYTHON_INSTALL_DIR": str(prepared_helper[2]),
        "UV_OFFLINE": "1",
    }
    for name in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
        env.pop(name, None)
    result = subprocess.run(
        [
            shell,
            "-NoProfile",
            "-File",
            str(packaged_hooks / "scripts" / "write-session-guidance.ps1"),
        ],
        env=env,
        input=payload,
        capture_output=True,
        text=True,
        timeout=50,
        creationflags=HEADLESS,
    )
    assert result.returncode == 0
    assert result.stdout == "{}"
    content = _target(home).read_text(encoding="utf-8")
    assert content.startswith("# Agent MCP session guidance\n\n")
    assert len(content.encode("utf-8")) <= writer._GUIDANCE_MAX_BYTES


def test_hook_and_projection_contracts() -> None:
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
    assert writer_entry["timeoutSec"] == 45
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
    assert "already-disclosed session folder" in pointer
    assert "instructions/agent-mcp/session-guidance.instructions.md" in pointer
    assert "~/.copilot/session-state" not in pointer
