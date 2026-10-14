"""Decode all objects emitted by ``gh api --paginate`` for check-runs."""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class CheckRunPages:
    """Decoded observations survive an incomplete or failed page stream."""

    runs: tuple[object, ...] = ()
    complete: bool = False


def check_run_evidence_from_pages(raw: str) -> CheckRunPages:
    """Retain decoded runs separately from whole-stream completeness."""
    decoder = json.JSONDecoder()
    remaining = raw.strip()
    runs: list[object] = []
    totals: set[int] = set()
    pages = 0
    valid = True
    while remaining:
        try:
            page, end = decoder.raw_decode(remaining)
        except json.JSONDecodeError:
            return CheckRunPages(tuple(runs), False)
        remaining = remaining[end:].strip()
        if not isinstance(page, dict) or not isinstance(page.get("check_runs"), list):
            valid = False
            continue
        batch = page["check_runs"]
        runs.extend(batch)
        pages += 1
        count = page.get("total_count", len(batch))
        if type(count) is not int or count < 0:
            valid = False
            continue
        totals.add(count)
    complete = bool(pages and valid and len(totals) == 1 and len(runs) == max(totals))
    return CheckRunPages(tuple(runs), complete)


def check_runs_from_pages(raw: str) -> list | None:
    """Compatibility reader: only a complete stream supplies success evidence."""
    evidence = check_run_evidence_from_pages(raw)
    return list(evidence.runs) if evidence.complete else None
