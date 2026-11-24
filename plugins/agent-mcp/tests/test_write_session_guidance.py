"""Tests for the agent-mcp session guidance writer."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import stat
import sys
import time
from collections.abc import Callable
from pathlib import Path
from statistics import median
from types import SimpleNamespace

import pytest

from agent_procutil.owned_budget import OwnedExecutionBudget
from agent_procutil.owned_process import run_caller_owned

from .managed_hook_fixture import packaged_hooks as packaged_hooks
from .managed_hook_fixture import prepared_helper as prepared_helper

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


@pytest.mark.parametrize("entry", ["powershell", "pwsh"])
def test_powershell_composed_hooks_write_bounded_session_file(
    tmp_path: Path, packaged_hooks: Path, prepared_helper: tuple[Path, Path, Path],
    entry: str, record_property: Callable[[str, object], None],
) -> None:
    shell = shutil.which(entry)
    if not shell or entry == "powershell" and os.name != "nt":
        pytest.skip("The addressed native PowerShell host is unavailable")
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
        "AGENT_MCP_NO_SELFPROVISION": "1",
    }
    for name in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
        env.pop(name, None)
    hooks = json.loads((_PLUGIN / "hooks.json").read_text(encoding="utf-8"))["hooks"]["sessionStart"]
    samples = []
    for _ in range(2):
        _target(home).unlink(missing_ok=True)
        begin = time.monotonic()
        for hook in hooks:
            limit = hook["timeoutSec"]
            result = run_caller_owned(
                [shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-Command", hook["powershell"]],
                env=env, cwd=tmp_path, input_text=payload,
                timeout=limit, max_stdout_bytes=65536, max_stderr_bytes=65536,
                budget=OwnedExecutionBudget.from_timeout(limit + 5, cleanup_reserve=5),
            )
            assert result.returncode == 0 and result.stdout == "{}", result
            assert not result.stderr or result.stderr.strip() == (
                "[agent-mcp] bootstrap deferred: self-provisioning is disabled or identity reads are pinned."
            ), result
        samples.append(time.monotonic() - begin)
        content = _target(home).read_text(encoding="utf-8")
        assert content.startswith("# Agent MCP session guidance\n\n")
        assert "session command catalog" in content
        assert len(content.encode("utf-8")) <= writer._GUIDANCE_MAX_BYTES
    record_property("mcp_composed_hook_performance", json.dumps({
        "entry": entry, "samples_seconds": samples, "expected_cycles": 2,
        "completed_cycles": len(samples), "errors": [], "roster_size": len(hooks),
        "boundary": "complete-plugin-sessionStart-roster",
        "conditions": "offline; prepared managed Python; automatic provisioning disabled; cache warmth uncontrolled",
        "source_sha": os.environ.get("GITHUB_SHA"), "installation": "materialized fixture only",
        "target_seconds": 2, "target_met": max(samples) < 2,
        "p50_seconds": median(samples), "max_seconds": max(samples),
        "p95_seconds": None, "p99_seconds": None,
        "tail_qualification": "insufficient samples",
        "complete_enabled_host_dispatch": "unavailable: the host's full enabled-plugin roster is not executed",
        "cpu_seconds": None, "child_spawn_count": None,
    }))


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
