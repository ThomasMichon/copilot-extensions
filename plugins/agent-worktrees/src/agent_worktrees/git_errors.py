"""Credential-safe error normalization for text and binary Git plumbing."""

from __future__ import annotations


def _redact_args(cmd: list[str]) -> list[str]:
    """Redact any injected credential header so tokens never reach logs/errors."""
    out: list[str] = []
    for arg in cmd:
        if arg.startswith("http.extraheader="):
            out.append("http.extraheader=<redacted>")
        else:
            out.append(arg)
    return out


class GitError(Exception):
    """A git command failed."""

    def __init__(self, cmd: list[str], returncode: int, stderr: str | bytes) -> None:
        self.cmd = _redact_args(cmd)
        self.returncode = returncode
        self.stderr = stderr.decode("utf-8", errors="replace") if isinstance(stderr, bytes) else stderr
        super().__init__(
            f"git {' '.join(self.cmd[1:])} failed (rc={returncode}): {self.stderr}"
        )
