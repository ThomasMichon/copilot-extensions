"""Explicit prepared-binding addresses on the container namespace boundary."""

from __future__ import annotations

import re

_REFERENCE = re.compile(r"dispatch/([0-9a-f]{64})/([0-9a-f]{32})/([a-zA-Z0-9][a-zA-Z0-9_.-]*)")


def reference(name: str, container_id: str, binding_id: str) -> str:
    value = f"dispatch/{container_id}/{binding_id}/{name}"
    parse(value)
    return value


def parse(value: str) -> tuple[str, str, str] | None:
    """Ordinary Docker names cannot overlap the slash-delimited task lane."""
    if not value.startswith("dispatch/"):
        return None
    match = _REFERENCE.fullmatch(value)
    if match is None:
        raise RuntimeError("Invalid prepared container reference")
    container_id, binding_id, name = match.groups()
    return name, container_id, binding_id


def launch_spec(name: str, binding: dict) -> dict:
    from ._invoke import payload_command_argv

    return {
        "agent": f"container:{reference(name, binding['container_id'], binding['binding_id'])}",
        "spawn_command": [
            *payload_command_argv(), "exec", "--stdio",
            "--dispatch-binding-id", binding["binding_id"],
            "--expected-container-id", binding["container_id"], name,
        ],
    }


def resolve(value: str) -> dict:
    """Revalidate retained birth, physical identity, posture and lifecycle hold."""
    from . import dispatch_projection, lease
    from .resolver import VENUE_PROVIDER, VENUE_SCHEMA_VERSION, resolve_live_exec_target

    parsed = parse(value)
    if parsed is None:
        raise RuntimeError("Expected an explicit prepared container reference")
    name, expected_id, binding_id = parsed
    with lease._lease_lock():
        target = resolve_live_exec_target(name, dispatch_binding_id=binding_id)
        if target.container_id != expected_id:
            raise RuntimeError("Prepared container physical identity changed")
        binding = dispatch_projection.verify_binding(expected_id, binding_id)
        holds = lease._read_live_records(
            lease._DEPLOY_HOLDS_FILE, lease.DeployHold, lease.DEPLOY_HOLD_TTL,
        )
        if name in holds:
            raise RuntimeError("Prepared container lifecycle is busy")
        return {
            "type": "command", **launch_spec(name, binding),
            "user": target.user, "workspace_folder": target.workspace_folder,
            "security_profile": target.actual_profile,
            "venue": {
                "schema_version": VENUE_SCHEMA_VERSION, "provider": VENUE_PROVIDER,
                "kind": "container", "target_id": f"container:{value}",
                "scope": "provider-instance", "instance_id": expected_id,
                "workspace_folder": target.workspace_folder,
                "security_profile": target.actual_profile, "ready": True,
                "posture_verified": True, "transport": "provider-exec", "dispatch_only": True,
                "capabilities": {
                    "host_credentials": False, "credential_relay": False,
                    "session_host": False, "task_projection": True,
                    "instance_pinned_exec": True,
                },
            },
        }
