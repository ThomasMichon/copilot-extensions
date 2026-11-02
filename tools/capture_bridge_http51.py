#!/usr/bin/env python3
"""Capture committed HTTP-51 standard native send without rewriting history."""

import argparse
import json
from pathlib import Path

import bridge_http51_evidence as evidence
import capture_bridge_http48 as common


def run_probes(root: Path, proof: dict, vector: dict) -> dict:
    return common.run_probes(root, proof, vector, evidence=evidence)


def capture(root: Path, commit: str, *, write: bool = False) -> dict[str, bytes]:
    return common.capture(root, commit, write=write, evidence=evidence, probe_runner=run_probes)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-commit", required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--prepare", action="store_true",
                      help="Bind semantic source declarations only; capture remains required")
    mode.add_argument("--write", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if args.prepare:
        from bridge_http51_declarations import prepare
        outputs = prepare(root, args.source_commit)
    else:
        outputs = capture(root, args.source_commit, write=args.write)
    print(json.dumps({
        "source_commit": args.source_commit, "generation": evidence.GENERATION,
        "prepare": args.prepare, "write": args.write, "outputs": len(outputs),
        "scope": "declarations only; not captured evidence" if args.prepare else evidence.SCOPE,
    }))
