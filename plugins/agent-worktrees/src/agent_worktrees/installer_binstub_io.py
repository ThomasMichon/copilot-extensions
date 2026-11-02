"""Content-preserving writes for installer-owned command binstubs."""

from __future__ import annotations

from pathlib import Path


def _write_binstub_if_changed(dst: Path, content: str) -> bool:
    """Write *content* to *dst* only if the on-disk content differs.

    Comparison is newline-normalized so that a stub written by a sibling
    generator (init.ps1 / install.ps1 with CRLF, init.sh with LF) is treated
    as unchanged when its logical content matches.

    This is critical on Windows: ``register``/``adopt``/``update`` run *through*
    the global ``agent-worktrees.cmd`` binstub, then call this function, which
    would otherwise rewrite that very file mid-execution. cmd.exe resumes
    reading a batch file by byte offset after a child process returns, so
    rewriting the running stub to a different length corrupts the read (the
    classic stray ``'b' is not recognized`` from ``exit /b`` plus a spurious
    re-run). Skipping the write when nothing changed avoids the corruption.

    Returns True if a write occurred, False if skipped.
    """
    def _norm(s: str) -> str:
        return s.replace("\r\n", "\n").replace("\r", "\n")

    if dst.exists():
        try:
            existing = dst.read_text(encoding="utf-8", errors="replace")
            if _norm(existing) == _norm(content):
                return False
        except OSError:
            pass
    # newline="" preserves the literal \r\n / \n already embedded in content
    dst.write_text(content, encoding="utf-8", newline="")
    return True
