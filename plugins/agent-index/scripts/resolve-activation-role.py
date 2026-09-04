#!/usr/bin/env python3
"""Resolve a repository's explicit agent-index activation role."""

from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from resolve_effective_config import (
    ConfigError,
    _load_yaml_mapping,
    _parse_simple_yaml,
    _validate_config,
)


def _fallback_machines(text: str) -> list[str]:
    try:
        normalized = _validate_config(_parse_simple_yaml(text))
    except ConfigError:
        return []
    return [item["machine"].casefold() for item in normalized["indexers"]]


def configured_machines(
    path: Path | None = None, *, data_b64: str | None = None
) -> list[str]:
    if data_b64 is not None:
        try:
            raw = base64.urlsafe_b64decode(data_b64.encode("ascii"))
            value = json.loads(raw.decode("utf-8"))
            normalized = _validate_config(value)
        except (ConfigError, TypeError, ValueError, UnicodeError):
            return []
    elif path is not None:
        state, value = _load_yaml_mapping(path)
        if state != "ready" or value is None:
            return []
        try:
            normalized = _validate_config(value)
        except ConfigError:
            return []
    else:
        return []
    return [item["machine"].casefold() for item in normalized["indexers"]]


def resolve(
    path: Path | None, machine: str, *, data_b64: str | None = None
) -> str:
    machines = configured_machines(path, data_b64=data_b64)
    if not machines:
        return "unconfigured"
    return "host" if machine.strip().casefold() in machines else "client"


def main() -> int:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--config")
    source.add_argument("--data-b64")
    parser.add_argument("--machine", required=True)
    args = parser.parse_args()
    print(
        resolve(
            Path(args.config) if args.config else None,
            args.machine,
            data_b64=args.data_b64,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
