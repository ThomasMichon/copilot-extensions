"""Resident status-monitor IPC for hot-path Copilot hook decisions."""

from __future__ import annotations

import json
import math
import os
import secrets
import socket
import socketserver
import threading
import time
from collections.abc import Callable
from pathlib import Path

Decision = Callable[[str, dict, float], dict]
_READ_TIMEOUT_S = 1.0
_RECENT_HOOK_DEMAND_S = 120.0
READ_DEMAND_CAPABILITY = "read-hook-demand-v1"
NATIVE_MUTATION_CAPABILITY = "native-hook-mutation-v1"
NATIVE_BATCH_MUTATION_CAPABILITY = "native-hook-batch-mutation-v1"
SHELL_MUTATION_CAPABILITY = "shell-hook-mutation-v1"
WORKTREE_CHANGE_CAPABILITY = "worktree-change-v1"
WORKTREE_CHANGE_KIND = "worktreeChanged"
MIXED_BATCH_MUTATION_CAPABILITY = "mixed-hook-batch-mutation-v1"
_CAPABILITIES = [
    "session-lifecycle-v1", "session-start-source-v1", "session-end-source-v1",
]


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
            if kind == "readHookDemand":
                owner = self.server.owner
                if owner is not None and len(raw) <= 2048 and raw.endswith(b"\n"):
                    owner._on_read_demand(request)
                return
            if kind in {"nativeToolMutation", "nativeToolBatchMutation", "shellToolMutation", "mixedToolBatchMutation"}:
                owner = self.server.owner
                if owner is not None and len(raw) <= 65536 and raw.endswith(b"\n"):
                    owner._on_native_mutation(request)
                return
            if kind == WORKTREE_CHANGE_KIND:
                owner = self.server.owner
                if owner is not None and len(raw) <= 65536 and raw.endswith(b"\n"):
                    owner._on_worktree_changed(request)
                return
            payload = request.get("payload")
            deadline = float(request.get("deadline") or 0)
            payload_valid = isinstance(payload, dict)
            if not payload_valid:
                payload = {}
            if deadline <= time.time():
                raise HookUnavailable
            if self.server.admission_closed:
                raise HookUnavailable(self.server.admission_closed_reason)
            result = self.server.decide(kind, payload, deadline)
            result_valid = isinstance(result, dict)
            if not result_valid:
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
                "capabilities": self.server.owner.capabilities(),
                "result": result,
            }
            self.wfile.write(
                json.dumps(response, separators=(",", ":")).encode("utf-8")
                + b"\n"
            )
            if payload_valid and result_valid and deadline > time.time():
                owner = self.server.owner
                if owner is not None:
                    owner._on_hook_completed(kind)
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

    def __init__(
        self, decide: Decision, *, read_demand_admitted: Callable[[], bool] | None = None,
        read_demand_lock=None,
        native_mutation_decide: Callable[[str, dict, float], dict | None] | None = None,
        worktree_changed: Callable[[str | None], None] | None = None,
    ):
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
        self._clock = time.monotonic
        self._last_completed_hook: float | None = None
        self._read_demand_admitted = read_demand_admitted
        self._native_mutation_decide = native_mutation_decide
        self._worktree_changed = worktree_changed
        self._read_demand_lock = read_demand_lock if read_demand_lock is not None else threading.RLock()

    def start(self) -> None:
        self.thread.start()

    def rendezvous(self) -> dict:
        host, port = self.server.server_address
        return {
            "hook_transport": "tcp",
            "hook_endpoint": f"{host}:{port}",
            "hook_token": self.token,
            "hook_generation": self.generation,
            "hook_capabilities": self.capabilities(),
        }

    def capabilities(self) -> list[str]:
        return [
            *_CAPABILITIES,
            *([READ_DEMAND_CAPABILITY] if self._read_demand_admitted is not None else []),
            *([WORKTREE_CHANGE_CAPABILITY] if self._worktree_changed is not None else []),
            *(
                [
                    NATIVE_MUTATION_CAPABILITY, NATIVE_BATCH_MUTATION_CAPABILITY,
                    SHELL_MUTATION_CAPABILITY, MIXED_BATCH_MUTATION_CAPABILITY,
                ]
                if self._native_mutation_decide is not None else []
            ),
        ]

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
        with self._active_handlers_lock:
            self.server.admission_closed = True
            self.server.admission_closed_reason = reason

    def open_admission(self) -> None:
        """Reverse of :meth:`close_admission` for an already-live, not-yet-
        ``close()``-d server -- see
        ``work_coalescing_singleton.server.CoalescingServer.open_admission``.
        """
        with self._active_handlers_lock:
            self.server.admission_closed = False
            self.server.admission_closed_reason = None

    def _on_read_demand(self, request: dict) -> None:
        if (
            set(request) != {"version", "token", "kind", "hook_generation", "deadline"}
            or type(request.get("version")) is not int
            or request.get("hook_generation") != self.generation
            or type(request.get("deadline")) not in (int, float)
        ):
            return
        deadline = request["deadline"]
        if not math.isfinite(deadline) or not 0 < deadline - time.time() <= 1.0:
            return
        if not self._read_demand_lock.acquire(timeout=0.05):
            return
        try:
            with self._active_handlers_lock:
                if (
                    self.server.admission_closed
                    or deadline <= time.time()
                    or self._read_demand_admitted is None
                    or not self._read_demand_admitted()
                ):
                    return
                self._last_completed_hook = self._clock()
        finally:
            self._read_demand_lock.release()

    def _on_native_mutation(self, request: dict) -> None:
        if (
            set(request) != {"version", "token", "kind", "hook_generation", "deadline", "payload"}
            or type(request.get("version")) is not int
            or request.get("hook_generation") != self.generation
            or type(request.get("deadline")) not in (int, float)
            or not isinstance(request.get("payload"), dict)
            or self._native_mutation_decide is None
        ):
            return
        deadline = request["deadline"]
        if (
            not math.isfinite(deadline) or not 0 < deadline - time.time() <= 1.0
            or self.server.admission_closed
        ):
            return
        if not self._read_demand_lock.acquire(timeout=0.05):
            return
        try:
            with self._active_handlers_lock:
                if self.server.admission_closed or deadline <= time.time() or (
                    self._read_demand_admitted is not None and not self._read_demand_admitted()
                ):
                    return
            try:
                result = self._native_mutation_decide(request["kind"], request["payload"], deadline)
            except HookUnavailable:
                return
            if isinstance(result, dict) and deadline > time.time():
                self._on_hook_completed("postToolUse")
        finally:
            self._read_demand_lock.release()

    def _on_worktree_changed(self, request: dict) -> None:
        if (
            set(request) != {"version", "token", "kind", "hook_generation", "deadline", "payload"}
            or type(request.get("version")) is not int
            or request.get("hook_generation") != self.generation
            or type(request.get("deadline")) not in (int, float)
            or not isinstance(request.get("payload"), dict)
            or set(request["payload"]) != {"cwd"}
            or self._worktree_changed is None
        ):
            return
        deadline, cwd = request["deadline"], request["payload"]["cwd"]
        if not math.isfinite(deadline) or not 0 < deadline - time.time() <= 1.0:
            return
        try:
            if cwd is not None and (
                not isinstance(cwd, str) or "\x00" in cwd
                or len(cwd.encode("utf-8")) > 4096 or not Path(cwd).is_absolute()
            ):
                return
        except (ValueError, UnicodeError):
            return
        if not self._read_demand_lock.acquire(timeout=0.05):
            return
        try:
            with self._active_handlers_lock:
                if self.server.admission_closed or deadline <= time.time() or (
                    self._read_demand_admitted is not None and not self._read_demand_admitted()
                ):
                    return
            try:
                self._worktree_changed(cwd)
            except Exception:
                return
            self._on_hook_completed(WORKTREE_CHANGE_KIND)
        finally:
            self._read_demand_lock.release()

    def _on_request_accepted(self) -> None:
        with self._active_handlers_lock:
            self._active_handlers += 1

    def _on_request_finished(self) -> None:
        with self._active_handlers_lock:
            self._active_handlers -= 1

    def active_handler_count(self) -> int:
        with self._active_handlers_lock:
            return self._active_handlers

    def _on_hook_completed(self, kind: str) -> None:
        """Record admitted response completion before handler settlement."""
        if kind in {"preToolUse", "postToolUse", WORKTREE_CHANGE_KIND}:
            with self._active_handlers_lock:
                self._last_completed_hook = self._clock()

    def has_recent_hook_demand(self) -> bool:
        """Idle grace only; active-handler counts still govern draining."""
        with self._active_handlers_lock:
            return self._last_completed_hook is not None and (
                0 <= self._clock() - self._last_completed_hook < _RECENT_HOOK_DEMAND_S
            )


