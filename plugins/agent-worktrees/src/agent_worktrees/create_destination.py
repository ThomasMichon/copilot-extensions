"""Ordinary destination selection with origin-owned, sessionless remote creation."""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import secrets
from collections.abc import Callable
from pathlib import Path

from machine_transport import (
    known_environment_label, resolve_machine_selection, resolve_ssh_target, split_machine_environment,
)

from . import allocation_creation_result, allocation_link_demand, claims_cli, config as cfg
from . import execution_spaces, invocation_project, owned_worktree as allocation
from . import owned_worktree_remote as remote, resource_producer as producer
from . import tracking, tracking_write, worktree_identity
from .creation_requests import CreationRequestFailure
from .resource_relationship import valid_request_id

_LOG = logging.getLogger(__name__)


class DestinationCreationFailure(CreationRequestFailure):
    """Keep the original retry identity visible after uncertain receiving effects."""

    def __init__(self, status: str, message: str, recovery: dict) -> None:
        super().__init__(
            status, f"{message}; recover original request with --creation-request-id "
            f"{recovery['request_id']}"
            + (f" or creation key {recovery['creation_key']!r}" if recovery["creation_key"] is not None else "")
            + " (never retry with a new key)",
        )
        self.recovery = recovery

    def payload(self) -> dict:
        return {**super().payload(), "allocation": self.recovery}


def _cwd_project(cwd: Path) -> str | None:
    from .front_door_cli import _reverse_lookup_project

    anchor = invocation_project._git_toplevel(cwd)
    return _reverse_lookup_project(anchor) if anchor is not None else None


def _origin(
    cwd: Path, owner_ref: str | None, *, standalone: bool,
) -> tuple[str | None, cfg.Config, str | None]:
    project = _cwd_project(cwd)
    parsed_owner = tracking.parse_claim_ref(owner_ref) if owner_ref else None
    context_project = (
        parsed_owner.project if parsed_owner is not None and parsed_owner.is_qualified
        else project or cfg.project_name()
    )
    config = cfg.load_project_config(context_project)
    if not owner_ref and not standalone:
        record = worktree_identity.tracked_invocation_record(
            cwd, project=project, anchor=Path(config.default_repo.anchor),
        ) if project else None
        if record is not None:
            execution_spaces.require_record_mutation(record, config)
            owner_ref = tracking.format_claim_ref(
                record.execution_machine, record.repo, record.worktree_id,
                os.environ.get("COPILOT_AGENT_SESSION_ID") or None,
            )
        else:
            from .worktree_ops_cli import _resolve_anchor_owner_ref

            if worktree_identity._worktree_id_from_git(cwd, isolated_repository=True) is not None:
                raise ValueError(
                    "original linked worktree owner is untracked; adopt it or explicitly select --no-owner",
                )
            if project:
                owner_ref = _resolve_anchor_owner_ref(config, cwd=cwd, project=project)
    return project, config, owner_ref


def _creator(owner_ref: str | None) -> tuple[str, Path]:
    parsed = tracking.parse_claim_ref(owner_ref) if owner_ref else None
    if parsed is None or not parsed.is_qualified or parsed.is_anchor:
        raise ValueError("remote creation requires a managed origin worktree; anchors are not creators")
    config = cfg.load_project_config(parsed.project)
    path, _, error = claims_cli._resolve_owner_ref_record_path(owner_ref, config)
    if error or path is None or not path.exists():
        raise ValueError(error or "remote creation requires the actual local origin ledger")
    record = tracking.load_record(path)
    execution_spaces.require_record_mutation(record, config)
    if record.status in producer.FROZEN_OWNER_STATUSES:
        raise ValueError(f"allocation creator is {record.status}; ownership is frozen")
    if (parsed.project, parsed.worktree_id) != (record.repo, record.worktree_id):
        raise ValueError("allocation creator differs from its actual origin ledger")
    creator = tracking.format_claim_ref(record.execution_machine, record.repo, record.worktree_id)
    allocation.qualified_ref(creator)
    return creator, path


def _target(config: cfg.Config, machine: str | None, project: str) -> dict:
    context = execution_spaces.current_execution_context(config)
    current = context.execution_machine
    if machine is None:
        key = current
    else:
        entries = cfg.load_machines_yaml(config.default_repo.anchor, config=config)
        selected = resolve_machine_selection(entries, machine)
        if selected.entry is None:
            raise ValueError("creation destination must resolve to one registered execution space")
        entry = selected.entry
        key = entry.key
        wanted = selected.target.environment
        if wanted:
            declared = (
                entry.execution_platform or split_machine_environment(entry.key).environment
                or known_environment_label(entry.environment)
            )
            if (declared and declared != wanted) or (key == current and context.environment != wanted):
                raise ValueError("creation destination conflicts with the requested execution environment")
            if key != current:
                alias, _ = resolve_ssh_target(entry)
                routed = {
                    known_environment_label(profile.name)
                    for profile in entry.ssh_environments if profile.alias == alias
                }
                if routed != {wanted}:
                    raise ValueError("selected SSH route does not prove the requested execution environment")
    target = {"machine": key, "project": project}
    if not allocation.valid_target(target):
        raise ValueError("invalid creation destination machine or repository")
    return target


