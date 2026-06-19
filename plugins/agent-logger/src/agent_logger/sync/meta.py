"""Sync metadata sidecar.

Each push drops a ``sync-meta.json`` at the machine root so a consumer can
see which machine last wrote, when, and via which transport. Ported from the
facility engine's ``write_sync_meta`` (local variant).
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

SYNC_VERSION = "1.0.0"


def write_sync_meta(
    dest: Path,
    machine: str,
    transport: str,
    status: str,
    session_count: int = 0,
) -> None:
    """Atomically write ``sync-meta.json`` into *dest* (best-effort)."""
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    meta = json.dumps(
        {
            "machine_id": machine,
            "last_sync_utc": now_utc,
            "sync_version": SYNC_VERSION,
            "transport": transport,
            "status": status,
            "session_count": session_count,
        },
        indent=2,
    )
    meta_file = dest / "sync-meta.json"
    tmp = meta_file.with_suffix(".json.tmp")
    try:
        dest.mkdir(parents=True, exist_ok=True)
        tmp.write_text(meta, encoding="utf-8")
        shutil.move(str(tmp), str(meta_file))
    except OSError:
        pass
