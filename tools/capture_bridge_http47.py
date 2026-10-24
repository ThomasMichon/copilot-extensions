#!/usr/bin/env python3
"""Capture HTTP-47 model-free observations from genuine committed source."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import xml.etree.ElementTree as ET
from unittest.mock import patch

import bridge_http47_evidence as evidence

ROOT = Path(__file__).resolve().parents[1]
REQUEST_ENV = "BRIDGE_HTTP47_CAPTURE_REQUEST"
REQUEST_SHA_ENV = "BRIDGE_HTTP47_CAPTURE_REQUEST_SHA256"
CASE_NAME = "http47-cold-drain-cases-v1"


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def expected_input(root: Path) -> dict:
    value = json.loads(evidence.safe(root, evidence.INPUT).read_bytes())
    if (
        value.get("schema") != CASE_NAME
        or value.get("generation") != 47
        or set(value) != {"schema", "generation", "requests", "expected"}
    ):
        raise ValueError("invalid independent HTTP-47 input vector")
    return value


def verify_junit(path: Path) -> dict[str, int]:
    counts = {}
    for case in ET.parse(path).getroot().iter("testcase"):
        name = case.attrib.get("name", "").split("[", 1)[0]
        if name not in evidence.PROBES or any(
            case.find(tag) is not None for tag in ("failure", "error", "skipped")
        ):
            raise ValueError(
                "capture probe portfolio failed, skipped or escaped its selection"
            )
        counts[name] = counts.get(name, 0) + 1
    if counts != evidence.PROBES:
        raise ValueError("capture probe portfolio is incomplete or duplicated")
    return counts


def run_probes(root: Path, proof: dict, expected: dict) -> dict:
    """Run actual ASGI/owned-process probes through the existing bounded runner."""
    from agent_procutil.owned_process import run_caller_owned

    parent = evidence.safe(root, ".test-venvs", directory=True)
    parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="http47-capture-", dir=parent) as temporary:
        directory = Path(temporary)
        request = directory / "request.json"
        report = directory / "observed.json"
        junit = directory / "junit.xml"
        payload = evidence.encode(
            {
                "root": str(root),
                "input_sha256": evidence.digest(
                    evidence.safe(root, evidence.INPUT).read_bytes()
                ),
                "control_sha256": evidence.digest(
                    evidence.safe(root, evidence.CONTROL_INPUT).read_bytes()
                ),
                "source_proof": proof,
                "report": str(report),
            }
        )
        request.write_bytes(payload)
        command = [
            sys.executable,
            str(root / "tools/run-plugin-tests.py"),
            "agent-bridge",
            "--test-file",
            "tests/test_http47_capture_probes.py",
            "--test-file",
            "tests/test_cold_drain.py",
            "--test-file",
            "tests/test_cold_recovery_races.py",
            "--test-file",
            "tests/test_cold_recovery_drain.py",
            "--test-file",
            "tests/test_cold_recovery_handoff_fence.py",
            "--test-file",
            "tests/test_cold_recovery_stop.py",
            "--test-file",
            "tests/test_cold_recovery_launch_cancellation.py",
            "-k",
            " or ".join(evidence.PROBES),
        ]
        with patch.dict(
            os.environ,
            {
                REQUEST_ENV: str(request),
                REQUEST_SHA_ENV: evidence.digest(payload),
                "PYTEST_ADDOPTS": "--junitxml=" + str(junit),
            },
        ):
            result = run_caller_owned(
                command,
                timeout=240,
                max_stdout_bytes=4 * 1024 * 1024,
                max_stderr_bytes=1024 * 1024,
            )
        print(result.stdout, end="")
        print(result.stderr, end="", file=sys.stderr)
        if result.returncode:
            raise ValueError(
                "HTTP-47 contained probes failed; no capture outputs written"
            )
        if request.read_bytes() != payload:
            raise ValueError("capture probe request changed")
        verify_junit(junit)
        from bridge_http47_runtime import verify_interpreters
        binding = verify_interpreters(directory / "interpreters", proof)
        if binding["policy"] != "every-owned-interpreter-source-and-entrypoint-v1":
            raise ValueError("HTTP-47 interpreter binding policy mismatch")
        if report.is_symlink() or not report.is_file() or report.stat().st_nlink != 1:
            raise ValueError("capture observation report is missing or aliased")
        observed = json.loads(report.read_bytes())
        if canonical(observed) != canonical(expected["expected"]):
            raise ValueError(
                "actual HTTP-47 observations differ from independently authored expectations"
            )
        return observed


def capture(root: Path, commit: str, *, write: bool = False) -> dict[str, bytes]:
    if write:
        from agent_bridge_contract_publication import recover_pending_links
        recover_pending_links(root, commit)
    original = evidence.baseline(root, commit)
    before = evidence.source_snapshot(root, commit, original)
    expected = expected_input(root)
    # Expected bytes only preflight ownership/paths. No vector is published
    # until actual observations and the complete probe portfolio agree.
    proposed = evidence.build_outputs(
        root,
        commit,
        original,
        before,
        json.loads(canonical(expected["expected"])),
    )
    evidence.validate_outputs(root, original, proposed)
    history = evidence.check_history(root, original, proposed)
    observed = run_probes(root, before, expected)
    if canonical(observed) != canonical(expected["expected"]):
        raise ValueError("actual capture input/output substitution refused")
    if evidence.source_snapshot(root, commit, original) != before:
        raise ValueError("source changed during capture; no capture outputs written")
    if evidence.check_history(root, original, proposed) != history:
        raise ValueError("historical/current evidence changed during capture")
    outputs = evidence.build_outputs(
        root,
        commit,
        original,
        before,
        json.loads(canonical(observed)),
    )
    if outputs != proposed:
        raise ValueError("capture output plan changed after probes")
    evidence.validate_outputs(root, original, outputs)
    if not write:
        for path, data in outputs.items():
            target = evidence.safe(root, path)
            if not target.is_file() or evidence.git_evidence.sha256_bytes(
                target.read_bytes()
            ) != evidence.digest(data):
                raise ValueError(f"capture output drift: {path}")
        return outputs
    from agent_bridge_contract_publication import publish

    publish(
        root, commit, outputs,
        validate=lambda: evidence.validate_outputs(root, original, outputs),
        input_hashes=history,
    )
    return outputs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    outputs = capture(ROOT, args.source_commit, write=args.write)
    print(
        json.dumps(
            {
                "source_commit": args.source_commit,
                "generation": 47,
                "write": args.write,
                "outputs": len(outputs),
                "scope": "model-free source evidence; not rollout qualification",
            }
        )
    )


if __name__ == "__main__":
    main()
