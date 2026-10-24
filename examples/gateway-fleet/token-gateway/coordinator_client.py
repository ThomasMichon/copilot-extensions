"""Experimental reference implementation; see the parent README."""


from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path

RUN_DIR = Path(os.environ.get("HGF_RUN_DIR", str(Path.home() / "harness-gateway-fleet" / "run")))
SOCKET_PATH = RUN_DIR / "coordinator.sock"


def _call(request: dict) -> dict:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(10)
        sock.connect(str(SOCKET_PATH))
        sock.sendall((json.dumps(request) + "\n").encode("utf-8"))
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
        return json.loads(buf.decode("utf-8"))


def main() -> int:
    if len(sys.argv) < 2:
        print(json.dumps({"ok": False, "error": "usage: status|ingest"}))
        return 2
    action = sys.argv[1]
    if action == "status":
        response = _call({"cmd": "status"})
    elif action == "ingest":
        payload = json.loads(sys.stdin.read())
        response = _call(
            {
                "cmd": "ingest",
                "token": payload["token"],
                "expires_epoch": payload["expires_epoch"],
            }
        )
    else:
        response = {"ok": False, "error": f"unknown action {action!r}"}
    print(json.dumps(response))
    return 0 if response.get("ok", True) is not False else 1


if __name__ == "__main__":
    raise SystemExit(main())
