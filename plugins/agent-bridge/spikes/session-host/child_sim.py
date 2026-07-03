"""Synthetic ACP-like child for the Session-Host spike.

Stands in for ``copilot --acp --stdio``. The Session Host does **no** ACP
semantic parsing (only newline frame boundaries + liveness), so a stand-in that
emits newline-delimited JSON frames is a faithful test surface for the
transport -- and it lets the spike control timing precisely so the mid-turn
window (front detached while frames keep arriving) is deterministic.

Protocol: reads one prompt line from stdin, then streams ``--frames`` JSON
frames at ``--interval`` seconds each, then a final ``turn_complete`` frame.
Crucially it keeps streaming regardless of whether any front is attached -- the
host owns its stdout pipe, so child progress is decoupled from front presence.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", type=int, default=12)
    ap.add_argument("--interval", type=float, default=0.2)
    args = ap.parse_args()

    out = sys.stdout.buffer
    pid = os.getpid()

    # Announce readiness (frame 1 is emitted before any prompt so an attach
    # handshake always has something; models an ACP "initialize" ack).
    out.write((json.dumps({"type": "ready", "pid": pid}) + "\n").encode())
    out.flush()

    # Block for the prompt (host relays the front's WRITE here).
    line = sys.stdin.readline()
    if not line:
        return 0
    try:
        prompt = json.loads(line).get("prompt", "")
    except Exception:
        prompt = line.strip()

    for i in range(1, args.frames + 1):
        frame = {
            "type": "session/update",
            "turn": 1,
            "chunk": i,
            "of": args.frames,
            "prompt": prompt,
            "text": f"token-{i}",
        }
        out.write((json.dumps(frame) + "\n").encode())
        out.flush()
        time.sleep(args.interval)

    out.write((json.dumps({"type": "turn_complete", "turn": 1, "chunks": args.frames}) + "\n").encode())
    out.flush()

    # Stay alive briefly so the host's liveness reflects a running child even
    # after the turn completes (the real copilot child persists across turns).
    time.sleep(2.0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
