"""Held-claim presentation from bounded current aggregates or legacy rows."""

from __future__ import annotations

_ASSET_CODES = {
    "pr": "PR",
    "worktree": "WT",
    "codespace": "CS",
    "container": "CTR",
    "ssh": "SSH",
    "workdir": "DIR",
}
_HELD_CLAIM_STATES = ("", "active", "at-rest")


def claim_details(payload: dict) -> list[dict]:
    """Explicit full-ledger inspection, never copied into a current row."""
    raw = payload.get("outbound") if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        raise TypeError("engine omitted the outbound claim ledger")
    result = []
    for claim in raw:
        if not isinstance(claim, dict) or any(
            claim.get(field) is not None and not isinstance(claim[field], str)
            for field in ("kind", "ref", "note", "state")
        ):
            raise ValueError("engine returned an invalid outbound claim")
        state = (claim.get("state") or "").strip()
        if state in _HELD_CLAIM_STATES:
            result.append({
                "kind": (claim.get("kind") or "").strip() or "resource",
                "ref": (claim.get("ref") or "").strip(),
                "note": (claim.get("note") or "").strip(),
                "state": state or "active",
            })
    return result


def asset_hints(row: dict) -> dict:
    kind_counts: dict[str, int] = {}
    details = []
    summary = row.get("claims_assets")
    if summary is not None:
        if not isinstance(summary, dict):
            raise ValueError("invalid current held-claim summary")
        counts = summary.get("by_kind")
        count = summary.get("held_count")
        available = summary.get("details_available")
        if (
            not isinstance(counts, dict) or len(counts) > 128
            or type(count) is not int or count < 0
            or type(available) is not bool or available != bool(count)
            or any(
                not isinstance(kind, str) or not kind or len(kind.encode("utf-8")) > 4096
                or type(value) is not int or value <= 0
                for kind, value in counts.items()
            )
            or sum(counts.values()) != count
        ):
            raise ValueError("invalid current held-claim summary")
        kind_counts = counts
    else:
        raw = row.get("resources")
        if isinstance(raw, list):
            for claim in raw:
                if not isinstance(claim, dict):
                    continue
                state = str(claim.get("state") or "").strip()
                if state not in _HELD_CLAIM_STATES:
                    continue
                kind = str(claim.get("kind") or "").strip() or "resource"
                kind_counts[kind] = kind_counts.get(kind, 0) + 1
                details.append({
                    "kind": kind,
                    "ref": str(claim.get("ref") or "").strip(),
                    "note": str(claim.get("note") or "").strip(),
                    "state": state or "active",
                })
    # Distinct raw kinds can share the same four-character display code.
    code_counts: dict[str, int] = {}
    for kind, count in kind_counts.items():
        code = _ASSET_CODES.get(kind, kind.upper()[:4] or "RES")
        code_counts[code] = code_counts.get(code, 0) + count
    ordered_codes = [
        code for code in _ASSET_CODES.values() if code in code_counts
    ] + sorted(code for code in code_counts if code not in _ASSET_CODES.values())
    hints = [
        code if code_counts[code] == 1 else f"{code}\u00d7{code_counts[code]}"
        for code in ordered_codes
    ]
    result = {"hints": hints[:4], "overflow": max(0, len(hints) - 4), "details": details}
    if summary is not None:
        result.update(held_count=summary["held_count"], details_available=available)
    return result
