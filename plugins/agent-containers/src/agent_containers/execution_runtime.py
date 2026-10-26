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
    root = Path(os.path.abspath(root.expanduser()))
    python = Path(os.path.abspath(sys.executable))
    slot = python.parent.parent.resolve()
    package = Path(os.path.abspath(__file__)).parent
    for name in (
        "__init__.py", Path(__file__).name, "_runtime_profile.py", "_versioned_runtime.py",
        "versioned_runtime_leases.py", "versioned_runtime_construction.py",
        "versioned_runtime_publication.py", "versioned_runtime_source.py",
    ):
        resource = package / name
        if not resource.is_file() or not resource.resolve().is_relative_to(slot):
            raise RuntimeError("Containers execution custody requires slot-owned resources")
    from . import _runtime_profile as profiles

    selection = profiles._read_execution(root, python)
    if selection.runtime_version != version or selection.payload_hash != payload_hash:
        raise RuntimeError("Executing Containers runtime differs from the selected runtime")
    with profiles.retain_execution(root, selection, timeout=timeout) as (_, descriptor):
        yield descriptor
