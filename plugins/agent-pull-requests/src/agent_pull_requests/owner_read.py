"""Protected-owner embedding contract; data labels never establish authority."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
import json
import math
import time
from types import MappingProxyType
from collections.abc import Callable, Mapping

from .review_workspace import MAX_GENERATION_BYTES, _scope, build_generation

PROFILE = "owner_executed_review_evidence/v1"
REQUEST_SCHEMA = "agent-pull-requests.owner-read-request"
RESPONSE_SCHEMA = "agent-pull-requests.owner-review-snapshot"
PRINCIPAL_SEMANTICS = "connectionData.authorizedUser.id"
MAX_REQUEST_BYTES = 8192


class OwnerReadUnavailable(RuntimeError):
    """The selected owner interface/current binding is unavailable, not a new grant."""


class OwnerReadBudgetExceeded(TimeoutError):
    """No result may be published after the operation's work deadline."""


class OwnerReadResponseTooLarge(ValueError):
    """The snapshot does not fit the executing owner's payload contract."""


def _label(value: object) -> str:
    if (
        not isinstance(value, str) or not 1 <= len(value) <= 256
        or value != value.strip() or any(ord(char) < 32 for char in value)
    ):
        raise ValueError("invalid owner-read identifier")
    return value


def _seconds(value: object) -> float:
    if type(value) not in (int, float):
        raise ValueError("owner-read budget must be finite and positive")
    try:
        result = float(value)
    except OverflowError:
        raise ValueError("owner-read budget must be finite and positive") from None
    if not math.isfinite(result) or result <= 0:
        raise ValueError("owner-read budget must be finite and positive")
    return result


def parse_request(value: object) -> dict:
    try:
        encoded = json.dumps(value, allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, RecursionError):
        raise ValueError("owner-read request must be bounded finite JSON") from None
    required = {"schema", "version", "binding_ref", "number", "budget_seconds"}
    if (
        len(encoded) > MAX_REQUEST_BYTES or not isinstance(value, dict)
        or not required <= set(value) or set(value) - required - {"expected_principal"}
        or value["schema"] != REQUEST_SCHEMA or type(value["version"]) is not int
        or value["version"] != 1
        or type(value["number"]) is not int or value["number"] <= 0
    ):
        raise ValueError("unsupported or malformed owner-read request")
    result = {
        "schema": REQUEST_SCHEMA, "version": 1,
        "binding_ref": _label(value["binding_ref"]), "number": value["number"],
        "budget_seconds": _seconds(value["budget_seconds"]),
    }
    if "expected_principal" in value:
        result["expected_principal"] = _label(value["expected_principal"])
    return result


@dataclass(frozen=True)
class OwnerReadBinding:
    """Supplied only by an existing protected owner after its normal gates.

    Constructing this type, its scope or its context ID does not authenticate
    anything. A resolver must not deserialize one from caller/forge JSON.
    """

    scope: Mapping
    context_id: str
    checkpoint: Callable[[], None] = field(repr=False)
    collect: Callable[[int, Callable[[], float]], dict] = field(repr=False)

    def __post_init__(self):
        if not isinstance(self.scope, Mapping) or "number" in self.scope:
            raise ValueError("owner binding requires a target scope without a PR number")
        scope = _scope({**dict(self.scope), "number": 1})
        scope.pop("number")
        object.__setattr__(self, "scope", MappingProxyType(copy.deepcopy(scope)))
        _label(self.context_id)
        if not callable(self.checkpoint) or not callable(self.collect):
            raise ValueError("owner binding requires live owner callbacks")


class OwnerReadHandler:
    """Invoke inside the existing owner's authenticated/authorized input pipeline.

    The owner supplies actual work/payload limits from that pipeline. The resolver
    selects only bindings authorized for its current caller; it is not a registry
    lookup that turns a caller's label into permission.
    """

    def __init__(
        self, resolve_binding: Callable, *, max_work_seconds: float,
        max_payload_bytes: int, clock=time.monotonic,
    ):
        if not callable(resolve_binding):
            raise OwnerReadUnavailable("an adopted protected read owner is required")
        if type(max_payload_bytes) is not int or max_payload_bytes <= 0:
            raise ValueError("the owner's actual payload budget is required")
        self.resolve_binding = resolve_binding
        self.max_work_seconds = _seconds(max_work_seconds)
        self.max_payload_bytes = min(max_payload_bytes, MAX_GENERATION_BYTES + 65536)
        self.clock = clock

    def read(self, request: object) -> dict:
        parsed = parse_request(request)
        started = self.clock()
        work_budget = min(parsed["budget_seconds"], self.max_work_seconds)
        deadline = started + work_budget

        def remaining():
            value = deadline - self.clock()
            if value <= 0:
                raise OwnerReadBudgetExceeded("owner-read work budget exhausted")
            return value

        binding = self.resolve_binding(parsed["binding_ref"], remaining)
        remaining()
        if not isinstance(binding, OwnerReadBinding):
            raise OwnerReadUnavailable("the selected owner did not supply an authorized binding")

        def checkpoint():
            remaining()
            if binding.checkpoint() is not None:
                raise OwnerReadUnavailable("owner checkpoint must enforce context, not return labels")
            remaining()

        checkpoint()
        scope = _scope({**dict(binding.scope), "number": parsed["number"]})
        if (
            "expected_principal" in parsed
            and parsed["expected_principal"] != scope["principal_id"]
        ):
            raise OwnerReadUnavailable("the owner's effective ADO principal does not match")
        evidence = binding.collect(parsed["number"], remaining)
        checkpoint()
        if not isinstance(evidence, dict) or _scope(evidence.get("scope")) != scope:
            raise OwnerReadUnavailable("the evidence does not match the owner's bound scope")
        acquisition = evidence.get("acquisition")
        if (
            not isinstance(acquisition, dict) or acquisition.get("source") != PROFILE
            or acquisition.get("effects") != "none"
            or acquisition.get("principal_semantics") != PRINCIPAL_SEMANTICS
        ):
            raise ValueError("the owner did not return the selected evidence contract")
        generation = build_generation(evidence)["generation"]
        checkpoint()
        allowed = (
            "source", "effects", "observed_from", "observed_to", "duration_ms",
            "principal_source", "principal_semantics", "omissions", "snapshot_atomic",
            "owner_checkpoint_count", "owner_identity_checks",
        )
        response = {
            "schema": RESPONSE_SCHEMA, "version": 1,
            "generation": generation,
            "acquisition": {key: copy.deepcopy(acquisition[key]) for key in allowed if key in acquisition},
            "owner": {
                "profile": PROFILE, "context_id": binding.context_id,
                "principal_semantics": PRINCIPAL_SEMANTICS,
            },
            "work_budget_seconds": work_budget,
            "duration_ms": round((self.clock() - started) * 1000, 3),
            "effects": "none", "payload_bytes": 0,
            "payload_limit_bytes": self.max_payload_bytes,
        }
        for _ in range(8):
            size = len(json.dumps(
                response, sort_keys=True, separators=(",", ":"), allow_nan=False,
            ).encode("utf-8"))
            if response["payload_bytes"] == size:
                break
            response["payload_bytes"] = size
        else:
            raise OwnerReadResponseTooLarge("owner-read payload size did not stabilize")
        if size > self.max_payload_bytes:
            raise OwnerReadResponseTooLarge("snapshot exceeds the owner's payload budget")
        checkpoint()
        return response
