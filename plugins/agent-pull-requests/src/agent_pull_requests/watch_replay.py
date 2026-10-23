"""PR-owned pull-delivery domain core, not a serving or authentication adapter.

The embedding owner supplies verified scope, one writer and an atomic durable
commit for the complete envelope. There are no pollers, callbacks or routes here.
"""

from __future__ import annotations

import copy
import json
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from .watch_contract import ALL_TRANSITIONS, PRSnapshot
from .watch_notification import positive_seconds
from .watch_registry import WatchKey, WatchRegistry
from .watch_storage import StateCommitUncertain

SCHEMA = "agent-pull-requests.watch-replay/v1"
MAX_BATCH = 16
MAX_BATCH_BYTES = 24 * 1024
MAX_RECORDS = 1024
RETENTION_SECONDS = 86400


class ReplayEventTooLarge(ValueError):
    """A valid observation cannot fit one replay event's delivery budget."""


@dataclass(frozen=True)
class ConsumerScope:
    """Trusted adapter input; constructing this value does not authenticate it."""

    consumer: str
    account: str
    repo: str

    def __post_init__(self) -> None:
        for value in (self.consumer, self.account, self.repo):
            if (
                not isinstance(value, str) or not value or len(value) > 256
                or any(ord(char) < 32 for char in value)
            ):
                raise ValueError("invalid replay scope")


@dataclass(frozen=True)
class ObservationTarget:
    """One account-scoped read and the registrations present before it starts."""

    account: str
    repo: str
    number: int
    registration_ids: frozenset[str]


def _timestamp(value: object) -> float:
    try:
        normalized = positive_seconds(value)
    except OverflowError:
        raise ValueError("invalid replay timestamp") from None
    if normalized != value:
        raise ValueError("replay timestamp cannot be represented exactly")
    return normalized


def _check_event_size(payload: dict) -> None:
    if len(json.dumps({
        "events": [payload], "more": False,
    }, allow_nan=False).encode("utf-8")) > MAX_BATCH_BYTES:
        raise ReplayEventTooLarge("pending replay event exceeds batch byte limit")


