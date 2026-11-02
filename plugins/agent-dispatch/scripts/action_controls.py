#!/usr/bin/env python3
"""Read-only native action admission from this payload, without a runtime venv."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_dispatch.action_controls import action_decision  # noqa: E402
from agent_dispatch.action_policy import ACTIONS  # noqa: E402


def check_actions(root: Path, actions: list[str], *, automatic: bool = False) -> dict:
    decisions = [
        action_decision(action, root=root, automatic=automatic)
        for action in dict.fromkeys(actions)
    ]
    for decision in decisions:
        if decision.inhibited or decision.error:
            print(
                f"agent-dispatch action {decision.action} "
                f"{'inhibited' if decision.inhibited else 'allowed'}: "
                f"scope={decision.scope} source={decision.source} "
                f"reason={decision.reason or '(none)'}"
                f"{'; ' + decision.error if decision.error else ''}",
                file=sys.stderr,
            )
    allowed = not any(decision.inhibited for decision in decisions)
    return {"ok": allowed, "decisions": [asdict(item) for item in decisions]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--action", choices=ACTIONS, action="append", required=True)
    parser.add_argument("--automatic", action="store_true")
    args = parser.parse_args(argv)
    result = check_actions(args.root, args.action, automatic=args.automatic)
    print(json.dumps(result))
    return 0 if result["ok"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
