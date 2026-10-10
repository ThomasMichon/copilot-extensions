from __future__ import annotations

import json
import sys
from types import SimpleNamespace

from agent_index_service.__main__ import main


def test_stage_cli_builds_only_explicit_candidate_request(tmp_path, monkeypatch, capsys):
    calls = []

    def stage(descriptor, **kwargs):
        calls.append((descriptor, kwargs))
        return {"state": "staged"}

    monkeypatch.setitem(sys.modules, "agent_index_service.staging", SimpleNamespace(
        NativeBuildConfig=lambda **kwargs: kwargs,
        stage_candidate=stage,
        inspect_candidates=None,
    ))
    paths = {
        name: tmp_path / name
        for name in ("config", "install-root", "descriptor", "python", "uv",
                     "third-party-lock", "uv-config")
    }
    arguments = ["stage", "--expected-source-commit", "a" * 40, "--timeout", "30"]
    for name, path in paths.items():
        arguments.extend([f"--{name}", str(path)])
    assert main(arguments) == 0
    descriptor, request = calls[0]
    assert descriptor == paths["descriptor"]
    assert request["expected_source_commit"] == "a" * 40
    assert request["install_root"] == paths["install-root"]
    assert request["host_config_path"] == paths["config"]
    assert request["build"] == {
        "python": paths["python"], "uv": paths["uv"],
        "third_party_lock": paths["third-party-lock"], "uv_config": paths["uv-config"],
        "timeout_seconds": 30.0,
    }
    assert json.loads(capsys.readouterr().out) == {"state": "staged"}
    assert not any(path.exists() for path in paths.values())


def test_candidate_inspection_is_not_host_status_or_runtime_selection(tmp_path, monkeypatch, capsys):
    calls = []

    def inspect(root, **kwargs):
        calls.append((root, kwargs))
        return {"candidates": []}

    monkeypatch.setitem(sys.modules, "agent_index_service.staging", SimpleNamespace(
        NativeBuildConfig=None, stage_candidate=None, inspect_candidates=inspect,
    ))
    root, config = tmp_path / "install", tmp_path / "host.yaml"
    assert main(["candidates", "--install-root", str(root), "--config", str(config)]) == 0
    assert calls == [(root, {"host_config_path": config})]
    assert json.loads(capsys.readouterr().out) == {"candidates": []}
    assert not root.exists()


def test_staging_failure_is_explicit_not_success(tmp_path, monkeypatch, capsys):
    def inspect(*args, **kwargs):
        raise ValueError("incomplete candidate")

    monkeypatch.setitem(sys.modules, "agent_index_service.staging", SimpleNamespace(
        NativeBuildConfig=None, stage_candidate=None, inspect_candidates=inspect,
    ))
    assert main([
        "candidates", "--install-root", str(tmp_path / "install"),
        "--config", str(tmp_path / "host.yaml"),
    ]) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert "incomplete candidate" in output.err
