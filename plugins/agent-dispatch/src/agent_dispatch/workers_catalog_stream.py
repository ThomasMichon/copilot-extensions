"""Progressive, read-only Workers snapshots owned by the catalog provider."""

from __future__ import annotations

import json


def stream_catalog() -> int:
    from .workers_catalog import builtin_template_rows, catalog

    def emit(kind, **fields):
        print(json.dumps({"type": kind, **fields}), flush=True)

    emit("begin")
    emit("summary", summary={
        "banner_text": "Loading configured workers and source declarations; available templates are shown first.",
        "banner_level": "info",
    })
    for row in builtin_template_rows():
        emit("row", entry=row)
    result = catalog()
    for row in result["entries"]:
        emit("row", entry=row)
    emit("summary", summary=result["summary"])
    emit("done")
    return 0
