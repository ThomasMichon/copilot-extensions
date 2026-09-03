"""Resident status-monitor IPC for hot-path Copilot hook decisions."""

from __future__ import annotations

import json
import secrets
import socketserver
import threading
import time
from collections.abc import Callable

Decision = Callable[[str, dict, float], dict]
_READ_TIMEOUT_S = 1.0


class HookUnavailable(Exception):
    """The resident cannot decide before the client's bounded deadline."""


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = False
    daemon_threads = True

    def __init__(self, address, handler, *, token: str, decide: Decision):
        self.token = token
        self.decide = decide
        super().__init__(address, handler)


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        try:
            self.request.settimeout(_READ_TIMEOUT_S)
            raw = self.rfile.readline(256 * 1024)
            request = json.loads(raw.decode("utf-8"))
            if (
                not isinstance(request, dict)
                or request.get("version") != 1
                or not secrets.compare_digest(
                    str(request.get("token") or ""), self.server.token
                )
            ):
                return
            kind = str(request.get("kind") or "")
            payload = request.get("payload")
            deadline = float(request.get("deadline") or 0)
            if not isinstance(payload, dict):
                payload = {}
            if deadline <= time.time():
                raise HookUnavailable
            result = self.server.decide(kind, payload, deadline)
            if deadline <= time.time():
                raise HookUnavailable
            if not isinstance(result, dict):
                result = {}
            response = {"version": 1, "result": result}
            self.wfile.write(
                json.dumps(response, separators=(",", ":")).encode("utf-8")
                + b"\n"
            )
        except HookUnavailable:
            self.wfile.write(b'{"version":1,"fallback":true}\n')
        except Exception:
            return


class HookIpcServer:
    """Dynamic-port, loopback-only server advertised through rendezvous files."""

    def __init__(self, decide: Decision):
        self.token = secrets.token_urlsafe(32)
        self.generation = secrets.token_hex(16)
        self.server = _Server(
            ("127.0.0.1", 0), _Handler, token=self.token, decide=decide
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            name="agent-worktrees-hook-ipc",
            daemon=True,
        )

    def start(self) -> None:
        self.thread.start()

    def rendezvous(self) -> dict:
        host, port = self.server.server_address
        return {
            "hook_transport": "tcp",
            "hook_endpoint": f"{host}:{port}",
            "hook_token": self.token,
            "hook_generation": self.generation,
        }

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)