def _receiver_capabilities(creator: str, target: dict) -> None:
    value = remote.capabilities(creator, target)
    expected = {
        "schema": allocation.SCHEMA, "version": allocation.VERSION, "role": "receiver",
        "lifetime": allocation.LIFETIME, "metadata_version": allocation.METADATA_VERSION,
        "receiving_receipt": 1, "claim_linkage": "non-blocking", "agent_launch": False,
        "max_input_bytes": producer.MAX_INPUT_BYTES,
        "max_identity_bytes": allocation.MAX_IDENTITY_BYTES,
        "lifecycle_capacity_reserved": True,
    }
    if any(type(value.get(key)) is not type(wanted) or value[key] != wanted for key, wanted in expected.items()):
        raise ValueError("selected receiver lacks compatible origin-owned creation; update the receiver")
    operations = value.get("operations")
    if (
        not isinstance(operations, list) or not all(isinstance(item, str) for item in operations)
        or not {"create", "result", "inspect", "confirm"}.issubset(operations)
    ):
        raise ValueError("selected receiver lacks the original creation-result operation")
    if value.get("creation_result") != {
        "schema": allocation_creation_result.SCHEMA, "version": allocation_creation_result.VERSION,
        "max_response_bytes": allocation_creation_result.MAX_RESPONSE_BYTES,
    } or type(value["creation_result"].get("version")) is not int:
        raise ValueError("selected receiver lacks compatible ordinary creation results")


def _origin_operation(creator: str, path: Path, operation: str, payload: dict) -> dict:
    result = tracking_write.run_direct("worktree_allocation", {
        "schema": allocation.SCHEMA, "version": allocation.VERSION,
        "operation": operation, "input": payload, "owner_ref": creator, "yaml_path": str(path),
    }, reason="ordinary creation uses the registered protected allocation writer")
    if result.get("ok") is not True:
        raise ValueError(result.get("message") or "origin allocation transaction refused")
    metadata = result.get("producer")
    if not allocation.valid_metadata(metadata) or metadata["creator"] != creator:
        raise ValueError("origin allocation transaction returned invalid responsibility")
    return metadata


def creation_kind(args: argparse.Namespace) -> str:
    bridge, system = bool(getattr(args, "bridge", False)), bool(getattr(args, "system", False))
    caller = getattr(args, "caller_worktree", None)
    if bridge and system:
        raise ValueError("--bridge and --system select different worktree kinds")
    if caller is not None and (not bridge or not isinstance(caller, str) or not caller.strip()):
        raise ValueError("--caller-worktree requires --bridge and a nonempty navigation identity")
    return "bridge" if bridge else "system" if system else "session"


def _remote_options(args: argparse.Namespace, dispatch_attempt: dict | None) -> dict:
    if getattr(args, "seed", None) is not None:
        raise ValueError("remote create does not support --seed; stage prompts through the composing host")
    options: dict = {"kind": creation_kind(args), "no_pair": bool(
        getattr(args, "no_pair", False) or os.environ.get("AGENT_WORKTREES_NO_PAIR"),
    )}
    for field in ("interface", "origin", "bound_agent", "caller_worktree"):
        value = getattr(args, field, None)
        if value is not None:
            options[field] = value
    if dispatch_attempt is not None:
        options["dispatch_attempt"] = dispatch_attempt
    return options


def creation_request_id(key: str) -> str:
    """Map the existing opaque key contract to one stable bounded allocation ID."""
    if not isinstance(key, str) or not key.strip() or len(key) > 1024:
        raise ValueError("creation key must be non-empty and at most 1024 characters")
    return "create_" + hashlib.sha256(
        b"agent-worktrees.create.destination.v1\0" + key.encode("utf-8"),
    ).hexdigest()


