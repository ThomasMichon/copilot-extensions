"""Content-free command-emitter and coordinator diagnostics, separate from CLI stdout."""

from __future__ import annotations

import atexit
import hashlib
import json
import os
import re
import threading
import time
from typing import Any
from process_telemetry.buffer import BufferedSink
from .identity import canonicalize_remote


def _write(event: dict[str, Any]) -> None:
    os.write(2, (json.dumps(event, separators=(",", ":")) + "\n").encode("ascii"))


_writer = BufferedSink(_write)
_init_lock = threading.Lock()
_started = False


def _close() -> None:
    # Short-lived tick processes get a finite off-path opportunity to drain.
    deadline = time.monotonic() + 0.15
    while time.monotonic() < deadline:
        counts = _writer.stats()
        if counts["delivered"] + counts["sink_failed"] >= counts["accepted"]:
            break
        time.sleep(0.005)
    _writer.close()


def start() -> None:
    global _started
    with _init_lock:
        if not _started:
            _started = True
            try:
                _writer.start()
                atexit.register(_close)
            except (RuntimeError, OSError):
                _writer.close()


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="surrogatepass")).hexdigest()[:24]


def record(stage: str, origin: str, *, row: dict[str, Any] | None = None,
           task_id: object = None, count: int | None = None, outcome: str | None = None) -> None:
    event: dict[str, object] = {
        "event": stage, "ts": time.time(), "emitter_ref": _fingerprint(origin),
    }
    if count is not None:
        event["candidate_count"] = count
    if outcome is not None:
        event["outcome"] = outcome
    if row is not None:
        key = row.get("dedup_key")
        repo = row.get("repo")
        lane = canonicalize_remote(repo) if isinstance(repo, str) else None
        if lane and isinstance(key, str):
            event["key_ref"] = _fingerprint(json.dumps([lane, key], separators=(",", ":")))
        inline = row.get("payload_inline")
        if isinstance(inline, str) and len(inline) <= 8192:
            try:
                scope = json.loads(inline)
            except (ValueError, RecursionError):
                scope = None
            if isinstance(scope, dict) and scope.get("kind") == "pr-review":
                number = scope.get("pr_number")
                if type(number) is int and 0 < number < 2**63:
                    event["pr_number"] = number
                head = scope.get("head_sha")
                if isinstance(head, str) and re.fullmatch(r"[a-fA-F0-9]{40}(?:[a-fA-F0-9]{24})?", head):
                    event["observed_head"] = head
    if isinstance(task_id, str) and re.fullmatch("[a-f0-9]{32}", task_id):
        event["task_id"] = task_id
    _writer(event)
