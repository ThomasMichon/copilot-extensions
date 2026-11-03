"""Optional historical monitor owner protocol; never enters normal startup."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def add_parsers(sub) -> None:
    parser = sub.add_parser("status-monitor-cleanup", help="Inspect or explicitly retire historical monitors")
    parser.add_argument("operation", choices=("capabilities", "inspect", "apply"))
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--request", type=Path, help="One bounded, explicitly selected owner finding")


def cmd_status_monitor_cleanup(args: argparse.Namespace) -> int:
    from . import historical_daemons as owner
    from .status_monitor_runtime import _aw_runtime_home

    if args.operation == "capabilities":
        result, code = owner.capabilities(), 0
    else:
        try:
            root = _aw_runtime_home()
            if args.operation == "inspect":
                result = {"findings": owner.inspect(root)}
                code = 0
            else:
                if args.request is None:
                    raise ValueError("apply requires an explicit selected finding")
                if str(args.request) == "-":
                    raw = sys.stdin.buffer.read(262145)
                else:
                    with args.request.open("rb") as stream:
                        raw = stream.read(262145)
                if len(raw) > 262144:
                    raise ValueError("historical monitor request exceeds its size bound")
                def unique(pairs):
                    value = {}
                    for key, item in pairs:
                        if key in value:
                            raise ValueError("duplicate historical monitor request field")
                        value[key] = item
                    return value
                def invalid(_value):
                    raise ValueError("invalid historical monitor request constant")
                finding = json.loads(raw, object_pairs_hook=unique, parse_constant=invalid)
                if isinstance(finding, dict):
                    selections = [finding]
                elif (isinstance(finding, list) and 0 < len(finding) <= 16
                      and all(isinstance(item, dict) for item in finding)):
                    selections = finding
                else:
                    raise ValueError("historical monitor request must select one object or up to 16 findings")
                resources = [item.get("resource") for item in selections]
                if any(not isinstance(resource, str) or not resource.strip() for resource in resources):
                    raise ValueError("historical monitor selection has no resource identity")
                if len(set(resources)) != len(resources):
                    raise ValueError("historical monitor selection repeats a resource identity")
                if isinstance(finding, dict):
                    result = owner.apply(root, finding)
                    code = 0 if result["status"] == "applied" else 1
                else:
                    actions = []
                    for item in selections:
                        try:
                            outcome = owner.apply(root, item)
                        except (OSError, ValueError, RuntimeError) as error:
                            outcome = {"resource": item["resource"], "status": "failed", "reason": str(error)}
                        actions.append(outcome)
                    result = {"actions": actions, "settled": all(item["status"] == "applied" for item in actions)}
                    code = 0 if result["settled"] else 1
        except (OSError, ValueError, RuntimeError) as error:
            result, code = {"status": "unavailable", "reason": str(error)}, 1
    print(json.dumps(result, indent=2) if args.json else json.dumps(result))
    return code
