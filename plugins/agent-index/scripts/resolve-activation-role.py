#!/usr/bin/env python3
"""Resolve a repository's explicit agent-index activation role."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


def _fallback_machines(text: str) -> list[str]:
    match = re.search(
        r"(?ms)^indexers?\s*:.*?(?=^(?!-)\S|\Z)",
        text,
    )
    if match is None:
        return []
    return [
        item.strip().lower()
        for item in re.findall(
            r"""machine\s*:\s*["']?([^,}\]\r\n#"'']+)""",
            match.group(0),
        )
        if item.strip()
    ]


def configured_machines(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return []
    try:
        import yaml
    except ImportError:
        return _fallback_machines(text)
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return []
    if not isinstance(data, dict):
        return []
    raw = data.get("indexers")
    if not isinstance(raw, list):
        singular = data.get("indexer")
        raw = [singular] if isinstance(singular, dict) else []
    return [
        str(item["machine"]).strip().lower()
        for item in raw
        if isinstance(item, dict) and str(item.get("machine") or "").strip()
    ]


def resolve(path: Path, machine: str) -> str:
    machines = configured_machines(path)
    if not machines:
        return "unconfigured"
    return "host" if machine.strip().lower() in machines else "client"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--machine", required=True)
    args = parser.parse_args()
    print(resolve(Path(args.config), args.machine))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
