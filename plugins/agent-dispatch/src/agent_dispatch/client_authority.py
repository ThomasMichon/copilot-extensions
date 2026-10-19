"""Explicit task authority, independent of coordinator reachability."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from typing import Any, Literal

from .client_errors import DispatchError

DefaultAuthority = Literal["legacy", "local", "central"]
Authority = Literal["legacy", "local", "central", "pinned_url"]
AUTHORITY_ENV = "AGENT_DISPATCH_TASK_AUTHORITY"


def default_authority() -> DefaultAuthority:
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
        return "pinned_url"
    if getattr(args, "shared", False):
        return "central"
    return default_authority()


def target_cli_args(args: argparse.Namespace, *, credentials: bool = True) -> list[str]:
    """Preserve the selected writer across generated commands and subprocesses."""
    authority = client_authority(args)
    flags: list[str] = []
    if getattr(args, "url", None):
        flags += ["--url", args.url]
    if getattr(args, "shared", False) or authority == "central":
        flags.append("--shared")
    elif authority == "local":
        flags.append("--local")
    if credentials:
        for name in ("token", "control_token"):
            if getattr(args, name, None):
                flags += ["--" + name.replace("_", "-"), getattr(args, name)]
    return flags


def supervisor_authority_target(args: argparse.Namespace) -> str:
    from .config import shared_url

    shared = shared_url() if client_authority(args) == "central" else None
    target = json.dumps({
        "flags": target_cli_args(args, credentials=False),
        "shared_endpoint": shared.rstrip("/") if shared else None,
    }, separators=(",", ":"))
    return hashlib.sha256(target.encode("utf-8")).hexdigest()


def locked_supervisor_status(
    args: argparse.Namespace, scope: str, *, status: dict | None, holder: int | None, error: str | None,
) -> dict:
    if client_authority(args) == "legacy":
        return {"ensured": False, "reason": "already running"}
    owner = (status or {}).get("owner")
    if (
        not isinstance(owner, dict) or not holder or owner.get("pid") != holder
        or not isinstance(owner.get("authority_target"), str)
        or len(owner["authority_target"]) != 64
    ):
        reason = "authority unavailable"
    elif owner["authority_target"] != supervisor_authority_target(args):
        reason = "authority conflict"
    else:
        return {"ensured": False, "reason": "already running"}
    print(
        f"supervise register --ensure: {reason} for existing scope '{scope}'; "
        "the registration is saved but its selected authority is not ensured. "
        "Reconcile the existing owner before changing its target."
        + (f" Status receipt error: {error}" if error else ""),
        file=sys.stderr,
    )
    return {"ensured": False, "reason": reason, "owner_pid": holder}


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
