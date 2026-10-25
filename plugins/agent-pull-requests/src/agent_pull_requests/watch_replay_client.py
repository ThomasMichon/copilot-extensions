"""Optional finite Gateway replay client; no waiter, retries or local persistence."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Literal

from fleet_manager.gateway_service_caller import ServiceCaller
from fleet_manager.gateway_service_channel import TransportError, target as service_target

from .watch_contract import ALL_TRANSITIONS
from .watch_notification import positive_seconds, validate_pending
from .watch_registry import WatchKey
from .watch_replay import MAX_BATCH, MAX_BATCH_BYTES, RETENTION_SECONDS
from .watch_replay_binding import SCOPED_PROTOCOL, ReplayBinding
from .watch_replay_store import json_object

PROTOCOL = "agent-pull-requests.watch/v1"
_OPERATIONS = ("capabilities", "register", "inspect", "pending", "acknowledge", "cancel")


def _identity(value: object, maximum: int = 256) -> str:
    if (not isinstance(value, str) or not value or len(value) > maximum
            or any(ord(char) < 32 for char in value)):
        raise ValueError("invalid replay identity")
    return value


def _timestamp(value: object) -> float:
    normalized = positive_seconds(value)
    if normalized != value:
        raise ValueError("replay timestamp cannot be represented exactly")
    return normalized


@dataclass(frozen=True)
class ReplayRequest:
    """Persist this original identity in the consumer before its first attempt."""

    request_id: str
    issued_at: float

    def __post_init__(self) -> None:
        _identity(self.request_id, 128)
        _timestamp(self.issued_at)


@dataclass(frozen=True)
class ReplayReceipt:
    status: Literal["active", "acknowledged", "cancelled"]
    registration_id: str
    deadline_at: float | None
    event_id: str | None


@dataclass(frozen=True)
class ReplayOutcome:
    status: Literal["uncertain", "conflict", "unavailable"]
    reason: str | None = None


@dataclass(frozen=True)
class ReplayCapabilities:
    protocol: str
    operations: tuple[str, ...]
    max_batch: int
    max_batch_bytes: int
    receipt_retention_seconds: int
    binding: ReplayBinding | None = None


@dataclass(frozen=True)
class ReplayEvent:
    repo: str
    number: int
    registration_id: str
    event_id: str
    _json: bytes = field(repr=False)

    def payload(self) -> dict:
        """Return a detached copy, retaining the complete native notification."""
        return json_object(self._json)


@dataclass(frozen=True)
class ReplayBatch:
    events: tuple[ReplayEvent, ...]
    more: bool


class ReplayClientError(ValueError):
    """Value-free refusal; no negative proof about an earlier request attempt."""

    def __init__(self, code: str, *, http_status: int | None = None,
                 outcome_unknown: bool = False) -> None:
        self.code, self.http_status = code, http_status
        self.outcome_unknown = outcome_unknown
        super().__init__(code)


class ReplayClient:
    """One configured target over the authenticated Fleet SDK.

    The caller owns credentials and admission. Expected repository is a response
    check, not an authority label sent to the service. The consumer must commit
    its own effect before acknowledging the exact event, and retain the original
    request/registration for reconciliation after any uncertain result.
    Cancellation propagates and may leave a transmitted operation committed.
    No method negotiates fallback, retries or creates a replacement identity.
    """

    def __init__(self, caller: ServiceCaller, *, route_prefix: str,
                 target: str, repo: str, binding: ReplayBinding | None = None) -> None:
        if not isinstance(target, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", target):
            raise ValueError("invalid replay target")
        if not isinstance(route_prefix, str) or route_prefix.endswith("/"):
            raise ValueError("invalid replay route prefix")
        if binding is not None and (
            type(binding) is not ReplayBinding or (binding.target, binding.repo) != (target, repo)
        ):
            raise ValueError("invalid replay binding")
        self._binding = binding
        self._protocol = SCOPED_PROTOCOL if binding is not None else PROTOCOL
        path = route_prefix + ("/v2/watch/" if binding is not None else "/v1/watch/") + target
        canonical, query = service_target(path)
        if canonical != path or query:
            raise ValueError("invalid replay route prefix")
        self._caller, self._path = caller, path
        self._repo = _identity(repo)

    async def _exchange(self, operation: str, payload: dict) -> dict:
        mutation = operation in ("register", "acknowledge", "cancel")
        if self._binding is not None and operation != "capabilities":
            payload = {"binding": self._binding.document(), "request": payload}
        try:
            response = await self._caller.request(
                "GET" if operation == "capabilities" else "POST",
                self._path + "/" + operation,
                request_headers=(("Content-Type", "application/json"),),
                body=b"" if operation == "capabilities" else json.dumps(
                    payload, allow_nan=False,
                ).encode("utf-8"),
                response_limit=MAX_BATCH_BYTES,
            )
        except TransportError as exc:
            raise ReplayClientError(
                exc.code, http_status=exc.status, outcome_unknown=exc.code == "outcome-unknown",
            ) from None
        if response.status != 200:
            raise ReplayClientError(
                "watch_http_refused", http_status=response.status,
                outcome_unknown=mutation,
            )
        try:
            content_types = [v for k, v in response.headers if k.lower() == "content-type"]
            if (len(response.body) > MAX_BATCH_BYTES or len(content_types) != 1
                    or content_types[0].split(";")[0].strip().lower() != "application/json"):
                raise ValueError
            return json_object(response.body)
        except (ValueError, UnicodeError, RecursionError, OverflowError):
            raise ReplayClientError("invalid_watch_response", outcome_unknown=mutation) from None

    @staticmethod
    def _outcome(value: dict, operation: str) -> ReplayOutcome | None:
        status = value.get("status")
        allowed = {
            "uncertain": {None, "state_commit_uncertain", "request_outside_retention"},
            "conflict": {None} if operation == "register" else set(),
            "unavailable": {"replay_capacity"} if operation == "register" else set(),
        }
        if isinstance(status, str) and status in allowed:
            if (set(value) - {"status", "reason"}
                    or value.get("reason") not in allowed[status]):
                raise ValueError
            return ReplayOutcome(status, value.get("reason"))
        return None

    async def _receipt(self, operation: str, payload: dict) -> ReplayReceipt | ReplayOutcome:
        value = await self._exchange(operation, payload)
        try:
            outcome = self._outcome(value, operation)
            if outcome is not None:
                return outcome
            if set(value) != {"status", "registration_id", "deadline_at", "event_id"}:
                raise ValueError
            status, event = value["status"], value["event_id"]
            registration = _identity(value["registration_id"])
            deadline = value["deadline_at"]
            if deadline is not None:
                deadline = _timestamp(deadline)
            if (status not in ("active", "acknowledged", "cancelled")
                    or (status != "acknowledged" and event is not None)):
                raise ValueError
            if status == "acknowledged":
                _identity(event)
            if operation in ("cancel", "acknowledge") and (
                registration != payload["registration_id"]
                or status != ("cancelled" if operation == "cancel" else "acknowledged")
                or (operation == "acknowledge" and event != payload["event_id"])
            ):
                raise ValueError
            if operation == "register" and deadline != payload["deadline_at"]:
                raise ValueError
            return ReplayReceipt(status, registration, deadline, event)
        except (ValueError, TypeError, OverflowError):
            raise ReplayClientError(
                "invalid_watch_response", outcome_unknown=operation != "inspect",
            ) from None

    @staticmethod
    def _request(request: ReplayRequest) -> dict:
        if type(request) is not ReplayRequest:
            raise ValueError("expected original replay request")
        return {"request_id": request.request_id, "issued_at": request.issued_at}

    async def capabilities(self) -> ReplayCapabilities:
        value = await self._exchange("capabilities", {})
        fields = {"protocol", "operations", "max_batch", "max_batch_bytes",
                  "receipt_retention_seconds"}
        if self._binding is not None:
            fields.add("binding")
        if (
            set(value) != fields or value["protocol"] != self._protocol
            or (self._binding is not None and value["binding"] != self._binding.document())
            or not isinstance(value["operations"], list)
            or any(not isinstance(item, str) for item in value["operations"])
            or sorted(value["operations"]) != sorted(_OPERATIONS)
            or any(type(value[name]) is not int or value[name] != expected for name, expected in (
                ("max_batch", MAX_BATCH), ("max_batch_bytes", MAX_BATCH_BYTES),
                ("receipt_retention_seconds", RETENTION_SECONDS),
            ))
        ):
            raise ReplayClientError("invalid_watch_capabilities")
        return ReplayCapabilities(
            self._protocol, tuple(value["operations"]), MAX_BATCH, MAX_BATCH_BYTES,
            RETENTION_SECONDS, self._binding,
        )

    async def register(self, request: ReplayRequest, *, number: int,
                       until: tuple[str, ...], deadline_at: float | None = None,
                       ) -> ReplayReceipt | ReplayOutcome:
        payload = self._request(request)
        if (type(number) is not int or number <= 0 or not isinstance(until, tuple) or not until
                or any(not isinstance(item, str) or item not in ALL_TRANSITIONS for item in until)
                or len(set(until)) != len(until)):
            raise ValueError("invalid replay watch criteria")
        if deadline_at is not None:
            _timestamp(deadline_at)
        payload.update(number=number, until=list(until), deadline_at=deadline_at)
        return await self._receipt("register", payload)

    async def inspect(self, request: ReplayRequest) -> ReplayReceipt | ReplayOutcome:
        return await self._receipt("inspect", self._request(request))

    async def cancel(self, request: ReplayRequest, *, registration_id: str,
                     ) -> ReplayReceipt | ReplayOutcome:
        return await self._receipt(
            "cancel", dict(self._request(request), registration_id=_identity(registration_id)),
        )

    async def acknowledge(self, request: ReplayRequest, *, registration_id: str, event_id: str,
                          ) -> ReplayReceipt | ReplayOutcome:
        return await self._receipt("acknowledge", dict(
            self._request(request), registration_id=_identity(registration_id),
            event_id=_identity(event_id),
        ))

    async def pending(self, *, limit: int = MAX_BATCH) -> ReplayBatch | ReplayOutcome:
        if type(limit) is not int or not 1 <= limit <= MAX_BATCH:
            raise ValueError("invalid replay batch limit")
        value = await self._exchange("pending", {"limit": limit})
        try:
            outcome = self._outcome(value, "pending")
            if outcome is not None:
                return outcome
            if (set(value) != {"events", "more"} or type(value["more"]) is not bool
                    or not isinstance(value["events"], list) or len(value["events"]) > limit
                    or (value["more"] and not value["events"])):
                raise ValueError
            events = []
            registrations = set()
            for payload in value["events"]:
                if not isinstance(payload, dict):
                    raise ValueError
                registration = _identity(payload.get("registration_id"))
                event = _identity(payload.get("event_id"))
                number = payload.get("number")
                if type(number) is not int or number <= 0 or registration in registrations:
                    raise ValueError
                registrations.add(registration)
                validate_pending(
                    {"payload": payload, "event_id": event, "attempts": 0,
                     "next_attempt": 0, "last_error": None},
                    WatchKey(self._repo, number),
                    SimpleNamespace(registration_id=registration, subscriber_id=registration),
                )
                events.append(ReplayEvent(
                    self._repo, number, registration, event,
                    json.dumps(payload, allow_nan=False).encode("utf-8"),
                ))
            return ReplayBatch(tuple(events), value["more"])
        except (ValueError, TypeError, KeyError, RecursionError, OverflowError):
            raise ReplayClientError("invalid_watch_response") from None
