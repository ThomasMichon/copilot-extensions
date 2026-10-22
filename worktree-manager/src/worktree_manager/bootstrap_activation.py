"""Activate the resident daemon using a constructed Manager's own dependencies."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


def main(argv: list[str] | None = None) -> int:
    from . import mux_daemon_cutover

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args(argv)
    root, slot = args.root.resolve(), Path(sys.prefix).resolve()
    if slot.parent != root / "versions" or not (slot / ".runtime-bootstrap").is_dir():
        raise RuntimeError("Bootstrap activation must execute from its constructed Manager slot")
    result = mux_daemon_cutover.activate_after_update(root=root, slot=slot, version=slot.name)
    if (
        not isinstance(result, dict)
        or result.get("action") not in ("noop", "cutover", "legacy-cutover", "error", "refused")
    ):
        raise RuntimeError("Manager cutover returned an invalid activation result")
    outcome = result.get("result")
    if outcome is not None and (
        not isinstance(outcome, dict) or type(outcome.get("ok")) is not bool
    ):
        raise RuntimeError("Manager cutover returned an invalid activation outcome")
    if result["action"] in ("cutover", "legacy-cutover") and outcome is None:
        raise RuntimeError("Manager cutover returned an invalid activation outcome")
    print(json.dumps(result), flush=True)
    return int(
        result["action"] in ("error", "refused")
        or (isinstance(outcome, dict) and outcome.get("ok") is False)
    )


if __name__ == "__main__":
    raise SystemExit(main())
