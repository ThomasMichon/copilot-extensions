"""CLI registration adapter, negotiating opt-in callback protocol explicitly."""

from __future__ import annotations

import json

from .watch_contract import DEFAULT_UNTIL
from .watch_notification import ACKNOWLEDGED_NOTIFICATIONS, positive_seconds, validate_notify


def subscribe(args, request) -> int:
    notify = {"argv": args.notify_argv} if args.notify_argv else {}
    acknowledged = getattr(args, "acknowledged_notifications", False)
    callback_timeout = getattr(args, "notify_timeout", None)
    try:
        if callback_timeout is not None:
            notify["timeout"] = callback_timeout
        notify = validate_notify(notify, acknowledged)
        if args.timeout is not None:
            positive_seconds(args.timeout)
        payload = {
            "repo": args.repo, "number": args.number, "subscriber_id": args.subscriber_id,
            "until": list(args.until or DEFAULT_UNTIL), "notify": notify, "timeout": args.timeout,
        }
        if acknowledged:
            health = request("health", {"repo": "", "number": 0})
            if ACKNOWLEDGED_NOTIFICATIONS not in health.get("capabilities", []):
                raise ValueError("watch owner lacks acknowledged_notifications/v1")
            payload["notification_protocol"] = ACKNOWLEDGED_NOTIFICATIONS
        result = request("register", payload)
    except ValueError as exc:
        result = {"error": str(exc)}
    print(json.dumps(result, indent=2, sort_keys=True) if args.json else result)
    return 0 if result.get("registered") else 1
