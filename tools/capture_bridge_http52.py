#!/usr/bin/env python3
"""Execute HTTP-52 membership evidence without refreshing historical HTTP-51."""

import argparse
import json
import os
from pathlib import Path
from collections.abc import Callable
from types import ModuleType
from typing import Protocol, cast

import bridge_http52_evidence as evidence
import capture_bridge_http48 as common
from bridge_capture_progress import observe


class ProbeRunner(Protocol):
    def __call__(
        self, root: Path, proof: dict[str, object], vector: dict[str, object], *,
        evidence: ModuleType, expected_roles: frozenset[str],
        isolated_probes: frozenset[str], isolated_test_file: str, aggregate_timeout: float,
    ) -> dict[str, object]: ...


class CaptureRunner(Protocol):
    def __call__(
        self, root: Path, commit: str, *, write: bool, evidence: ModuleType,
        probe_runner: Callable[[Path, dict[str, object], dict[str, object]], dict[str, object]],
    ) -> dict[str, bytes]: ...


def run_probes(root: Path, proof: dict[str, object], vector: dict[str, object]) -> dict[str, object]:
    with observe(root):
        return cast(ProbeRunner, common.run_probes)(
            root, proof, vector, evidence=evidence,
            expected_roles=frozenset({"cli", "record-writer", "roster-source"}),
            isolated_probes=frozenset({
                "test_retained_acquisition_and_later_lineage_use_actual_owner_processes",
                "test_keyed_creation_uses_actual_retained_serializer_and_preserves_claim",
            }),
            isolated_test_file="tests/test_http52_owner_capture.py",
            aggregate_timeout=1200 if os.name == "nt" else 240,
        )


def capture(root: Path, commit: str, *, write: bool = False) -> dict[str, bytes]:
    return cast(CaptureRunner, common.capture)(
        root, commit, write=write, evidence=evidence, probe_runner=run_probes,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-commit", required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--write", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if args.prepare:
        from bridge_http51_declarations import prepare
        outputs = prepare(root, args.source_commit, evidence=evidence)
    else:
        outputs = capture(root, args.source_commit, write=args.write)
    print(json.dumps({
        "source_commit": args.source_commit, "generation": evidence.GENERATION,
        "prepare": args.prepare, "write": args.write, "outputs": len(outputs),
        "scope": "declarations only; not captured evidence" if args.prepare else evidence.SCOPE,
    }))
