"""Negotiated PR-owner optimization of transactional detached run waiters."""

from __future__ import annotations

import json
import os
import subprocess
import uuid

import httpx
from .delegated_waiter_contract import (
    NOTIFICATIONS, PROTOCOL, positive_timeout, validate_delegation,
)
from . import procutil


def owner_request(prefix: list[str], *args: str) -> dict:
    try:
        result = procutil.run_background_capture([*prefix, "watch", *args, "--json"], timeout=30)
    except subprocess.SubprocessError:
        raise RuntimeError("ambiguous_watch_response") from None
    if result is None:
        raise RuntimeError("watch_owner_unavailable")
    try:
        value = json.loads(result.stdout)
    except (ValueError, TypeError):
        raise RuntimeError("ambiguous_watch_response") from None
    if not isinstance(value, dict):
        raise RuntimeError("ambiguous_watch_response")
    if result.returncode and not value.get("error"):
        raise RuntimeError("ambiguous_watch_response")
    return value


def credentials_reconstructable(
    args, command: tuple[str, ...], *, intrinsic_environment: dict[str, str] | None = None,
) -> bool:
    # The current PR-owner capability does not attest to its credential
    # environment. Never assume a caller's overrides reached a resident daemon.
    if any(getattr(args, field, None) for field in (
        "token", "control_token", "url", "shared", "local", "message",
        "task_token", "task_token_file", "task_scope_id", "task_url", "task_worker",
    )):
        return False
    if any("token" in part.lower() or "credential" in part.lower() for part in command):
        return False
    intrinsic_environment = intrinsic_environment or {}
    return not any(
        (key.startswith("AGENT_DISPATCH_") and value != intrinsic_environment.get(key))
        or key in {
            "GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN",
            "GH_HOST", "GH_CONFIG_DIR",
            "AGENT_PULL_REQUESTS_HOME",
        }
        for key, value in os.environ.items()
    )


def negotiate(args, command: tuple[str, ...], client_factory) -> tuple[dict | None, list[str] | None, str]:
    if not getattr(args, "pr_watch_repo", None):
        return None, None, "not_requested"
    from .delegated_waiter_context import capture
    installation, intrinsic_environment = capture()
    identity = {
        "coordinator_id": "pending", "preparation_id": uuid.uuid4().hex,
        "subscriber_id": "dispatch:" + uuid.uuid4().hex,
        "repo": args.pr_watch_repo, "number": args.pr_watch_number,
        "timeout": positive_timeout(args.pr_watch_timeout),
        "until": list(args.pr_watch_until or ("merged", "closed")), "shared": False,
        **({"installation": installation} if installation is not None else {}),
    }
    validate_delegation(identity)
    if not credentials_reconstructable(args, command, intrinsic_environment=intrinsic_environment):
        return None, None, "caller_context_requires_process"
    try:
        callback_prefix(installation)
    except (OSError, ValueError):
        return None, None, "callback_entry_unavailable"
    prefix = procutil.agent_pull_requests_launch_prefix()
    if prefix is None:
        return None, None, "optional_owner_absent"
    try:
        with client_factory(args) as client:
            capability = client.health().get("delegated_run_waiters", {})
        if not isinstance(capability, dict) or capability.get("protocol") != PROTOCOL:
            return None, None, "coordinator_protocol_unavailable"
        identity["coordinator_id"] = capability["coordinator_id"]
        validate_delegation(identity)
        health = owner_request(prefix, "health")
        if (
            not isinstance(health.get("capabilities"), list)
            or NOTIFICATIONS not in health["capabilities"]
            or health.get("persistence_error") or health.get("delivery_error")
            or type(health.get("pid")) is not int or health["pid"] <= 0
        ):
            return None, None, "watch_protocol_unavailable"
    except (OSError, RuntimeError, httpx.HTTPError, ValueError, KeyError, AttributeError):
        return None, None, "optional_owner_not_ready"
    return identity, prefix, "negotiated"


