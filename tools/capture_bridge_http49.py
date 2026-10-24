#!/usr/bin/env python3
"""Capture committed HTTP-49 producer behavior without changing HTTP-48 evidence."""

from pathlib import Path

import bridge_http49_evidence as evidence
import capture_bridge_http48 as common


def run_probes(root: Path, proof: dict, vector: dict) -> dict:
    return common.run_probes(root, proof, vector, evidence=evidence)


def capture(root: Path, commit: str, *, write: bool = False) -> dict[str, bytes]:
    return common.capture(root, commit, write=write, evidence=evidence, probe_runner=run_probes)


if __name__ == "__main__":
    common.main(evidence=evidence, capture_fn=capture)
