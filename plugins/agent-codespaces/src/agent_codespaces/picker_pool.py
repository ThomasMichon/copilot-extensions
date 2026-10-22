"""Canonical one-shot and subscribed pool snapshots, including authentication."""
from __future__ import annotations

import json
import subprocess
import sys
import time

from . import picker_auth, pool


def snapshot(args, *, annotate=None) -> dict:
    auth = picker_auth.probe()
    if auth["status"] != "ready":
        return picker_auth.blocked_payload(auth)
    try:
        members, budget = pool.build_pool(
            budget_cores=args.budget if args.budget is not None else pool.DEFAULT_BUDGET_CORES,
            stale_after=args.stale_after if args.stale_after is not None else pool.DEFAULT_STALE_AFTER,
        )
        if annotate is not None:
            annotate(members)
        payload = pool.picker_payload(members, budget)
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        auth = {
            **auth, "status": "error",
            "message": f"CodeSpaces inventory failed ({type(exc).__name__}); refresh to retry.",
        }
        return picker_auth.blocked_payload(auth)
    payload["summary"].update(picker_auth.summary(auth))
    return payload


def command(args, *, annotate=None) -> int:
    payload = snapshot(args, annotate=annotate)
    if not getattr(args, "stream", False):
        print(json.dumps(payload))
        return 0
    out = sys.__stdout__

    def emit(frame):
        out.write(json.dumps(frame) + "\n")
        out.flush()

    try:
        for frame in picker_auth.frames(payload):
            emit(frame)
        if not getattr(args, "subscribe", False):
            return 0
        while True:
            time.sleep(max(0.5, float(getattr(args, "interval", 5.0))))
            current = snapshot(args, annotate=annotate)
            deltas, removed = pool.diff_entries(payload["entries"], current["entries"])
            for entry in deltas:
                emit({"type": "delta", "entry": entry})
            for identity in removed:
                emit({"type": "removed", "id": identity})
            if current["summary"] != payload["summary"]:
                emit({"type": "summary", "summary": current["summary"]})
            payload = current
    except (BrokenPipeError, KeyboardInterrupt):
        return 0
