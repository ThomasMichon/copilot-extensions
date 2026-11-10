"""Local one-shot collection through existing same-cell installation ownership."""

from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from agent_procutil.owned_process import OwnedCleanupError, run_caller_owned
from agent_procutil.owned_budget import OwnedExecutionBudget
from process_telemetry.hourly import declaration, decode, hour_start, validate

from . import _peer_launch

MAX_SERVICES = 8
COLLECTION_SECONDS = 30
SERVICE_SECONDS = 10
RESPONSE_MARGIN = .25


@dataclass(frozen=True)
class SelectedService:
    """Local identity is never serialized into the report's presentation namespace."""

    identity: str
    service: str
    command: list[str]
    environment: dict[str, str]


def select(service: str) -> SelectedService:
    """Reuse the installation owner; never resolve PATH or provision a missing peer."""
    root = Path(os.environ.get("AGENT_RT_ROOT") or (Path.home() / ".agent-machines"))
    raw = os.environ.get(_peer_launch.CONTEXT_ENV, "")
    if not raw or service not in _peer_launch.PEERS:
        raise ValueError("service installation unavailable")
    if service == "agent-dispatch" and any(name.startswith("AGENT_DISPATCH_TASK_") for name in os.environ):
        raise ValueError("task authority preserved")
    context, environment, python = _peer_launch._resolve_peer(
        "agent-machines", root, raw, service, allow_native=False,
    )
    if python is None:
        raise ValueError("service runtime unsupported")
    peer_root = Path(context["pluginRoot"])
    module = _peer_launch.PEERS[service]
    identity = _peer_launch.retained_runtime_identity(peer_root, python, module)
    retained = _peer_launch.retained_runtime_prefix(
        peer_root, python, module, runtime_identity=identity,
    )
    command = [
        *retained[:-2], str(Path(__file__).with_name("service_report_entry.py")),
        service, module,
    ]
    return SelectedService(context["installReceipt"], service, command, environment)


def collect(services: list[str], hour: str, *, now_ms: int) -> list[dict]:
    """Collect once per installation/hour, isolate gaps, never merge percentiles."""
    if hour_start(hour) > now_ms:
        raise ValueError("future service hour")
    if len(services) > MAX_SERVICES:
        raise ValueError("invalid service selection")
    requests = []
    labels = {}
    for request in services:
        label, separator, service = request.partition("=")
        service = service if separator else label
        if (
            any(re.fullmatch(r"[a-z][a-z0-9-]{0,62}", item) is None for item in (label, service))
            or label in labels and labels[label] != service
        ):
            raise ValueError("ambiguous service presentation identity")
        labels[label] = service
        requests.append((label, service))
    deadline = time.monotonic() + COLLECTION_SECONDS
    results = []
    seen = {}
    presented = {}
    for label, service in requests:
        row = {"instance": label, "service": service, "hour": hour, "state": "unavailable"}
        try:
            selected = select(service)
            if selected.service != service:
                raise ValueError("service owner mismatch")
            if selected.identity in seen:
                row["state"] = "duplicate" if seen[selected.identity] == service else "invalid"
                results.append(row)
                continue
            if label in presented and presented[label] != selected.identity:
                row["state"] = "invalid"
                results.append(row)
                continue
            seen[selected.identity] = service
            presented[label] = selected.identity
            started = time.monotonic()
            child_deadline = min(deadline - RESPONSE_MARGIN, started + SERVICE_SECONDS)
            remaining = child_deadline - started
            if remaining <= 0:
                row["state"] = "timeout"
            else:
                budget = OwnedExecutionBudget(
                    deadline=child_deadline, cleanup_reserve=min(2.0, remaining / 4),
                )
                response = run_caller_owned(
                    [*selected.command, hour], timeout=min(SERVICE_SECONDS, remaining),
                    max_stdout_bytes=64 * 1024, max_stderr_bytes=64 * 1024,
                    env=selected.environment, binary=True, budget=budget,
                )
                if response.returncode != 0:
                    raise ValueError("service export failed")
                try:
                    value = decode(response.stdout)
                except ValueError:
                    row["state"] = "invalid"
                    results.append(row)
                    continue
                if value in ({"status": "unsupported"}, {"status": "invalid"}, {"status": "unavailable"}):
                    row["state"] = value["status"]
                else:
                    if type(value) is not dict or set(value) != {"status", "declaration", "contribution"} or value["status"] != "collected":
                        row["state"] = "invalid"
                        results.append(row)
                        continue
                    try:
                        support = declaration(value["declaration"], service)
                        contribution = validate(
                            value["contribution"], service=service, hour=hour,
                            metrics=support["metrics"], now_ms=int(time.time() * 1000),
                        )
                    except ValueError:
                        row["state"] = "invalid"
                        results.append(row)
                        continue
                    row.update(state=contribution["coverage"]["state"], contribution=contribution)
        except OwnedCleanupError:
            # Unsettled child custody is not an ordinary independent provider gap.
            raise
        except subprocess.TimeoutExpired:
            row["state"] = "timeout"
        except (ValueError, OSError, RuntimeError, subprocess.SubprocessError):
            row["state"] = "unavailable"
        results.append(row)
    return results
