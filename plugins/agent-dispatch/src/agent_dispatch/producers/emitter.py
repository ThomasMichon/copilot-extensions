"""Lease-gated periodic command emitters.

An emitter spec names a command and cadence. The singleton supervisor owns the
emitter process; this module owns its repeated ticks and the cross-host job lease
that ensures only one eligible supervisor invokes the command.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..client import DispatchClient


class EmitterError(ValueError):
    """A malformed command-emitter spec."""


def validate_spec(spec: dict[str, Any]) -> None:
    """Validate a periodic command-emitter spec."""
    if not isinstance(spec, dict):
        raise EmitterError("emitter spec must be a JSON object")
    if not isinstance(spec.get("id"), str) or not spec["id"]:
        raise EmitterError("command emitter needs a non-empty 'id'")
    command = spec.get("command")
    if (
        not isinstance(command, list)
        or not command
        or any(not isinstance(part, str) or not part for part in command)
    ):
        raise EmitterError("'command' must be a non-empty list of non-empty strings")
    try:
        interval = float(spec.get("interval_seconds"))
    except (TypeError, ValueError) as exc:
        raise EmitterError("'interval_seconds' must be a number > 0") from exc
    if interval <= 0:
        raise EmitterError("'interval_seconds' must be > 0")
    for key in ("cwd", "lease_scope", "holder_session"):
        value = spec.get(key)
        if value is not None and (not isinstance(value, str) or not value):
            raise EmitterError(f"'{key}' must be a non-empty string")
    timeout = spec.get("timeout_seconds")
    if timeout is not None:
        try:
            if float(timeout) <= 0:
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise EmitterError("'timeout_seconds' must be a number > 0") from exc
    env = spec.get("env")
    if env is not None and (
        not isinstance(env, dict)
        or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in env.items()
        )
    ):
        raise EmitterError("'env' must be an object of string keys and values")


def load_spec(path: str | Path) -> dict[str, Any]:
    """Load and validate a command-emitter JSON spec."""
    data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    validate_spec(data)
    return data


def lease_scope(spec: dict[str, Any]) -> str:
    """Return the stable single-producer lease scope for ``spec``."""
    return str(spec.get("lease_scope") or f"emitter:{spec['id']}")


def run_tick(
    client: DispatchClient,
    spec: dict[str, Any],
    *,
    holder: str,
    runner: Callable[..., Any] = subprocess.run,
    clock: Callable[[], float] = time.time,
) -> dict[str, Any]:
    """Acquire/renew the emitter lease and invoke one command tick when held."""
    validate_spec(spec)
    scope = lease_scope(spec)
    lease = client.acquire_schedule_lease(
        scope,
        holder,
        holder_session=spec.get("holder_session"),
        ttl=spec.get("lease_ttl"),
    )
    if not lease.get("granted"):
        return {"held": False, "lease": lease.get("lease"), "scope": scope}

    env = None
    if spec.get("env"):
        env = {**os.environ, **spec["env"]}
    started_at = clock()
    try:
        completed = runner(
            list(spec["command"]),
            cwd=spec.get("cwd"),
            env=env,
            timeout=spec.get("timeout_seconds"),
            check=False,
        )
        returncode = int(completed.returncode)
        error = None
    except subprocess.TimeoutExpired as exc:
        returncode = None
        error = f"timed out after {exc.timeout}s"
    except OSError as exc:
        returncode = None
        error = str(exc)
    return {
        "held": True,
        "lease": lease.get("lease"),
        "scope": scope,
        "returncode": returncode,
        "error": error,
        "duration_seconds": max(0.0, clock() - started_at),
    }


def serve(
    spec_path: str | Path,
    *,
    url: str,
    holder: str,
    token: str | None = None,
    on_tick: Callable[[dict[str, Any]], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Reload and tick a command emitter on its declared cadence."""

    def _default_on_tick(result: dict[str, Any]) -> None:
        if not result.get("held"):
            print(
                f"agent-dispatch emitter: lease {result['scope']!r} held by "
                f"{(result.get('lease') or {}).get('holder')!r} -- idling",
                file=sys.stderr,
            )
        elif result.get("error"):
            print(
                f"agent-dispatch emitter: tick failed: {result['error']}",
                file=sys.stderr,
            )
        else:
            print(
                f"agent-dispatch emitter: tick returncode={result['returncode']} "
                f"duration={result['duration_seconds']:.3f}s",
                file=sys.stderr,
            )

    report = on_tick or _default_on_tick
    while True:
        interval = 60.0
        try:
            spec = load_spec(spec_path)
            interval = float(spec["interval_seconds"])
            with DispatchClient(url, token=token) as client:
                report(run_tick(client, spec, holder=holder))
        except KeyboardInterrupt:
            return
        except Exception as exc:
            print(f"agent-dispatch emitter: tick failed: {exc}", file=sys.stderr)
        try:
            sleep(interval)
        except KeyboardInterrupt:
            return
