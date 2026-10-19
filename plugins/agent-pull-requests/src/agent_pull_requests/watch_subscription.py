"""CLI registration adapter, negotiating opt-in callback protocol explicitly."""

from __future__ import annotations

import json
import sys

from .watch_contract import DEFAULT_UNTIL
from .watch_notification import ACKNOWLEDGED_NOTIFICATIONS, positive_seconds, validate_notify
from .watch_provider import CONTINUOUS_NOTIFICATIONS, NATIVE_SNAPSHOTS, command_path


def subscribe(args, request) -> int:
    notify = {"argv": args.notify_argv} if args.notify_argv else {}
    acknowledged = getattr(args, "acknowledged_notifications", False)
    callback_timeout = getattr(args, "notify_timeout", None)
    try:
        if getattr(args, "notify_stdin", False):
            if args.notify_argv:
                raise ValueError("choose notify stdin or notify argv, not both")
            raw = sys.stdin.read(65_537)
            if len(raw.encode("utf-8")) > 65_536:
                raise ValueError("notify specification exceeds its size budget")
            try:
                notify = json.loads(raw)
            except ValueError:
                raise ValueError("notify stdin must contain one JSON object") from None
        if callback_timeout is not None:
            if not isinstance(notify, dict):
                raise ValueError("notify stdin must contain one JSON object")
            notify["timeout"] = callback_timeout
        notify = validate_notify(notify, acknowledged)
        if args.timeout is not None:
            positive_seconds(args.timeout)
        payload = {
            "repo": args.repo, "number": args.number, "subscriber_id": args.subscriber_id,
            "until": list(args.until or DEFAULT_UNTIL), "notify": notify, "timeout": args.timeout,
        }
        command = getattr(args, "worktrees_command", None)
        continuous = getattr(args, "continuous", False)
        if command:
            payload["worktrees_command"] = command_path(command)
        if continuous:
            if not command or not acknowledged:
                raise ValueError("continuous subscriptions require native acknowledged snapshots")
            payload["continuous"] = True
        if acknowledged:
            health = request("health", {"repo": "", "number": 0})
            if ACKNOWLEDGED_NOTIFICATIONS not in health.get("capabilities", []):
                raise ValueError("watch owner lacks acknowledged_notifications/v1")
            if command and NATIVE_SNAPSHOTS not in health.get("capabilities", []):
                raise ValueError("watch owner lacks native_snapshots/v1")
            if continuous and CONTINUOUS_NOTIFICATIONS not in health.get("capabilities", []):
                raise ValueError("watch owner lacks continuous_notifications/v1")
            payload["notification_protocol"] = ACKNOWLEDGED_NOTIFICATIONS
        result = request("register", payload)
        if acknowledged and result.get("registered") and (
            result.get("notification_protocol") != ACKNOWLEDGED_NOTIFICATIONS
            or not isinstance(result.get("registration_id"), str)
            or not result["registration_id"].strip()
        ):
            result = {
                "error": "ambiguous watch registration; reconcile subscription before retry or fallback",
                "ambiguous_registration": True,
                "repo": args.repo, "number": args.number, "subscriber_id": args.subscriber_id,
            }
        if command and result.get("registered") and (
            result.get("snapshot_protocol") != "agent-worktrees.pr-snapshot/v1"
            or continuous and result.get("continuous") is not True
        ):
            result = {
                "error": "ambiguous native registration; reconcile before retry",
                "ambiguous_registration": True,
            }
    except ValueError as exc:
        result = {"error": str(exc)}
    print(json.dumps(result, indent=2, sort_keys=True) if args.json else result)
    return 0 if result.get("registered") else 1
