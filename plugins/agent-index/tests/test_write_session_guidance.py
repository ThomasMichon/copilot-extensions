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
SPEC = importlib.util.spec_from_file_location("index_guidance", SCRIPT)
assert SPEC and SPEC.loader
writer = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = writer
SPEC.loader.exec_module(writer)


def _target(home: Path) -> Path:
    return (
        home / ".copilot" / "session-state" / "session-1" / "instructions"
        / "agent-index" / "session-guidance.instructions.md"
    )


def test_writes_catalog_and_replaces_stale_content(monkeypatch, tmp_path):
    monkeypatch.setattr(writer, "_run_producer", lambda *args: "catalog")
    target = _target(tmp_path)
    target.parent.mkdir(parents=True)
    target.write_text("stale", encoding="utf-8")
    assert writer.write_session_guidance({"sessionId": "session-1"}, home=tmp_path)
    content = target.read_text(encoding="utf-8")
    assert "catalog" in content
    assert "stale" not in content


def test_writes_combined_catalog_and_scope_binding(monkeypatch, tmp_path):
    monkeypatch.setattr(writer, "_run_producer", lambda *args: "catalog")
    monkeypatch.setattr(
        writer, "_scope_binding_context", lambda *args: "scope binding"
    )
    assert writer.write_session_guidance({"sessionId": "session-1"}, home=tmp_path)
    content = _target(tmp_path).read_text(encoding="utf-8")
    assert "catalog" in content
    assert "scope binding" in content


def test_scope_binding_context_passes_authoritative_payload_cwd(
    monkeypatch, tmp_path
):
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        return SimpleNamespace(
            returncode=0, stdout=json.dumps({"additionalContext": "scoped"})
        )

    monkeypatch.setattr(writer.subprocess, "run", fake_run)
    repo = tmp_path / "repo"
    repo.mkdir()
    (PLUGIN / "scripts" / "emit_scope_binding.py").touch(exist_ok=True)
    context = writer._scope_binding_context(PLUGIN, {"cwd": str(repo)})
    assert context == "scoped"
    assert "--cwd" in seen["argv"]
    assert seen["argv"][seen["argv"].index("--cwd") + 1] == str(repo)


def test_scope_binding_context_ignores_invalid_cwd(monkeypatch, tmp_path):
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        return SimpleNamespace(
            returncode=0, stdout=json.dumps({"additionalContext": "scoped"})
        )

    monkeypatch.setattr(writer.subprocess, "run", fake_run)
    for bad_cwd in (None, "relative/path", str(tmp_path / "missing")):
        context = writer._scope_binding_context(PLUGIN, {"cwd": bad_cwd})
        assert context == "scoped"
        assert "--cwd" not in seen["argv"]


def test_run_producer_passes_cwd_explicitly_and_closes_stdin(monkeypatch, tmp_path):
    """The catalog producer must get the session's real cwd via the
    subprocess ``cwd=`` kwarg (not rely on the calling process's OWN
    inherited working directory matching it) and must explicitly close
    stdin (``subprocess.DEVNULL``) rather than piping the session payload as
    ``input=`` -- the producer script never reads its own stdin, and piping
    unused bytes there risked a nested-process stdin-handle inheritance
    deadlock on Windows (a real production bug this fix closes)."""
    seen = {}

    def fake_run(argv, **kwargs):
        seen.update(kwargs)
        return SimpleNamespace(
            returncode=0, stdout=json.dumps({"additionalContext": "catalog"}).encode("utf-8")
        )

    monkeypatch.setattr(writer.subprocess, "run", fake_run)
    monkeypatch.setattr(writer, "_producer_argv", lambda _root: ["fake"])
    repo = tmp_path / "repo"
    repo.mkdir()

    context = writer._run_producer(PLUGIN, {"cwd": str(repo)})
    assert context == "catalog"
    assert seen["cwd"] == str(repo)
    assert seen["stdin"] is writer.subprocess.DEVNULL
    assert "input" not in seen


