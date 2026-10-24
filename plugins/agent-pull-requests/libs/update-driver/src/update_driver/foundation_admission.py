"""Fixed retained-foundation admission; no queue mutation or construction recipe."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from . import foundation_state
from .foundation_inputs import check_health
from .participant import borrowed_custody


def admit(*, root: Path, source_hash: str) -> foundation_state.FoundationOperation:
    """Read the canonical queue and begin progress under the native owner's witness."""
    custody = borrowed_custody(root=root)
    if custody is None:
        raise foundation_state.FoundationStateError("foundation admission requires live inherited custody")
    with custody:
        check_health()
        return foundation_state.begin(root, source_hash, custody)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--source-hash", required=True)
    args = parser.parse_args()
    operation = admit(root=args.root, source_hash=args.source_hash)
    print(json.dumps({
        "schema": foundation_state.SCHEMA, "version": foundation_state.VERSION, **asdict(operation),
    }, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
