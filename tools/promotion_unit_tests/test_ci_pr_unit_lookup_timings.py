"""Lookup attribution uses the real packet verifier without provider or child actors."""

from datetime import datetime
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Protocol

import pytest

import ci_pr_unit_reuse as reuse
from candidate_qualification_receipt import PACKET_FILES
from promotion_unit_tests.test_ci_pr_unit_reuse import case as case
from promotion_unit_tests.test_ci_pr_unit_reuse import packet_case as packet_case
from promotion_unit_tests.test_packet import SOURCE


class PacketCase(Protocol):
    directory: Path
    root: Path
    manifest: reuse.JsonObject
    environment: dict[str, str]
    event: reuse.JsonObject
    runtime: reuse.JsonObject
    identity: reuse.JsonObject
    run: reuse.JsonObject
    job: reuse.JsonObject
    now: datetime
    calls: list[tuple[str, bool]]
    raw: bytes
    artifact: reuse.JsonObject


class Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


class BufferedOutput(io.StringIO):
    def __init__(self) -> None:
        super().__init__()
        self.visible = ""

    def flush(self) -> None:
        self.visible = self.getvalue()


def read_provider(case: PacketCase, path: str, binary: bool = False) -> reuse.JsonObject | bytes:
    case.calls.append((path, binary))
    if "workflows/ci.yml/runs?" in path:
        return {"total_count": 1, "workflow_runs": [case.run]}
    if path.endswith("runs/20/artifacts?per_page=100"):
        return {"total_count": 1, "artifacts": [case.artifact]}
    if path.endswith("runs/20/attempts/2/jobs?per_page=100"):
        return {"total_count": 1, "jobs": [case.job]}
    assert path.endswith("artifacts/60/zip") and binary
    return case.raw


def test_lookup_attributes_provider_and_actual_packet_work_without_extra_requests(
    case: PacketCase, monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = Clock()
    timings = reuse._LookupTimings(clock)
    original = reuse.verify_download

    def verify(
        raw: bytes, artifact: reuse.JsonObject, identity: reuse.JsonObject,
        manifest: reuse.JsonObject, root: Path,
    ) -> reuse.JsonObject:
        result = original(raw, artifact, identity, manifest, root)
        clock.value += 0.25
        return result

    def read(path: str, binary: bool = False) -> reuse.JsonObject | bytes:
        clock.value += 0.125
        return read_provider(case, path, binary)

    monkeypatch.setattr(reuse, "verify_download", verify)
    result = reuse.resolve(
        case.identity, case.manifest, case.root, 100, read, case.now, timings.phase,
    )
    assert result["mode"] == "reused"
    assert len(case.calls) == 4 and case.calls[-1][1]
    report = timings.snapshot()
    assert report["elapsed_ms"] == 750
    assert report["phases"] == [
        {"phase": "run_inventory", "elapsed_ms": 125, "outcome": "success"},
        {"phase": "artifact_inventory", "elapsed_ms": 125, "outcome": "success"},
        {"phase": "job_inventory", "elapsed_ms": 125, "outcome": "success"},
        {"phase": "producer_qualification", "elapsed_ms": 0, "outcome": "success"},
        {"phase": "artifact_download", "elapsed_ms": 125, "outcome": "success"},
        {"phase": "packet_verification", "elapsed_ms": 250, "outcome": "success"},
    ]
    rendered = json.dumps(report)
    assert case.identity["repository"] not in rendered
    assert str(case.root) not in rendered


def test_failed_provider_phase_is_retained_without_consuming_another_request(
    case: PacketCase,
) -> None:
    clock = Clock()
    timings = reuse._LookupTimings(clock)

    def read(path: str, binary: bool = False) -> reuse.JsonObject | bytes:
        clock.value += 0.25
        if "/artifacts?" in path:
            case.calls.append((path, binary))
            raise subprocess.TimeoutExpired(["gh", "api", "private-target"], timeout=0.25)
        return read_provider(case, path, binary)

    with pytest.raises(subprocess.TimeoutExpired):
        reuse.resolve(
            case.identity, case.manifest, case.root, 100, read, case.now, timings.phase,
        )
    report = timings.snapshot()
    assert len(case.calls) == 2
    assert report["phases"] == [
        {"phase": "run_inventory", "elapsed_ms": 250, "outcome": "success"},
        {"phase": "artifact_inventory", "elapsed_ms": 250, "outcome": "error"},
    ]
    assert "private-target" not in json.dumps(report)


def test_expired_lookup_emits_attribution_and_keeps_the_existing_fresh_fallback(
    case: PacketCase, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (case.root / "tools/promotion-unit-manifest.json").write_text(json.dumps(case.manifest))
    event, output, destination = tmp_path / "event", tmp_path / "outputs", tmp_path / "result"
    event.write_text(json.dumps(case.event))
    environment = {
        **case.environment, "GITHUB_EVENT_PATH": str(event), "GITHUB_OUTPUT": str(output),
        "GITHUB_RUN_ID": "100", "GITHUB_RUN_ATTEMPT": "1", "GH_TOKEN": "synthetic-credential",
    }
    calls: list[list[str]] = []
    stream = BufferedOutput()

    def process(arguments: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append(arguments)
        assert arguments[0] != "gh", "expired lookup must not issue a provider request"
        if arguments[0] == "git":
            body = (SOURCE + "\n").encode() if "rev-parse" in arguments else b""
            return subprocess.CompletedProcess(arguments, 0, stdout=body, stderr=b"")
        assert arguments[1] == str(case.root / "tools/run-promotion-unit-tests.py")
        assert '"pr_unit_lookup":' in stream.visible, "diagnostic must be visible before fallback"
        assert kwargs["env"] == reuse.unit_environment(environment)
        destination.mkdir()
        for name in PACKET_FILES:
            (destination / name).write_bytes((case.directory / name).read_bytes())
        return subprocess.CompletedProcess(arguments, 0)

    monkeypatch.setattr(reuse, "ROOT", case.root)
    monkeypatch.setattr(os, "environ", environment)
    monkeypatch.setattr(reuse, "_process", process)
    monkeypatch.setattr(reuse, "runtime_identity", lambda: case.runtime)
    monkeypatch.setattr(reuse, "MAX_LOOKUP_SECONDS", -1)
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(sys, "argv", [
        "ci_pr_unit_reuse.py", "--source-sha", SOURCE, "--output-dir", str(destination),
    ])
    assert reuse.main() == 0
    captured = stream.getvalue()
    reports = [
        json.loads(line)["pr_unit_lookup"] for line in captured.splitlines()
        if line.startswith('{"pr_unit_lookup":')
    ]
    assert len(reports) == 1
    assert reports[0]["phases"][0]["phase"] == "run_inventory"
    assert reports[0]["phases"][0]["outcome"] == "error"
    assert "synthetic-credential" not in captured
    assert len([arguments for arguments in calls if arguments[0] != "git"]) == 1
    outputs = output.read_text()
    assert "mode=executed\n" in outputs
    assert "reason=provider-lookup-budget-exhausted\n" in outputs
    assert "origin_run=100\n" in outputs
    assert "pr_unit_lookup" not in outputs
