"""Atomic publication of the existing private Session Host authority record."""

import json
import os
from pathlib import Path
import tempfile


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
        os.replace(temp, path)
        return True
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
