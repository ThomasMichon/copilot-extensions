"""JSON-only provider-neutral producer CLI."""

from __future__ import annotations

import json
import sys

from . import claims_cli, config as cfg, output, resource_producer as producer
from . import tracking, worktree_ops_cli


def add_parsers(sub) -> None:
    """Register the producer ABI without provider imports or provisioning."""
    parser = sub.add_parser("resource-producer", help="Reserve/fence/bind/inspect/verify-close disposable ownership; JSON stdin/stdout")
    parser.add_argument("operation", choices=("capabilities", "reserve", "begin", "complete", "bind", "inspect", "close"))
    parser.add_argument("--json", action="store_true", required=True)
    parser.add_argument("--owner-ref", help="Qualified local owner; omitted, resolve current worktree/supported anchor")


def cmd_resource_producer(args) -> int:
    """Consume exactly one JSON object and dispatch through tracking authority."""
    try:
        if args.operation == "capabilities":
            output._json_output({
                "schema": producer.SCHEMA, "version": producer.VERSION,
                "operations": ["reserve", "begin", "complete", "bind", "inspect", "close"],
                "allocation_fencing": 1,
                "lifetime": "disposable-owned", "ref_encoding": "namespace:r1.base64url",
                "max_ref_bytes": producer.MAX_REF_BYTES, "max_input_bytes": producer.MAX_INPUT_BYTES,
            })
            return 0
        raw_bytes = sys.stdin.buffer.read(producer.MAX_INPUT_BYTES + 1)
        if len(raw_bytes) > producer.MAX_INPUT_BYTES:
            raise ValueError("producer stdin exceeds size limit")
        raw = raw_bytes.decode("utf-8", errors="strict")
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate producer JSON key")
                result[key] = value
            return result
        payload = json.loads(raw, object_pairs_hook=unique)
        if not isinstance(payload, dict):
            raise ValueError("producer stdin must be exactly one JSON object")
        owner_ref = args.owner_ref or worktree_ops_cli._resolve_owner_ref()
        if not owner_ref:
            raise ValueError("no durable producer owner resolved from current directory")
        parsed = tracking.parse_claim_ref(owner_ref)
        if parsed is None or not parsed.is_qualified:
            raise ValueError("producer owner must be a qualified reference")
        config = cfg.load_project_config(parsed.project)
        owner_ref = tracking.format_claim_ref(parsed.machine, parsed.project, parsed.worktree_id)
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
