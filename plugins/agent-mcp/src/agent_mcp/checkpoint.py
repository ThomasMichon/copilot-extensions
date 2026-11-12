"""Quiescent JSON data bound to one custody session and complete caller context."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import secrets

from . import execution_context

SCHEMA = "agent-mcp.session-checkpoint"
VERSION = 1
MAX_BYTES = 8 * 1024 * 1024


class CheckpointRefused(ValueError):
    """The requested state cannot safely cross an execution attachment."""


@dataclass(frozen=True)
class Binding:
    """Custody-issued private context shared only with authenticated backends."""

    session: str
    key: str = field(repr=False)
    trace_bookkeeping: int = 0

    def __post_init__(self):
        if type(self.trace_bookkeeping) is not int or self.trace_bookkeeping not in (0, 1):
            raise CheckpointRefused("unsupported checkpoint identity mode")
        for value in (self.session, self.key):
            if (
                not isinstance(value, str)
                or len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
            ):
                raise CheckpointRefused("invalid private checkpoint binding")

    @classmethod
    def issue(cls, *, trace_bookkeeping=0):
        """Allocate one session identity and an independent private comparison key."""
        return cls(secrets.token_hex(32), secrets.token_hex(32), trace_bookkeeping)

    @classmethod
    def from_wire(cls, value):
        """Missing mode retains the original strict two-field binding semantics."""
        if (
            not isinstance(value, dict)
            or set(value) not in (
                {"session", "key"}, {"session", "key", "trace_bookkeeping"},
            )
        ):
            raise CheckpointRefused("private checkpoint binding is required")
        return cls(**value)

    def wire(self) -> dict[str, str | int]:
        value: dict[str, str | int] = {"session": self.session, "key": self.key}
        if self.trace_bookkeeping:
            value["trace_bookkeeping"] = self.trace_bookkeeping
        return value

    def identity(self, cfg):
        """Use the existing complete config/environment/cwd equivalence owner."""
        try:
            return execution_context._identity(
                cfg, self.key, trace_bookkeeping=self.trace_bookkeeping == 1,
            )
        except (OSError, ValueError, TypeError) as exc:
            raise CheckpointRefused("checkpoint execution context cannot be qualified") from exc


def data_copy(value):
    """Reject executable/opaque state, nonfinite numbers and oversized snapshots."""
    try:
        wire = json.dumps(value, allow_nan=False, separators=(",", ":"))
        if len(wire.encode("utf-8")) > MAX_BYTES:
            raise CheckpointRefused("session checkpoint exceeds its byte budget")
        return json.loads(wire)
    except (TypeError, ValueError, RecursionError) as exc:
        raise CheckpointRefused("session checkpoint is not bounded JSON data") from exc


def envelope(cfg, state: dict, binding: Binding) -> dict:
    """Export data without embedding the private binding key or execution objects."""
    from .custody_contract import CONTRACT, MAX_SEQUENCE
    from .custody_wire import Peer

    value = {
        "schema": SCHEMA,
        "version": VERSION,
        "session": binding.session,
        "configuration": binding.identity(cfg),
        "state": state,
    }
    if binding.trace_bookkeeping:
        value["trace_bookkeeping"] = binding.trace_bookkeeping
    snapshot = data_copy(value)
    try:
        Peer._encode({"receipt": MAX_SEQUENCE, "result": snapshot})
        Peer._encode({
            "sequence": MAX_SEQUENCE, "operation": "prepare",
            "data": {
                "epoch": MAX_SEQUENCE, "contract": CONTRACT,
                "binding": binding.wire(),
                "checkpoint": snapshot,
            },
        })
    except ValueError as exc:
        raise CheckpointRefused("checkpoint exceeds its complete attachment frame budget") from exc
    return snapshot


def unpack(cfg, snapshot: dict, binding: Binding) -> dict:
    """Validate session, version and actual caller context before importing data."""
    snapshot = data_copy(snapshot)
    fields = {"schema", "version", "session", "configuration", "state"}
    if binding.trace_bookkeeping:
        fields.add("trace_bookkeeping")
    if (
        not isinstance(snapshot, dict)
        or set(snapshot) != fields
        or (
            binding.trace_bookkeeping
            and (
                type(snapshot.get("trace_bookkeeping")) is not int
                or snapshot["trace_bookkeeping"] != binding.trace_bookkeeping
            )
        )
        or snapshot["schema"] != SCHEMA
        or type(snapshot["version"]) is not int
        or snapshot["version"] != VERSION
        or snapshot["session"] != binding.session
        or snapshot["configuration"] != binding.identity(cfg)
        or not isinstance(snapshot["state"], dict)
    ):
        raise CheckpointRefused("unsupported checkpoint or incompatible execution context")
    return snapshot["state"]


def pipeline_state(pipeline) -> list[dict]:
    """Export only the declared state of the known built-in decorator classes."""
    from .decorators import REGISTRY

    result = []
    for decorator in pipeline.decorators:
        if REGISTRY.get(decorator.type) is not type(decorator):
            raise CheckpointRefused("decorator has no supported checkpoint contract")
        state = {}
        if decorator.type in {"defer", "code-mode"}:
            state["catalog"] = decorator._catalog
        if decorator.type == "defer":
            state["loaded"] = sorted(decorator._loaded)
        if decorator.type == "rename":
            state["reverse"] = decorator._reverse
        if decorator.type == "gate":
            state["cache"] = decorator._cache
        result.append({"type": decorator.type, "data": state})
    return data_copy(result)


def restore_pipeline(pipeline, snapshot: list[dict]) -> None:
    """Validate the entire built-in stack before assigning any imported state."""
    expected = pipeline_state(pipeline)
    snapshot = data_copy(snapshot)
    if not isinstance(snapshot, list) or len(snapshot) != len(expected):
        raise CheckpointRefused("decorator checkpoint stack differs")
    prepared = []
    for decorator, item, shape in zip(pipeline.decorators, snapshot, expected):
        if (
            not isinstance(item, dict)
            or set(item) != {"type", "data"}
            or item["type"] != shape["type"]
            or not isinstance(item["data"], dict)
            or set(item["data"]) != set(shape["data"])
        ):
            raise CheckpointRefused("decorator checkpoint schema differs")
        state = item["data"]
        if "catalog" in state and (
            not isinstance(state["catalog"], list)
            or any(
                not isinstance(tool, dict) or not isinstance(tool.get("name"), str)
                for tool in state["catalog"]
            )
        ):
            raise CheckpointRefused("invalid catalog checkpoint")
        if "loaded" in state and (
            not isinstance(state["loaded"], list)
            or any(not isinstance(name, str) for name in state["loaded"])
        ):
            raise CheckpointRefused("invalid loaded-tool checkpoint")
        if "reverse" in state and (
            not isinstance(state["reverse"], dict)
            or any(
                not isinstance(key, str) or not isinstance(value, str)
                for key, value in state["reverse"].items()
            )
        ):
            raise CheckpointRefused("invalid rename checkpoint")
        if "cache" in state and not isinstance(state["cache"], dict):
            raise CheckpointRefused("invalid gate checkpoint")
        prepared.append((decorator, state))
    for decorator, state in prepared:
        if "catalog" in state:
            decorator._catalog = state["catalog"]
        if "loaded" in state:
            decorator._loaded = set(state["loaded"])
        if "reverse" in state:
            decorator._reverse = state["reverse"]
        if "cache" in state:
            decorator._cache = state["cache"]


def client_state(client) -> int:
    """Refuse an export until every upstream correlation is settled."""
    if client._pending:
        raise CheckpointRefused("upstream correlations have not settled")
    return client._counter


def restore_client(client, counter: int) -> None:
    """Continue the internal correlation namespace without initialize replay."""
    if type(counter) is not int or counter < 0 or client._pending:
        raise CheckpointRefused("invalid upstream correlation checkpoint")
    client._counter = counter
