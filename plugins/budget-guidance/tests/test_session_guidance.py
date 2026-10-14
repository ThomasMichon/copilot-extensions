from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace

from agent_procutil import no_window_kwargs
import pytest

from budget_guidance.models import ModelError, load_json, parse_config, without_reader_grants
from budget_guidance.posture import build_posture
from budget_guidance.session_guidance import (
    MAX_GUIDANCE_BYTES, cue, render_guidance, startup_posture, write_session_guidance,
)
from test_external_reader import _reading

PLUGIN = Path(__file__).resolve().parents[1]
AT = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _config(tmp_path, monkeypatch, *, reading=None, external=False):
    path = tmp_path / "budget.json"
    adapter = (
        {"type": "external-command", "id": "report", "authority": 10, "source": "account-reader"}
        if external else
        {"type": "static", "id": "report", "authority": 10, "reading": reading or _reading()}
    )
    path.write_text(json.dumps({
        "schema": "copilot-extensions.budget-guidance-config", "version": 1,
        "adapters": [adapter],
    }))
    monkeypatch.setenv("BUDGET_GUIDANCE_CONFIG", str(path))
    return path


def _payload(tmp_path, **overrides):
    return {"sessionId": "test-session", "cwd": str(tmp_path), **overrides}


def _data(path):
    marker, data = path.read_text().split("\n", 1)
    assert marker.startswith("[owner: budget-guidance@")
    return json.loads(data)


def test_cue_reuses_status_posture_and_attribution(tmp_path, monkeypatch):
    path = _config(tmp_path, monkeypatch)
    expected = build_posture(without_reader_grants(parse_config(load_json(path)), AT), AT)
    written = write_session_guidance(_payload(tmp_path), home=tmp_path, at=AT)
    data = _data(written)
    assert data == cue(expected)
    assert data["calculated"]["remaining"] == "60"
    assert data["fields"]["consumption"]["selected"] == expected["fields"]["consumption"]["selected"]
    assert data["adapters"][0]["source"] == "account-reader"
    assert data["adapters"][0]["captured_at"] == "2026-01-01T00:00:00Z"
    assert data["adapters"][0]["freshness_seconds"] == 86400
    assert written.stat().st_size <= MAX_GUIDANCE_BYTES


@pytest.mark.parametrize("source", ["startup", "resume", "acp", "unknown-future-source"])
def test_resume_and_new_sources_replace_old_values(tmp_path, monkeypatch, source):
    path = _config(tmp_path, monkeypatch)
    written = write_session_guidance(_payload(tmp_path), home=tmp_path, at=AT)
    value = json.loads(path.read_text())
    value["adapters"][0]["reading"]["consumption"] = 61
    path.write_text(json.dumps(value))
    assert write_session_guidance(
        _payload(tmp_path, source=source), home=tmp_path, at=AT,
    ) == written
    assert _data(written)["calculated"]["remaining"] == "39"
    assert not list(written.parent.glob("*.tmp"))


@pytest.mark.parametrize("bad", ["", "../other", "/absolute", ".", "..", "x/y", "x\\y", "x" * 129, None])
def test_invalid_session_identifier_never_creates_state(tmp_path, bad):
    with pytest.raises(ModelError, match="identifier"):
        write_session_guidance(_payload(tmp_path, sessionId=bad), home=tmp_path, at=AT)
    assert not (tmp_path / ".copilot").exists()


@pytest.mark.parametrize("part", [
    ".copilot", ".copilot/session-state", ".copilot/session-state/test-session",
    ".copilot/session-state/test-session/instructions",
    ".copilot/session-state/test-session/instructions/budget-guidance",
])
def test_symlink_directory_refuses_escape(tmp_path, monkeypatch, part):
    _config(tmp_path, monkeypatch)
    target = tmp_path / part
    target.parent.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        target.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable on this host")
    with pytest.raises(ModelError, match="plain directory"):
        write_session_guidance(_payload(tmp_path), home=tmp_path, at=AT)
    assert list(outside.iterdir()) == []


