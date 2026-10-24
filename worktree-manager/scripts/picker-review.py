#!/usr/bin/env python3
"""Run only this checkout's Manager candidate; never install or select it."""

from pathlib import Path
import hashlib
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from worktree_manager.picker_review_cli import main  # noqa: E402
from worktree_manager._shutdown_exit import run_and_exit  # noqa: E402

if __name__ == "__main__":
    args = sys.argv[1:]
    if not any(value == "--candidate" or value.startswith("--candidate=") for value in args):
        source = Path(__file__).resolve().parents[1] / "src" / "worktree_manager"
        digest = hashlib.sha256()
        for path in sorted(source.rglob("*.py")):
            digest.update(path.relative_to(source).as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
        args += ["--candidate", "source-" + digest.hexdigest()[:12]]
    run_and_exit(lambda: main(args))
