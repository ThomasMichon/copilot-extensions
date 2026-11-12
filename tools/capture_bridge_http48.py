#!/usr/bin/env python3
"""Capture a selected retained-inbox generation without rewriting history."""

from __future__ import annotations

import argparse
import json
import math
import os
import shlex
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch
import xml.etree.ElementTree as ET

import bridge_http48_evidence as evidence
import bridge_http47_runtime as binding

ROOT = Path(__file__).resolve().parents[1]


def run_probes(
    root: Path, proof: dict, vector: dict, *,
    evidence=evidence, expected_roles: frozenset[str] = frozenset(),
    isolated_probes: frozenset[str] = frozenset(),
    aggregate_timeout: float | None = None,
) -> dict:
    from agent_procutil.owned_process import run_caller_owned

    parent = evidence.safe(root, ".test-venvs", directory=True)
    parent.mkdir(exist_ok=True)
    # Reuse the source/entrypoint guard, including its confined request path.
    # Its historical name does not select a protocol generation or writer.
    label = f"HTTP-{evidence.GENERATION}"
    if not isolated_probes <= evidence.PROBES.keys():
        raise ValueError(f"{label} isolated probes escaped the declared portfolio")
    groups = [
        group for group in (
            [name for name in evidence.PROBES if name in isolated_probes],
            [name for name in evidence.PROBES if name not in isolated_probes],
        ) if group
    ]
    with tempfile.TemporaryDirectory(
        prefix=f"http47-capture-http{evidence.GENERATION}-", dir=parent,
    ) as temporary:
        directory = Path(temporary)
        request = directory / "request.json"
        report = directory / "observed.json"
        payload = evidence.encode({
            "root": str(root), "capture_generation": evidence.GENERATION, "source_proof": proof,
            "input_sha256": proof[evidence.INPUT]["sha256"], "report": str(report),
        })
        request.write_bytes(payload)
        cases: dict[str, int] = {}
        budget = aggregate_timeout if aggregate_timeout is not None else (600 if os.name == "nt" else 240)
        if not math.isfinite(budget) or budget <= 0:
            raise ValueError(f"{label} aggregate probe budget must be finite and positive")
        deadline = time.monotonic() + budget
        for index, group in enumerate(groups):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError(f"{label} aggregate probe deadline expired")
            junit = directory / f"junit-{index}.xml"
            print(f"{label} bounded probe group {index + 1}/{len(groups)}", flush=True)
            with patch.dict(os.environ, {
                binding.REQUEST: str(request), binding.REQUEST_SHA: evidence.digest(payload),
                "PYTEST_ADDOPTS": shlex.quote("--junitxml=" + str(junit)) + " -vv --durations=20",
            }):
                result = run_caller_owned([
                    sys.executable, str(root / "tools/run-plugin-tests.py"), "agent-bridge",
                    "--test-file", Path(evidence.TEST).relative_to("plugins/agent-bridge").as_posix(),
                    "-k", " or ".join(group),
                    "--subsuite-timeout", str(remaining),
                    "--plugin-timeout", str(remaining),
                    *(["--test-timeout", "120"] if os.name == "nt" else []),
                ], timeout=remaining, max_stdout_bytes=4 * 1024 * 1024,
                    max_stderr_bytes=1024 * 1024)
            print(result.stdout, end="")
            print(result.stderr, end="", file=sys.stderr)
            if result.returncode or request.read_bytes() != payload:
                raise ValueError(f"{label} bounded probes failed or request changed")
            for case in ET.parse(junit).getroot().iter("testcase"):
                name = case.attrib.get("name", "").split("[", 1)[0]
                if name not in group or any(
                    case.find(tag) is not None for tag in ("failure", "error", "skipped")
                ):
                    raise ValueError(f"{label} probe failed, skipped or escaped its portfolio")
                cases[name] = cases.get(name, 0) + 1
        if cases != evidence.PROBES:
            raise ValueError(f"{label} probe portfolio is incomplete or duplicated")
        receipt = binding.verify_interpreters(
            directory / "interpreters", proof, expected_roles=expected_roles,
            required_sources=evidence.installation.required_sources(
                evidence.safe(root, evidence.installation.ENTRY).read_bytes(),
            ),
            expected_observers=len(groups),
        )
        if receipt["roles"] != ["observer", *sorted(expected_roles)]:
            raise ValueError(f"{label} ASGI observer scope changed")
        if report.is_symlink() or not report.is_file() or report.stat().st_nlink != 1:
            raise ValueError(f"{label} observation report is absent or aliased")
        observed = json.loads(report.read_bytes())
        expected = {case: vector["expected"] for case in ("legacy", "namespaced")}
        if observed != expected:
            raise ValueError(f"{label} observations differ from independent expectations")
        return observed


def capture(
    root: Path, commit: str, *, write: bool = False, evidence=evidence, probe_runner=None,
) -> dict[str, bytes]:
    if write:
        from agent_bridge_contract_publication import recover_pending_links
        recover_pending_links(root, commit)
    snapshot = evidence.baseline(root, commit)
    before = evidence.source_snapshot(root, commit, snapshot.materialized)
    vector = json.loads(evidence.safe(root, evidence.INPUT).read_bytes())
    if (set(vector) != {"schema", "generation", "requests", "expected"}
            or vector["schema"] != evidence.VECTOR_SCHEMA
            or vector["generation"] != evidence.GENERATION):
        raise ValueError(f"HTTP-{evidence.GENERATION} independent input identity mismatch")
    expected = {case: vector["expected"] for case in ("legacy", "namespaced")}
    proposed = evidence.build_outputs(root, commit, snapshot, before, expected)
    evidence.validate_outputs(root, snapshot, proposed)
    history = evidence.history(root, snapshot.materialized, proposed)
    observed = (probe_runner or run_probes)(root, before, vector)
    if evidence.source_snapshot(root, commit, snapshot.materialized) != before:
        raise ValueError(f"HTTP-{evidence.GENERATION} source changed during capture")
    if evidence.history(root, snapshot.materialized, proposed) != history:
        raise ValueError(f"HTTP-{evidence.GENERATION} historical inputs changed during capture")
    outputs = evidence.build_outputs(root, commit, snapshot, before, observed)
    if outputs != proposed:
        raise ValueError(f"HTTP-{evidence.GENERATION} capture plan changed after actual probes")
    evidence.validate_outputs(root, snapshot, outputs)
    if write:
        from agent_bridge_contract_publication import publish
        publish(root, commit, outputs,
                validate=lambda: evidence.validate_outputs(root, snapshot, outputs),
                input_hashes=history)
    else:
        for path, data in outputs.items():
            if evidence.safe(root, path).read_bytes() != data:
                raise ValueError(f"HTTP-{evidence.GENERATION} capture output drift: {path}")
    return outputs


def main(*, evidence=evidence, capture_fn=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    outputs = (capture_fn or capture)(ROOT, args.source_commit, write=args.write)
    print(json.dumps({
        "source_commit": args.source_commit, "generation": evidence.GENERATION,
        "write": args.write, "outputs": len(outputs),
        "scope": evidence.SCOPE,
    }))


if __name__ == "__main__":
    main()