_DEFAULT_CLIENT_TIMEOUT_S = 1.0


def _lock_matches_installation_context(lock: dict) -> bool:
    """Whether ``lock``'s installation-context fields match THIS process's
    own selected cell -- mirrors ``scripts/hook_client.py``'s
    ``_lock_matches_context`` (that script re-derives the context itself
    since it cannot import this package; here ``registry_paths.
    installation_context()`` already resolves the identical value for an
    in-package caller).

    Without this check, a stale rendezvous lock belonging to a DIFFERENT,
    still-live installation (a different marketplace install, a different
    plugin root under an explicit registry context) could be read as
    "reachable" and accept the connection, returning ``True`` while the
    monitor actually intended to be woken is never touched.
    """
    from . import registry_paths

    context = registry_paths.installation_context()
    if context is None:
        return not (
            str(lock.get("installReceipt") or "").strip()
            or str(lock.get("marketplaceId") or "").strip()
        )
    return (
        str(lock.get("installReceipt") or "").strip()
        == str(context.get("installReceipt") or "").strip()
        and str(lock.get("marketplaceId") or "").strip()
        == str(context.get("marketplaceId") or "").strip()
        and str(lock.get("pluginRoot") or "").strip()
        == str(context.get("pluginRoot") or "").strip()
    )


