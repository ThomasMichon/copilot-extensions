"""The retained-lifetime front door, without bootstrap or legacy claim fallback."""

from __future__ import annotations

from . import claims_cli, config as cfg, output, resource_producer as producer
from . import resource_relationship as relationship, tracking, tracking_write
from .resource_producer_cli import read_input


def cmd_resource_relationship(args) -> int:
    """Resolve one explicit owner and invoke the registered direct authority."""
    try:
        if args.operation == "capabilities":
            output._json_output({
                "schema": relationship.SCHEMA, "version": relationship.VERSION,
                "operations": ["reserve", "bind", "inspect", "close"],
                "lifetime": relationship.LIFETIME, "metadata_version": relationship.METADATA_VERSION,
                "handoff": False, "ref_encoding": "namespace:l1.base64url",
                "inspect_absence": True,
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
        result = tracking_write.run_direct("resource_relationship", {
            "schema": relationship.SCHEMA, "version": relationship.VERSION,
            "operation": args.operation, "input": payload,
            "owner_ref": owner_ref, "yaml_path": str(path),
        }, reason="relationship uses registered bounded RMW and exact replay; no monitor bootstrap")
        output._json_output(result)
        return 1 if result.get("error") else 0
    except (ValueError, TypeError, OSError, RuntimeError, RecursionError) as exc:
        output._json_output({
            "schema": relationship.SCHEMA, "version": relationship.VERSION,
            "error": "rejected", "message": str(exc),
        })
        return 1
