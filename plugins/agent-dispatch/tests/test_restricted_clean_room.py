"""The host-driven report must not accept skipped or incomplete Docker proof."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def lane():
    script = Path(__file__).resolve().parents[3] / "tools/clean-room/restricted-dispatch/run.py"
    spec = importlib.util.spec_from_file_location("restricted_clean_room", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _proof(root, outcome):
    root.mkdir()
    proof = {
        "outcome": outcome, "status": "completed" if outcome == "complete" else "abandoned",
        "native_preserved": True, "prompts": 1, "rotated": True,
        "terminal_credential_refused": True,
        "refused": [{"status": 403} for _ in range(5)],
    }
    (root / "proof.json").write_text(json.dumps(proof), encoding="utf-8")
    (root / "stages.jsonl").write_text(
        '{"stage":"acceptance","event":"complete"}\n'
        '{"stage":"cleanup","event":"complete"}\n', encoding="utf-8",
    )
    return proof


def test_clean_room_retains_two_independent_cases_and_original_limits(lane, tmp_path, monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        outcome = argv[argv.index("-k") + 1]
        _proof(Path(kwargs["env"]["AGENT_DISPATCH_E2E_EVIDENCE_DIR"]), outcome)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(lane.subprocess, "run", run)
    report = lane.run(
        tmp_path, central_image="candidate", bridge_python=Path("bridge"), provider_python=Path("provider"),
    )
    assert report["result"] == "pass" and report["jams"] == []
    assert [row["case"] for row in report["stages"]] == ["complete", "abandon"]
    for argv, kwargs in calls:
        assert argv[argv.index("--test-timeout") + 1] == "300"
        assert argv[argv.index("--timeout") + 1] == "360"
        assert argv[argv.index("--plugin-timeout") + 1] == "360"
        assert kwargs["timeout"] == 360
        assert "--allow-host-state" not in argv
    assert calls[0][1]["env"]["AGENT_DISPATCH_E2E_EVIDENCE_DIR"] != (
        calls[1][1]["env"]["AGENT_DISPATCH_E2E_EVIDENCE_DIR"]
    )
    assert json.loads((tmp_path / "cr-report.json").read_text())["result"] == "pass"


@pytest.mark.parametrize("failure", ["nonzero", "skip", "malformed", "revocation", "cleanup", "refusals"])
def test_clean_room_fails_closed_and_never_resamples(lane, tmp_path, monkeypatch, failure):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        root = Path(kwargs["env"]["AGENT_DISPATCH_E2E_EVIDENCE_DIR"])
        if failure != "skip":
            proof = _proof(root, "complete")
            if failure == "revocation":
                proof["terminal_credential_refused"] = False
            elif failure == "refusals":
                proof["refused"] = []
            (root / "proof.json").write_text(
                "{" if failure == "malformed" else json.dumps(proof), encoding="utf-8",
            )
            if failure == "cleanup":
                (root / "stages.jsonl").write_text(
                    '{"stage":"acceptance","event":"complete"}\n'
                    '{"stage":"cleanup","event":"start"}\n', encoding="utf-8",
                )
        return SimpleNamespace(returncode=1 if failure == "nonzero" else 0)

    monkeypatch.setattr(lane.subprocess, "run", run)
    report = lane.run(
        tmp_path, central_image="candidate", bridge_python=Path("bridge"), provider_python=Path("provider"),
    )
    assert report["result"] == "fail"
    assert len(calls) == 1 and len(report["stages"]) == 1
    assert report["jams"][0]["evidence"] == "complete/runner.log"


@pytest.mark.parametrize("failure", [None, "remove", "inspect", "timeout"])
def test_docker_cleanup_confirms_absence_and_attempts_every_resource(monkeypatch, failure):
    script = Path(__file__).with_name("test_restricted_supervisor_docker.py")
    spec = importlib.util.spec_from_file_location("restricted_docker_case", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        kind = "network" if argv[1] == "network" else "container"
        name = argv[-1]
        missing = f"No such container: {name}" if kind == "container" else f"network {name} not found"
        if name == "worker" and failure:
            if failure == "timeout":
                raise module.subprocess.TimeoutExpired(argv, kwargs["timeout"])
            if failure == "remove" and "inspect" not in argv:
                return SimpleNamespace(returncode=1, stderr="access denied")
            if failure == "inspect" and "inspect" in argv:
                return SimpleNamespace(returncode=0, stderr="")
        return SimpleNamespace(returncode=1, stderr=missing)

    monkeypatch.setattr(module.subprocess, "run", run)
    if failure:
        with pytest.raises(AssertionError, match="cleanup|absence|removal"):
            module._cleanup_docker(("worker", "central"), ("internal", "management"))
    else:
        module._cleanup_docker(("worker", "central"), ("internal", "management"))
    assert {argv[-1] for argv in calls} == {"worker", "central", "internal", "management"}
