"""Frontend-only scratch custody and positively observed Host creation."""

from __future__ import annotations

import contextlib
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path


@contextlib.contextmanager
def launch_state(
    state_dir: str | None, session_id: str, acquisition_launch: object | None,
) -> Iterator[tuple[Path, dict[str, bool]]]:
    from agent_procutil.scratch import create_labeled_scratch, remove_scratch

    if state_dir:
        sd, fallback = Path(os.path.abspath(state_dir)), None
    else:
        sd = fallback = create_labeled_scratch(
            "agbridge-host", "agent Session Host state and authority record",
            owner_refs={"session_id": session_id} if session_id else None,
            retention="retained while its host authority may be read; removed on failed launch",
        )
    phase = {"spawn_attempted": False, "creation_refused": False, "exited": False}
    try:
        yield sd, phase
    except BaseException:
        # Unknown post-spawn outcomes and custody owners keep their evidence.
        if fallback is not None and acquisition_launch is None and (
            not phase["spawn_attempted"] or phase["creation_refused"] or phase["exited"]
        ):
            remove_scratch(fallback)
        raise


def tracked_creation(phase: dict[str, bool]):
    """Observe refusal at Popen itself, not at a potentially post-spawn wrapper."""
    def create_host(*args, **kwargs):
        phase["spawn_attempted"] = True
        try:
            return subprocess.Popen(*args, **kwargs)
        except OSError:
            phase["creation_refused"] = True
            raise

    return create_host
