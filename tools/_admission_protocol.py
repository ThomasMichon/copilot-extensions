"""Environment-scoped exclusion shared by repository test runners."""

from __future__ import annotations

import hashlib
import math
import os
import sys
import time
from pathlib import Path

LEASE_LIB = Path(__file__).resolve().parents[1] / "libs" / "single-instance-lease" / "src"
sys.path.insert(0, str(LEASE_LIB))
from single_instance_lease import AlreadyRunningError, SingleInstance  # noqa: E402

ADMISSION_SERVICE = "copilot-extensions-test-runner"


def admission_dir(environment_root: Path) -> Path:
    """Key by the concrete environment directory, including filesystem aliases."""
    identity = os.path.normcase(str(environment_root.resolve()))
    key = hashlib.sha256(os.fsencode(identity)).hexdigest()
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
