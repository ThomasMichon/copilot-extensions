from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
SCRIPT = PLUGIN / "scripts" / "write_session_guidance.py"
SPEC = importlib.util.spec_from_file_location("codespaces_guidance", SCRIPT)
assert SPEC and SPEC.loader
writer = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = writer
SPEC.loader.exec_module(writer)


def _target(home: Path) -> Path:
    return (
        home / ".copilot" / "session-state" / "session-1" / "instructions"
        / "agent-codespaces" / "session-guidance.instructions.md"
    )


def test_writes_catalog_and_replaces_stale_content(monkeypatch, tmp_path):
    monkeypatch.setattr(
        writer,
        "_run_producer",
        lambda root, payload: "## agent-codespaces session command catalog\n\ncatalog",
    )
    monkeypatch.setattr(writer, "_run_codespace_map", lambda root, payload: "")
    target = _target(tmp_path)
    target.parent.mkdir(parents=True)
    target.write_text("stale", encoding="utf-8")
    assert writer.write_session_guidance({"sessionId": "session-1"}, home=tmp_path)
    content = target.read_text(encoding="utf-8")
    assert content.startswith("# Agent Codespaces session guidance\n\n")
    assert "catalog" in content
    assert "stale" not in content


def test_writes_combined_catalog_and_codespace_map(monkeypatch, tmp_path):
    monkeypatch.setattr(writer, "_run_producer", lambda root, payload: "catalog")
    monkeypatch.setattr(
        writer, "_run_codespace_map", lambda root, payload: "codespace map"
    )
    assert writer.write_session_guidance({"sessionId": "session-1"}, home=tmp_path)
    content = _target(tmp_path).read_text(encoding="utf-8")
    assert "catalog" in content
    assert "codespace map" in content


def test_run_codespace_map_passes_authoritative_payload_cwd(monkeypatch, tmp_path):
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        return SimpleNamespace(returncode=0, stdout=b'{"additionalContext":"map"}')

    monkeypatch.setattr(writer.subprocess, "run", fake_run)
    repo = tmp_path / "repo"
    repo.mkdir()
    context = writer._run_codespace_map(PLUGIN, {"cwd": str(repo)})
    assert context == "map"
    assert "--cwd" in seen["argv"]
    assert seen["argv"][seen["argv"].index("--cwd") + 1] == str(repo)


def test_run_codespace_map_ignores_invalid_cwd(monkeypatch, tmp_path):
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        return SimpleNamespace(returncode=0, stdout=b'{"additionalContext":"map"}')

    monkeypatch.setattr(writer.subprocess, "run", fake_run)
    for bad_cwd in (None, "relative/path", str(tmp_path / "missing")):
        context = writer._run_codespace_map(PLUGIN, {"cwd": bad_cwd})
        assert context == "map"
        assert "--cwd" not in seen["argv"]


def test_rejects_invalid_session_ids_without_running_producer(
    monkeypatch, tmp_path, 
):
    monkeypatch.setattr(
        writer, "_run_producer",
        lambda *args: pytest.fail("producer must not run"),
    )
    for session_id in ("", "../escape", "slash/value", "a" * 129):
        assert not writer.write_session_guidance(
            {"sessionId": session_id}, home=tmp_path
        )
    assert not (tmp_path / ".copilot").exists()


def test_rejects_ancestor_symlink_escape(monkeypatch, tmp_path):
    monkeypatch.setattr(writer, "_run_producer", lambda *args: "catalog")
    monkeypatch.setattr(writer, "_run_codespace_map", lambda *args: "")
    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / ".copilot"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")
    assert not writer.write_session_guidance(
        {"sessionId": "session-1"}, home=tmp_path
    )
    assert not (outside / "session-state").exists()


def test_over_budget_output_is_replaced_with_bounded_status(monkeypatch, tmp_path):
    monkeypatch.setattr(writer, "_run_producer", lambda *args: "x" * 5000)
    monkeypatch.setattr(writer, "_run_codespace_map", lambda *args: "")
    assert writer.write_session_guidance({"sessionId": "session-1"}, home=tmp_path)
    content = _target(tmp_path).read_text(encoding="utf-8")
    assert "omitted because" in content
    assert len(content.encode("utf-8")) <= writer._GUIDANCE_MAX_BYTES


def test_reparse_detection_is_fail_closed():
    path = SimpleNamespace(
        lstat=lambda: SimpleNamespace(
            st_mode=stat.S_IFDIR,
            st_file_attributes=getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400),
        )
    )
    assert writer._is_link_or_reparse(path)


def test_main_always_emits_empty_object(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(b"bad")))
    assert writer.main() == 0
    assert capsys.readouterr().out == "{}"


def test_wrapper_writes_exact_session_file(tmp_path):
    shell = shutil.which("pwsh") or (
        shutil.which("powershell.exe") if os.name == "nt" else None
    )
    command = [shell, "-NoProfile", "-File", str(PLUGIN / "scripts" / "write-session-guidance.ps1")]
    if not shell:
        shell = shutil.which("bash")
        if not shell:
            pytest.skip("no supported shell")
        command = [shell, str(PLUGIN / "scripts" / "write-session-guidance.sh")]
    home = tmp_path / "home"
    home.mkdir()
    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home),
           "COPILOT_PLUGIN_ROOT": str(PLUGIN)}
    for name in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
        env.pop(name, None)
    result = subprocess.run(
        command, input='{"sessionId":"session-1"}', text=True,
        capture_output=True, env=env,
    )
    assert result.returncode == 0
    assert result.stdout == "{}"
    assert _target(home).is_file()


def test_hook_and_projection_contracts():
    hooks = json.loads((PLUGIN / "hooks.json").read_text(encoding="utf-8"))
    entries = hooks["hooks"]["sessionStart"]
    writer_entries = [entry for entry in entries if "write-session-guidance" in entry["bash"]]
    assert len(writer_entries) == 1
    assert writer_entries[0]["timeoutSec"] == 45
    assert "emit-command-catalog" not in json.dumps(entries)
    declaration = json.loads(
        (PLUGIN / "session-context.json").read_text(encoding="utf-8")
    )
    assert declaration["contributors"] == []
    assert declaration["sessionStart"]["context"] == "none"
    projection = json.loads(
        (PLUGIN / "instruction-projections.json").read_text(encoding="utf-8")
    )
    assert projection["projections"][0]["legacyMarkers"] == []
    pointer = (PLUGIN / "instructions" / "session-guidance.instructions.md").read_text(
        encoding="utf-8"
    )
    assert "COPILOT_AGENT_SESSION_ID" in pointer
    assert "instructions/agent-codespaces/session-guidance.instructions.md" in pointer
