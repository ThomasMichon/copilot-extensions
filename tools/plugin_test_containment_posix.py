"""Non-/proc POSIX process-group observation adapters."""
from __future__ import annotations

import errno
import shutil
import subprocess
import time

if __package__:
    from .plugin_test_containment_darwin import GroupObservationChanged as GroupObservationChanged
    from .plugin_test_containment_darwin import _ObservationDeadline
else:
    from plugin_test_containment_darwin import GroupObservationChanged as GroupObservationChanged
    from plugin_test_containment_darwin import _ObservationDeadline


def _ps_group_usage(pgid: int, *, live_only: bool = False) -> tuple[int, int] | None:
    ps = shutil.which("ps")
    if not ps:
        return None
    result = subprocess.run(
        [ps, "-eo", "pgid=,rss=,stat="],
        capture_output=True,
        text=True,
        check=False,
        timeout=2,
    )
    if result.returncode != 0:
        return None
    count = 0
    rss_kib = 0
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) != 3:
            if live_only and fields:
                raise ValueError("Cannot parse POSIX process-group membership")
            continue
        if live_only and fields[2].startswith("Z"):
            continue
        try:
            row_pgid, row_rss = map(int, fields[:2])
        except ValueError:
            if live_only:
                raise
            continue
        if row_pgid == pgid:
            count += 1
            rss_kib += row_rss
    return count, rss_kib * 1024


def _darwin_group_usage(
    pgid: int, *, live_only: bool = False, deadline: float | None = None,
) -> tuple[int, int]:
    if __package__:
        from .plugin_test_containment_darwin import group_usage
    else:
        from plugin_test_containment_darwin import group_usage
    if deadline is None:
        return group_usage(pgid, live_only=live_only)
    return group_usage(pgid, live_only=live_only, deadline=deadline)


def _darwin_poll_group_usage(pgid: int, *, deadline: float) -> tuple[int, int]:
    """Finish a fresh complete sample inside this existing poll cycle or fail."""
    last_change: GroupObservationChanged | None = None
    while True:
        if time.monotonic() >= deadline:
            if last_change is not None:
                raise last_change
            raise _ObservationDeadline(errno.ETIMEDOUT, "Darwin sampling cycle expired before census")
        try:
            usage = _darwin_group_usage(pgid, deadline=deadline)
        except GroupObservationChanged as change:
            last_change = change
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            time.sleep(min(0.05, remaining))
            continue
        except _ObservationDeadline as expired:
            if last_change is not None:
                raise last_change from expired
            raise
        if (not isinstance(usage, tuple) or len(usage) != 2
                or any(type(value) is not int or value < 0 for value in usage)):
            raise ValueError("Darwin polling requires complete non-negative count/RSS counters")
        if time.monotonic() >= deadline:
            raise _ObservationDeadline(
                errno.ETIMEDOUT, "Darwin census completed outside its cycle",
            ) from last_change
        return usage
