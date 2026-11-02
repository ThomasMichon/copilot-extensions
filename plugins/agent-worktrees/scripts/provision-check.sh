#!/usr/bin/env bash
# Report-only compatibility contract: provision-status-v1
# Report retained failures without resolving, previewing or constructing runtimes.
set -uo pipefail
status="$HOME/.agent-worktrees/logs/provision-status.json"
if [[ ! -e "$status" && ! -L "$status" ]]; then exit 0; fi
python="$(command -v python3 || command -v python || true)"
if [[ -z "$python" ]]; then
    printf '[agent-worktrees] Could not read provisioning status: %s (Python unavailable)\n' "$status" >&2
    exit 0
fi
if ! PYTHONPATH="" "$python" -I -B - "$status" "$PWD" <<'PY'
import json
import os
from pathlib import Path
import stat
import sys

status = Path(sys.argv[1])
try:
    named = status.lstat()
    if not stat.S_ISREG(named.st_mode):
        raise ValueError("status is not an ordinary file")
    if os.name != "posix" or not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_NONBLOCK"):
        raise ValueError("native nonblocking file access is unavailable")
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(status, flags)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_size > 65536:
            raise ValueError("status is not an ordinary bounded file")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(65537)
    finally:
        os.close(descriptor)
    if len(raw) > 65536:
        raise ValueError("status exceeds the report budget")
    previous = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(previous, dict):
        raise ValueError("status is not an object")
    repo = previous.get("repo")
    if previous.get("ok") is not False or not isinstance(repo, str) or not repo:
        raise SystemExit(0)
    if os.path.realpath(repo) != os.path.realpath(sys.argv[2]):
        raise SystemExit(0)
    failures = previous.get("failed")
    names = [
        item["service"][:200] for item in failures
        if isinstance(item, dict) and isinstance(item.get("service"), str) and item["service"]
    ][:8] if isinstance(failures, list) else []
    reason = previous.get("reason")
    failed = ", ".join(names) or (reason[:512] if isinstance(reason, str) and reason else "unknown failure")
    print(
        f"[agent-worktrees] Previous background provisioning failed: {failed}. "
        f"Inspect {status.parent}/provision-*.log* and rerun reconcile-plugins.",
        file=sys.stderr,
    )
except (OSError, UnicodeError, ValueError, TypeError, RecursionError):
    print(f"[agent-worktrees] Could not read provisioning status: {status}", file=sys.stderr)
PY
then
    printf '[agent-worktrees] Could not read provisioning status: %s\n' "$status" >&2
fi
exit 0
