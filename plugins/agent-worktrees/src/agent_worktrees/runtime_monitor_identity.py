"""Resolve the serving monitor's retained runtime without trusting a stale lock."""

from __future__ import annotations

from pathlib import Path

from . import locks, status_monitor_cutover as cutover


def monitor_slot(root: Path, identity: tuple[int, str]) -> str | None:
    record = locks.read_lock(root / "status-monitor.lock")
    if locks.lock_is_live(record) and record.get("pid") == identity[0]:
        if (
            locks.process_start_time(identity[0]) != identity[1]
            or not isinstance(record.get("prefix"), str)
            or not record["prefix"]
        ):
            raise ValueError("Serving monitor runtime identity changed before retention")
        prefix = Path(record["prefix"]).resolve(strict=True)
        return prefix.name if prefix.parent == (root / "versions").resolve() else None
    else:
        routed = cutover.routed_monitor_identity(root)
        if (
            routed is None or routed[:2] != identity or not routed[2]
            or Path(routed[2]).name != routed[2]
        ):
            raise ValueError("Routed monitor runtime identity cannot be retained")
        prefix = (root / "versions" / routed[2]).resolve(strict=True)
    if prefix.parent == (root / "versions").resolve():
        return prefix.name
    raise ValueError("Serving monitor runtime is outside the retained runtime root")
