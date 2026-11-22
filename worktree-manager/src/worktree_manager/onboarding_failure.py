"""Report an onboarding boundary without exposing command lines or private inputs."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Iterator, Literal

from .runtime_source_repository import _ordinary

Phase = Literal["manager-handoff", "binding", "composition", "preference", "marketplace",
                "roster", "preparation", "planning", "installation"]


@contextmanager
def phase(root: Path, name: Phase) -> Iterator[None]:
    try:
        yield
    except Exception as error:
        if isinstance(error, subprocess.CalledProcessError):
            code, reason = error.returncode, "child-exit"
        elif isinstance(error, subprocess.TimeoutExpired):
            code, reason = None, "timeout"
        elif isinstance(error, OSError):
            code, reason = error.errno, "os-error"
        else:
            code, reason = None, "operation-refused"
        record = {"schema": "copilot-extensions.onboarding-failure/v1", "phase": name,
                  "reason": reason, "code": code}
        message = json.dumps(record, sort_keys=True)
        print(message, file=sys.stderr, flush=True)
        # Retention is best effort; never replace the original failure.
        try:
            if root.parent == Path(tempfile.gettempdir()).resolve() and root.name.startswith("ce-bootstrap-trial-"):
                marker = root / ".bootstrap-trial.json"
                _ordinary(marker)
                if marker.is_file() and json.loads(marker.read_bytes()) == str(root):
                    fd, _ = tempfile.mkstemp(prefix=".onboarding-failure-", suffix=".json", dir=root)
                    with os.fdopen(fd, "w", encoding="utf-8") as stream:
                        stream.write(message + "\n")
        except (OSError, ValueError):
            pass
        raise
