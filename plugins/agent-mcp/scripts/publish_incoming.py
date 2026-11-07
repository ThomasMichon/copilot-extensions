#!/usr/bin/env python3
"""Publish the selected incoming MCP generation without resetting live custody."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args(argv)
    os.environ["AGENT_MCP_INSTALL_DIR"] = str(args.root.absolute())
    from agent_mcp.cutover import (
        ControlError, _configured_readiness, _installed_slot, _run_slot_startup,
        source_serving_intent,
    )

    layout = _installed_slot()
    readiness = _configured_readiness()
    if readiness["state"] == "failed":
        raise ControlError(readiness["detail"])
    wanted = source_serving_intent()
    if os.environ.get("AGENT_MCP_NO_SERVE") or os.environ.get("AGENT_MCP_NO_CUTOVER"):
        result = {"ok": True, "skipped": "serving disabled by explicit policy"}
    elif wanted:
        result = _run_slot_startup(
            layout, layout[0], health_timeout=15,
        )
    else:
        result = {"ok": True, "skipped": "serving remains on demand"}
    print(json.dumps(result))
    return 0 if result.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
