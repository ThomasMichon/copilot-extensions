"""Process-independent subscriber state, including immutable fired payloads."""

from __future__ import annotations

import copy
import time

from .watch_contract import ALL_TRANSITIONS, Baseline
from .watch_notification import (
    ACKNOWLEDGED_NOTIFICATIONS, positive_seconds, validate_notify, validate_pending,
)


class RegistryState:
    def snapshot_state(self) -> list[dict]:
        with self._lock:
            now = time.monotonic()
            out = []
            for key, bucket in self._subscribers.items():
                for sub in bucket.values():
                    entry = {
                        "repo": key.repo, "number": key.number,
                        "subscriber_id": sub.subscriber_id, "until": list(sub.until),
                        "notify": sub.notify,
                        "baseline": vars(sub.baseline) if sub.baseline is not None else None,
                        "remaining_timeout": (
                            max(0.0, sub.deadline - now) if sub.deadline is not None else None
                        ),
                    }
                    if sub.acknowledged:
                        entry.update(
                            notification_protocol=ACKNOWLEDGED_NOTIFICATIONS,
                            registration_id=sub.registration_id, pending=sub.pending,
                            deadline_at=(
                                time.time() + sub.deadline - now if sub.deadline is not None else None
                            ),
                        )
                    out.append(entry)
            return copy.deepcopy(out)

    def restore_state(self, entries: list[dict]) -> int:
        from .watch_registry import Subscriber, WatchKey

        restored = 0
        identities = set()
        with self._lock:
            for entry in entries:
                opted_in = isinstance(entry, dict) and (
                    "notification_protocol" in entry or "pending" in entry
                    or "registration_id" in entry
                )
                try:
                    if opted_in and entry.get("notification_protocol") != ACKNOWLEDGED_NOTIFICATIONS:
                        raise ValueError("unsupported notification protocol")
                    key = WatchKey(repo=str(entry["repo"]), number=int(entry["number"]))
                    baseline = entry.get("baseline")
                    if opted_in and baseline is not None:
                        if (
                            not isinstance(baseline, dict)
                            or set(baseline) != {
                                "merged", "closed", "review_decision", "mergeable", "checks_state",
                            }
                            or any(type(baseline[field]) is not bool for field in ("merged", "closed"))
                            or any(
                                baseline[field] is not None and not isinstance(baseline[field], str)
                                for field in ("review_decision", "mergeable", "checks_state")
                            )
                        ):
                            raise ValueError("invalid acknowledged baseline")
                    sub = Subscriber(
                        subscriber_id=str(entry["subscriber_id"]), until=tuple(entry["until"]),
                        notify=dict(entry.get("notify") or {}),
                        baseline=Baseline(**baseline) if baseline is not None else None,
                        acknowledged=opted_in,
                    )
                    if opted_in:
                        if "deadline_at" not in entry:
                            raise ValueError("missing acknowledged deadline")
                        sub.registration_id = entry["registration_id"]
                        if (
                            not isinstance(sub.registration_id, str) or not sub.registration_id
                            or not sub.subscriber_id or not key.repo
                            or type(entry["number"]) is not int or key.number <= 0
                            or not sub.until or any(t not in ALL_TRANSITIONS for t in sub.until)
                        ):
                            raise ValueError("invalid registration")
                        if sub.registration_id in identities:
                            raise ValueError("duplicate registration identity")
                        identities.add(sub.registration_id)
                        sub.notify = validate_notify(sub.notify, True)
                        sub.pending = copy.deepcopy(entry.get("pending"))
                        if sub.pending is not None:
                            validate_pending(sub.pending, key, sub)
                    remaining = entry.get("remaining_timeout")
                    if opted_in and entry.get("deadline_at") is not None:
                        deadline_at = positive_seconds(entry["deadline_at"])
                        remaining = max(0.0, deadline_at - time.time())
                    if remaining is not None:
                        if opted_in and remaining != 0:
                            positive_seconds(remaining)
                        elif opted_in and (type(remaining) not in (int, float)):
                            raise ValueError("invalid timeout")
                        sub.deadline = time.monotonic() + float(remaining)
                    bucket = self._subscribers.setdefault(key, {})
                    if opted_in and sub.subscriber_id in bucket:
                        raise ValueError("duplicate registration")
                    bucket[sub.subscriber_id] = sub
                    restored += 1
                except (KeyError, TypeError, ValueError, OverflowError):
                    if opted_in:
                        raise ValueError("invalid persisted acknowledged subscription") from None
            return restored
