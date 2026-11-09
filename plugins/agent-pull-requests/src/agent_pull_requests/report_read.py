"""Finite existing-owner reports, deliberately separate from review generations."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import json
import math
import time
from types import MappingProxyType

from fleet_manager.gateway_service_channel import BODY_BYTES, RESPONSE_BYTES

from .native_diff import _report as diff_report
from .native_threads import _report as thread_report
from .owner_read import _label, _seconds
from .watch_replay import ConsumerScope
from .watch_replay_store import ReplayCallerExpired

PROTOCOL = "agent-pull-requests.report-read/v1"
PROFILE = "owner_executed_report/v1"
OPERATIONS = ("capabilities", "threads", "diff")


def response_budget(value: int) -> int:
    """Validate a trusted, finite response selection without granting support."""
    if type(value) is not int or not BODY_BYTES <= value <= RESPONSE_BYTES:
        raise ValueError("invalid report response budget")
    return value


def _contract(response_limit: int) -> tuple[str, str]:
    response_budget(response_limit)
    if response_limit == BODY_BYTES:
        return PROTOCOL, PROFILE
    return "agent-pull-requests.report-read/v2", "owner_executed_report/v2"


class ReportUnavailable(RuntimeError):
    """The existing selected owner cannot supply this bounded exchange."""


def report_scope(value: object) -> dict:
    """Validate configured routing facts, never observed-principal evidence."""
    if not isinstance(value, Mapping) or set(value) != {"provider", "endpoint", "account", "repo"}:
        raise ValueError("invalid report scope")
    return {name: _label(value[name]) for name in ("provider", "endpoint", "account", "repo")}


def parse_request(operation: str, value: object, *, response_limit: int = BODY_BYTES) -> dict:
    """Reject overrides and nonfinite/boolean numeric inputs before acquisition."""
    response_budget(response_limit)
    if operation not in OPERATIONS or not isinstance(value, dict):
        raise ValueError("invalid report operation")
    if operation == "capabilities":
        if value:
            raise ValueError("invalid report capabilities request")
        return {}
    fields = {"number", "budget_seconds"}
    if response_limit > BODY_BYTES:
        fields.add("expected_response_bytes")
    if (
        set(value) != fields
        or type(value["number"]) is not int or value["number"] <= 0
    ):
        raise ValueError("invalid report request")
    parsed = {"number": value["number"], "budget_seconds": _seconds(value["budget_seconds"])}
    if response_limit > BODY_BYTES:
        if (
            type(value["expected_response_bytes"]) is not int
            or value["expected_response_bytes"] != response_limit
        ):
            raise ValueError("report response expectation differs")
        parsed["expected_response_bytes"] = response_limit
    return parsed


def validate_report(operation: str, text: str, scope: dict, number: int, *,
                    response_limit: int = BODY_BYTES) -> dict:
    response_budget(response_limit)
    options = {"max_bytes": response_limit} if response_limit > BODY_BYTES else {}
    report = (thread_report if operation == "threads" else diff_report)(
        text, scope["repo"], number, **options,
    )
    if report["provider"] != scope["provider"]:
        raise ValueError("report provider differs from selected owner")
    return report


def capabilities(scope: dict, *, response_limit: int = BODY_BYTES) -> dict:
    protocol, profile = _contract(response_limit)
    return {
        "protocol": protocol, "profile": profile, "scope": dict(scope),
        "operations": list(OPERATIONS), "max_request_bytes": BODY_BYTES,
        "max_response_bytes": response_limit, "effects": "none",
    }


def validate_exchange(value: object, *, operation: str, scope: dict, number=None,
                      response_limit: int = BODY_BYTES) -> dict:
    """Validate the whole exchange without normalizing or dropping native fields."""
    protocol, profile = _contract(response_limit)
    if not isinstance(value, dict):
        raise ValueError("invalid report response")
    if operation == "capabilities":
        if (
            type(value.get("max_request_bytes")) is not int
            or type(value.get("max_response_bytes")) is not int
            or value != capabilities(scope, response_limit=response_limit)
        ):
            raise ValueError("unsupported report contract")
        return value
    required = {
        "protocol", "profile", "scope", "operation", "number", "owner_context_id",
        "observed_from", "observed_to", "effects", "owner_exit", "report",
    }
    if response_limit > BODY_BYTES:
        required.add("max_response_bytes")
        if (
            type(value.get("max_response_bytes")) is not int
            or value["max_response_bytes"] != response_limit
        ):
            raise ValueError("report response expectation differs")
    if (
        set(value) != required or value["protocol"] != protocol or value["profile"] != profile
        or value["scope"] != scope or value["operation"] != operation
        or type(value["number"]) is not int or value["number"] != number
        or value["effects"] != "none" or type(value["owner_exit"]) is not int
    ):
        raise ValueError("invalid report exchange")
    _label(value["owner_context_id"])
    for name in ("observed_from", "observed_to"):
        if type(value[name]) not in (int, float) or not math.isfinite(value[name]) or value[name] < 0:
            raise ValueError("invalid report observation interval")
    if value["observed_to"] < value["observed_from"]:
        raise ValueError("invalid report observation interval")
    validate_report(operation, json.dumps(value["report"], allow_nan=False), scope, number,
                    response_limit=response_limit)
    return value


@dataclass(frozen=True)
class ReportBinding:
    """Trusted owner selection after its real target/account/credential gates.

    The checkpoint must enforce continued permission and selected owner identity.
    The synchronous collector owns transport/process settlement and returns the
    original exit status and complete JSON text, never raw helper diagnostics.
    A blocking checkpoint supplies a separate nonblocking final_checkpoint for
    the last event-loop deadline, authorization and binding recheck.
    """

    scope: Mapping
    context_id: str
    checkpoint: Callable[[], None] = field(repr=False)
    collect: Callable[[str, int, Callable[[], float]], tuple[int, str]] = field(repr=False)
    final_checkpoint: Callable[[], None] | None = field(default=None, repr=False)
    response_limit: int = field(default=BODY_BYTES, kw_only=True)

    def __post_init__(self):
        object.__setattr__(self, "scope", MappingProxyType(report_scope(self.scope)))
        _label(self.context_id)
        response_budget(self.response_limit)
        if not callable(self.checkpoint) or not callable(self.collect):
            raise ValueError("live report owner callbacks required")
        if self.final_checkpoint is not None and not callable(self.final_checkpoint):
            raise ValueError("lightweight report publication callback required")


class ReportReader:
    """Embed in an admitted read pipeline, not a loader, grant or credential issuer."""

    def __init__(
        self, consumer_scope: ConsumerScope, scope: Mapping, resolve_binding: Callable, *,
        max_work_seconds: float, clock=time.monotonic, wall_clock=time.time,
        response_limit: int = BODY_BYTES,
    ):
        selected = report_scope(scope)
        if (
            type(consumer_scope) is not ConsumerScope or not callable(resolve_binding)
            or (consumer_scope.account, consumer_scope.repo) != (selected["account"], selected["repo"])
        ):
            raise ValueError("report reader must use its actual admitted scope")
        self.consumer_scope = consumer_scope
        self.scope = MappingProxyType(selected)
        self.resolve_binding = resolve_binding
        self.max_work_seconds = _seconds(max_work_seconds)
        self.response_limit = response_budget(response_limit)
        self.clock, self.wall_clock = clock, wall_clock

    def execute(self, operation, payload, *, expires_at, checkpoint, response_validator=None,
                publication_validator=None, response_limit=BODY_BYTES):
        response_budget(response_limit)
        if response_limit > BODY_BYTES and response_limit != self.response_limit:
            raise ReportUnavailable("report response selection differs")
        protocol, profile = _contract(response_limit)
        parsed = parse_request(operation, payload, response_limit=response_limit)
        deadline = self.clock() + min(
            parsed.get("budget_seconds", self.max_work_seconds), self.max_work_seconds,
        )

        def remaining():
            checkpoint()
            if self.wall_clock() >= expires_at:
                raise ReplayCallerExpired
            left = deadline - self.clock()
            if left <= 0:
                raise ReportUnavailable("report deadline exhausted")
            return left

        remaining()
        binding = (
            self.resolve_binding(remaining, response_limit=response_limit)
            if response_limit > BODY_BYTES else self.resolve_binding(remaining)
        )
        remaining()
        if not isinstance(binding, ReportBinding) or dict(binding.scope) != dict(self.scope):
            raise ReportUnavailable("selected report owner differs")
        if binding.response_limit != response_limit:
            raise ReportUnavailable("selected report owner response budget differs")

        def current():
            remaining()
            if binding.checkpoint() is not None:
                raise ReportUnavailable("report owner checkpoint must enforce current selection")
            return remaining()

        def final_current():
            remaining()
            check = binding.final_checkpoint or binding.checkpoint
            if check() is not None:
                raise ReportUnavailable("report owner checkpoint must enforce current selection")
            return remaining()

        current()
        if operation == "capabilities":
            value = capabilities(self.scope, response_limit=response_limit)
        else:
            started = self.wall_clock()
            result = binding.collect(operation, parsed["number"], current)
            current()
            if not isinstance(result, tuple) or len(result) != 2 or type(result[0]) is not int:
                raise ReportUnavailable("invalid report owner outcome")
            report = validate_report(operation, result[1], self.scope, parsed["number"],
                                     response_limit=response_limit)
            value = {
                "protocol": protocol, "profile": profile, "scope": dict(self.scope),
                "operation": operation, "number": parsed["number"],
                "owner_context_id": binding.context_id,
                "observed_from": started, "observed_to": self.wall_clock(),
                "effects": "none", "owner_exit": result[0], "report": report,
            }
            if response_limit > BODY_BYTES:
                value["max_response_bytes"] = response_limit
        validate_exchange(value, operation=operation, scope=dict(self.scope), number=parsed.get("number"),
                          response_limit=response_limit)
        if response_validator is not None:
            response_validator(value, current)
        if publication_validator is not None:
            publication_validator(final_current)
        current()
        return value
