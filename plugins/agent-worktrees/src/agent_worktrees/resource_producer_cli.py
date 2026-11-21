"""JSON-only provider-neutral producer CLI."""

from __future__ import annotations

import json
import sys
from argparse import ArgumentParser, Namespace, _SubParsersAction
from typing import Any

from . import config as cfg, output, resource_producer as producer
from .tracking_claims import format_claim_ref, parse_claim_ref


def add_relationship_parser(sub: _SubParsersAction[ArgumentParser]) -> None:
    relationship = sub.add_parser("resource-relationship", help="Reserve/bind/inspect/verify-retire a retained relationship; JSON stdin/stdout")
    relationship.add_argument("operation", choices=("capabilities", "reserve", "bind", "compare-and-bind", "inspect", "close"))
    relationship.add_argument("--json", action="store_true", required=True)
    relationship.add_argument("--owner-ref", help="Qualified local worktree owner; required except for capabilities")
    relationship.add_argument("--resident-only", action="store_true", help="Inspect the selected resident without bootstrap or direct fallback; requires expected_creation_nonce")


def add_parsers(sub: _SubParsersAction[ArgumentParser]) -> None:
    """Register the producer ABI without provider imports or provisioning."""
    from . import artifact_output_cli, worktree_allocation_cli

    worktree_allocation_cli.add_parsers(sub)
    parser = sub.add_parser("resource-producer", help="Reserve/fence/bind/inspect/verify-close disposable ownership; JSON stdin/stdout")
    parser.add_argument("operation", choices=(
        "capabilities", "reserve", "begin", "complete", "bind", "inspect", "retain", "deliver", "close",
        *artifact_output_cli.CLI_OPERATIONS,
    ))
    parser.add_argument("--json", action="store_true", required=True)
    parser.add_argument("--owner-ref", help="Qualified local owner; omitted, resolve current worktree/supported anchor")
    add_relationship_parser(sub)


def read_input(label: str = "producer") -> dict[str, Any]:
    """Read one byte-bounded strict JSON object before resolving authority."""
    raw_bytes = sys.stdin.buffer.read(producer.MAX_INPUT_BYTES + 1)
    if len(raw_bytes) > producer.MAX_INPUT_BYTES:
        raise ValueError(f"{label} stdin exceeds size limit")

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate {label} JSON key")
            result[key] = value
        return result

    payload = json.loads(raw_bytes.decode("utf-8", errors="strict"), object_pairs_hook=unique)
    if not isinstance(payload, dict):
        raise ValueError(f"{label} stdin must be exactly one JSON object")
    return payload


def cmd_resource_relationship(args: Namespace) -> int:
    from .resource_relationship_cli import cmd_resource_relationship as command
    return command(args)


def cmd_worktree_allocation(args: Namespace) -> int:
    from .worktree_allocation_cli import cmd_worktree_allocation as command
    return command(args)


def cmd_worktree_allocation_receive(args: Namespace) -> int:
    from .worktree_allocation_cli import cmd_worktree_allocation_receive as command
    return command(args)


def cmd_resource_producer(args: Namespace) -> int:
    """Consume exactly one JSON object and dispatch through tracking authority."""
    try:
        if args.operation.startswith("output-"):
            from .artifact_output_cli import cmd_user_output
            return cmd_user_output(Namespace(**{**vars(args), "operation": args.operation.removeprefix("output-")}))
        if args.operation == "capabilities":
            output._json_output({
                "schema": producer.SCHEMA, "version": producer.VERSION,
                "operations": ["reserve", "begin", "complete", "bind", "inspect", "retain", "deliver", "close"],
                "allocation_fencing": 1,
                "artifact_retention": 1,
                "user_output_delivery": 1,
                "optional_lifetimes": [producer.ARTIFACT_LIFETIME, producer.USER_OUTPUT_LIFETIME],
                "lifetime": "disposable-owned", "ref_encoding": "namespace:r1.base64url",
                "max_ref_bytes": producer.MAX_REF_BYTES, "max_input_bytes": producer.MAX_INPUT_BYTES,
            })
            return 0
        from . import claims_cli, worktree_ops_cli

        payload = read_input()
        owner_ref = args.owner_ref or worktree_ops_cli._resolve_owner_ref()
        if not owner_ref:
            raise ValueError("no durable producer owner resolved from current directory")
        parsed = parse_claim_ref(owner_ref)
        if parsed is None or parsed.project is None or not parsed.is_qualified:
            raise ValueError("producer owner must be a qualified reference")
        config = cfg.load_project_config(parsed.project)
        owner_ref = format_claim_ref(parsed.machine, parsed.project, parsed.worktree_id)
        path, _, error = claims_cli._resolve_owner_ref_record_path(owner_ref, config)
        if error or path is None or not path.exists():
            raise ValueError(error or "local producer owner ledger is unavailable")
        readiness = claims_cli._coordination_readiness_for_owner_ref(owner_ref, config)
        if not readiness.ready:
            raise ValueError(readiness.error or readiness.code)
        # Close can run a registered status probe for up to 15 seconds. Use the
        # registered direct RMW authority, not the daemon's 8-second deadline.
        from . import tracking_write
        result = tracking_write.run_direct("resource_producer", {
            "schema": producer.SCHEMA, "version": producer.VERSION, "operation": args.operation,
            "input": payload, "owner_ref": owner_ref, "yaml_path": str(path),
        }, reason="producer uses bounded direct registered RMW; no monitor bootstrap")
        output._json_output(result)
        return 1 if result.get("error") else 0
    except (ValueError, TypeError, OSError, RuntimeError) as exc:
        output._json_output({"schema": producer.SCHEMA, "version": producer.VERSION, "error": "rejected", "message": str(exc)})
        return 1
