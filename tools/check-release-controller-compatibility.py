#!/usr/bin/env python3
"""Check live Main and the prepared controller before a source PR can ship."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import yaml

from promotion_unit_contract import UnitEvidenceError
from promotion_release_helpers import verify_helper_contract
from release_controller_contract import (
    active_controller_compatibility, helper_implementations,
    verify_controller_inputs, verify_helper_selection,
)

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ".github/workflows/validate-and-promote.yml"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--main-ref", required=True)
    args = parser.parse_args()
    prepared = yaml.safe_load((ROOT / WORKFLOW).read_bytes())
    implementations = {name: (ROOT / "tools" / name).read_bytes()
                       for name in sorted(helper_implementations(prepared))}
    verify_helper_selection(prepared)
    verify_controller_inputs(prepared, implementations)
    manifest = json.loads((ROOT / "tools/promotion-unit-manifest.json").read_bytes())
    main = subprocess.run(
        ["git", "--no-replace-objects", "rev-parse", "--verify", f"{args.main_ref}^{{commit}}"],
        cwd=ROOT, check=True, capture_output=True, text=True, timeout=30,
    ).stdout.strip()
    current_bytes = subprocess.run(
        ["git", "--no-replace-objects", "show", f"{main}:{WORKFLOW}"],
        cwd=ROOT, check=True, capture_output=True, timeout=30,
    ).stdout
    current = yaml.safe_load(current_bytes)
    current_implementations = {name: (ROOT / "tools" / name).read_bytes()
                               for name in sorted(helper_implementations(current))}
    verify_helper_contract(ROOT, manifest, controllers=(current_bytes.decode("utf-8"),))
    compatible, reason = active_controller_compatibility(current, current_implementations)
    if not compatible:
        print(f"::warning::Main {main} requires a separately reviewed legacy controller bootstrap: {reason}")
        print(json.dumps({"main": main, "active_compatible": False, "prepared_compatible": True}))
    else:
        print(json.dumps({"main": main, "active_compatible": True, "prepared_compatible": True}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (UnitEvidenceError, OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(f"::error::Release controller compatibility: {error}", file=sys.stderr)
        raise SystemExit(1)
