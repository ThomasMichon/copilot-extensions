"""Bounded disposable legacy-v1 control peer, never a production resident."""
from __future__ import annotations

import json
import os
import socketserver
import sys
import threading
from pathlib import Path

from agent_worktrees import locks


def serve(root: Path, slot: str, mode: str):
    start = locks.process_start_time(os.getpid())
    stopping = threading.Event()
    closed = False
    token = "historical-native-fixture"

    def dispatch(action, payload):
        nonlocal closed
        if action == "health":
            return {"pid": os.getpid(), "process_start_time": start,
                    "runtime_slot": slot, "runtime_prefix": str(root / "versions" / slot / ".venv"),
                    "status": "draining" if closed else "ready", "published": True}
        if action == "drain":
            assert payload["force"] is False
            closed = True
            (root / (slot + "-drain-requested")).touch()
            return {"drained": mode != "busy" or (root / "release-work").exists()}
        if action == "shutdown":
            assert closed and (mode != "busy" or (root / "release-work").exists())
            (root / (slot + "-shutdown-requested")).touch()
            return {"shutdown": True}
        raise ValueError("unexpected historical fixture action")

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            raw = self.rfile.readline(262145)
            if len(raw) > 262144 or not raw.endswith(b"\n"):
                raise ValueError("legacy v1 fixture frame exceeds its bound")
            frame = json.loads(raw)
            # This archived decoder is deliberately independent of today's server.
            if (not isinstance(frame, dict) or set(frame) != {"version", "token", "action", "payload"}
                or type(frame["version"]) is not int or frame["version"] != 1
                or frame["token"] != token or not isinstance(frame["payload"], dict)):
                raise ValueError("legacy v1 fixture rejected the control frame")
            result = dispatch(frame["action"], frame["payload"])
            self.wfile.write(json.dumps({"result": result}).encode("utf-8") + b"\n")
            self.wfile.flush()
            if frame["action"] == "shutdown" and mode != "needs-force":
                stopping.set()

    class Server(socketserver.ThreadingTCPServer):
        daemon_threads = True
        allow_reuse_address = False

    server = Server(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    ready = root / (slot + "-ready.tmp")
    ready.write_text(json.dumps({"pid": os.getpid(), "start": start, "port": server.server_address[1]}), encoding="utf-8")
    ready.replace(root / (slot + "-ready.json"))
    try:
        if not stopping.wait(60):
            raise TimeoutError("historical native fixture lifetime expired")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


if __name__ == "__main__":
    serve(Path(sys.argv[1]), sys.argv[2], sys.argv[3])
