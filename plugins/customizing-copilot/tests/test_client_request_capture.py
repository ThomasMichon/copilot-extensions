from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys

import pytest

SCRIPT = (
    Path(__file__).parents[1] / "skills" / "capturing-client-requests"
    / "scripts" / "capture-client-request.py"
)
spec = importlib.util.spec_from_file_location("client_request_capture", SCRIPT)
capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture)

pytestmark = pytest.mark.guard


def test_real_authorization_and_scoped_environment(monkeypatch):
    monkeypatch.setenv("COPILOT_PROVIDER_BASE_URL", "https://example.com/shared")
    monkeypatch.setenv("COPILOT_PROVIDER_UNKNOWN_OPTION", "must-not-survive")
    monkeypatch.setenv("GH_TOKEN", "synthetic-ambient-credential")
    for name in ("COPILOT_GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN"):
        monkeypatch.setenv(name, "synthetic-ambient-credential")
    monkeypatch.setenv("HTTPS_PROXY", "https://example.com/proxy")
    monkeypatch.setenv("COPILOT_CUSTOM_INSTRUCTIONS_DIRS", "synthetic-instructions")
    environment = capture.child_environment("synthetic-key", 12345, "synthetic-model")
    assert capture.authorization("synthetic-key").split() == ["Bearer", "synthetic-key"]
    assert environment["COPILOT_PROVIDER_BASE_URL"] == "http://127.0.0.1:12345/v1"
    assert environment["COPILOT_PROVIDER_API_KEY"] == "synthetic-key"
    assert environment["COPILOT_CUSTOM_INSTRUCTIONS_DIRS"] == "synthetic-instructions"
    assert environment["COPILOT_OFFLINE"] == "true"
    assert environment["NO_PROXY"] == "127.0.0.1"
    assert not {
        "GH_TOKEN", "COPILOT_GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN",
        "HTTPS_PROXY", "COPILOT_PROVIDER_UNKNOWN_OPTION",
    } & environment.keys()
    assert os.environ["COPILOT_PROVIDER_BASE_URL"] == "https://example.com/shared"


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
def test_invalid_timeout_never_reserves_output(tmp_path, timeout):
    target = tmp_path / "request.json"
    with pytest.raises(ValueError, match="finite and positive"):
        capture.capture([sys.executable], tmp_path, target, prompt="synthetic", model="m", timeout=timeout)
    assert not target.exists()


def test_existing_output_is_never_overwritten(tmp_path):
    target = tmp_path / "request.json"
    target.write_bytes(b"existing-private-output")
    with pytest.raises(FileExistsError):
        capture.capture([sys.executable], tmp_path, target, prompt="synthetic", model="m", timeout=1)
    assert target.read_bytes() == b"existing-private-output"


def test_missing_client_reports_failure_without_prompt(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(capture.shutil, "which", lambda _: str(tmp_path / "absent.exe"))

    def missing_client(*_args, **_kwargs):
        raise FileNotFoundError("SYNTHETIC_PRIVATE_SENTINEL in potentially sensitive exception")

    monkeypatch.setattr(capture, "capture", missing_client)
    assert capture.main([
        "--working-directory", str(tmp_path), "--output", str(tmp_path / "request.json"),
        "--prompt", "SYNTHETIC_PRIVATE_SENTINEL",
    ]) == 1
    printed = capsys.readouterr().out
    assert "FileNotFoundError" in printed
    assert "SYNTHETIC_PRIVATE_SENTINEL" not in printed
    assert not (tmp_path / "request.json").exists()


def test_relative_client_is_pinned_before_entering_harness(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(capture.shutil, "which", lambda _: "selected-client.exe")
    harness = tmp_path / "different-harness"
    seen = []

    def selected_client(command, working_directory, *_args, **_kwargs):
        seen.append((command, working_directory))
        return {"status": "captured"}

    monkeypatch.setattr(capture, "capture", selected_client)
    assert capture.main([
        "--working-directory", str(harness), "--output", str(tmp_path / "request.json"),
    ]) == 0
    assert seen == [([str(tmp_path / "selected-client.exe")], harness)]
    capsys.readouterr()
