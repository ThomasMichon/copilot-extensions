"""Preserve occupied version slots when native passive retirement is refused."""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)


def reap_stranded_cutover_passive(root: Path, slot: Path, mux_daemon_cutover) -> str | None:
    """Run under the caller's install lease; leave the recovery record intact."""
    try:
        try:
            slot.lstat()
        except FileNotFoundError:
            return None
        from zdd import breadcrumb

        record = breadcrumb.read_breadcrumb(mux_daemon_cutover.routing_dir(root))
        result = mux_daemon_cutover._reap_abandoned_passive(root, record)
        if not isinstance(result, dict) or result.get("refused"):
            reason = "native passive custody refused; version slot retained"
        else:
            candidate = (record or {}).get("new_pid")
            if type(candidate) is not int or candidate <= 0:
                return None
            if candidate not in mux_daemon_cutover._iter_mux_daemon_pids():
                return None
            reason = "passive daemon exit unconfirmed; version slot retained"
    except Exception as exc:
        reason = f"native passive recovery unavailable ({type(exc).__name__}); version slot retained"
    log.error(reason)
    return reason
