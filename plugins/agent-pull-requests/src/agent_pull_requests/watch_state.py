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
                        "baseline": (
                            {name: getattr(sub.baseline, name) for name in (
                                "merged", "closed", "review_decision", "mergeable", "checks_state",
                            )} if sub.baseline is not None else None
                        ),
                        "remaining_timeout": (
                            max(0.0, sub.deadline - now) if sub.deadline is not None else None
                        ),
                    }
                    if sub.acknowledged:
                        entry.update(
                            notification_protocol=ACKNOWLEDGED_NOTIFICATIONS,
                            registration_id=sub.registration_id, pending=sub.pending,
                            deadline_at=sub.deadline_at,
                        )
                    if key.worktrees_command:
                        entry.update(
                            worktrees_command=key.worktrees_command,
                            target_id=sub.target_id,
                            native_baseline=(
                                {"head_sha": sub.baseline.head_sha,
                                 "fingerprints": sub.baseline.fingerprints}
                                if sub.baseline is not None else None
                            ),
                        )
                    elif sub.baseline is not None and sub.baseline.head_sha is not None:
                        entry["baseline_head_sha"] = sub.baseline.head_sha
                    if sub.continuous:
                        entry.update(
                            continuous=True, queued=sub.queued,
                            terminal_observed=sub.terminal_observed,
                        )
                    out.append(entry)
            return copy.deepcopy(out)

    def restore_state(self, entries: list[dict], *, strict: bool = False) -> int:
        from .watch_registry import Subscriber, WatchKey

        restored = 0
        identities = set()
        with self._lock:
            for entry in entries:
                durable_collision = False
                opted_in = isinstance(entry, dict) and (
                    "notification_protocol" in entry or "pending" in entry
                    or "registration_id" in entry or "deadline_at" in entry
                    or "continuous" in entry or "queued" in entry
                    or "native_baseline" in entry or "worktrees_command" in entry
                )
                try:
                    if not isinstance(entry, dict):
                        raise ValueError("invalid registration")
                    if strict and (
                        not isinstance(entry, dict)
                        or any(
                            not isinstance(entry.get(field), str) or not entry[field].strip()
                            for field in ("repo", "subscriber_id")
                        )
                        or type(entry.get("number")) is not int or entry["number"] <= 0
                        or not isinstance(entry.get("until"), list) or not entry["until"]
                        or any(value not in ALL_TRANSITIONS for value in entry["until"])
                        or not isinstance(entry.get("notify", {}), dict)
                    ):
                        raise ValueError("invalid registration")
                    if opted_in and entry.get("notification_protocol") != ACKNOWLEDGED_NOTIFICATIONS:
                        raise ValueError("unsupported notification protocol")
                    if opted_in and any(
                        not isinstance(entry.get(field), str) or not entry[field].strip()
                        for field in ("repo", "subscriber_id", "registration_id")
                    ):
                        raise ValueError("invalid acknowledged identity")
                    command = entry.get("worktrees_command", "")
                    if not isinstance(command, str):
                        raise ValueError("invalid native command")
                    if command:
                        from pathlib import Path

                        if not Path(command).is_absolute() or "\0" in command:
                            raise ValueError("invalid native command")
                    if "native_baseline" in entry and not command:
                        raise ValueError("native baseline requires an attributed source")
                    key = WatchKey(
                        repo=str(entry["repo"]), number=int(entry["number"]),
                        worktrees_command=command,
                    )
                    baseline = entry.get("baseline")
                    if (opted_in or strict) and baseline is not None:
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
                    if "baseline_head_sha" in entry:
                        head = entry["baseline_head_sha"]
                        if (
                            command or sub.baseline is None
                            or head is not None and not isinstance(head, str)
                        ):
                            raise ValueError("invalid persisted baseline head")
                        sub.baseline = Baseline(**baseline, head_sha=head)
                    if command:
                        if not isinstance(entry.get("target_id"), str) or not entry["target_id"]:
                            raise ValueError("native registration requires a bound target")
                        sub.target_id = entry["target_id"]
                    native_baseline = entry.get("native_baseline")
                    if native_baseline is not None:
                        if (
                            not command or sub.baseline is None
                            or not isinstance(native_baseline, dict)
                            or set(native_baseline) != {"head_sha", "fingerprints"}
                            or native_baseline["head_sha"] is not None
                            and not isinstance(native_baseline["head_sha"], str)
                            or not isinstance(native_baseline["fingerprints"], dict)
                            or any(
                                name not in (
                                    "votes", "approvals", "discussions", "policies", "gates", "readiness",
                                )
                                or not isinstance(value, str)
                                for name, value in native_baseline["fingerprints"].items()
                            )
                        ):
                            raise ValueError("invalid native baseline")
                        sub.baseline = Baseline(**baseline, **native_baseline)
                    continuous = entry.get("continuous", False)
                    if type(continuous) is not bool or continuous and not (opted_in and command):
                        raise ValueError("invalid continuous registration")
                    sub.continuous = continuous
                    if not continuous and ("queued" in entry or "terminal_observed" in entry):
                        raise ValueError("continuous state cannot downgrade a queued event")
                    if continuous:
                        if type(entry.get("terminal_observed")) is not bool:
                            raise ValueError("invalid terminal observation")
                        sub.terminal_observed = entry["terminal_observed"]
                        sub.queued = copy.deepcopy(entry.get("queued"))
                    if strict:
                        sub.notify = validate_notify(sub.notify, opted_in)
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
                        if sub.queued is not None:
                            if sub.pending is None:
                                raise ValueError("queued event requires pending delivery")
                            validate_pending(sub.queued, key, sub)
                            if sub.queued["event_id"] == sub.pending["event_id"]:
                                raise ValueError("queued event must have its own identity")
                        if sub.terminal_observed and not any(
                            event is not None and (
                                event["payload"]["timed_out"]
                                or event["payload"].get("merged")
                                or event["payload"].get("pr_state") == "closed"
                            ) for event in (sub.pending, sub.queued)
                        ):
                            raise ValueError("terminal observation requires a final event")
                    remaining = entry.get("remaining_timeout")
                    if opted_in and entry["deadline_at"] is None and remaining is not None:
                        raise ValueError("inconsistent acknowledged deadline")
                    if opted_in and entry.get("deadline_at") is not None:
                        sub.deadline_at = positive_seconds(entry["deadline_at"])
                        remaining = max(0.0, sub.deadline_at - time.time())
                    if remaining is not None:
                        if (opted_in or strict) and remaining != 0:
                            positive_seconds(remaining)
                        elif (opted_in or strict) and (type(remaining) not in (int, float)):
                            raise ValueError("invalid timeout")
                        sub.deadline = time.monotonic() + float(remaining)
                    bucket = self._subscribers.setdefault(key, {})
                    if sub.subscriber_id in bucket and (
                        opted_in or strict or bucket[sub.subscriber_id].acknowledged
                    ):
                        durable_collision = True
                        raise ValueError("duplicate registration")
                    bucket[sub.subscriber_id] = sub
                    restored += 1
                except (KeyError, TypeError, ValueError, OverflowError):
                    if strict:
                        raise ValueError("invalid persisted watch subscription") from None
                    if opted_in or durable_collision:
                        raise ValueError("invalid persisted acknowledged subscription") from None
            return restored
