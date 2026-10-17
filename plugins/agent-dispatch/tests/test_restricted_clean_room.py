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
        "native": "native-1", "owner_native": "native-1", "bridge_native": "native-1",
        "prompts": 1, "rotated": True, "terminal_credential_status": 403,
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
        evidence = Path(kwargs["env"]["AGENT_DISPATCH_E2E_EVIDENCE_DIR"])
        assert not evidence.exists()
        _proof(evidence, outcome)
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


@pytest.mark.parametrize("failure", [
    "nonzero", "skip", "malformed", "revocation", "cleanup", "refusals", "native", "rotation",
])
def test_clean_room_fails_closed_and_never_resamples(lane, tmp_path, monkeypatch, failure):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        root = Path(kwargs["env"]["AGENT_DISPATCH_E2E_EVIDENCE_DIR"])
        if failure != "skip":
            proof = _proof(root, "complete")
            if failure == "revocation":
                proof["terminal_credential_status"] = 200
            elif failure == "refusals":
                proof["refused"] = []
            elif failure == "native":
                proof["bridge_native"] = "replacement-native"
            elif failure == "rotation":
                proof["rotated"] = False
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


@pytest.mark.parametrize("failure", [
    None, "substring-only", "remaining", "inventory", "malformed", "nonstring", "timeout",
])
def test_docker_cleanup_confirms_absence_and_attempts_every_resource(monkeypatch, failure):
    script = Path(__file__).with_name("test_restricted_supervisor_docker.py")
    spec = importlib.util.spec_from_file_location("restricted_docker_case", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        inventory = "ls" in argv
        name = argv[argv.index("--filter") + 1].removeprefix("name=") if inventory else argv[-1]
        if not inventory:
            if name == "worker" and failure == "timeout":
                raise module.subprocess.TimeoutExpired(argv, kwargs["timeout"])
            return SimpleNamespace(returncode=1, stderr="arbitrary localized removal diagnostic")
        if name == "worker":
            if failure == "timeout":
                raise module.subprocess.TimeoutExpired(argv, kwargs["timeout"])
            if failure == "inventory":
                return SimpleNamespace(returncode=1, stderr="daemon unavailable", stdout="")
            if failure == "remaining":
                return SimpleNamespace(returncode=0, stderr="", stdout='"worker"\n')
            if failure == "malformed":
                return SimpleNamespace(returncode=0, stderr="", stdout="{")
            if failure == "nonstring":
                return SimpleNamespace(returncode=0, stderr="", stdout="null")
            if failure == "substring-only":
                return SimpleNamespace(returncode=0, stderr="", stdout='"worker-suffix"\n')
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(module.subprocess, "run", run)
    if failure not in (None, "substring-only"):
        with pytest.raises(AssertionError, match="cleanup|inventory|still exists"):
            module._cleanup_docker(("worker", "central"), ("internal", "management"))
    else:
        module._cleanup_docker(("worker", "central"), ("internal", "management"))
    assert {argv[-1] for argv in calls if "ls" not in argv} == {
        "worker", "central", "internal", "management",
    }
