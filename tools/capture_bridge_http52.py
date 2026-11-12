#!/usr/bin/env python3
"""Execute HTTP-52 membership evidence without refreshing historical HTTP-51."""

import argparse
import json
import os
from pathlib import Path

import bridge_http52_evidence as evidence
import capture_bridge_http48 as common


def run_probes(root: Path, proof: dict, vector: dict) -> dict:
    return common.run_probes(
        root, proof, vector, evidence=evidence, expected_roles=frozenset({"cli", "record-writer"}),
        isolated_probes=frozenset({
            "test_retained_acquisition_and_later_lineage_use_actual_owner_processes",
            "test_keyed_creation_uses_actual_retained_serializer_and_preserves_claim",
        }),
        aggregate_timeout=1200 if os.name == "nt" else 240,
    )


def capture(root: Path, commit: str, *, write: bool = False) -> dict[str, bytes]:
    return common.capture(root, commit, write=write, evidence=evidence, probe_runner=run_probes)


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
