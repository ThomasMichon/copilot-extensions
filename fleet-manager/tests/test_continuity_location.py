import hashlib
import json
from pathlib import Path

import pytest

from fleet_manager.__main__ import main


def test_reconnect_is_discoverable_without_touching_enrollment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert main(["capabilities"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["schema"] == "fleet-manager.capabilities" and result["version"] == 1
    assert "repository-continuity-reconnect-v1" in result["capabilities"]
    assert not list(tmp_path.iterdir())


def test_legacy_location_without_creating_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    original = json.dumps(["https://example.invalid/team/repo", "harness-a"], separators=(",", ":"))
    expected = tmp_path / (".fleet-continuity-" + hashlib.sha256(original.encode()).hexdigest())
    assert main(["continuity-state-path", "--repository", "https://EXAMPLE.INVALID:443/team/repo/",
                 "--harness", "harness-a"]) == 0
    assert capsys.readouterr().out.strip() == str(expected)
    assert not list(tmp_path.iterdir())


def test_invalid_repository_produces_no_state_path(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["continuity-state-path", "--repository", "https://user@example.invalid/repo",
                 "--harness", "harness-a"]) == 1
    captured = capsys.readouterr()
    assert not captured.out and "user@" not in captured.err
