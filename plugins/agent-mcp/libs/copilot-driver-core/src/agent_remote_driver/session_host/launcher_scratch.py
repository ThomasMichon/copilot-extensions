"""Frontend-only scratch custody and positively observed Host creation."""

from __future__ import annotations

import contextlib
import os
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
    phase = {
        "spawn_attempted": False, "creation_refused": False, "cleanup_safe": False,
        "disposable": fallback is not None and acquisition_launch is None,
    }
    try:
        yield sd, phase
    except BaseException:
        # Unknown post-spawn outcomes and custody owners keep their evidence.
        if fallback is not None and acquisition_launch is None and (
            not phase["spawn_attempted"] or phase["creation_refused"] or phase["cleanup_safe"]
        ):
            remove_scratch(fallback)
        raise


def observe_exit(phase: dict[str, bool], process) -> None:
    """Settle an exited Host's exact registry record before removing its root."""
    phase["cleanup_safe"] = False
    receipt = getattr(process, "instance_receipt", None)
    if phase["disposable"] and receipt is not None:
        from agent_procutil.daemon_instances import cleanup_instance

        phase["cleanup_safe"] = cleanup_instance(receipt, terminate=False).status in (
            "settled", "missing",
        )
