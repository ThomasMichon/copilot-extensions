#!/usr/bin/env python3
"""Capture committed HTTP-48 local ASGI observations without rewriting history."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
import xml.etree.ElementTree as ET

import bridge_http48_evidence as evidence
import bridge_http47_runtime as binding

ROOT = Path(__file__).resolve().parents[1]


def run_probes(root: Path, proof: dict, vector: dict) -> dict:
    from agent_procutil.owned_process import run_caller_owned

    parent = evidence.safe(root, ".test-venvs", directory=True)
    parent.mkdir(exist_ok=True)
    # Reuse the source/entrypoint guard, including its confined request path.
    # Its historical name does not select a protocol generation or writer.
    with tempfile.TemporaryDirectory(prefix="http47-capture-http48-", dir=parent) as temporary:
        directory = Path(temporary)
        request = directory / "request.json"
        report = directory / "observed.json"
        junit = directory / "junit.xml"
        payload = evidence.encode({
            "root": str(root), "capture_generation": 48, "source_proof": proof,
            "input_sha256": proof[evidence.INPUT]["sha256"], "report": str(report),
        })
        request.write_bytes(payload)
        with patch.dict(os.environ, {
            binding.REQUEST: str(request), binding.REQUEST_SHA: evidence.digest(payload),
            "PYTEST_ADDOPTS": "--junitxml=" + str(junit),
        }):
            result = run_caller_owned([
                sys.executable, str(root / "tools/run-plugin-tests.py"), "agent-bridge",
                "--test-file", "tests/test_http48_capture_probes.py",
                "-k", "test_http48_capture_actual_observations",
            ], timeout=240, max_stdout_bytes=4 * 1024 * 1024, max_stderr_bytes=1024 * 1024)
        print(result.stdout, end="")
        print(result.stderr, end="", file=sys.stderr)
        if result.returncode or request.read_bytes() != payload:
            raise ValueError("HTTP-48 bounded probes failed or request changed")
        cases: dict[str, int] = {}
        for case in ET.parse(junit).getroot().iter("testcase"):
            name = case.attrib.get("name", "").split("[", 1)[0]
            if name not in evidence.PROBES or any(
                case.find(tag) is not None for tag in ("failure", "error", "skipped")
            ):
                raise ValueError("HTTP-48 probe failed, skipped or escaped its portfolio")
            cases[name] = cases.get(name, 0) + 1
        if cases != evidence.PROBES:
            raise ValueError("HTTP-48 probe portfolio is incomplete or duplicated")
        receipt = binding.verify_interpreters(
            directory / "interpreters", proof, expected_roles=frozenset(),
            required_sources=evidence.installation.required_sources(
                evidence.safe(root, evidence.installation.ENTRY).read_bytes(),
            ),
        )
        if receipt["roles"] != ["observer"]:
            raise ValueError("HTTP-48 ASGI observer scope changed")
        if report.is_symlink() or not report.is_file() or report.stat().st_nlink != 1:
            raise ValueError("HTTP-48 observation report is absent or aliased")
        observed = json.loads(report.read_bytes())
        expected = {case: vector["expected"] for case in ("legacy", "namespaced")}
        if observed != expected:
            raise ValueError("HTTP-48 observations differ from independent expectations")
        return observed


def capture(root: Path, commit: str, *, write: bool = False) -> dict[str, bytes]:
    if write:
        from agent_bridge_contract_publication import recover_pending_links
        recover_pending_links(root, commit)
    snapshot = evidence.baseline(root, commit)
    before = evidence.source_snapshot(root, commit, snapshot.materialized)
    vector = json.loads(evidence.safe(root, evidence.INPUT).read_bytes())
    if (set(vector) != {"schema", "generation", "requests", "expected"}
            or vector["schema"] != "http48-retained-inbox-cases-v1" or vector["generation"] != 48):
        raise ValueError("HTTP-48 independent input identity mismatch")
    expected = {case: vector["expected"] for case in ("legacy", "namespaced")}
    proposed = evidence.build_outputs(root, commit, snapshot, before, expected)
    evidence.validate_outputs(root, snapshot, proposed)
    history = evidence.history(root, snapshot.materialized, proposed)
    observed = run_probes(root, before, vector)
    if evidence.source_snapshot(root, commit, snapshot.materialized) != before:
        raise ValueError("HTTP-48 source changed during capture")
    if evidence.history(root, snapshot.materialized, proposed) != history:
        raise ValueError("HTTP-48 historical inputs changed during capture")
    outputs = evidence.build_outputs(root, commit, snapshot, before, observed)
    if outputs != proposed:
        raise ValueError("HTTP-48 capture plan changed after actual probes")
    evidence.validate_outputs(root, snapshot, outputs)
    if write:
        from agent_bridge_contract_publication import publish
        publish(root, commit, outputs,
                validate=lambda: evidence.validate_outputs(root, snapshot, outputs),
                input_hashes=history)
    else:
        for path, data in outputs.items():
            if evidence.safe(root, path).read_bytes() != data:
                raise ValueError(f"HTTP-48 capture output drift: {path}")
    return outputs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    outputs = capture(ROOT, args.source_commit, write=args.write)
    print(json.dumps({
        "source_commit": args.source_commit, "generation": 48,
        "write": args.write, "outputs": len(outputs),
        "scope": "model-free local ASGI/ordinary-producer source evidence; not SDK/native qualification",
    }))


if __name__ == "__main__":
    main()
