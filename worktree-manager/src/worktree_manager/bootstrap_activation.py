"""Activate the resident daemon using a constructed Manager's own dependencies."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .runtime_identity import running_slot


def main(argv: list[str] | None = None) -> int:
    from . import mux_daemon, mux_daemon_cutover

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--start-if-missing", action="store_true")
    args = parser.parse_args(argv)
    root, slot = args.root.resolve(), running_slot()
    if slot is None or slot.parent != root / "versions" or not (slot / ".runtime-bootstrap").is_dir():
        raise RuntimeError("Bootstrap activation must execute from its constructed Manager slot")
    for attempt in range(2):
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
        missing = result["action"] == "noop" and result.get("reason") == "no-live-daemon"
        if not (args.start_if_missing and missing):
            break
        if attempt or not mux_daemon.ensure_daemon_running(root):
            raise RuntimeError("Constructed Manager daemon startup did not become ready")
    print(json.dumps(result), flush=True)
    return int(
        result["action"] in ("error", "refused")
        or (isinstance(outcome, dict) and outcome.get("ok") is False)
    )


if __name__ == "__main__":
    raise SystemExit(main())
