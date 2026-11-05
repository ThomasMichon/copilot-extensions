"""The retained-lifetime front door, without bootstrap or legacy claim fallback."""

from __future__ import annotations

import uuid
from pathlib import Path

from . import claims_cli, config as cfg, output, resource_producer as producer
from . import resource_relationship as relationship, tracking, tracking_write
from .resource_producer_cli import read_input


def _resident_selection() -> tuple[Path, tuple[str, int, str], str, str]:
    from . import locks, status_monitor_runtime

    path = status_monitor_runtime._monitor_lock_path()
    data = locks.read_lock(path)
    endpoint = tracking_write.endpoint_from_rendezvous(
        data, verb="resource_relationship", min_version=2,
    )
    generation = data.get("tracking_write_generation") if isinstance(data, dict) else None
    runtime = data.get("runtime_generation") if isinstance(data, dict) else None
    if (
        endpoint is None or not isinstance(generation, str) or not generation.strip()
        or not isinstance(runtime, str) or not runtime.strip()
    ):
        raise ValueError("selected resident lacks incarnation-checked relationship inspection")
    return path, endpoint, generation, runtime


def _inspect_resident(args: dict) -> dict:
    from .tracking_relationship_write import _input

    payload = _input("inspect", args["input"])
    nonce = payload.get("expected_creation_nonce")
    if nonce is None:
        raise ValueError("resident-only inspection requires expected_creation_nonce")
    selection = _resident_selection()
    _, endpoint, generation, runtime = selection
    try:
        result = tracking_write.wcs_client.request(
            *endpoint, kind=tracking_write.KIND, key=uuid.uuid4().hex,
            payload={"verb": "resource_relationship", "args": args},
            request_deadline_s=tracking_write.REQUEST_DEADLINE_S,
            completion_timeout_s=tracking_write.REQUEST_DEADLINE_S,
        )
    except tracking_write.wcs_client.DaemonUnavailable as exc:
        raise ValueError(f"resident-only inspection unavailable: {exc}") from exc
    if _resident_selection() != selection:
        raise ValueError("selected resident changed during relationship inspection")
    if result.get("error"):
        return result
    contract = result.get("writer_contract")
    identity_field = "token" if "token" in payload else "request_id"
    if (
        result.get("schema") != relationship.SCHEMA
        or type(result.get("version")) is not int or result["version"] != relationship.VERSION
        or result.get("ok") is not True or result.get("owner") != args["owner_ref"]
        or result.get(identity_field) != payload[identity_field]
        or result.get("creation_nonce") != nonce
        or not isinstance(contract, dict)
        or any(
            type(contract.get(key)) is not type(value) or contract[key] != value
            for key, value in relationship.writer_contract().items()
        )
    ):
        raise ValueError("resident response lacks the checked incarnation and executing writer contract")
    return {**result, "resident": {"generation": generation, "runtime_generation": runtime}}


def cmd_resource_relationship(args) -> int:
    """Resolve one explicit owner and invoke its selected registered authority."""
    try:
        resident_only = getattr(args, "resident_only", False)
        if resident_only and args.operation != "inspect":
            raise ValueError("--resident-only is supported only for inspect")
        if args.operation == "capabilities":
            output._json_output({
                "schema": relationship.SCHEMA, "version": relationship.VERSION,
                "operations": ["reserve", "bind", "inspect", "close"],
                "lifetime": relationship.LIFETIME, "metadata_version": relationship.METADATA_VERSION,
                "handoff": False, "ref_encoding": "namespace:l1.base64url",
                "inspect_absence": True,
                "expected_creation_nonce": True,
                "resident_inspect": 1,
                "writer_scope": "executing-process",
                "writer_contract": relationship.writer_contract(),
                "max_ref_bytes": producer.MAX_REF_BYTES, "max_input_bytes": producer.MAX_INPUT_BYTES,
            })
            return 0
        payload = read_input("relationship")
        parsed = tracking.parse_claim_ref(args.owner_ref)
        if parsed is None or not parsed.is_qualified:
            raise ValueError("relationship owner must be an explicit qualified reference")
        owner_ref = tracking.format_claim_ref(parsed.machine, parsed.project, parsed.worktree_id)
        config = cfg.load_project_config(parsed.project)
        path, _, error = claims_cli._resolve_owner_ref_record_path(owner_ref, config)
        if error or path is None or not path.exists():
            raise ValueError(error or "local relationship owner ledger is unavailable")
        request = {
            "schema": relationship.SCHEMA, "version": relationship.VERSION,
            "operation": args.operation, "input": payload,
            "owner_ref": owner_ref, "yaml_path": str(path),
        }
        result = _inspect_resident(request) if resident_only else tracking_write.run_direct(
            "resource_relationship", request,
            reason="relationship uses registered bounded RMW and exact replay; no monitor bootstrap",
        )
        output._json_output(result)
        return 1 if result.get("error") else 0
    except (ValueError, TypeError, OSError, RuntimeError, RecursionError) as exc:
        output._json_output({
            "schema": relationship.SCHEMA, "version": relationship.VERSION,
            "error": "rejected", "message": str(exc),
        })
        return 1
