"""Opt-in managed creation using an existing owner-supplied producer grant."""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from collections.abc import Callable
from typing import Any

from agent_procutil import no_window_kwargs

from ..identity import canonicalize_remote
from ..repository_resources import resolve_execution_resources, validate_resource_arguments


def validate_config(spec: dict[str, Any]) -> None:
    config = spec["managed_producer"]
    if (
        not isinstance(config, dict)
        or set(config) - {"grant_command", "timeout_seconds"}
        or spec.get("task_output") != "json"
        or spec.get("command") is None
    ):
        raise ValueError("managed_producer requires a JSON task-authoring command emitter")
    command = config.get("grant_command")
    if (
        not isinstance(command, list)
        or not command
        or any(not isinstance(part, str) or not part for part in command)
    ):
        raise ValueError("managed_producer.grant_command must be an argument list")
    validate_resource_arguments(command)
    timeout = config.get("timeout_seconds", 30)
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or not 0 < timeout <= 300
    ):
        raise ValueError("managed_producer.timeout_seconds must be finite and in (0, 300]")


def load_grant(
    client: Any,
    spec: dict[str, Any],
    *,
    runner: Callable[..., Any],
) -> dict[str, Any]:
    config = spec["managed_producer"]
    command, cwd = resolve_execution_resources(
        [part.replace("{python}", sys.executable) for part in config["grant_command"]],
        spec.get("cwd"),
        bind_relative_executable=True,
    )
    try:
        completed = runner(
            command,
            cwd=cwd,
            env={**os.environ, **(spec.get("env") or {})},
            timeout=config.get("timeout_seconds", 30),
            check=False,
            capture_output=True,
            text=True,
            **no_window_kwargs(),
        )
        if completed.returncode:
            raise ValueError
        grant = json.loads(completed.stdout)
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        # A credential resolver's output and argv must not enter tick diagnostics.
        raise ValueError("managed producer grant resolution failed") from None
    source = spec.get("source") or "emitter"
    scope = grant.get("scope") if isinstance(grant, dict) else None
    if (
        not isinstance(scope, dict)
        or set(scope) != {"repo", "source"}
        or not isinstance(scope["repo"], str)
        or not canonicalize_remote(scope["repo"])
        or scope["source"] != source
        or grant.get("active_producer") != spec["id"]
        or type(grant.get("current_generation")) is not int
        or grant["current_generation"] < 1
        or not isinstance(grant.get("producer_capability"), str)
        or not grant["producer_capability"]
    ):
        raise ValueError("managed producer grant does not match the emitter")
    scope = {"repo": canonicalize_remote(scope["repo"]), "source": source}
    state = client.producer_scope_status(scope["repo"], source)
    if (
        state.get("managed") is not True
        or state.get("scope") != scope
        or state.get("active_producer") != spec["id"]
        or state.get("current_generation") != grant["current_generation"]
    ):
        raise ValueError("managed producer grant is not the selected generation")
    return {
        "producer_scope": scope,
        "producer_id": spec["id"],
        "producer_generation": grant["current_generation"],
        "producer_capability": grant["producer_capability"],
    }


def create_or_observe(
    client: Any,
    title: str,
    fields: dict[str, Any],
    grant: dict[str, Any],
) -> dict[str, Any]:
    if (
        not isinstance(fields.get("producer_request_id"), str)
        or not fields["producer_request_id"]
        or not isinstance(fields.get("dedup_key"), str)
        or not fields["dedup_key"]
        or canonicalize_remote(fields.get("repo")) != grant["producer_scope"]["repo"]
        or any(key in fields for key in grant)
    ):
        raise ValueError("managed emission needs an occurrence request id and matching repo/dedup")
    matches = client.lookup_dedup(fields["dedup_key"], repo=fields["repo"])
    if matches:
        task = matches[0]
        fence = task.get("producer_fence") or {}
        if (
            len(matches) != 1
            or task.get("repo") != grant["producer_scope"]["repo"]
            or task.get("source") != grant["producer_scope"]["source"]
            or task.get("origin_ref") != grant["producer_id"]
            or task.get("evaluator_ref") != fields.get("evaluator_ref")
            or task.get("dedup_key") != fields["dedup_key"]
            or fence.get("scope") != grant["producer_scope"]
            or fence.get("producer_id") != grant["producer_id"]
            or fence.get("generation") != grant["producer_generation"]
            or not fence.get("request_id")
        ):
            raise ValueError("managed emission cannot adopt the existing dedup occupant")
        # Discovery can advance while the same goal is still owned. Observe its
        # original request, rather than replaying that request with changed bytes.
        return task
    return client.create(title, **fields, **grant)
