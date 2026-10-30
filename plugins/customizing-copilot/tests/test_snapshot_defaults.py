from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).parents[1] / "skills" / "capturing-client-requests" / "scripts"
sys.path.insert(0, str(SCRIPTS))
from snapshot_preferences import PreferenceError, resolve_preferences

spec = importlib.util.spec_from_file_location("snapshot_capture_defaults", SCRIPTS / "capture-client-request.py")
capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture)
pytestmark = pytest.mark.guard


def settings(tmp_path, value):
    root = tmp_path / "operator-config"
    root.mkdir()
    (root / "settings.json").write_text(json.dumps(value), encoding="utf-8-sig")
    return {"COPILOT_HOME": str(root)}


def test_current_preferences_and_per_field_overrides(tmp_path):
    environment = settings(tmp_path, {
        "model": "configured-large", "effortLevel": "medium", "contextTier": "long_context",
    })
    current = resolve_preferences(environ=environment)
    assert (current["model"], current["reasoning_effort"], current["context"]) == (
        "configured-large", "medium", "long_context",
    )
    overridden = resolve_preferences(model="explicit-model", context="default", environ=environment)
    assert (overridden["model"], overridden["reasoning_effort"], overridden["context"]) == (
        "explicit-model", "medium", "default",
    )
    assert overridden["sources"]["model"] == "explicit"
    environment["COPILOT_MODEL"] = "profile-model"
    assert resolve_preferences(environ=environment)["model"] == "profile-model"
    assert resolve_preferences(model="explicit", environ=environment)["model"] == "explicit"


def test_no_small_model_fallback_and_invalid_preferences(tmp_path):
    environment = settings(tmp_path, {})
    with pytest.raises(PreferenceError, match="operator_model_unavailable"):
        resolve_preferences(environ=environment)
    root = Path(environment["COPILOT_HOME"])
    (root / "settings.json").write_text('{"model":42}', encoding="utf-8")
    with pytest.raises(PreferenceError, match="invalid_model"):
        resolve_preferences(environ=environment)
    (root / "settings.json").write_text('{"model":"one","model":"two"}', encoding="utf-8")
    with pytest.raises(PreferenceError, match="unreadable"):
        resolve_preferences(environ=environment)


def test_relative_config_root_is_resolved_for_the_selected_harness(tmp_path):
    settings(tmp_path, {"model": "selected-harness-model"})
    assert resolve_preferences(
        environ={"COPILOT_HOME": "operator-config"}, working_directory=tmp_path,
    )["model"] == "selected-harness-model"


def test_native_settings_line_comments_preserve_slashes_in_strings(tmp_path):
    environment = settings(tmp_path, {})
    (Path(environment["COPILOT_HOME"]) / "settings.json").write_text(
        '// operator settings\n{\n"model":"family//variant", // selected model\n'
        '"effortLevel":"medium", "url":"https://example.com"\n}\n',
        encoding="utf-8-sig",
    )
    assert resolve_preferences(environ=environment)["model"] == "family//variant"


def test_custom_provider_identity_is_not_replaced_by_an_ordinary_cloud_default(tmp_path):
    environment = settings(tmp_path, {"model": "ordinary-cloud"})
    environment["COPILOT_PROVIDER_BASE_URL"] = "http://127.0.0.1:12345/v1"
    with pytest.raises(PreferenceError, match="provider_model_unavailable"):
        resolve_preferences(environ=environment)
    environment["COPILOT_PROVIDER_MODEL_ID"] = "operator-local-model"
    assert resolve_preferences(environ=environment)["model"] == "operator-local-model"
    environment["COPILOT_MODEL"] = "explicit-profile-model"
    assert resolve_preferences(environ=environment)["model"] == "explicit-profile-model"
    assert resolve_preferences(model="snapshot-override", environ=environment)["model"] == "snapshot-override"


