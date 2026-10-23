"""Per-write records in the existing local form-draft directory."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import time
import uuid


def write_draft(base: Path, binding, task_id, values, *, pending: bool) -> Path:
    path = base if binding is None else base.with_stem(f"{base.stem}-{uuid.uuid4().hex}")
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = stream.name
            json.dump({
                "task_id": task_id, "binding": binding, "values": values,
                "pending": pending, "written_at_ns": time.time_ns(),
            }, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        return path
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def read_drafts(base: Path, binding) -> list[tuple[Path, dict]]:
    records = []
    paths = [base] if binding is None else base.parent.glob(f"{base.stem}-*.json")
    for path in paths:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if binding is not None and isinstance(record, dict) and not {"pending", "written_at_ns"} <= record.keys():
                continue
            if (isinstance(record, dict) and isinstance(record.get("values"), dict)
                    and record.get("binding") == binding
                    and type(record.get("written_at_ns", 0)) is int
                    and type(record.get("pending", False)) is bool):
                records.append((path, record))
        except (OSError, ValueError):
            continue
    return sorted(records, key=lambda item: (item[1].get("written_at_ns", 0), item[0].name), reverse=True)
