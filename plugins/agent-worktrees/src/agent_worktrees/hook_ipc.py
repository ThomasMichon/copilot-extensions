"""Resident status-monitor IPC for hot-path Copilot hook decisions."""

from __future__ import annotations

import json
import os
import secrets
import socketserver
import threading
import time
from collections.abc import Callable

Decision = Callable[[str, dict, float], dict]
_READ_TIMEOUT_S = 1.0


class HookUnavailable(Exception):
    """The resident cannot decide before the client's bounded deadline.

    ``reason``, when set, distinguishes a structured admission-closed
    rejection (``"superseded"`` -- see ``HookIpcServer.close_admission``)
    from an ordinary deadline miss (``None``, unchanged wire shape).
    """

    def __init__(self, reason: str | None = None):
        super().__init__(reason or "")
        self.reason = reason


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = False
    daemon_threads = True

    def __init__(self, address, handler, *, token: str, decide: Decision):
        self.token = token
        self.decide = decide
        self.owner = None
        # Set only by HookIpcServer.close_admission() -- see its docstring.
        # Distinct from actually closing the socket: a superseded-but-not-
        # yet-exited daemon keeps listening and accepting connections, but
        # answers every new request with a structured rejection instead of
        # running `decide`.
        self.admission_closed = False
        self.admission_closed_reason: str | None = None
        super().__init__(address, handler)

    def process_request(self, request, client_address) -> None:
        owner = self.owner
        if owner is not None:
            owner._on_request_accepted()
        try:
            super().process_request(request, client_address)
        except BaseException:
            if owner is not None:
                owner._on_request_finished()
            raise

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            owner = self.owner
            if owner is not None:
                owner._on_request_finished()


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        try:
            self.request.settimeout(_READ_TIMEOUT_S)
            raw = self.rfile.readline(2 * 1024 * 1024)
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
            if self.server.admission_closed:
                raise HookUnavailable(self.server.admission_closed_reason)
            result = self.server.decide(kind, payload, deadline)
            if not isinstance(result, dict):
                result = {}
            if os.environ.get("PYTEST_CURRENT_TEST"):
                try:
                    delay_s = float(payload.get("__test_delay_s") or 0.0)
                except (TypeError, ValueError):
                    delay_s = 0.0
                if delay_s > 0:
                    time.sleep(delay_s)
            response = {
                "version": 1,
                "capabilities": ["session-lifecycle-v1"],
                "result": result,
            }
            self.wfile.write(
                json.dumps(response, separators=(",", ":")).encode("utf-8")
                + b"\n"
            )
        except HookUnavailable as exc:
            try:
                response = {"version": 1, "fallback": True}
                if exc.reason:
                    response["reason"] = exc.reason
                self.wfile.write(
                    json.dumps(response, separators=(",", ":")).encode("utf-8") + b"\n"
                )
            except OSError:
                return
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
        self.server.owner = self
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            name="agent-worktrees-hook-ipc",
            daemon=True,
        )
        self._active_handlers = 0
        self._active_handlers_lock = threading.Lock()

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

    def close_admission(self, reason: str = "superseded") -> None:
        """Stop admitting genuinely new hook decisions while leaving the
        listening socket open -- see
        ``work_coalescing_singleton.server.CoalescingServer.close_admission``'s
        docstring for the full single-shot-caller admission-discipline
        rationale this mirrors. A request already inside ``decide()`` before
        this call is unaffected; only a new connection reaching this point
        afterward gets the structured ``reason`` rejection. Actual socket
        teardown remains :meth:`close`'s job.
        """
        self.server.admission_closed = True
        self.server.admission_closed_reason = reason

    def open_admission(self) -> None:
        """Reverse of :meth:`close_admission` for an already-live, not-yet-
        ``close()``-d server -- see
        ``work_coalescing_singleton.server.CoalescingServer.open_admission``.
        """
        self.server.admission_closed = False
        self.server.admission_closed_reason = None

    def _on_request_accepted(self) -> None:
        with self._active_handlers_lock:
            self._active_handlers += 1

    def _on_request_finished(self) -> None:
        with self._active_handlers_lock:
            self._active_handlers -= 1

    def active_handler_count(self) -> int:
        with self._active_handlers_lock:
            return self._active_handlers
