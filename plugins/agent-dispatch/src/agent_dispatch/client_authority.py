"""Explicit task authority, independent of coordinator reachability."""

from __future__ import annotations

import argparse
import os
from typing import Any, Literal

from .client import DispatchError

Authority = Literal["legacy", "local", "central", "explicit"]
AUTHORITY_ENV = "AGENT_DISPATCH_TASK_AUTHORITY"


def default_authority() -> Authority:
    value = os.environ.get(AUTHORITY_ENV, "").strip().lower()
    if not value:
        return "legacy"
    if value not in {"local", "central"}:
        raise DispatchError(400, f"{AUTHORITY_ENV} must be local or central")
    return "central" if value == "central" else "local"


def client_authority(args: argparse.Namespace) -> Authority:
    if getattr(args, "local", False):
        if getattr(args, "url", None) or getattr(args, "shared", False):
            raise DispatchError(400, "--local cannot be combined with --url or --shared")
        return "local"
    if getattr(args, "url", None):
        return "explicit"
    if getattr(args, "shared", False):
        return "central"
    return default_authority()


def validate_server_authority() -> None:
    from .config import federation_role, shared_url

    authority = default_authority()
    if authority == "legacy":
        return
    if authority == "central" and not shared_url():
        raise DispatchError(400, "central task authority requires AGENT_DISPATCH_SHARED_URL")
    role = federation_role()
    if authority == "central" or role is not None:
        validate_federated_role(role)


def validate_federated_role(role: str | None) -> None:
    if default_authority() != "legacy" and role not in {"peer", "satellite"}:
        raise DispatchError(
            400, "an explicit task authority participant must be a peer or satellite; "
            "its independent database cannot become the central coordinator",
        )


def local_service_args(args: argparse.Namespace) -> argparse.Namespace:
    """Installer-owned local lifecycle operations never target the fleet."""
    return argparse.Namespace(**{
        **vars(args), "local": True, "shared": False, "url": None,
    })


def qualify_task(args: argparse.Namespace, task: dict[str, Any]) -> dict[str, Any]:
    authority = client_authority(args)
    if authority not in {"local", "central"}:
        return task
    task_id = task.get("id")
    if not isinstance(task_id, str) or not task_id:
        raise DispatchError(502, "coordinator returned an invalid task identity")
    return {
        **task, "task_authority": authority,
        "claim_ref": f"dispatch-{authority}-task:{task_id}",
    }


def require_explicit_claim_authority(args: argparse.Namespace) -> None:
    if default_authority() != "legacy" and not any(
        getattr(args, flag, None) for flag in ("local", "shared", "url")
    ):
        raise DispatchError(
            409, "an unqualified task claim reference cannot establish its original authority; "
            "use dispatch-local-task: or dispatch-central-task: after reconciling the original writer",
        )