def send_best_effort(
    kind: str,
    payload: dict,
    *,
    lock_path: Path | None = None,
    timeout: float = _DEFAULT_CLIENT_TIMEOUT_S,
    required_capability: str | None = None,
    send_only: bool = False,
) -> bool:
    """Send a fire-and-forget hook-ipc request to the resident monitor, if
    one is running and reachable through the current install's rendezvous
    lock (``status-monitor.lock`` by default -- the SAME file
    :meth:`HookIpcServer.rendezvous` publishes into via the monitor's own
    ``_lock_extra``).

    Returns whether the resident accepted the request (not whether it did
    anything useful with it -- ``kind`` handlers that only need to cause an
    early wake, like ``"handoffWake"``, return an empty result either way).
    Never raises: every failure mode (no resident running, a stale/
    mismatched lock, a connection error, a malformed response, a resident
    that explicitly declines via ``fallback``) is swallowed and reported as
    ``False``, identically to how a missed :func:`resident_push.notify`
    costs only latency, never correctness -- the periodic sweep interval
    remains the backstop. This is the counterpart, for a caller OUTSIDE the
    resident process, to that same in-process notify: this performs the
    actual bytes-on-the-wire half a cross-process caller (e.g. a short-lived
    ``note-handoff`` CLI invocation) needs that an in-process
    ``threading.Event`` cannot provide by itself.

    This duplicates (deliberately, not by oversight) some wire-protocol
    logic already present in ``scripts/hook_client.py``'s own ``_request()``:
    that script is intentionally import-free of this package (it runs as a
    standalone hook subprocess, sometimes before the package itself is even
    installed), so it cannot depend on this function, and this function is
    not reachable from that bootstrap-sensitive context either. Both sides
    implement the same small wire format described in this module's server
    classes above.

    ``send_only`` requires a qualified advertised capability and generation.
    It reports only that bytes were sent, never acceptance or publication.
    """
    try:
        from . import config as cfg

        resolved_lock_path = lock_path if lock_path is not None else cfg.install_dir() / "status-monitor.lock"
        endpoint = json.loads(resolved_lock_path.read_text("utf-8"))
    except Exception:
        return False
    if not isinstance(endpoint, dict) or endpoint.get("hook_transport") != "tcp":
        return False
    if required_capability is not None:
        capabilities = endpoint.get("hook_capabilities")
        if (
            not isinstance(required_capability, str) or not required_capability
            or not isinstance(capabilities, list)
            or not all(isinstance(value, str) for value in capabilities)
            or required_capability not in capabilities
        ):
            return False
    if send_only and (
        required_capability is None
        or not isinstance(endpoint.get("hook_generation"), str)
        or not endpoint["hook_generation"]
    ):
        return False
    try:
        if not _lock_matches_installation_context(endpoint):
            return False
    except Exception:
        # registry_paths.installation_context() can raise (e.g.
        # RegistryRootError) for a malformed/unavailable explicit context --
        # never let that escape into the caller; an unverifiable context is
        # just another unreachable-endpoint outcome for this best-effort call.
        return False
    address = str(endpoint.get("hook_endpoint") or "")
    host, sep, port_text = address.rpartition(":")
    token = endpoint.get("hook_token")
    if not sep or host != "127.0.0.1" or not port_text.isdigit():
        return False
    port = int(port_text)
    if not (0 < port <= 65535):
        # isdigit() alone accepts any non-negative integer string, but
        # socket.create_connection raises OverflowError (not OSError) for a
        # port outside the valid 1..65535 range -- uncaught, that would
        # violate this function's never-raises contract and could turn an
        # already-persisted handoff's best-effort wake into a failed CLI
        # invocation. Treat an invalid port the same as any other
        # unreachable endpoint.
        return False
    if not isinstance(token, str) or not token:
        return False
    try:
        request = json.dumps(
            {
                "version": 1,
                "token": token,
                "kind": kind,
                "payload": payload,
                "deadline": time.time() + (1.0 if send_only else timeout),
                **({"hook_generation": endpoint["hook_generation"]} if send_only else {}),
            },
            separators=(",", ":"),
        ).encode("utf-8") + b"\n"
        if send_only and len(request) > 65536:
            return False
    except (TypeError, ValueError):
        # A non-JSON-serializable payload or an invalid timeout must not
        # propagate into the caller -- an already-persisted handoff's
        # best-effort wake can never turn into a failed CLI invocation.
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout) as conn:
            conn.settimeout(timeout)
            conn.sendall(request)
            if send_only:
                return True
            raw = b""
            while b"\n" not in raw and len(raw) < 2 * 1024 * 1024:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                raw += chunk
    except OSError:
        return False
    except (TypeError, ValueError, OverflowError):
        # OverflowError: socket.create_connection/settimeout raise it for
        # float('inf') or another too-large finite timeout -- not an
        # OSError, but just as much an "unreachable" outcome for this
        # never-raises, best-effort call.
        return False
    try:
        value = json.loads(raw.split(b"\n", 1)[0].decode("utf-8"))
    except Exception:
        return False
    if not isinstance(value, dict) or value.get("version") != 1 or value.get("fallback") is True:
        return False
    return True
