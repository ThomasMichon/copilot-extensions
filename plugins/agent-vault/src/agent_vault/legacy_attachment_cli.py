"""Ordinary key-attachment commands, separate from non-activating contracts."""

from __future__ import annotations

import base64
from pathlib import Path
import sys
from typing import Any

from .mutation_client import ordinary_mutation


def command_import(args: Any) -> int:
    key_file = Path(args.key_file)
    pub_file = key_file.with_suffix(key_file.suffix + ".pub") if key_file.suffix else Path(
        str(key_file) + ".pub"
    )
    if not key_file.exists():
        print(f"Error: key file not found: {key_file}", file=sys.stderr)
        return 1
    if not pub_file.exists():
        print(f"Error: public key not found: {pub_file}", file=sys.stderr)
        return 1

    @ordinary_mutation
    def execute(args: Any) -> int:
        from . import cli

        if not cli._ensure_unlocked_service():
            return 1
        key_data = base64.b64encode(key_file.read_bytes()).decode()
        pub_data = base64.b64encode(pub_file.read_bytes()).decode()
        key_name = key_file.name
        response = cli.send_command({
            "action": "import-key", "entry": args.entry, "key_name": key_name,
            "key_data": key_data, "pub_data": pub_data,
        }, timeout=15.0)
        if response and response.get("ok"):
            print(response.get("message", f"Imported {key_name}"))
            return 0
        error = response.get("error", "unknown") if response else "service unreachable"
        print(f"Error: {error}", file=sys.stderr)
        return 1

    return execute(args)


def command_export(args: Any) -> int:
    dest_dir = Path(args.dest_dir)
    if not dest_dir.is_dir():
        print(f"Error: destination directory not found: {dest_dir}", file=sys.stderr)
        return 1

    @ordinary_mutation
    def execute(args: Any) -> int:
        from . import cli

        if not cli._ensure_unlocked_service():
            return 1
        response = cli.send_command({
            "action": "export-key", "entry": args.entry, "key_name": args.key_name,
        }, timeout=15.0)
        if not response or not response.get("ok"):
            error = response.get("error", "unknown") if response else "service unreachable"
            print(f"Error: {error}", file=sys.stderr)
            return 1
        key_file = dest_dir / args.key_name
        pub_file = dest_dir / (args.key_name + ".pub")
        key_file.write_bytes(base64.b64decode(response["key_data"]))
        pub_file.write_bytes(base64.b64decode(response["pub_data"]))
        if not cli.IS_WINDOWS:
            key_file.chmod(0o600)
            pub_file.chmod(0o644)
        print(f"Exported {args.key_name} to {dest_dir}")
        return 0

    return execute(args)
