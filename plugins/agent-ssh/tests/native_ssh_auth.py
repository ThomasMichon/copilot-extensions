"""Authenticate with native SSH without opening a shell, command or subsystem."""

from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
import time
import uuid

from agent_procutil import no_window_kwargs


@dataclass(frozen=True)
class Authentication:
    authenticated: bool
    native_exit: int
    diagnostic: str


def authenticate(client: list[str], directory: Path) -> Authentication:
    argv = [*client, "-v", "-N", "-T", "127.0.0.1"]
    log_path = directory / ("ssh-auth-" + uuid.uuid4().hex + ".log")
    authenticated = False
    with log_path.open("wb") as log:
        child = subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log,
            **no_window_kwargs(),
        )
        try:
            deadline = time.monotonic() + 10
            while True:
                diagnostic = log_path.read_text(encoding="utf-8", errors="replace")
                assert len(diagnostic) <= 65536, "Native authentication diagnostic exceeded its bound"
                authenticated = bool(re.search(
                    r'^Authenticated to .+ using "publickey"\.\r?$', diagnostic, re.MULTILINE,
                ))
                if authenticated or child.poll() is not None:
                    break
                assert time.monotonic() < deadline, "Native SSH authentication did not settle: " + diagnostic
                time.sleep(0.05)
        finally:
            if child.poll() is None:
                child.terminate()
            code = child.wait(timeout=5)
    return Authentication(authenticated, code, log_path.read_text(encoding="utf-8", errors="replace"))
