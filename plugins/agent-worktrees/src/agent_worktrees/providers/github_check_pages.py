"""Decode all objects emitted by ``gh api --paginate`` for check-runs."""

from __future__ import annotations

import json


def check_runs_from_pages(raw: str) -> list | None:
    decoder = json.JSONDecoder()
    remaining = raw.strip()
    runs: list = []
    total = 0
    pages = 0
    while remaining:
        try:
            page, end = decoder.raw_decode(remaining)
        except json.JSONDecodeError:
            return None
        if not isinstance(page, dict) or not isinstance(page.get("check_runs"), list):
            return None
        batch = page["check_runs"]
        count = page.get("total_count", len(batch))
        if not isinstance(count, int) or count < 0:
            return None
        total = max(total, count)
        runs.extend(batch)
        pages += 1
        remaining = remaining[end:].strip()
    return runs if pages and len(runs) >= total else None