def callback_prefix(installation: dict | None) -> list[str]:
    from pathlib import Path
    from .install_paths import install_dir

    root = Path(installation["root"]) if installation else install_dir()
    suffix = "ps1" if os.name == "nt" else "sh"
    entry = root / "bin" / f"delegated-waiter-callback.{suffix}"
    resolver = entry.with_name(f"resolve-runtime.{suffix}")
    if not root.is_absolute() or not entry.is_file() or not resolver.is_file():
        raise ValueError("stable delegated callback entry unavailable")
    if os.name == "nt":
        host = Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        # Slash options are accepted by Windows PowerShell and remain
        # positional to the PR owner's --notify-argv parser.
        return [str(host), "/NoProfile", "/NonInteractive", "/ExecutionPolicy", "Bypass",
                "/File", str(entry)]
    return ["/bin/sh", str(entry)]


def callback_argv(task_id: str, generation: int, identity: dict) -> list[str]:
    from .install_paths import install_dir

    return [
        *callback_prefix(identity.get("installation")),
        "hex:" + json.dumps({
            "task_id": task_id, "generation": generation, "delegation": identity,
            **({"legacy_root": str(install_dir())} if "installation" not in identity else {}),
        }, separators=(",", ":")).encode("utf-8").hex(),
    ]


def activate(args, spec, prepared: dict, prefix: list[str], client_factory) -> dict:
    identity = prepared["delegation"]
    generation = int(prepared["generation"])
    argv = ["subscribe", "--repo", identity["repo"], "--number", str(identity["number"]),
            "--subscriber-id", identity["subscriber_id"], "--timeout", str(identity["timeout"]),
            "--acknowledged-notifications", "--notify-timeout", "30", "--json"]
    for transition in identity["until"]:
        argv += ["--until", transition]
    # Resolve the runtime at delivery, not registration: dormant subscriptions
    # outlive ordinary version-slot garbage collection.
    try:
        callback = callback_argv(spec.task_id, generation, identity)
        result = procutil.run_background_capture(
            [*prefix, "watch", *argv, "--notify-argv", *callback], timeout=30,
        )
        if result is None:
            raise RuntimeError("ambiguous_watch_registration")
        registration = json.loads(result.stdout)
        if (
            result.returncode or not isinstance(registration, dict)
            or registration.get("registered") is not True
            or registration.get("notification_protocol") != NOTIFICATIONS
        ):
            raise RuntimeError("ambiguous_watch_registration")
        armed_identity = {**identity, "registration_id": registration.get("registration_id")}
        validate_delegation(armed_identity, armed=True)
        with client_factory(args) as client:
            try:
                armed = client.arm_delegated_waiter(
                    spec.task_id, generation=generation, delegation=armed_identity,
                )
            except (OSError, RuntimeError, httpx.HTTPError):
                # An arm can commit before its HTTP acknowledgement is lost.
                current = client.delegated_preparation(spec.task_id, identity["preparation_id"])
                waiter = current.get("waiter")
                if (
                    current.get("coordinator_id") == identity["coordinator_id"]
                    and waiter and waiter["generation"] == generation
                    and waiter["state"] in ("active", "retired")
                    and waiter["delegation"] == armed_identity
                ):
                    return {"detached": True, "kind": "delegated", "waiter": waiter}
                raise RuntimeError("ambiguous_delegated_arm") from None
        if not armed.get("accepted"):
            raise RuntimeError("delegated_arm_rejected")
        return {"detached": True, "kind": "delegated", "waiter": armed["waiter"]}
    except (OSError, RuntimeError, subprocess.SubprocessError, httpx.HTTPError, ValueError, KeyError, TypeError):
        # No fallback after preparation: a registration may already exist.
        # Generation-fenced abort either wakes the exact preparation or refuses
        # an already active one, which recovery still owns. Late callbacks can
        # acknowledge that terminal generation without waking another task.
        recovery = "awaiting_generation_recovery"
        try:
            with client_factory(args) as client:
                aborted = client.abort_run_waiter(
                    spec.task_id, generation=generation,
                    message="PR delegation setup was uncertain. Re-check external state before retrying.",
                )
            if aborted.get("accepted"):
                recovery = "generation_aborted_with_wake"
        except (OSError, RuntimeError, httpx.HTTPError):
            pass
        return {"detached": False, "kind": "delegated", "error": "ambiguous_delegation_setup",
                "recovery": recovery, "waiter_generation": generation}
