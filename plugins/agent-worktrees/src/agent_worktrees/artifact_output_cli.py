"""Strict, opt-in JSON front door for the native user-output producer."""

from __future__ import annotations

import hashlib
from argparse import Namespace
from pathlib import Path
from typing import Any

import yaml

from . import artifact_output as artifacts, config as cfg, output
from .tracking_claims import format_claim_ref, parse_claim_ref
from .resource_producer import canonical_json
from .resource_producer_cli import read_input

OPERATIONS = ("resolve", "produce", "inspect", "recover", "deliver")
CLI_OPERATIONS = tuple("output-" + operation for operation in ("capabilities", *OPERATIONS))


def _owner(args: Namespace) -> tuple[Path, str, cfg.Config]:
    from . import claims_cli, worktree_ops_cli

    owner = args.owner_ref or worktree_ops_cli._resolve_owner_ref()
    parsed = parse_claim_ref(owner) if owner else None
    if not args.owner_ref and parsed is not None and parsed.is_qualified and parsed.project is not None:
        owner = format_claim_ref(parsed.machine, parsed.project, parsed.worktree_id)
        parsed = parse_claim_ref(owner)
    if (
        parsed is None or parsed.project is None or not parsed.is_qualified
        or parsed.session or parsed.canonical() != owner
    ):
        raise ValueError("user output requires an exact qualified local owner")
    config = cfg.load_project_config(parsed.project)
    path, _, error = claims_cli._resolve_owner_ref_record_path(owner, config)
    if error or path is None or not path.exists():
        raise ValueError(error or "user-output owner ledger is unavailable")
    readiness = claims_cli._coordination_readiness_for_owner_ref(owner, config)
    if not readiness.ready:
        raise ValueError(readiness.error or readiness.code)
    artifacts._record(path, owner)
    assert isinstance(owner, str)
    return path, owner, config


def cmd_user_output(args: Namespace) -> int:
    """Use existing configuration and ownership; never configure or discover a root."""
    try:
        if args.operation not in {"capabilities", *OPERATIONS}:
            raise ValueError("unsupported user-output operation")
        if args.operation == "capabilities":
            output._json_output({**artifacts.capabilities(), "operations": list(CLI_OPERATIONS[1:])})
            return 0
        payload = read_input("user output")
        allowed = {"topic", "owner_roots", "destination", "target"}
        required = {"topic", "owner_roots"}
        if args.operation == "produce":
            allowed |= {"files", "operation_id"}
            required |= {"files", "operation_id"}
        if args.operation in {"inspect", "recover", "deliver"}:
            allowed = required = {"token"}
        if set(payload) - allowed or not required.issubset(payload):
            raise ValueError("unsupported user-output input fields")
        path, owner, config = _owner(args)
        if args.operation == "inspect":
            artifacts._claim(path, owner, payload["token"])
            result = artifacts._invoke(path, owner, "inspect", payload)
        elif args.operation in {"recover", "deliver"}:
            result = getattr(artifacts, args.operation)(path, owner, payload["token"])
        else:
            from .artifact_output_policy import resolve_user_output
            from .artifact_storage import load_storage
            from .config_sources import configuration_report

            request_hash = hashlib.sha256(canonical_json(payload).encode("ascii")).hexdigest()
            existing = (
                artifacts.existing_operation(path, owner, payload["operation_id"])
                if args.operation == "produce" else None
            )
            if existing is not None:
                recovery = existing["recovery"]
                if recovery["request_hash"] != request_hash:
                    raise ValueError("user-output retry changed the original request")
                selection: dict[str, Any] = {
                    "schema": "agent-worktrees.user-output-destination/v1", "target": "explicit",
                    "root": recovery["root"], "members": recovery["members"], "root_admitted": False,
                    "destination": str(Path(recovery["root"]).joinpath(*recovery["members"])),
                    "origin": "recorded-operation",
                }
            else:
                storage = load_storage(configuration_report(config))
                selection = resolve_user_output(
                    storage, topic=payload["topic"], owner_roots=payload["owner_roots"],
                    destination=payload.get("destination"), target=payload.get("target"),
                )
            if args.operation == "resolve":
                result = selection
            else:
                files = payload["files"]
                if not isinstance(files, dict) or not all(isinstance(value, str) for value in files.values()):
                    raise ValueError("user-output CLI files must map portable names to UTF-8 text")
                result = artifacts.produce_files(
                    path, owner, selection=selection,
                    files={name: data.encode("utf-8") for name, data in files.items()},
                    operation_id=payload["operation_id"], request_hash=request_hash,
                )
        output._json_output(result)
        return 0
    except (ValueError, TypeError, KeyError, OSError, RuntimeError, yaml.YAMLError) as exc:
        output._json_output({"schema": artifacts.SCHEMA, "error": "rejected", "message": str(exc)})
        return 1
