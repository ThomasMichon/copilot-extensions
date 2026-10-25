"""Internal custody for the immutable runtime executing this provider process."""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import sys
from typing import Iterator


@contextmanager
def retain_current_execution(
    root: Path, *, version: str, payload_hash: str, timeout: float = 60.0,
) -> Iterator[int]:
    """Recheck this process's exact slot and borrow its canonical retention fd.

    This is not provider/guest admission or a launch grant. The caller must keep
    the context alive and must not close its borrowed descriptor. POSIX children
    need deliberate inheritance; Windows custody remains process-owned.
    """
    from . import _runtime_profile as profiles

    root = Path(os.path.abspath(root.expanduser()))
    python = Path(os.path.abspath(sys.executable))
    selection = profiles._read_execution(root, python)
    if selection.runtime_version != version or selection.payload_hash != payload_hash:
        raise RuntimeError("Executing Containers runtime differs from the selected runtime")
    if not Path(os.path.abspath(__file__)).is_relative_to(python.parent.parent):
        raise RuntimeError("Containers execution custody requires a slot-owned package")
    with profiles.retain_execution(root, selection, timeout=timeout) as (_, descriptor):
        yield descriptor