def test_run_producer_falls_back_to_none_cwd_for_invalid_payload_cwd(
    monkeypatch, tmp_path
):
    seen = {}

    def fake_run(argv, **kwargs):
        seen.update(kwargs)
        return SimpleNamespace(
            returncode=0, stdout=json.dumps({"additionalContext": "catalog"}).encode("utf-8")
        )

    monkeypatch.setattr(writer.subprocess, "run", fake_run)
    monkeypatch.setattr(writer, "_producer_argv", lambda _root: ["fake"])

    for bad_cwd in (None, "relative/path", str(tmp_path / "missing")):
        writer._run_producer(PLUGIN, {"cwd": bad_cwd})
        assert seen["cwd"] is None


def test_two_producers_run_concurrently_not_sequentially(monkeypatch, tmp_path):
    """Regression test for a real production bug: the two producers used to
    run as sequential tuple elements, so two SLOW-but-legitimate subprocess
    calls (measured ~10-11s each on a real repo) summed to ~20s+, silently
    eating most of the sessionStart hook's own 45s budget and regularly
    tripping the per-producer subprocess timeout -- making the ENTIRE command
    catalog (and therefore agent-index itself) invisible to every agent turn,
    every session, with no visible error anywhere. Each producer here sleeps
    for a fixed amount; if they ever regress to running sequentially again,
    the elapsed wall-clock time will be roughly double and this assertion
    will catch it well before actually waiting ~10s in CI.
    """
    import time

    SLEEP_S = 0.3

    def slow_producer_1(*_args):
        time.sleep(SLEEP_S)
        return "catalog"

    def slow_producer_2(*_args):
        time.sleep(SLEEP_S)
        return "scope binding"

    monkeypatch.setattr(writer, "_run_producer", slow_producer_1)
    monkeypatch.setattr(writer, "_scope_binding_context", slow_producer_2)

    start = time.monotonic()
    assert writer.write_session_guidance({"sessionId": "session-1"}, home=tmp_path)
    elapsed = time.monotonic() - start

    # Running sequentially would take >= 2 * SLEEP_S; concurrently it should
    # stay close to ONE SLEEP_S. Use 1.5x as the dividing line with headroom
    # for scheduling jitter -- comfortably below the 2x a sequential
    # regression would produce, comfortably above the ~1x true concurrent cost.
    assert elapsed < SLEEP_S * 1.5, (
        f"producers took {elapsed:.3f}s for a {SLEEP_S}s sleep each -- "
        "they appear to be running sequentially again, not concurrently"
    )
    content = _target(tmp_path).read_text(encoding="utf-8")
    assert "catalog" in content
    assert "scope binding" in content


def test_rejects_invalid_session_ids_without_running_producer(monkeypatch, tmp_path):
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
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (tmp_path / ".copilot").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")
    assert not writer.write_session_guidance(
        {"sessionId": "session-1"}, home=tmp_path
    )


def test_over_budget_output_is_bounded(monkeypatch, tmp_path):
    monkeypatch.setattr(writer, "_run_producer", lambda *args: "x" * 5000)
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
    shell = shutil.which("pwsh") or shutil.which("powershell.exe")
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
        capture_output=True,
        env=env,
    )
    assert result.returncode == 0
    assert result.stdout == "{}"
    assert _target(home).is_file()


def test_hook_and_projection_contracts():
    hooks = json.loads((PLUGIN / "hooks.json").read_text(encoding="utf-8"))
    entries = hooks["hooks"]["sessionStart"]
    writer_entries = [entry for entry in entries if "write-session-guidance" in entry["bash"]]
    assert len(writer_entries) == 1
    ensure_entries = [entry for entry in entries if 'bash "$s" ensure' in entry["bash"]]
    assert len(ensure_entries) == 1
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
    assert "already-disclosed session folder" in pointer
    assert "instructions/agent-index/session-guidance.instructions.md" in pointer
