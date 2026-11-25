"""Materialize the real native resource owner inside one private CLI fixture."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools"))

import materialize_payload_invocation


def main() -> None:
    """Prepare a copied payload without installing or starting a runtime."""
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    log = materialize_payload_invocation.materialize(
        args.destination, canonical_root=ROOT,
        source_consumer=ROOT / "plugins/agent-remote-driver",
    )
    if len(log) != 6 or any(line.startswith("SKIP") for line in log):
        raise RuntimeError("; ".join(log) or "No native evidence assets were materialized")


if __name__ == "__main__":
    main()
