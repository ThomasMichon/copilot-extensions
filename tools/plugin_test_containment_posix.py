"""Non-/proc POSIX process-group observation adapters."""
from __future__ import annotations

import shutil
import subprocess

if __package__:
    from .plugin_test_containment_darwin import GroupObservationChanged as GroupObservationChanged
else:
    from plugin_test_containment_darwin import GroupObservationChanged as GroupObservationChanged


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


def _darwin_group_usage(pgid: int, *, live_only: bool = False) -> tuple[int, int]:
    if __package__:
        from .plugin_test_containment_darwin import group_usage
    else:
        from plugin_test_containment_darwin import group_usage
    return group_usage(pgid, live_only=live_only)
