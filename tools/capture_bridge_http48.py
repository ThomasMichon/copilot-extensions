#!/usr/bin/env python3
"""Capture a selected retained-inbox generation without rewriting history."""

from __future__ import annotations

import argparse
from collections.abc import Callable
import json
import math
import os
import shlex
from pathlib import Path
import sys
import tempfile
import time
from types import ModuleType
from typing import Any, Protocol
from unittest.mock import patch
import xml.etree.ElementTree as ET

import bridge_http48_evidence as evidence
import bridge_http47_runtime as binding

ROOT = Path(__file__).resolve().parents[1]

ProbeRunner = Callable[[Path, dict[str, Any], dict[str, Any]], dict[str, Any]]


class CaptureRunner(Protocol):
    def __call__(
        self, root: Path, commit: str, *, write: bool,
    ) -> dict[str, bytes]: ...


def run_probes(
    root: Path, proof: dict[str, Any], vector: dict[str, Any], *,
    evidence: ModuleType = evidence, expected_roles: frozenset[str] = frozenset(),
    isolated_probes: frozenset[str] = frozenset(),
    isolated_test_file: str | None = None,
    aggregate_timeout: float | None = None,
) -> dict[str, Any]:
    from agent_procutil.owned_process import run_caller_owned

    parent = evidence.safe(root, ".test-venvs", directory=True)
    parent.mkdir(exist_ok=True)
    # Reuse the source/entrypoint guard, including its confined request path.
    # Its historical name does not select a protocol generation or writer.
    label = f"HTTP-{evidence.GENERATION}"
    if not isolated_probes <= evidence.PROBES.keys():
        raise ValueError(f"{label} isolated probes escaped the declared portfolio")
    if isolated_test_file is not None and (
        not isolated_probes
        or (Path("plugins/agent-bridge") / isolated_test_file).as_posix() not in proof
    ):
        raise ValueError(f"{label} isolated test source is not bound to its probes")
    groups = [
        group for group in (
            [name for name in evidence.PROBES if name in isolated_probes],
            [name for name in evidence.PROBES if name not in isolated_probes],
        ) if group
    ]
    if isolated_test_file is not None and len(groups) != 2:
        raise ValueError(f"{label} isolated collection requires a separate remainder")
    invocations = [groups] if isolated_test_file is not None else [[group] for group in groups]
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
        for index, cohorts in enumerate(invocations):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError(f"{label} aggregate probe deadline expired")
            junit_root = directory / f"junit-{index}"
            test_file = Path(evidence.TEST).relative_to("plugins/agent-bridge").as_posix()
            if isolated_test_file is not None:
                files = [isolated_test_file, test_file]
                reports = [
                    (junit_root / "agent-bridge" / f"group-{number:03d}.xml", group)
                    for number, group in enumerate(cohorts, start=1)
                ]
                report_arguments = ["--junit-dir", str(junit_root)]
                pytest_options = "-vv --durations=20"
            else:
                files = [test_file]
                reports = [(junit_root.with_suffix(".xml"), cohorts[0])]
                report_arguments = []
                pytest_options = (
                    shlex.quote("--junitxml=" + str(reports[0][0])) + " -vv --durations=20"
                )
            print(
                f"{label} bounded runner {index + 1}/{len(invocations)} "
                f"({len(cohorts)} observer cohorts)", flush=True,
            )
            with patch.dict(os.environ, {
                binding.REQUEST: str(request), binding.REQUEST_SHA: evidence.digest(payload),
                "PYTEST_ADDOPTS": pytest_options,
            }):
                result = run_caller_owned([
                    sys.executable, str(root / "tools/run-plugin-tests.py"), "agent-bridge",
                    *(argument for name in files for argument in ("--test-file", name)),
                    *report_arguments,
                    "-k", " or ".join(name for group in cohorts for name in group),
                    "--subsuite-timeout", str(remaining),
                    "--plugin-timeout", str(remaining),
                    *(["--test-timeout", "120"] if os.name == "nt" else []),
                ], timeout=remaining, max_stdout_bytes=4 * 1024 * 1024,
                    max_stderr_bytes=1024 * 1024)
            print(result.stdout, end="")
            print(result.stderr, end="", file=sys.stderr)
            if result.returncode or request.read_bytes() != payload:
                raise ValueError(f"{label} bounded probes failed or request changed")
            if isolated_test_file is not None and set(junit_root.rglob("*.xml")) != {
                path for path, _ in reports
            }:
                raise ValueError(f"{label} observer cohort reports are incomplete or unexpected")
            for junit, group in reports:
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
        observed: dict[str, Any] = json.loads(report.read_bytes())
        expected = {case: vector["expected"] for case in ("legacy", "namespaced")}
        if observed != expected:
            raise ValueError(f"{label} observations differ from independent expectations")
        return observed


def capture(
    root: Path, commit: str, *, write: bool = False, evidence: ModuleType = evidence,
    probe_runner: ProbeRunner | None = None,
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
    outputs: dict[str, bytes] = evidence.build_outputs(root, commit, snapshot, before, observed)
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


def main(
    *, evidence: ModuleType = evidence, capture_fn: CaptureRunner | None = None,
) -> None:
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
