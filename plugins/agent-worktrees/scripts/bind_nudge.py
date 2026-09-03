#!/usr/bin/env python3
"""Lightweight monitor-down fallback for the advisory bind reminder."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import time
from pathlib import Path

_COOLDOWN_S = 90
_HEAD_RE = re.compile(
    r"^[ \t]*head_session:[ \t]*([^\r\n]*)$", re.MULTILINE)
_WORKTREE_PATH_RE = re.compile(
    r"^[ \t]*worktree_path:[ \t]*([^\r\n]+)$", re.MULTILINE)
_SESSION_RE = re.compile(
    r"^[ \t]*-[ \t]+session_id:[ \t]*([^\r\n]+)$", re.MULTILINE)
_STATE_RE = re.compile(
    r"^[ \t]+state:[ \t]*([^\r\n]+)$", re.MULTILINE)
_CONCLUDED = frozenset({
    "concluded", "handed-off", "handed_off", "retired", "ended",
})


def _load_nudge_module():
    path = Path(__file__).resolve().with_name("nudge_status.py")
    spec = importlib.util.spec_from_file_location("_aw_nudge_lookup", path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _active_head(text: str) -> bool:
    head = _HEAD_RE.search(text)
    if head and head.group(1).strip().strip("'\""):
        return True
    sessions = list(_SESSION_RE.finditer(text))
    if not sessions:
        return False
    for index, session in enumerate(sessions):
        end = sessions[index + 1].start() if index + 1 < len(sessions) else len(text)
        state = _STATE_RE.search(text, session.end(), end)
        value = state.group(1).strip().strip("'\"").lower() if state else ""
        if value not in _CONCLUDED:
            return True
    return False


def decide(payload: dict, *, home: Path | None = None) -> str | None:
    home = Path(home) if home is not None else Path.home()
    lookup = _load_nudge_module()
    if lookup is None:
        return None
    cwd = str(payload.get("workingDirectory") or payload.get("cwd") or "")
    found = lookup._find_tracking_yaml(cwd, home)
    if not found:
        return None
    worktree_id, yaml_path = found
    try:
        text = yaml_path.read_text("utf-8", errors="replace")
    except OSError:
        return None
    if _active_head(text):
        return None
    stamp = yaml_path.parent / f"{worktree_id}.bind-nudge-at"
    now = time.time()
    try:
        if stamp.exists() and now - stamp.stat().st_mtime < _COOLDOWN_S:
            return None
    except OSError:
        pass
    try:
        stamp.write_text(str(now), encoding="utf-8")
    except OSError:
        pass
    path_match = _WORKTREE_PATH_RE.search(text)
    wdir = (
        path_match.group(1).strip().strip("'\"")
        if path_match else str(Path(cwd).resolve())
    )
    return (
        "[agent-worktrees] This worktree has no session bound on record, but "
        "you are working in it -- restore live tracking by running:\n"
        f"    agent-worktrees bind-session --worktree-dir={wdir}\n"
        "This is a one-time declaration; once bound, this reminder stops."
    )


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        text = decide(payload if isinstance(payload, dict) else {})
        if text:
            sys.stdout.write(json.dumps({"additionalContext": text}))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
