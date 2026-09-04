#!/usr/bin/env python3
"""Publish this plugin's attributed agent-dispatch registrar candidate."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def main() -> int:
    try:
        plugin_root = Path(__file__).resolve().parents[1]
        registrar = plugin_root / "references" / "agent-dispatch" / "registrar"
        if not registrar.is_dir():
            return 0
        target_dir = Path(
            os.environ.get("AGENT_DISPATCH_REGISTRAR_DROPINS_DIR")
            or Path.home() / ".agent-dispatch" / "registrar.d"
        ).expanduser()
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / "agent-index-copilot-extensions.json"
        payload = (
            json.dumps(
                {
                    "schema_version": 1,
                    "plugin": "agent-index@copilot-extensions",
                    "plugin_root": str(plugin_root),
                    "registrar": "references/agent-dispatch/registrar",
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        if target.is_file() and target.read_text(encoding="utf-8-sig") == payload:
            return 0
        handle, temporary = tempfile.mkstemp(
            dir=target_dir,
            prefix=f".{target.name}.",
            suffix=".tmp",
            text=True,
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            try:
                Path(temporary).unlink()
            except FileNotFoundError:
                pass
    except (OSError, TypeError, ValueError):
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