@pytest.mark.guard
@pytest.mark.parametrize("part", [
    ".copilot", ".copilot/session-state", ".copilot/session-state/test-session",
    ".copilot/session-state/test-session/instructions",
    ".copilot/session-state/test-session/instructions/budget-guidance",
])
def test_reparse_directory_refuses_write_without_symlink_privileges(tmp_path, monkeypatch, part):
    import budget_guidance.session_guidance as module

    _config(tmp_path, monkeypatch)
    written = write_session_guidance(_payload(tmp_path), home=tmp_path, at=AT)
    previous = written.read_bytes()
    reparse = tmp_path / part
    original_lstat = Path.lstat
    monkeypatch.setattr(module.stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400, raising=False)

    def reparse_metadata(path, *args, **kwargs):
        metadata = original_lstat(path, *args, **kwargs)
        if path == reparse:
            return SimpleNamespace(st_mode=metadata.st_mode, st_file_attributes=0x410)
        return metadata

    monkeypatch.setattr(Path, "lstat", reparse_metadata)
    with pytest.raises(ModelError, match="plain directory"):
        write_session_guidance(_payload(tmp_path), home=tmp_path, at=AT)
    assert written.read_bytes() == previous
    assert not list(written.parent.glob("*.tmp"))


def test_atomic_failure_retains_previous_cue_and_removes_only_owned_temp(tmp_path, monkeypatch):
    import budget_guidance.session_guidance as module

    _config(tmp_path, monkeypatch)
    target = write_session_guidance(_payload(tmp_path), home=tmp_path, at=AT)
    previous = target.read_bytes()

    def fail_replace(*args):
        raise OSError("private-native-detail")

    monkeypatch.setattr(module.os, "replace", fail_replace)
    with pytest.raises(OSError):
        write_session_guidance(_payload(tmp_path), home=tmp_path, at=AT)
    assert target.read_bytes() == previous
    assert not list(target.parent.glob("*.tmp"))


@pytest.mark.parametrize("contents", [
    "secret-invalid-json", '{"secret":1e999999999999999999999}',
    '{"schema":1,"schema":2}', "x" * 65537,
], ids=["invalid-json", "overflow", "duplicate-key", "oversized"])
def test_invalid_config_is_explicit_redacted_unavailability(tmp_path, monkeypatch, contents):
    path = _config(tmp_path, monkeypatch)
    path.write_text(contents)
    written = write_session_guidance(_payload(tmp_path), home=tmp_path, at=AT)
    data = _data(written)
    assert data["availability"] == "unavailable"
    assert data["fields"] == {}
    assert data["calculated"] is None
    assert data["error"] == "configuration could not provide a valid budget posture"
    assert "secret" not in written.read_text()
    assert str(tmp_path) not in written.read_text()


@pytest.mark.parametrize("availability", [[], {}, ["available"]])
def test_malformed_reading_state_is_unavailable_not_a_raw_type_error(tmp_path, monkeypatch, availability):
    _config(tmp_path, monkeypatch, reading=_reading(availability=availability))
    data = _data(write_session_guidance(_payload(tmp_path), home=tmp_path, at=AT))
    assert data["availability"] == "unavailable"
    assert data["calculated"] is None
    assert data["error"] == "configuration could not provide a valid budget posture"


def test_unresolvable_operator_home_expression_is_explicit_unavailability(tmp_path, monkeypatch):
    monkeypatch.setenv("BUDGET_GUIDANCE_CONFIG", "~nonexistent-budget-test-user/config.json")
    data = _data(write_session_guidance(_payload(tmp_path), home=tmp_path, at=AT))
    assert data["availability"] == "unavailable"
    assert data["calculated"] is None


def test_missing_config_and_invalid_cwd_replace_old_posture(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch)
    target = write_session_guidance(_payload(tmp_path), home=tmp_path, at=AT)
    monkeypatch.setenv("BUDGET_GUIDANCE_CONFIG", str(tmp_path / "missing-secret.json"))
    write_session_guidance(_payload(tmp_path), home=tmp_path, at=AT)
    assert _data(target)["error"] == "configuration not found"
    assert _data(target)["calculated"] is None
    write_session_guidance(_payload(tmp_path, cwd="relative"), home=tmp_path, at=AT)
    assert _data(target)["error"] == "session directory is unavailable"


