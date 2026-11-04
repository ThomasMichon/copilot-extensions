"""Owned durable-placement commands; discovery never allocates or ensures a host."""

from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
import json
from pathlib import Path
import sys
from typing import Any

from . import config as cfg
from .worktree_data_contract import (
    PROJECT_INVENTORY_CAPABILITY as INVENTORY_CAPABILITY,
    MAX_INVENTORY_BYTES,
)

CAPABILITY = "worktree-data-v1"
VERSION = 1
SCHEMA = "agent-worktrees.worktree-data"
MAX_RESPONSE_BYTES = 128 * 1024
COMMANDS = frozenset({"worktree-data-read", "worktree-data-allocate"})


def add_parsers(sub: argparse._SubParsersAction) -> None:
    """Register independently negotiated reads and explicit new-home allocation."""
    for name in sorted(COMMANDS):
        parser = sub.add_parser(
            name, allow_abbrev=False,
            help="Inspect durable worktree placement"
            if name == "worktree-data-read" else "Allocate a new managed data home",
        )
        parser.add_argument("--json", action="store_true")
        parser.add_argument("--contract-version", type=int, choices=(VERSION,), default=VERSION)
        if name == "worktree-data-read":
            selected = parser.add_mutually_exclusive_group(required=True)
            selected.add_argument("--capabilities", action="store_true")
            selected.add_argument("--worktree")
            selected.add_argument("--inventory", action="store_true")
            parser.add_argument("--creation-nonce")
            parser.add_argument("--store-id")
            parser.add_argument("--placement-generation", type=int)
        else:
            parser.add_argument("--worktree", required=True)
            parser.add_argument("--creation-nonce", required=True)
            parser.add_argument("--domain", choices=("agendas",), default="agendas")
            parser.add_argument("--lane", required=True)
            parser.add_argument("--store-id", required=True)
        parser.set_defaults(func=cmd_worktree_data)


def _json_value(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    raise TypeError("placement response contains an unsupported value")


def _emit(value: dict[str, Any], *, max_bytes: int = MAX_RESPONSE_BYTES) -> None:
    raw = json.dumps(
        {"schema": SCHEMA, "version": VERSION, **value},
        ensure_ascii=False, allow_nan=False, separators=(",", ":"), default=_json_value,
    )
    if len(raw.encode("utf-8")) + 1 > max_bytes:
        raise ValueError("placement response exceeds its bounded point-read size")
    sys.stdout.write(raw + "\n")
    sys.stdout.flush()


def _failure(code: str, message: str) -> int:
    message = message.encode("utf-8", errors="backslashreplace").decode("utf-8")
    diagnostic = "".join(
        char if ord(char) >= 32 and ord(char) != 127 else f"\\u{ord(char):04x}"
        for char in message[:1024]
    )
    _emit({"ok": False, "error": {"code": code, "message": diagnostic}})
    sys.stderr.write(f"{code}: {diagnostic}\n")
    return 1


def project_unavailable(argv: list[str], message: str) -> int:
    """Validate actual syntax before reporting absent invocation context."""
    parser = argparse.ArgumentParser(allow_abbrev=False)
    add_parsers(parser.add_subparsers(dest="command", required=True))
    parser.parse_args(argv)
    return _failure("project-unavailable", message)


def cmd_worktree_data(args: argparse.Namespace) -> int:
    """Use only the selected installation's durable-placement owner."""
    try:
        capabilities = getattr(args, "capabilities", False)
        inventory = getattr(args, "inventory", False)
        if capabilities or inventory:
            if any(getattr(args, name, None) is not None for name in (
                "creation_nonce", "store_id", "placement_generation",
            )):
                return _failure("invalid-request", "discovery takes no store selectors")
            if capabilities:
                _emit({
                    "ok": True, "capabilities": [CAPABILITY],
                    "optional_capabilities": [INVENTORY_CAPABILITY],
                })
                return 0
        project = cfg.project_name()
    except (RuntimeError, ValueError) as error:
        return _failure("project-unavailable", str(error))
    try:
        from .worktree_data_store import (
            WorktreeDataError, allocate_worktree_data, read_worktree_data,
        )
    except ImportError as error:
        return _failure("unavailable", f"placement implementation unavailable: {error}")
    try:
        if inventory:
            from .worktree_data_inventory import read_project_worktree_data

            value = read_project_worktree_data(project)
            try:
                _emit({
                    "ok": True, "project": project,
                    "capability": INVENTORY_CAPABILITY, "data": value.to_dict(),
                }, max_bytes=MAX_INVENTORY_BYTES)
            except ValueError:
                return _failure("inventory-limit", "project inventory response exceeds byte limit")
            return 0
        elif args.command == "worktree-data-allocate":
            value = allocate_worktree_data(
                project, args.worktree, creation_nonce=args.creation_nonce,
                domain=args.domain, lane=args.lane, store_id=args.store_id,
            )
        else:
            value = read_worktree_data(
                project, args.worktree, store_id=args.store_id,
                creation_nonce=args.creation_nonce,
                placement_generation=args.placement_generation,
            )
        data = (
            asdict(value) if is_dataclass(value)
            else value.to_dict() if value is not None else None
        )
        _emit({
            "ok": True, "project": project, "worktree_id": args.worktree,
            "availability": "registered" if value is not None else "not-registered",
            "data": data,
        })
        return 0
    except WorktreeDataError as error:
        return _failure(error.code, str(error))
    except (OSError, ValueError, TypeError, RuntimeError) as error:
        return _failure("unavailable", str(error))
    except KeyboardInterrupt:
        return 130