def test_complete_explicit_launch_does_not_depend_on_an_invalid_preferences_file(tmp_path):
    environment = settings(tmp_path, {})
    (Path(environment["COPILOT_HOME"]) / "settings.json").write_text("invalid", encoding="utf-8")
    result = resolve_preferences(
        model="explicit-model", reasoning_effort="medium", context="long_context", environ=environment,
    )
    assert result["sources"] == {"model": "explicit", "reasoning_effort": "explicit", "context": "explicit"}


def test_capture_defaults_preferences_timeout_and_companion(tmp_path, monkeypatch, capsys):
    seen = []
    preferences = {
        "model": "current-model", "reasoning_effort": "medium", "context": "long_context",
        "sources": {"model": "operator_settings"},
    }
    monkeypatch.setattr(capture, "resolve_preferences", lambda **_: preferences)
    monkeypatch.setattr(capture.shutil, "which", lambda _: str(tmp_path / "client.exe"))
    raw = b'{"model":"current-model","messages":[]}'

    def fake_capture(command, directory, output, **kwargs):
        seen.append(kwargs)
        output.write_bytes(raw)
        return {"status": "captured", "captured_bytes": len(raw), "capture_path": str(output)}

    monkeypatch.setattr(capture, "capture", fake_capture)
    monkeypatch.setattr(capture, "analyze_file", lambda *_args, **_kwargs: {
        "schema": "copilot-extensions.client-request-analysis", "version": 1,
        "instruction_files": [],
        "warnings": [],
    })
    output = tmp_path / "request.json"
    assert capture.main(["--working-directory", str(tmp_path), "--output", str(output)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert seen[0]["timeout"] == 300
    assert seen[0]["model"] == "current-model"
    assert seen[0]["reasoning_effort"] == "medium" and seen[0]["context"] == "long_context"
    assert output.read_bytes() == raw
    report = Path(result["analysis_path"])
    assert json.loads(report.read_bytes())["version"] == 1
    assert report.name == "request.analysis.json"
    assert "messages" not in result
    second = tmp_path / "other.json"
    assert capture.main([
        "--working-directory", str(tmp_path), "--output", str(second),
        "--timeout-seconds", "42",
    ]) == 0
    assert seen[-1]["timeout"] == 42
    capsys.readouterr()


def test_existing_report_prevents_launch(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(capture, "resolve_preferences", lambda **_: {
        "model": "current", "reasoning_effort": None, "context": None,
    })
    monkeypatch.setattr(capture.shutil, "which", lambda _: str(tmp_path / "client.exe"))
    monkeypatch.setattr(capture, "capture", lambda *_a, **_k: pytest.fail("must not launch"))
    report = tmp_path / "request.analysis.json"
    report.write_bytes(b"existing-report")
    assert capture.main([
        "--working-directory", str(tmp_path), "--output", str(tmp_path / "request.json"),
    ]) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "FileExistsError"
    assert report.read_bytes() == b"existing-report"
    assert not (tmp_path / "request.json").exists()


def test_analysis_failure_retains_complete_raw_request(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(capture, "resolve_preferences", lambda **_: {
        "model": "current", "reasoning_effort": None, "context": None,
    })
    monkeypatch.setattr(capture.shutil, "which", lambda _: str(tmp_path / "client.exe"))
    raw = b"SYNTHETIC_PRIVATE_SENTINEL"

    def fake_capture(_command, _directory, output, **_kwargs):
        output.write_bytes(raw)
        return {"status": "captured", "captured_bytes": len(raw), "capture_path": str(output)}

    def fail_analysis(*_args, **_kwargs):
        raise ValueError("SYNTHETIC_PRIVATE_SENTINEL")

    monkeypatch.setattr(capture, "capture", fake_capture)
    monkeypatch.setattr(capture, "analyze_file", fail_analysis)
    output = tmp_path / "request.json"
    assert capture.main(["--working-directory", str(tmp_path), "--output", str(output)]) == 1
    printed = capsys.readouterr().out
    assert json.loads(printed)["error"] == "analysis_ValueError"
    assert "SYNTHETIC_PRIVATE_SENTINEL" not in printed
    assert output.read_bytes() == raw
    assert not output.with_name("request.analysis.json").exists()
