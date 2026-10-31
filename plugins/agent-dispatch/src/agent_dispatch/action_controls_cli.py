"""Local control of reversible actions; no coordinator autostart or lane stop."""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict

from .action_controls import AUTOMATIC_ACTIONS, action_decision
from .action_policy import ACTIONS, MAX_STATE_BYTES, STATE_NAME, read_controls
from .install_paths import install_dir
from .overrides import mutate_overrides

CONTRACT = {
    "id": "agent-dispatch.reversible-actions",
    "version": 1,
    "scope": "selected-installation",
    "actions": list(ACTIONS),
    "consumer": "executing-runtime-only",
    "running_consumers": "not-qualified-by-this-command",
}


def command(args: argparse.Namespace) -> int:
    root = install_dir()
    path = root / STATE_NAME
    try:
        if any(getattr(args, name, None) for name in (
            "url", "token", "control_token", "shared", "local",
        )):
            raise ValueError(
                "actions are installation-local; coordinator routing options are unsupported"
            )
        if args.action_command in ("inhibit", "clear"):
            def mutate(controls: dict[str, dict]) -> None:
                if args.action_command == "clear":
                    controls.pop(args.action, None)
                else:
                    controls[args.action] = {
                        "disabled": True, "reason": args.reason, "at": time.time(),
                    }
                payload = json.dumps(controls, indent=2, sort_keys=True).encode("utf-8")
                if len(payload) > MAX_STATE_BYTES:
                    raise ValueError("action policy would exceed 64 KiB")

            mutate_overrides(path, mutate, loader=read_controls)
        if args.action_command == "contract":
            result = {"ok": True, "contract": CONTRACT}
        else:
            decisions = [
                asdict(action_decision(action, automatic=action in AUTOMATIC_ACTIONS))
                for action in ACTIONS
                if getattr(args, "action", None) in (None, action)
            ]
            result = {
                "ok": not any(item["error"] for item in decisions),
                "contract": CONTRACT, "scope": str(root), "state_file": str(path),
                "action_attempts": decisions,
                "automatic_attempts": [
                    item for item in decisions if item["action"] in AUTOMATIC_ACTIONS
                ],
            }
    except (OSError, ValueError, TimeoutError) as exc:
        result = {"ok": False, "contract": CONTRACT, "error": str(exc)}
        print(f"agent-dispatch actions: {exc}", file=sys.stderr)
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 2


def register_commands(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser(
        "actions", help="Inhibit actions without stopping their polling services.",
        allow_abbrev=False,
    )
    verbs = parser.add_subparsers(dest="action_command", required=True)
    for verb in ("contract", "status", "inhibit", "clear"):
        child = verbs.add_parser(verb, allow_abbrev=False)
        if verb in ("inhibit", "clear"):
            child.add_argument("action", choices=ACTIONS)
        elif verb == "status":
            child.add_argument("action", choices=ACTIONS, nargs="?")
        if verb == "inhibit":
            child.add_argument("--reason", default="")
        child.add_argument("--json", action="store_true", help="JSON (also the default).")
        child.set_defaults(func=command)
