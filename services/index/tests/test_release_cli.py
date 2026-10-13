from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_index_service.__main__ import main


@pytest.mark.parametrize("command", ["describe", "verify"])
def test_release_cli_needs_no_host_config_or_core(command, tmp_path, monkeypatch, capsys):
    calls = []
    descriptor = {"schema": "fixture", "source_commit": "a" * 40}

    def build(bundle, *, source_commit):
        calls.append((bundle, source_commit))
        return descriptor

    def verify(path, *, expected_source_commit):
        calls.append((path, expected_source_commit))
        return descriptor

    monkeypatch.setitem(sys.modules, "agent_index_service.release", SimpleNamespace(
        build_descriptor=build, verify_descriptor=verify,
    ))
    path = tmp_path / ("bundle" if command == "describe" else "release.json")
    option = "--bundle" if command == "describe" else "--descriptor"
    commit = "--source-commit" if command == "describe" else "--expected-source-commit"
    assert main(["release", command, option, str(path), commit, "a" * 40]) == 0
    assert calls == [(path, "a" * 40)]
    payload = json.loads(capsys.readouterr().out)
    assert payload == (descriptor if command == "describe" else {
        "valid": True, "release": descriptor,
    })
    assert not path.exists()


def test_release_verification_error_is_not_success(tmp_path, monkeypatch, capsys):
    def verify(path: Path, *, expected_source_commit: str):
        raise ValueError("artifact digest mismatch")

    monkeypatch.setitem(sys.modules, "agent_index_service.release", SimpleNamespace(
        build_descriptor=None, verify_descriptor=verify,
    ))
    assert main([
        "release", "verify", "--descriptor", str(tmp_path / "release.json"),
        "--expected-source-commit", "a" * 40,
    ]) == 2
    result = capsys.readouterr()
    assert result.out == ""
    assert "artifact digest mismatch" in result.err


def test_release_verify_requires_independent_commit_pin(tmp_path):
    with pytest.raises(SystemExit) as exc:
        main(["release", "verify", "--descriptor", str(tmp_path / "release.json")])
    assert exc.value.code == 2
