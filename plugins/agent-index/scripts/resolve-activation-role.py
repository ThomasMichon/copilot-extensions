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
    resolve as resolve_effective_config,
)


def _fallback_machines(text: str) -> list[str]:
    try:
        normalized = _validate_config(_parse_simple_yaml(text))
    except ConfigError:
        return []
    return [item["machine"].casefold() for item in normalized["indexers"]]


def configured_machines(
    path: Path | None = None, *, data_b64: str | None = None, strict: bool = False,
) -> list[str]:
    if data_b64 is not None:
        try:
            raw = base64.urlsafe_b64decode(data_b64.encode("ascii"))
            value = json.loads(raw.decode("utf-8"))
            normalized = _validate_config(value)
        except (ConfigError, TypeError, ValueError, UnicodeError):
            if strict:
                raise ConfigError("explicit activation configuration is invalid")
            return []
    elif path is not None:
        state, value = _load_yaml_mapping(path)
        if state != "ready" or value is None:
            if strict:
                raise ConfigError("explicit activation configuration is unavailable or invalid")
            return []
        try:
            normalized = _validate_config(value)
        except ConfigError:
            if strict:
                raise
            return []
    else:
        return []
    return [item["machine"].casefold() for item in normalized["indexers"]]


def resolve(
    path: Path | None, machine: str, *, data_b64: str | None = None, strict: bool = False,
) -> str:
    machines = configured_machines(path, data_b64=data_b64, strict=strict)
    if not machines:
        return "unconfigured"
    return "host" if machine.strip().casefold() in machines else "client"


def resolve_repository(repo: str, machine: str) -> str:
    effective = resolve_effective_config(repo)
    if not effective["opted_in"]:
        if effective["reason"] not in {
            "repository-unavailable", "repository-config-absent",
            "external-state-root-config-absent",
        }:
            raise ConfigError("repository activation configuration could not be resolved")
        return "unconfigured"
    machines = [item["machine"].casefold() for item in effective["indexers"]]
    if not machines:
        return "unconfigured"
    return "host" if machine.strip().casefold() in machines else "client"


def main() -> int:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--config")
    source.add_argument("--data-b64")
    source.add_argument("--repo")
    parser.add_argument("--machine", required=True)
    args = parser.parse_args()
    try:
        role = resolve_repository(args.repo, args.machine) if args.repo else resolve(
            Path(args.config) if args.config else None, args.machine,
            data_b64=args.data_b64, strict=True,
        )
    except (ConfigError, OSError, ValueError):
        print("agent-index: activation configuration could not be resolved", file=sys.stderr)
        return 1
    print(role)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