def _remote_create(
    args: argparse.Namespace, owner_ref: str | None, target: dict,
    dispatch_attempt: dict | None,
) -> dict:
    if getattr(args, "no_owner", False) or getattr(args, "system", False):
        raise ValueError("remote create requires origin ownership; --no-owner and --system are not supported")
    creator, path = _creator(owner_ref)
    target = {**target, "options": _remote_options(args, dispatch_attempt)}
    key = getattr(args, "creation_key", None)
    request_id = getattr(args, "creation_request_id", None)
    recovering = request_id is not None
    if recovering:
        if not valid_request_id(request_id):
            raise ValueError("invalid original creation request ID")
        if key is not None and creation_request_id(key) != request_id:
            raise ValueError("creation key conflicts with the original request ID")
    else:
        if key is None:
            key = secrets.token_urlsafe(24)
        request_id = creation_request_id(key)
    recovery = {
        "version": 1, "creator": creator, "creation_key": key, "request_id": request_id,
        "target": target, "linkage": "unreserved",
    }
    # Discovery is pre-effect. The writer is this actual process, not a possibly
    # older resident's advertised implementation of another verb generation.
    tracking_write._ensure_verb_modules_loaded()
    if "worktree_allocation" not in tracking_write.registered_verbs():
        raise ValueError("origin writer lacks protected worktree allocation")
    metadata = None
    if recovering:
        try:
            metadata = _origin_operation(creator, path, "inspect", {"request_id": request_id})
        except (OSError, ValueError, RuntimeError) as exc:
            raise DestinationCreationFailure("pending", str(exc), {
                **recovery, "linkage": "unknown",
            }) from exc
        if producer.canonical_json(metadata["recovery"]) != producer.canonical_json(target):
            raise DestinationCreationFailure(
                "conflict", "recovery destination/options differ from the original allocation", recovery,
            )
    allocation_link_demand.require_capability(creator)
    _receiver_capabilities(creator, target)
    try:
        if metadata is None:
            metadata = _origin_operation(creator, path, "reserve", {
                "request_id": request_id, "target": target,
            })
    except (OSError, ValueError, RuntimeError) as exc:
        raise DestinationCreationFailure("pending", str(exc), {
            **recovery, "linkage": "unknown",
        }) from exc
    recovery["linkage"] = metadata["phase"]
    try:
        if metadata["phase"] == "closed":
            raise DestinationCreationFailure("terminal", "original allocation is concluded", recovery)
        if metadata["phase"] == "pending":
            proof = remote.request("create", metadata)
            if not allocation.verified_observation(proof, metadata):
                allocation_creation_result.validate({
                    "schema": allocation_creation_result.SCHEMA,
                    "version": allocation_creation_result.VERSION,
                    "allocation": proof, "result": None,
                }, metadata)
        response = remote.request("result", metadata)
        result = allocation_creation_result.validate(response, metadata)
        recovery["receiving_status"] = response["allocation"]["status"]
        if result is None:
            status = "pending" if recovery["receiving_status"] in {"pending", "unknown"} else "terminal"
            raise DestinationCreationFailure(
                status, "original receiving allocation has no usable completed result", recovery,
            )
    except DestinationCreationFailure:
        raise
    except (OSError, ValueError, RuntimeError) as exc:
        raise DestinationCreationFailure(
            "pending", f"{exc}; retain the original creation key and allocation responsibility", recovery,
        ) from exc
    try:
        current = _origin_operation(creator, path, "inspect", {"request_id": request_id})
        recovery["linkage"] = current["phase"]
    except (OSError, ValueError, RuntimeError) as exc:
        recovery["linkage"] = "unknown"
        _LOG.warning("Receiving creation is durable; current origin linkage is unknown: %s", exc)
    if recovery["linkage"] == "closed":
        raise DestinationCreationFailure("terminal", "original allocation is concluded", recovery)
    return {**result, "allocation": recovery}


def create(
    args: argparse.Namespace, *, owner_ref: str | None, dispatch_attempt: dict | None,
    invocation_cwd: Path, create_local: Callable[..., dict],
) -> dict:
    """Capture origin before selecting a destination or changing ambient project."""
    kind = creation_kind(args)
    standalone = bool(getattr(args, "no_owner", False) or getattr(args, "system", False))
    project, origin_config, origin_owner = _origin(invocation_cwd, owner_ref, standalone=standalone)
    requested_project = getattr(args, "repo", None)
    destination_project = project if requested_project is None else requested_project
    if not destination_project:
        raise ValueError("create --repo is required when the original CWD has no registered repository")
    target = _target(origin_config, getattr(args, "machine", None), destination_project)
    current = execution_spaces.current_execution_context(origin_config).execution_machine
    if target["machine"] != current:
        return _remote_create(args, origin_owner, target, dispatch_attempt)
    if getattr(args, "creation_request_id", None) is not None:
        raise ValueError("request-ID recovery requires the original remote --machine destination")
    previous = cfg.active_project()
    cfg.set_active_project(destination_project)
    try:
        from . import project_state

        if project_state.namespaced():
            project_state.ensure_project_state(destination_project)
        config = cfg.load_config()
        system = getattr(args, "system", False)
        return create_local(
            config, no_mux=True, kind=kind,
            owner=(getattr(args, "owner", None) or getattr(args, "name", None)) if system else None,
            name=getattr(args, "name", None) if system else None,
            interface=getattr(args, "interface", None), origin=getattr(args, "origin", None),
            caller_worktree=getattr(args, "caller_worktree", None),
            owner_ref=None if standalone else origin_owner, inherit_parent_session=not standalone,
            dispatch_attempt=dispatch_attempt, bound_agent=getattr(args, "bound_agent", None),
            no_pair=getattr(args, "no_pair", False), pending_seed=getattr(args, "seed", None),
            creation_key=getattr(args, "creation_key", None),
            build_launch_plan=False, identity_mode="tuple-v1",
        )
    finally:
        cfg.set_active_project(previous)
