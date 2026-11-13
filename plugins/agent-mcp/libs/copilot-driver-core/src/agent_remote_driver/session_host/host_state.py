"""Atomic publication of the existing private Session Host authority record."""

import json
import os
from pathlib import Path
import sys
import tempfile
import time


def write_host_state(path: Path, payload: dict) -> bool:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "nt":
        path.parent.chmod(0o700)
        stat = path.parent.stat()
        if stat.st_uid != os.geteuid() or stat.st_mode & 0o777 != 0o700:
            raise PermissionError(
                f"unsafe Session Host catalogue directory: {path.parent}"
            )
    fd, temp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        if hasattr(os, "fchmod"):
            try:
                os.fchmod(fd, 0o600)
            except OSError:
                pass
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        for attempt in range(20):
            try:
                os.replace(temp, path)
                break
            except OSError as error:
                if (
                    sys.platform != "win32" or getattr(error, "winerror", None) not in (5, 32, 33)
                    or attempt == 19
                ):
                    raise
                time.sleep(.01)
        return True
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