def test_external_reference_can_never_execute_at_startup(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("startup attempted process creation")

    _config(tmp_path, monkeypatch, external=True)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    data = _data(write_session_guidance(_payload(tmp_path), home=tmp_path, at=AT))
    assert data["calculated"] is None
    assert data["fields"] == {}
    assert data["adapters"][0]["availability"] == "unavailable"


def test_relative_operator_config_is_relative_to_session_cwd(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch)
    monkeypatch.setenv("BUDGET_GUIDANCE_CONFIG", "budget.json")
    assert startup_posture(tmp_path, AT)["calculated"]["remaining"] == "60"


def test_opaque_adapter_errors_do_not_enter_the_cue(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch, reading=_reading(
        availability="error", error="secret-adapter-detail",
        allowance=None, consumption=None, reset_at=None,
    ))
    data = _data(write_session_guidance(_payload(tmp_path), home=tmp_path, at=AT))
    assert data["adapters"][0]["availability"] == "error"
    assert "secret-adapter-detail" not in json.dumps(data)


@pytest.mark.guard
def test_utf8_and_token_budget_withholds_oversized_evidence_without_truncation(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch, reading=_reading(source="😺" * 2000))
    posture = startup_posture(tmp_path, AT)
    text = render_guidance(posture)
    assert len(text.encode("utf-8")) <= MAX_GUIDANCE_BYTES
    assert (len(text) + 3) // 4 <= 1024
    data = json.loads(text.split("\n", 1)[1])
    assert data["calculated"] is None
    assert data["fields"] == {}
    assert "byte budget" in data["error"]


@pytest.mark.parametrize("state", ["stale", "contradictory"])
def test_cue_preserves_warning_and_no_optimistic_freshness(tmp_path, monkeypatch, state):
    path = _config(tmp_path, monkeypatch)
    if state == "stale":
        at = datetime(2026, 1, 4, tzinfo=timezone.utc)
    else:
        value = json.loads(path.read_text())
        value["adapters"].append({
            "type": "static", "id": "other", "authority": 20,
            "reading": _reading(consumption=41),
        })
        path.write_text(json.dumps(value))
        at = AT
    data = cue(startup_posture(tmp_path, at))
    assert data["availability"] == state
    assert data["calculated"]["warning_band"] == state
    if state == "contradictory":
        assert data["fields"]["consumption"]["contradiction_count"] == 1


@pytest.mark.guard
def test_projection_and_hook_declarations_are_output_free_and_owner_scoped():
    declarations = json.loads((PLUGIN / "instruction-projections.json").read_text())
    assert {p["id"] for p in declarations["projections"]} == {
        "session-guidance", "posture-boundary",
    }
    for declaration in declarations["projections"]:
        assert (PLUGIN / declaration["template"]).is_file()
        assert declaration["destination"].startswith(".github/instructions/budget-guidance/")
    hooks = json.loads((PLUGIN / "hooks.json").read_text())
    assert len(hooks["hooks"]["sessionStart"]) == 2
    assert "write-session-guidance.sh" in hooks["hooks"]["sessionStart"][1]["bash"]
    assert "write-session-guidance.ps1" in hooks["hooks"]["sessionStart"][1]["powershell"]
    assert json.loads((PLUGIN / "session-context.json").read_text())["sessionStart"]["context"] == "none"
    pointer = (PLUGIN / "instructions/session-guidance.instructions.md").read_text()
    assert "as authoritative for this session" in pointer


@pytest.mark.parametrize("platform", ["python", "posix", "powershell"])
def test_real_payload_writer_and_wrappers_do_not_provision_or_emit_context(tmp_path, platform):
    shell = shutil.which("bash" if platform == "posix" else "pwsh")
    if platform != "python" and (not shell or (platform == "posix" and os.name == "nt")):
        pytest.skip(f"{platform} interpreter unavailable")
    env = {**os.environ, "HOME": str(tmp_path), "USERPROFILE": str(tmp_path)}
    env["COPILOT_PLUGIN_ROOT"] = str(PLUGIN)
    env["BUDGET_GUIDANCE_CONFIG"] = str(tmp_path / "not-configured.json")
    env["PYTHONPATH"] = "invalid-ambient-startup-path"
    script = PLUGIN / "scripts"
    argv = (
        [sys.executable, "-I", "-S", str(script / "write_session_guidance.py")]
        if platform == "python" else
        [shell, str(script / "write-session-guidance.sh")] if platform == "posix" else
        [shell, "-NoProfile", "-File", str(script / "write-session-guidance.ps1")]
    )
    for source in ("startup", "resume"):
        result = subprocess.run(
            argv, input=json.dumps(_payload(tmp_path, source=source)),
            text=True, capture_output=True, cwd=tmp_path, env=env, timeout=10,
            **no_window_kwargs(),
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == "{}"
        written = tmp_path / ".copilot/session-state/test-session/instructions/budget-guidance/session-guidance.instructions.md"
        assert _data(written)["error"] == "configuration not found"
    assert not (tmp_path / ".budget-guidance").exists()


def test_writer_failure_envelope_is_single_and_redacted(tmp_path):
    result = subprocess.run(
        [sys.executable, "-I", "-S", str(PLUGIN / "scripts/write_session_guidance.py")],
        input="secret-not-json", text=True, capture_output=True, cwd=tmp_path,
        timeout=10, **no_window_kwargs(),
    )
    assert result.returncode == 1
    assert result.stdout == "{}"
    assert result.stderr.strip() == "budget-guidance: session cue could not be written"


@pytest.mark.skipif(os.name == "nt", reason="POSIX one-shot writer deadline")
@pytest.mark.parametrize("stall", ["import", "main"])
def test_posix_writer_has_its_own_deadline_without_host_timeout(tmp_path, stall):
    shell = shutil.which("bash")
    if shell is None:
        pytest.skip("Bash unavailable")
    payload = tmp_path / "payload"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    for name in ("write_session_guidance.py", "write-session-guidance.sh"):
        shutil.copy2(PLUGIN / "scripts" / name, scripts)
    source = payload / "src/budget_guidance"
    source.mkdir(parents=True)
    (source / "__init__.py").write_text("")
    workload = (
        "import time\ntime.sleep(20)\ndef main():\n    return 0\n"
        if stall == "import" else
        "import time\ndef main():\n    time.sleep(20)\n    return 0\n"
    )
    (source / "session_guidance.py").write_text(workload)
    env = {**os.environ, "COPILOT_PLUGIN_ROOT": str(payload)}
    started = time.monotonic()
    result = subprocess.run(
        [shell, str(scripts / "write-session-guidance.sh")], input="{}",
        text=True, capture_output=True, env=env, timeout=8,
    )
    assert 5 <= time.monotonic() - started < 8
    assert result.returncode == 0
    assert result.stdout == "{}"
    assert "session cue could not be written" in result.stderr


@pytest.mark.skipif(os.name == "nt", reason="POSIX session wrapper")
def test_posix_wrapper_missing_writer_emits_one_empty_envelope(tmp_path):
    shell = shutil.which("bash")
    if shell is None:
        pytest.skip("Bash unavailable")
    result = subprocess.run(
        [shell, str(PLUGIN / "scripts/write-session-guidance.sh")],
        input="{}", text=True, capture_output=True,
        env={**os.environ, "COPILOT_PLUGIN_ROOT": str(tmp_path / "missing")},
        timeout=8, **no_window_kwargs(),
    )
    assert result.returncode == 0
    assert result.stdout == "{}"
    assert result.stderr.strip() == "budget-guidance: session cue writer or Python is unavailable"


@pytest.mark.skipif(os.name != "nt", reason="native Windows venv redirector resolution")
def test_windows_writer_resolves_venv_base_without_launching_redirector(tmp_path):
    shell = shutil.which("pwsh") or shutil.which("powershell.exe")
    assert shell, "native Windows writer coverage requires PowerShell"
    base = Path(sys._base_executable)
    assert base.is_file()
    scripts = tmp_path / "venv/Scripts"
    scripts.mkdir(parents=True)
    (scripts / "python3.exe").write_bytes(b"not-a-launchable-interpreter")
    (scripts.parent / "pyvenv.cfg").write_text(f"home = {base.parent}\n", encoding="utf-8")
    env = {
        **os.environ, "HOME": str(tmp_path), "USERPROFILE": str(tmp_path),
        "COPILOT_PLUGIN_ROOT": str(PLUGIN),
        "BUDGET_GUIDANCE_CONFIG": str(tmp_path / "missing.json"),
        "PATH": str(scripts) + os.pathsep + os.environ.get("PATH", ""),
    }
    result = subprocess.run(
        [shell, "-NoProfile", "-File", str(PLUGIN / "scripts/write-session-guidance.ps1")],
        input=json.dumps(_payload(tmp_path)), text=True, capture_output=True, env=env,
        timeout=10, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "{}"
    target = tmp_path / ".copilot/session-state/test-session/instructions/budget-guidance/session-guidance.instructions.md"
    assert _data(target)["error"] == "configuration not found"


def test_powershell_writer_bounds_maximum_stdin_delivery_to_stalled_child(tmp_path):
    shell = shutil.which("pwsh") or (shutil.which("powershell.exe") if os.name == "nt" else None)
    if shell is None:
        pytest.skip("PowerShell unavailable")
    payload = tmp_path / "payload"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(PLUGIN / "scripts/write-session-guidance.ps1", scripts)
    (scripts / "write_session_guidance.py").write_text(
        "import time\ntime.sleep(20)\n", encoding="utf-8",
    )
    env = {**os.environ, "COPILOT_PLUGIN_ROOT": str(payload)}
    started = time.monotonic()
    result = subprocess.run(
        [shell, "-NoProfile", "-File", str(scripts / "write-session-guidance.ps1")],
        input="x" * 65536, text=True, capture_output=True, env=env,
        timeout=9, **no_window_kwargs(),
    )
    assert 5 <= time.monotonic() - started < 9
    assert result.returncode == 0
    assert result.stdout == "{}"
    assert result.stderr.strip() == "budget-guidance: session cue could not be written"
