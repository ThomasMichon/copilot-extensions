#!/usr/bin/env python3
"""Resolve one clean-room drive from its create-owned session-id file."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


MODEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _rows(path: Path) -> list[dict[str, object]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(value, dict):
        value = value.get("sessions", [])
    if not isinstance(value, list):
        raise ValueError(f"session snapshot is not a list: {path}")
    return [row for row in value if isinstance(row, dict)]


def resolve(
    session_id_path: Path,
    sessions_path: Path,
    agent: str,
) -> dict[str, object]:
    try:
        lines = session_id_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    if (
        len(lines) != 1
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{5,127}", lines[0]) is None
    ):
        return {
            "session_id": "",
            "model": "",
            "reason": "missing-or-invalid-session-id-file",
            "candidate_count": 0,
        }
    session_id = lines[0]
    candidates = [
        row
        for row in _rows(sessions_path)
        if (
            row.get("agent_name") == agent
            and str(row.get("session_id") or "") == session_id
        )
    ]
    if len(candidates) != 1:
        return {
            "session_id": session_id,
            "model": "",
            "reason": (
                "session-not-found"
                if not candidates
                else "duplicate-session-records"
            ),
            "candidate_count": len(candidates),
        }
    row = candidates[0]
    model = row.get("usage_model")
    if not isinstance(model, str) or MODEL_PATTERN.fullmatch(model) is None:
        return {
            "session_id": str(row["session_id"]),
            "model": "",
            "reason": "missing-or-invalid-model",
            "candidate_count": 1,
        }
    return {
        "session_id": str(row["session_id"]),
        "model": model,
        "reason": "resolved",
        "candidate_count": 1,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-id-file", type=Path, required=True)
    parser.add_argument("--sessions", type=Path, required=True)
    parser.add_argument("--agent", required=True)
    parser.add_argument(
        "--format", choices=("json", "tsv", "pipe"), default="json"
    )
    args = parser.parse_args()
    result = resolve(args.session_id_file, args.sessions, args.agent)
    if args.format in {"tsv", "pipe"}:
        separator = "\t" if args.format == "tsv" else "|"
        print(
            separator.join(
                (
                    str(result["session_id"]),
                    str(result["model"]),
                    str(result["reason"]),
                )
            )
        )
    else:
        print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
