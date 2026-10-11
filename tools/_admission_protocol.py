"""Environment-scoped exclusion shared by repository test runners."""

from __future__ import annotations

import hashlib
import math
import os
import stat
import sys
import time
from pathlib import Path

LEASE_LIB = Path(__file__).resolve().parents[1] / "libs" / "single-instance-lease" / "src"
sys.path.insert(0, str(LEASE_LIB))
from single_instance_lease import AlreadyRunningError, SingleInstance  # noqa: E402

ADMISSION_SERVICE = "copilot-extensions-test-runner"


def admission_dir(environment_root: Path) -> Path:
    """Key by directory filesystem identity; callers preserve that directory."""
    metadata = environment_root.stat()
    if not stat.S_ISDIR(metadata.st_mode) or not metadata.st_ino:
        raise ValueError("test environment requires a directory with filesystem identity")
    identity = f"{metadata.st_dev}:{metadata.st_ino}"
    key = hashlib.sha256(identity.encode("ascii")).hexdigest()
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "copilot-extensions" / "test-runner" / "environments" / key


def acquire(wait_seconds: float, environment_root: Path) -> SingleInstance:
    """Exclude mutation and use of one environment for a bounded wait."""
    if not math.isfinite(wait_seconds) or wait_seconds < 0:
        raise ValueError("admission wait must be a non-negative, finite number")
    lease = SingleInstance(admission_dir(environment_root), service=ADMISSION_SERVICE)
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            lease.acquire()
            return lease
        except AlreadyRunningError:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            time.sleep(min(0.25, remaining))
