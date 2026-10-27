"""Strict JSON front doors for the origin ledger and named receiving operation."""

from __future__ import annotations

import logging
import sqlite3

import yaml

from . import claims_cli, config as cfg, output, owned_worktree as allocation
from . import resource_producer as producer, tracking_write
from .resource_producer_cli import read_input

ORIGIN_OPERATIONS = ("reserve", "inspect", "bind", "confirm", "close", "cancel")
RECEIVING_OPERATIONS = ("create", "inspect", "confirm", "cancel")


def add_parsers(sub) -> None:
    """Keep unknown arguments strict without importing the full CLI cluster."""
    origin = sub.add_parser(
        "worktree-allocation", help="Reserve/link/reconcile origin-owned worktrees; JSON stdin/stdout",
    )
    origin.add_argument("operation", choices=("capabilities", *ORIGIN_OPERATIONS))
    origin.add_argument("--json", action="store_true", required=True)
    origin.add_argument("--owner-ref", help="Exact qualified local creator worktree")
    receiver = sub.add_parser(
        "worktree-allocation-receive", help="Named receiving worktree owner; JSON stdin/stdout",
    )
    receiver.add_argument("operation", choices=("capabilities", *RECEIVING_OPERATIONS))
    receiver.add_argument("--json", action="store_true", required=True)


def capabilities(*, receiver: bool) -> dict:
    return {
        "schema": allocation.SCHEMA, "version": allocation.VERSION,
        "role": "receiver" if receiver else "origin",
        "operations": list(RECEIVING_OPERATIONS if receiver else ORIGIN_OPERATIONS),
        "lifetime": allocation.LIFETIME, "metadata_version": allocation.METADATA_VERSION,
        "receiving_receipt": 1, "claim_linkage": "non-blocking",
        "handoff": False, "agent_launch": False,
        "max_input_bytes": producer.MAX_INPUT_BYTES,
        "max_identity_bytes": allocation.MAX_IDENTITY_BYTES,
        "lifecycle_capacity_reserved": True,
    }


def _emit(value: dict) -> None:
    output.write_real_stdout(allocation.wire_json(value))


def _failure(exc: Exception) -> int:
    logging.getLogger(__name__).warning("Worktree allocation refused: %s", exc)
    _emit({
        "schema": allocation.SCHEMA, "version": allocation.VERSION,
        "error": "rejected", "message": str(exc),
    })
    return 1


def cmd_worktree_allocation(args) -> int:
    """Resolve the creator's own authority, never the receiving project's ledger."""
    try:
        if args.operation == "capabilities":
            _emit(capabilities(receiver=False))
            return 0
        payload = read_input("worktree allocation")
        producer.canonical_json(payload)
        _, project, _ = allocation.qualified_ref(args.owner_ref)
        config = cfg.load_project_config(project)
        path, _, error = claims_cli._resolve_owner_ref_record_path(args.owner_ref, config)
        if error or path is None or not path.exists():
            raise ValueError(error or "local worktree allocation creator ledger is unavailable")
        result = tracking_write.run_direct("worktree_allocation", {
            "schema": allocation.SCHEMA, "version": allocation.VERSION,
            "operation": args.operation, "input": payload,
            "owner_ref": args.owner_ref, "yaml_path": str(path),
        }, reason="allocation observes outside bounded registered RMW; no monitor bootstrap")
        _emit(result)
        return 1 if result.get("error") else 0
    except (ValueError, TypeError, OSError, RuntimeError, sqlite3.Error, yaml.YAMLError) as exc:
        return _failure(exc)


def cmd_worktree_allocation_receive(args) -> int:
    """Apply one target-local operation; never call back into the origin."""
    from .worktree_allocation_receiver import receive

    try:
        if args.operation == "capabilities":
            _emit(capabilities(receiver=True))
            return 0
        payload = read_input("receiving worktree allocation")
        producer.canonical_json(payload)
        result = receive(args.operation, payload, cfg.load_config())
        _emit(result)
        return 0
    except (ValueError, TypeError, OSError, RuntimeError, sqlite3.Error, yaml.YAMLError) as exc:
        return _failure(exc)
