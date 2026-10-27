"""Receiver-bound rollback on the monitor's existing authenticated control surface."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from zdd import routing

from . import locks
from .handoff_prompt_receipt_control import unique_object

if TYPE_CHECKING:
    from .status_monitor_cutover import ControlClient

ACTION = "guarded-undrain"
CAPABILITY = "guarded-undrain/v1"
FRAME_BYTES = 256 * 1024


class RecoveryUnavailable(ValueError):
    """The retained predecessor cannot be safely resumed by this operation."""


def refused(reason: str) -> dict:
    return {"contractVersion": 1, "status": "refused", "reason": reason}


def encode_result(result: dict) -> bytes:
    return json.dumps({"version": 1, "result": result}, separators=(",", ":")).encode("utf-8") + b"\n"


def _frame(raw: bytes) -> dict:
    if len(raw) >= FRAME_BYTES or not raw.endswith(b"\n"):
        raise RecoveryUnavailable("invalid-frame")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
    except (UnicodeError, ValueError) as exc:
        raise RecoveryUnavailable("invalid-frame") from exc
    if not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != 1:
        raise RecoveryUnavailable("invalid-frame")
    return value


def decode_request(raw: bytes) -> dict:
    value = _frame(raw)
    if (
        set(value) != {"version", "token", "action", "payload"}
        or value["action"] != ACTION or not isinstance(value["token"], str)
        or not isinstance(value["payload"], dict)
    ):
        raise RecoveryUnavailable("invalid-frame")
    return value


def decode_response(raw: bytes) -> dict:
    value = _frame(raw)
    if set(value) != {"version", "result"} or not isinstance(value["result"], dict):
        raise RecoveryUnavailable("invalid-response")
    return value


@dataclass(frozen=True)
class ReceiverIdentity:
    pid: int
    process_start_time: str
    runtime_generation: str

    def to_dict(self) -> dict:
        return {
            "pid": self.pid, "processStartTime": self.process_start_time,
            "runtimeGeneration": self.runtime_generation,
        }

    @classmethod
    def parse(cls, raw: object) -> ReceiverIdentity:
        if (
            not isinstance(raw, dict)
            or set(raw) != {"pid", "processStartTime", "runtimeGeneration"}
            or type(raw.get("pid")) is not int or raw["pid"] <= 0
            or not isinstance(raw.get("processStartTime"), str) or not raw["processStartTime"]
            or not isinstance(raw.get("runtimeGeneration"), str) or not raw["runtimeGeneration"]
        ):
            raise RecoveryUnavailable("invalid-receiver")
        return cls(raw["pid"], raw["processStartTime"], raw["runtimeGeneration"])


def endpoint(raw: object) -> routing.Endpoint:
    required = {"bind", "port", "pid", "generation", "process_start_time"}
    if (
        not isinstance(raw, dict) or not required <= set(raw)
        or set(raw) - (required | {"version"})
        or not isinstance(raw.get("bind"), str) or not raw["bind"]
        or type(raw.get("port")) is not int or not 0 < raw["port"] <= 65535
        or type(raw.get("pid")) is not int or raw["pid"] <= 0
        or type(raw.get("generation")) is not int or raw["generation"] < 0
        or not isinstance(raw.get("process_start_time"), str) or not raw["process_start_time"]
        or ("version" in raw and not isinstance(raw["version"], str))
    ):
        raise RecoveryUnavailable("invalid-route")
    result = routing.Endpoint.from_dict(raw)
    if result is None or result.to_dict() != raw:
        raise RecoveryUnavailable("invalid-route")
    return result


def active_endpoint(directory: Path) -> routing.Endpoint:
    table = routing.read_table(directory, strict=True)
    return endpoint(table.get("active") if isinstance(table, dict) else None)


def same_receiver(route: routing.Endpoint, receiver: ReceiverIdentity) -> bool:
    return route.pid == receiver.pid and route.process_start_time == receiver.process_start_time


def handle(
    payload: dict, *, directory: Path, generation: str, port: int,
    reopen, close_on_failure, lock_timeout: float,
) -> dict:
    try:
        if set(payload) != {"contractVersion", "expectedReceiver", "expectedRoute"} or (
            type(payload.get("contractVersion")) is not int or payload["contractVersion"] != 1
        ):
            raise RecoveryUnavailable("invalid-operation")
        expected = ReceiverIdentity.parse(payload["expectedReceiver"])
        expected_route = endpoint(payload["expectedRoute"])
        if not same_receiver(expected_route, expected):
            raise RecoveryUnavailable("receiver-route-mismatch")
        # The monitor already holds its state lock. No network/Git or reverse
        # lock acquisition occurs while this existing publication lock is held.
        with routing.routing_lock(directory, deadline=time.monotonic() + lock_timeout):
            start = locks.process_start_time(os.getpid())
            if not isinstance(start, str) or not start:
                raise RecoveryUnavailable("receiver-identity-unavailable")
            actual = ReceiverIdentity(os.getpid(), start, generation)
            if actual != expected or port != expected_route.port:
                raise RecoveryUnavailable("receiver-changed")
            if active_endpoint(directory) != expected_route:
                raise RecoveryUnavailable("route-changed")
            try:
                result = reopen()
            except BaseException:
                close_on_failure()
                raise
            if not isinstance(result, dict) or result.get("draining") is not False:
                close_on_failure()
                return refused("reopen-unconfirmed")
            return {
                "contractVersion": 1, "status": "undrained",
                "receiver": actual.to_dict(), "route": expected_route.to_dict(),
            }
    except (RecoveryUnavailable, routing.RoutingLockTimeout) as exc:
        return refused(str(exc))


@dataclass(frozen=True)
class GuardedPredecessor:
    route: routing.Endpoint
    receiver: ReceiverIdentity
    token: str = field(repr=False)

    def refuse_old(self, _active: dict | None = None) -> str | None:
        if locks.process_start_time(self.receiver.pid) != self.receiver.process_start_time:
            return "predecessor-identity-changed"
        return None

    def require_target(self, base_url: str) -> None:
        if base_url != self.route.base_url or self.refuse_old() is not None:
            raise RecoveryUnavailable("predecessor-identity-changed")

    def operation(self, directory: Path, base_url: str) -> dict:
        self.require_target(base_url)
        restored = active_endpoint(directory)
        if (
            not same_receiver(restored, self.receiver)
            or restored.bind != self.route.bind or restored.port != self.route.port
            or restored.version != self.route.version
        ):
            raise RecoveryUnavailable("route-changed")
        return {
            "contractVersion": 1, "expectedReceiver": self.receiver.to_dict(),
            "expectedRoute": restored.to_dict(),
        }

    def undrain(self, client: ControlClient) -> dict:
        if client.config_dir is None:
            raise RecoveryUnavailable("missing-route-context")
        payload = self.operation(client.config_dir, client.base_url)
        result = client._request(
            ACTION, payload, max_response_bytes=FRAME_BYTES, total_timeout=client.timeout,
        )
        if (
            not isinstance(result, dict)
            or type(result.get("contractVersion")) is not int or result["contractVersion"] != 1
            or result.get("status") != "undrained"
            or ReceiverIdentity.parse(result.get("receiver")) != self.receiver
            or endpoint(result.get("route")) != endpoint(payload["expectedRoute"])
        ):
            raise RecoveryUnavailable("undrain-unconfirmed")
        return {**result, "draining": False}

    def predecessor_healthy(self, host: str, port: int, make_client) -> bool:
        if host != self.route.client_host or port != self.route.port or self.refuse_old() is not None:
            return False
        try:
            health = make_client(self.route.base_url).health()
        except (OSError, ValueError):
            return False
        roster = health.get("worktrees")
        return (
            health.get("pid") == self.receiver.pid
            and health.get("runtime_generation") == self.receiver.runtime_generation
            and health.get("status") in {"ready", "draining", "unavailable"}
            and (not isinstance(roster, dict) or roster.get("status") not in {"shutdown", "closed"})
            and self.refuse_old() is None
        )


def negotiate(directory: Path, token_path: Path, client_type, *, timeout: float) -> GuardedPredecessor:
    try:
        token = token_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RecoveryUnavailable("existing-token-unavailable") from exc
    if not token:
        raise RecoveryUnavailable("existing-token-unavailable")
    route = active_endpoint(directory)
    start = locks.process_start_time(route.pid)
    if start != route.process_start_time:
        raise RecoveryUnavailable("predecessor-identity-changed")
    health = client_type(route.base_url, token=token, config_dir=directory, timeout=timeout).health()
    receiver = ReceiverIdentity.parse({
        "pid": health.get("pid"), "processStartTime": start,
        "runtimeGeneration": health.get("runtime_generation"),
    })
    roster = health.get("worktrees")
    if (
        not same_receiver(route, receiver) or not isinstance(health.get("capabilities"), list)
        or CAPABILITY not in health["capabilities"]
        or health.get("status") not in {"ready", "draining", "unavailable"}
        or (isinstance(roster, dict) and roster.get("status") in {"shutdown", "closed"})
        or locks.process_start_time(route.pid) != start or active_endpoint(directory) != route
    ):
        raise RecoveryUnavailable("guarded-recovery-unsupported-or-owner-changed")
    return GuardedPredecessor(route, receiver, token)


def monitor_health(
    worktrees_runtime, receipts, repositories_runtime, *, shutdown: bool, draining: bool,
    published: bool, generation: str | None, version: str | None, runtime_slot: str,
) -> dict:
    """Advertise the same receiver capability used by guarded recovery negotiation."""
    roster_health = worktrees_runtime.health()
    receipt_health = receipts.health()
    repository_health = repositories_runtime.health()
    roster_health["capabilities"] = [
        *roster_health["capabilities"], *receipt_health["capabilities"],
        *repository_health["capabilities"], CAPABILITY,
    ]
    if shutdown:
        roster_health["worktrees"]["status"] = "shutdown"
    roster_ready = roster_health["worktrees"]["status"] in {"ready", "unready"}
    return {
        "status": "draining" if draining else (
            "ready" if roster_ready else "unavailable"
        ),
        "published": published,
        "pid": os.getpid(),
        **roster_health,
        "promptReceipt": receipt_health["promptReceipt"],
        "repositories": repository_health["repositories"],
        "runtime_generation": generation,
        "version": version,
        "runtime_slot": runtime_slot,
    }


def enabled() -> bool:
    from . import git_ops

    return git_ops._windows_observation()


def routed_owner_present(directory: Path) -> bool:
    from zdd import diagnostics

    table = routing.read_table(directory, strict=True)
    found = False
    for name in ("active", "previous"):
        raw = (table or {}).get(name)
        if raw is None:
            continue
        candidate = endpoint(raw)
        start = locks.process_start_time(candidate.pid)
        if start is None:
            if diagnostics._pid_alive(candidate.pid):
                raise RecoveryUnavailable("routed-owner-identity-unavailable")
            continue
        if start != candidate.process_start_time:
            raise RecoveryUnavailable("routed-owner-identity-changed")
        found = True
    return found


def successor_healthy(host: str, port: int, make_client) -> bool:
    try:
        health = make_client(f"http://{routing.format_authority(host, port)}").health()
    except (OSError, ValueError):
        return False
    if health.get("status") != "ready":
        return False
    roster = health.get("worktrees")
    return not isinstance(roster, dict) or roster.get("status") == "ready" or (
        health.get("published") is False and roster.get("status") == "unready"
    )
