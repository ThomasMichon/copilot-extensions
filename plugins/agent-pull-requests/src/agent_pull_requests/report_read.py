"""Finite existing-owner reports, deliberately separate from review generations."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import json
import math
import time
from types import MappingProxyType

from fleet_manager.gateway_service_channel import BODY_BYTES

from .native_diff import _report as diff_report
from .native_threads import _report as thread_report
from .owner_read import _label, _seconds
from .watch_replay import ConsumerScope
from .watch_replay_store import ReplayCallerExpired

PROTOCOL = "agent-pull-requests.report-read/v1"
PROFILE = "owner_executed_report/v1"
OPERATIONS = ("capabilities", "threads", "diff")


class ReportUnavailable(RuntimeError):
    """The existing selected owner cannot supply this bounded exchange."""


def report_scope(value: object) -> dict:
    """Validate configured routing facts, never observed-principal evidence."""
    if not isinstance(value, Mapping) or set(value) != {"provider", "endpoint", "account", "repo"}:
        raise ValueError("invalid report scope")
    return {name: _label(value[name]) for name in ("provider", "endpoint", "account", "repo")}


def parse_request(operation: str, value: object) -> dict:
    """Reject overrides and nonfinite/boolean numeric inputs before acquisition."""
    if operation not in OPERATIONS or not isinstance(value, dict):
        raise ValueError("invalid report operation")
    if operation == "capabilities":
        if value:
            raise ValueError("invalid report capabilities request")
        return {}
    if (
        set(value) != {"number", "budget_seconds"}
        or type(value["number"]) is not int or value["number"] <= 0
    ):
        raise ValueError("invalid report request")
    return {"number": value["number"], "budget_seconds": _seconds(value["budget_seconds"])}


def validate_report(operation: str, text: str, scope: dict, number: int) -> dict:
    report = (thread_report if operation == "threads" else diff_report)(text, scope["repo"], number)
    if report["provider"] != scope["provider"]:
        raise ValueError("report provider differs from selected owner")
    return report


def capabilities(scope: dict) -> dict:
    return {
        "protocol": PROTOCOL, "profile": PROFILE, "scope": dict(scope),
        "operations": list(OPERATIONS), "max_request_bytes": BODY_BYTES,
        "max_response_bytes": BODY_BYTES, "effects": "none",
    }


def validate_exchange(value: object, *, operation: str, scope: dict, number=None) -> dict:
    """Validate the whole exchange without normalizing or dropping native fields."""
    if not isinstance(value, dict):
        raise ValueError("invalid report response")
    if operation == "capabilities":
        if value != capabilities(scope):
            raise ValueError("unsupported report contract")
        return value
    required = {
        "protocol", "profile", "scope", "operation", "number", "owner_context_id",
        "observed_from", "observed_to", "effects", "owner_exit", "report",
    }
    if (
        set(value) != required or value["protocol"] != PROTOCOL or value["profile"] != PROFILE
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
    validate_report(operation, json.dumps(value["report"], allow_nan=False), scope, number)
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

    def __post_init__(self):
        object.__setattr__(self, "scope", MappingProxyType(report_scope(self.scope)))
        _label(self.context_id)
        if not callable(self.checkpoint) or not callable(self.collect):
            raise ValueError("live report owner callbacks required")
        if self.final_checkpoint is not None and not callable(self.final_checkpoint):
            raise ValueError("lightweight report publication callback required")


class ReportReader:
    """Embed in an admitted read pipeline, not a loader, grant or credential issuer."""

    def __init__(
        self, consumer_scope: ConsumerScope, scope: Mapping, resolve_binding: Callable, *,
        max_work_seconds: float, clock=time.monotonic, wall_clock=time.time,
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
        self.clock, self.wall_clock = clock, wall_clock

    def execute(self, operation, payload, *, expires_at, checkpoint, response_validator=None,
                publication_validator=None):
        parsed = parse_request(operation, payload)
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
        binding = self.resolve_binding(remaining)
        remaining()
        if not isinstance(binding, ReportBinding) or dict(binding.scope) != dict(self.scope):
            raise ReportUnavailable("selected report owner differs")

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
            value = capabilities(self.scope)
        else:
            started = self.wall_clock()
            result = binding.collect(operation, parsed["number"], current)
            current()
            if not isinstance(result, tuple) or len(result) != 2 or type(result[0]) is not int:
                raise ReportUnavailable("invalid report owner outcome")
            report = validate_report(operation, result[1], self.scope, parsed["number"])
            value = {
                "protocol": PROTOCOL, "profile": PROFILE, "scope": dict(self.scope),
                "operation": operation, "number": parsed["number"],
                "owner_context_id": binding.context_id,
                "observed_from": started, "observed_to": self.wall_clock(),
                "effects": "none", "owner_exit": result[0], "report": report,
            }
        validate_exchange(value, operation=operation, scope=dict(self.scope), number=parsed.get("number"))
        if response_validator is not None:
            response_validator(value, current)
        if publication_validator is not None:
            publication_validator(final_current)
        current()
        return value