class WatchReplay:
    """Single-owner transaction core reusing the native subscription registry.

    ``state=None`` is explicit first provisioning, never missing-file recovery.
    ``commit`` must atomically persist the supplied complete envelope before
    returning. A pre-replace failure leaves memory unchanged. An uncertain
    post-replace commit adopts memory but returns uncertainty to the caller.
    """

    def __init__(
        self, namespace: str, *, state: dict | None,
        commit: Callable[[dict], None], clock: Callable[[], float] = time.time,
    ) -> None:
        if not isinstance(namespace, str) or not namespace or len(namespace) > 256:
            raise ValueError("invalid replay namespace")
        self.namespace = namespace
        self._commit = commit
        self._clock = clock
        self._lock = threading.Lock()
        self._records: list[dict] = []
        self._registries: dict[tuple[str, str], WatchRegistry] = {}
        if state is not None:
            self._restore(state)

    def _restore(self, state: dict) -> None:
        try:
            if (
                set(state) != {"schema", "namespace", "records", "registries"}
                or state["schema"] != SCHEMA or state["namespace"] != self.namespace
                or not isinstance(state["records"], list)
                or len(state["records"]) > MAX_RECORDS
                or not isinstance(state["registries"], list)
                or len(state["registries"]) > MAX_RECORDS
            ):
                raise ValueError
            expected: dict[tuple[str, str], set[str]] = {}
            active_records: dict[tuple[str, str], list[tuple[ConsumerScope, dict]]] = {}
            identities = set()
            registration_ids = set()
            for record in state["records"]:
                if set(record) != {
                    "scope", "request_id", "issued_at", "number", "until", "deadline_at",
                    "registration_id", "outcome", "completed_at", "event_id",
                }:
                    raise ValueError
                scope = ConsumerScope(**record["scope"])
                token = self._token(record["request_id"], record["issued_at"])
                identity = (scope, token)
                if identity in identities:
                    raise ValueError
                identities.add(identity)
                if (
                    type(record["number"]) is not int or record["number"] <= 0
                    or not isinstance(record["until"], list) or not record["until"]
                    or any(item not in ALL_TRANSITIONS for item in record["until"])
                    or not isinstance(record["registration_id"], str)
                    or not record["registration_id"]
                    or record["registration_id"] in registration_ids
                    or record["outcome"] not in {"active", "acknowledged", "cancelled"}
                ):
                    raise ValueError
                registration_ids.add(record["registration_id"])
                if record["deadline_at"] is not None:
                    _timestamp(record["deadline_at"])
                if record["outcome"] == "active":
                    expected.setdefault((scope.account, scope.repo), set()).add(
                        record["registration_id"],
                    )
                    active_records.setdefault((scope.account, scope.repo), []).append((scope, record))
                    if record["completed_at"] is not None or record["event_id"] is not None:
                        raise ValueError
                else:
                    _timestamp(record["completed_at"])
                    if record["completed_at"] < record["issued_at"]:
                        raise ValueError
                    if record["outcome"] == "acknowledged" and (
                        not isinstance(record["event_id"], str) or not record["event_id"]
                    ):
                        raise ValueError
                    if record["outcome"] == "cancelled" and record["event_id"] is not None:
                        raise ValueError
            registries = {}
            for group in state["registries"]:
                if (
                    set(group) != {"account", "repo", "subscriptions"}
                    or not isinstance(group["subscriptions"], list)
                    or len(group["subscriptions"]) > MAX_RECORDS
                ):
                    raise ValueError
                key = (group["account"], group["repo"])
                if key in registries or key not in expected:
                    raise ValueError
                registry = WatchRegistry()
                registry.restore_state(group["subscriptions"], strict=True)
                for event in registry.pending_events():
                    _check_event_size(event.frozen_payload)
                entries = registry.snapshot_state()
                if (
                    {entry["registration_id"] for entry in entries} != expected[key]
                    or len(entries) != len(expected[key])
                    or any(
                        entry.get("delivery_mode") != "pull"
                        or entry["repo"] != key[1] or entry.get("worktrees_command")
                        or entry.get("continuous")
                        for entry in entries
                    )
                ):
                    raise ValueError
                for scope, record in active_records[key]:
                    sub = registry.subscriber(
                        WatchKey(scope.repo, record["number"]), record["registration_id"],
                    )
                    if (
                        sub is None or sub.registration_id != record["registration_id"]
                        or list(sub.until) != record["until"]
                        or sub.deadline_at != record["deadline_at"]
                    ):
                        raise ValueError
                    if record["deadline_at"] is not None:
                        sub.deadline = time.monotonic() + max(
                            0, record["deadline_at"] - self._clock(),
                        )
                registries[key] = registry
            if set(registries) != set(expected):
                raise ValueError
            self._records = copy.deepcopy(state["records"])
            self._registries = registries
        except (KeyError, TypeError, ValueError, OverflowError):
            raise ValueError("invalid persisted watch replay state") from None

    @staticmethod
    def _token(request_id: str, issued_at: float) -> tuple[str, float]:
        if (
            not isinstance(request_id, str) or not request_id or len(request_id) > 128
            or any(ord(char) < 32 for char in request_id)
        ):
            raise ValueError("invalid replay request identity")
        return request_id, _timestamp(issued_at)

    def _snapshot(self) -> dict:
        return copy.deepcopy({
            "schema": SCHEMA, "namespace": self.namespace, "records": self._records,
            "registries": [
                {"account": account, "repo": repo, "subscriptions": registry.snapshot_state()}
                for (account, repo), registry in self._registries.items()
                if registry.active_keys()
            ],
        })

    def snapshot(self) -> dict:
        with self._lock:
            return self._snapshot()

    def _find(self, scope: ConsumerScope, request_id: str, issued_at: float) -> dict | None:
        token = self._token(request_id, issued_at)
        now = self._clock()
        return next((
            record for record in self._records
            if record["scope"] == vars(scope)
            and (record["request_id"], record["issued_at"]) == token
            and (
                record["outcome"] == "active"
                or record["completed_at"] + RETENTION_SECONDS > now
            )
        ), None)

    def _change(
        self, operation: Callable[[WatchReplay], dict], *, skip_unchanged: bool = False,
    ) -> dict:
        with self._lock:
            before = self._snapshot()
            staged = WatchReplay(
                self.namespace, state=before, commit=self._commit, clock=self._clock,
            )
            staged._records = [
                record for record in staged._records
                if record["outcome"] == "active"
                or record["completed_at"] + RETENTION_SECONDS > self._clock()
            ]
            result = operation(staged)
            envelope = staged._snapshot()
            comparable = envelope
            if skip_unchanged:
                comparable = copy.deepcopy(envelope)
                # Replay deadlines are absolute; elapsed monotonic projections
                # alone are not a durable state change.
                for state in (before, comparable):
                    for group in state["registries"]:
                        for entry in group["subscriptions"]:
                            entry.pop("remaining_timeout", None)
            if not skip_unchanged or comparable != before:
                try:
                    self._commit(envelope)
                except StateCommitUncertain:
                    self._records, self._registries = staged._records, staged._registries
                    return {"status": "uncertain", "reason": "state_commit_uncertain"}
            self._records, self._registries = staged._records, staged._registries
            return result

    def register(
        self, scope: ConsumerScope, request_id: str, issued_at: float, *,
        number: int, until: tuple[str, ...], deadline_at: float | None = None,
    ) -> dict:
        self._token(request_id, issued_at)
        if (
            type(number) is not int or number <= 0 or not isinstance(until, tuple) or not until
            or any(item not in ALL_TRANSITIONS for item in until)
            or len(set(until)) != len(until)
        ):
            raise ValueError("invalid replay watch criteria")
        if deadline_at is not None:
            _timestamp(deadline_at)

        def accept(staged: WatchReplay) -> dict:
            previous = staged._find(scope, request_id, issued_at)
            if previous is not None:
                if (
                    previous["number"] != number or previous["until"] != list(until)
                    or previous["deadline_at"] != deadline_at
                ):
                    return {"status": "conflict"}
                return staged._receipt(previous)
            now = staged._clock()
            if not now - RETENTION_SECONDS < issued_at <= now:
                return {"status": "uncertain", "reason": "request_outside_retention"}
            if len(staged._records) >= MAX_RECORDS:
                return {"status": "unavailable", "reason": "replay_capacity"}
            identity = uuid.uuid4().hex
            record = {
                "scope": dict(vars(scope)), "request_id": request_id, "issued_at": issued_at,
                "number": number, "until": list(until), "deadline_at": deadline_at,
                "registration_id": identity, "outcome": "active", "completed_at": None,
                "event_id": None,
            }
            registry = staged._registries.setdefault((scope.account, scope.repo), WatchRegistry())
            sub = registry.register(
                WatchKey(scope.repo, number), identity, until=until, notify={},
                acknowledged=True, delivery_mode="pull",
            )
            sub.registration_id = identity
            if deadline_at is not None:
                sub.deadline_at = deadline_at
                sub.deadline = time.monotonic() + max(0, deadline_at - now)
            staged._records.append(record)
            return staged._receipt(record)

        return self._change(accept)

    @staticmethod
    def _receipt(record: dict) -> dict:
        return {
            "status": record["outcome"], "registration_id": record["registration_id"],
            "deadline_at": record["deadline_at"], "event_id": record["event_id"],
        }

    def inspect(self, scope: ConsumerScope, request_id: str, issued_at: float) -> dict:
        with self._lock:
            record = self._find(scope, request_id, issued_at)
            return self._receipt(record) if record else {"status": "uncertain"}

    def observation_targets(self) -> tuple[ObservationTarget, ...]:
        """Coalesce waiting subscriptions, excluding already pending delivery."""
        with self._lock:
            groups: dict[tuple[str, str, int], set[str]] = {}
            for record in self._records:
                if record["outcome"] != "active":
                    continue
                scope = ConsumerScope(**record["scope"])
                registry = self._registries[(scope.account, scope.repo)]
                sub = registry.subscriber(
                    WatchKey(scope.repo, record["number"]), record["registration_id"],
                )
                if sub.pending is None:
                    groups.setdefault((scope.account, scope.repo, record["number"]), set()).add(
                        record["registration_id"],
                    )
            return tuple(
                ObservationTarget(*key, frozenset(identities))
                for key, identities in sorted(groups.items())
            )

    def waiting_target(self, target: ObservationTarget) -> ObservationTarget | None:
        """Refresh eligibility without admitting registrations added since capture."""
        with self._lock:
            registry = self._registries.get((target.account, target.repo))
            if registry is None:
                return None
            waiting = frozenset(
                identity for identity in target.registration_ids
                if (sub := registry.subscriber(WatchKey(target.repo, target.number), identity))
                is not None and sub.pending is None
            )
            return (
                ObservationTarget(target.account, target.repo, target.number, waiting)
                if waiting else None
            )

    def observe(
        self, account: str, repo: str, number: int, snapshot: PRSnapshot, *,
        registration_ids: frozenset[str] | None = None,
    ) -> dict:
        """Apply one already-authorized observation to all matching subscriptions."""
        def apply(staged: WatchReplay) -> dict:
            registry = staged._registries.get((account, repo))
            if registry is not None:
                fired = registry.apply_snapshot(
                    WatchKey(repo, number), snapshot, registration_ids=registration_ids,
                )
                for event in fired:
                    _check_event_size(event.frozen_payload)
            return {"status": "observed"}

        return self._change(apply)

    def sweep_timeouts(self) -> dict:
        def sweep(staged: WatchReplay) -> dict:
            for registry in staged._registries.values():
                registry.sweep_timeouts()
            return {"status": "swept"}

        return self._change(sweep, skip_unchanged=True)

    def pending(self, scope: ConsumerScope, *, limit: int = MAX_BATCH) -> dict:
        if type(limit) is not int or not 1 <= limit <= MAX_BATCH:
            raise ValueError("invalid replay batch limit")
        with self._lock:
            registry = self._registries.get((scope.account, scope.repo))
            owned = {
                record["registration_id"] for record in self._records
                if record["scope"] == vars(scope) and record["outcome"] == "active"
            }
            events = (
                sorted(
                    (event for event in registry.pending_events(delivery_mode="pull")
                     if event.subscriber.registration_id in owned),
                    key=lambda event: event.subscriber.registration_id,
                ) if registry is not None else []
            )
            result = {"events": [], "more": False}
            for event in events:
                payload = copy.deepcopy(event.frozen_payload)
                result["events"].append(payload)
                if len(json.dumps(result, allow_nan=False).encode("utf-8")) > MAX_BATCH_BYTES:
                    result["events"].pop()
                    if not result["events"]:
                        raise ReplayEventTooLarge("pending replay event exceeds batch byte limit")
                    break
                if len(result["events"]) == limit:
                    break
            result["more"] = len(events) > len(result["events"])
            return result

    def acknowledge(
        self, scope: ConsumerScope, request_id: str, issued_at: float, *,
        registration_id: str, event_id: str,
    ) -> dict:
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("invalid replay acknowledgement identity")
        return self._finish(
            scope, request_id, issued_at, registration_id=registration_id, event_id=event_id,
        )

    def cancel(
        self, scope: ConsumerScope, request_id: str, issued_at: float, *,
        registration_id: str,
    ) -> dict:
        return self._finish(scope, request_id, issued_at, registration_id=registration_id)

    def _finish(
        self, scope: ConsumerScope, request_id: str, issued_at: float, *,
        registration_id: str, event_id: str | None = None,
    ) -> dict:
        """Complete the exact registration; public entrypoints distinguish ACK/cancel."""
        for identity in (registration_id,) + ((event_id,) if event_id is not None else ()):
            if not isinstance(identity, str) or not identity:
                raise ValueError("invalid replay completion identity")

        def finish(staged: WatchReplay) -> dict:
            record = staged._find(scope, request_id, issued_at)
            if record is None or record["registration_id"] != registration_id:
                return {"status": "uncertain"}
            outcome = "acknowledged" if event_id is not None else "cancelled"
            if record["outcome"] != "active":
                if record["outcome"] == outcome and record["event_id"] == event_id:
                    return staged._receipt(record)
                return {"status": "uncertain"}
            registry = staged._registries[(scope.account, scope.repo)]
            key = WatchKey(scope.repo, record["number"])
            sub = registry.subscriber(key, registration_id)
            if event_id is not None and (
                sub.pending is None or sub.pending["event_id"] != event_id
            ):
                return {"status": "uncertain"}
            registry.unregister(key, registration_id, registration_id=registration_id)
            record.update(outcome=outcome, event_id=event_id, completed_at=staged._clock())
            return staged._receipt(record)

        return self._change(finish)
