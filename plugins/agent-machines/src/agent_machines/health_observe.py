"""One bounded read of an explicitly selected local Bridge health listener."""

from __future__ import annotations

import http.client
import json
import socket
import time

from . import health_slots

TIMEOUT_SECONDS = 2.0
MAX_WIRE_BYTES = health_slots.MAX_BYTES
HEALTH_PROTOCOL = 1


class _WireLimit(ValueError):
    pass


class _DeadlineSocket(socket.socket):
    def __init__(self, deadline):
        super().__init__(socket.AF_INET, socket.SOCK_STREAM)
        self.deadline = deadline
        self.remaining = MAX_WIRE_BYTES

    def _timeout(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        self.settimeout(remaining)

    def recv_into(self, buffer, nbytes=0, flags=0):
        self._timeout()
        if self.remaining <= 0:
            raise _WireLimit
        count = super().recv_into(buffer, min(nbytes or len(buffer), self.remaining), flags)
        self.remaining -= count
        return count


def _read_bridge(port: int):
    connection = http.client.HTTPConnection("127.0.0.1", port)
    try:
        connection.sock = _DeadlineSocket(time.monotonic() + TIMEOUT_SECONDS)
        connection.sock._timeout()
        connection.sock.connect(("127.0.0.1", port))
        connection.sock._timeout()
        connection.request("GET", "/health", headers={"Connection": "close"})
        response = connection.getresponse()
        with response:
            if response.status != 200:
                return "unknown", "unavailable"
            if response.getheader("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                return "unknown", "invalid"
            body = response.read(health_slots.MAX_BYTES + 1)
        value = json.loads(
            body.decode("utf-8"), object_pairs_hook=health_slots._pairs,
            parse_constant=health_slots._nonfinite,
            parse_int=lambda raw: int(raw) if len(raw.lstrip("-")) <= 16 else None,
            parse_float=lambda _raw: None,
        )
        if type(value) is not dict:
            return "unknown", "invalid"
        low, high = value.get("min_protocol_version"), value.get("protocol_version")
        if type(low) is not int or type(high) is not int or not 1 <= low <= HEALTH_PROTOCOL <= high:
            return "unsupported", "not-observed"
        if value.get("service") != "agent-bridge" or value.get("status") != "ok":
            return "unknown", "invalid"
        return "ok", "none"
    except TimeoutError:
        return "unknown", "timeout"
    except (OSError, http.client.HTTPException):
        return "unknown", "unavailable"
    except (ValueError, UnicodeError, RecursionError):
        return "unknown", "invalid"
    finally:
        connection.close()


def observe_bridge(port: int, publisher: str, continuity: str) -> dict:
    if type(port) is not int or not 1 <= port <= 65535:
        raise health_slots.HealthError("invalid-bridge-port")
    started_ms = time.time_ns() // 1_000_000
    observation = health_slots.validate_record({
        "schema": health_slots.SCHEMA, "version": 1,
        "publisher": publisher, "continuity": continuity,
        "hour": started_ms // health_slots.HOUR_MS, "observed_at_ms": started_ms,
        "revision": 1, "parent": None, "domains": {},
    })
    state, reason = _read_bridge(port)
    stamp = time.time_ns() // 1_000_000
    if stamp < started_ms or stamp - started_ms > int(TIMEOUT_SECONDS * 1000) + 1000:
        state, reason = "unknown", "clock-skew"
    observation.update(hour=stamp // health_slots.HOUR_MS, observed_at_ms=stamp)
    for domain in health_slots.EVIDENCE:
        result = {
            "state": "unknown", "evidence": "not-observed",
            "reason": "not-observed", "observed_at_ms": None,
        }
        if domain == "logical_agents":
            result["count"] = None
        if domain == "ssh_authenticated":
            result["direction"] = None
        if domain == "local_listener":
            result.update(
                state=state, reason=reason,
                evidence="listener-check" if state == "ok" else "not-observed",
                observed_at_ms=stamp if state == "ok" else None,
            )
        observation["domains"][domain] = result
    return health_slots.validate_record(observation)
