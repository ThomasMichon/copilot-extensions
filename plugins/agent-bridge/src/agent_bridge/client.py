"""HTTP client for the agent-bridge REST API.

Used by CLI commands to talk to a running agent-bridge service.
Uses only stdlib (urllib) to avoid adding runtime dependencies.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator

import yaml

if TYPE_CHECKING:
    from collections.abc import Callable


DEFAULT_RESTART_GRACE = 30.0
DEFAULT_SESSION_SETTLE_GRACE = 5.0


class BridgeClientError(Exception):
    """Raised when the API returns an error."""

    def __init__(self, status: int, detail: Any) -> None:
        self.status = status
        self.detail = detail
        super().__init__(f"HTTP {status}: {detail}")


class BridgeConnectionError(Exception):
    """Raised when the service is unreachable (e.g. mid-restart).

    Unlike the one-shot command path (which exits), the streaming engine
    catches this and retries -- so a service restart mid-workflow is
    survivable: the client reconnects and resumes from its acked cursor.
    """


class SseStream(Iterator[dict[str, Any]]):
    """Closable incremental SSE parser used by long-lived subscriptions."""

    def __init__(self, response: Any) -> None:
        self._response = response
        self._lines = iter(response)
        self._event_type = ""
        self._event_id = ""
        self._data_lines: list[str] = []
        self.headers = getattr(response, "headers", {})

    def __iter__(self) -> SseStream:
        return self

    def __enter__(self) -> SseStream:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def __del__(self) -> None:
        self.close()

    def __next__(self) -> dict[str, Any]:
        while True:
            try:
                raw_line = next(self._lines)
            except StopIteration:
                self.close()
                raise
            line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")

            if line.startswith(":"):
                body = line[1:].strip()
                if body.startswith("tool_progress"):
                    raw = body[len("tool_progress"):].strip()
                    try:
                        data = json.loads(raw) if raw else {}
                    except json.JSONDecodeError:
                        data = {}
                    return {
                        "id": "",
                        "event": "tool_progress",
                        "data": data,
                    }
                return {"id": "", "event": "_heartbeat", "data": {}}
            if line.startswith("id: "):
                self._event_id = line[4:]
            elif line.startswith("event: "):
                self._event_type = line[7:]
            elif line.startswith("data: "):
                self._data_lines.append(line[6:])
            elif line == "":
                if not self._data_lines:
                    self._event_type = ""
                    self._event_id = ""
                    continue
                raw_data = "\n".join(self._data_lines)
                try:
                    parsed = json.loads(raw_data)
                except json.JSONDecodeError:
                    parsed = {"raw": raw_data}
                event = {
                    "id": self._event_id,
                    "event": self._event_type or parsed.get("event", ""),
                    "data": parsed.get("data", parsed),
                }
                if "timestamp" in parsed:
                    event["timestamp"] = parsed["timestamp"]
                if "continuity_id" in parsed:
                    event["continuity_id"] = parsed["continuity_id"]
                self._event_type = ""
                self._event_id = ""
                self._data_lines = []
                return event

    def close(self) -> None:
        response, self._response = self._response, None
        if response is not None:
            response.close()


class BridgeClient:
    """Sync HTTP client for the agent-bridge REST API."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: int = 120,
        connect_grace: float = DEFAULT_RESTART_GRACE,
        session_settle_grace: float | None = None,
        reresolve: "Callable[[], str | None] | None" = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout
        # One sustained-outage budget is shared by every request from this
        # short-lived CLI client, including its protocol preflight.
        self._connect_grace = max(0.0, connect_grace)
        self._outage_deadline: float | None = None
        self._outage_replacement_used = False
        # A healthy daemon may briefly lack an adopted session around a routing
        # flip, but a genuinely unknown session must settle much sooner than a
        # full daemon outage. An explicit shorter connect grace remains an
        # upper bound for backward-compatible tests and pinned clients.
        requested_settle_grace = (
            DEFAULT_SESSION_SETTLE_GRACE
            if session_settle_grace is None
            else max(0.0, session_settle_grace)
        )
        self._session_settle_grace = min(
            self._connect_grace, requested_settle_grace
        )
        # Optional live re-resolver: on a connection rejection, re-read
        # the routing table (listener-verified) to follow a coordinator that
        # moved to a new dynamic port during a zero-downtime cutover, instead of
        # hammering the remembered-but-dead port. ``None`` (e.g. an explicit-URL
        # or directly-constructed client) disables re-resolution -- behavior is
        # then exactly the old memoized-endpoint retry.
        self._reresolve = reresolve
        # Memoized (protocol_version, min_protocol_version) the daemon advertises
        # on /health. Cached for this client's (short) lifetime -- a CLI
        # invocation dials one daemon -- so repeated capability gates cost one GET.
        self._daemon_proto: tuple[int, int] | None = None

    def _mark_connected(self) -> None:
        """Reset continuous-outage state after confirmed HTTP contact."""
        self._outage_deadline = None
        self._outage_replacement_used = False

    # -- Factory -------------------------------------------------------------

    @classmethod
    def from_config(cls) -> BridgeClient:
        """Build a client from ~/.agent-bridge/ config and auth files.

        Fails clearly if the auth token is missing (unlike the server
        path which auto-generates one).
        """
        import os

        from .models import default_port

        config_dir = Path(
            os.environ.get("AGENT_BRIDGE_CONFIG_DIR", "~/.agent-bridge")
        ).expanduser()

        # Load config
        cfg_path = config_dir / "config.yaml"
        port = default_port()
        bind = "127.0.0.1"
        if cfg_path.exists():
            try:
                data = yaml.safe_load(cfg_path.read_text()) or {}
                # Port 0 is the dynamic sentinel (#694): treat as unset so the
                # fallback stays default_port(); the routing table resolves the
                # real (ephemeral) port below.
                port = data.get("port") or port
                bind = data.get("bind", bind)
            except Exception:
                pass

        # Normalize bind address for client connections
        if bind in ("0.0.0.0", ""):
            bind = "127.0.0.1"
        elif bind == "::":
            bind = "::1"

        # The static config port is the *fallback*. Prefer the routing table
        # (active.json) so a zero-downtime redeploy that flipped to a new port
        # transparently reroutes this client -- without it the CLI would dial a
        # retired daemon mid-cutover. The table is consulted unless explicitly
        # overridden; absence falls back to the config port (backward compatible).
        base_url = f"http://{bind}:{port}"
        explicit = os.environ.get("AGENT_BRIDGE_BASE_URL")
        # A live re-resolver follows a dynamic-port cutover on a connection
        # rejection. Enabled only on the routing-table discovery path --
        # an explicit URL or a disabled routing table pins the endpoint, so
        # re-resolution stays off there (the operator dialed a specific daemon).
        reresolve: "Callable[[], str | None] | None" = None
        if explicit:
            # Highest priority: the deploy orchestrator dials a *specific*
            # daemon (old or passive) by URL, bypassing the table entirely.
            base_url = explicit.rstrip("/")
        elif os.environ.get("AGENT_BRIDGE_NO_ROUTING_TABLE") not in ("1", "true"):
            def _reresolve_from_table() -> str | None:
                """The current listener-verified active endpoint, or None.

                ``verify_listener=True`` skips an advertised-but-dead port
                (healing active->previous), so a stale entry pointing at a
                retired daemon is never handed back as 'live'."""
                try:
                    from zdd.routing import read_active_endpoint

                    ep = read_active_endpoint(config_dir, verify_listener=True)
                except Exception:
                    return None
                return ep.base_url if ep is not None else None

            reresolve = _reresolve_from_table
            try:
                from zdd.routing import read_active_endpoint

                ep = read_active_endpoint(config_dir)
                if ep is not None:
                    base_url = ep.base_url
            except Exception:
                # The routing table is an optimization, never a hard dependency.
                pass

        # Client timeout (seconds) -- configurable, validated
        raw_timeout = data.get("client_timeout", 120) if cfg_path.exists() else 120
        try:
            timeout = int(raw_timeout)
            if timeout <= 0:
                raise ValueError("must be positive")
        except (TypeError, ValueError):
            print(
                "[WARN] Invalid client_timeout in config (%r), using 120s"
                % raw_timeout,
                file=sys.stderr,
            )
            timeout = 120

        # Load auth token -- fail if missing
        auth_path = config_dir / "auth.yaml"
        if not auth_path.exists():
            print(
                "[FAIL] Auth token not found at %s\n"
                "       Is agent-bridge running? Start it with: agent-bridge start"
                % auth_path,
                file=sys.stderr,
            )
            sys.exit(1)

        try:
            auth_data = yaml.safe_load(auth_path.read_text()) or {}
            token = auth_data.get("token")
            if not token:
                raise ValueError("Empty token")
        except Exception as exc:
            print(
                "[FAIL] Could not read auth token from %s: %s" % (auth_path, exc),
                file=sys.stderr,
            )
            sys.exit(1)

        return cls(base_url, str(token), timeout=timeout, reresolve=reresolve)

    # -- HTTP helpers --------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        params: dict[str, Any] | list[tuple[str, Any]] | None = None,
        request_timeout: float | None = None,
    ) -> dict[str, Any] | None:
        """Make an authenticated HTTP request. Returns parsed JSON or None for 204."""
        data = json.dumps(body).encode() if body else None

        def _build_request() -> urllib.request.Request:
            url = f"{self._base}{path}"
            if params:
                pairs = (
                    list(params.items())
                    if isinstance(params, dict)
                    else params
                )
                qs = urllib.parse.urlencode(
                    [(key, value) for key, value in pairs if value is not None],
                    doseq=True,
                )
                if qs:
                    url = f"{url}?{qs}"
            request = urllib.request.Request(url, data=data, method=method)
            request.add_header("Authorization", f"Bearer {self._token}")
            if data:
                request.add_header("Content-Type", "application/json")
            return request

        def _follow_active_endpoint() -> bool:
            """Rebuild the request if endpoint discovery reports a new daemon."""
            nonlocal req
            if self._reresolve is None:
                return False
            self._daemon_proto = None
            new_base = self._reresolve()
            if not new_base or new_base.rstrip("/") == self._base:
                return False
            self._base = new_base.rstrip("/")
            req = _build_request()
            return True

        req = _build_request()

        import time as _time

        sock_timeout = request_timeout if request_timeout is not None else self._timeout
        session_deadline: float | None = None
        session_replacement_used = False
        readiness_deadline: float | None = None
        backoff = 0.25
        while True:
            try:
                with urllib.request.urlopen(req, timeout=sock_timeout) as resp:
                    self._mark_connected()
                    if resp.status == 204:
                        return None
                    return json.loads(resp.read().decode())
            except urllib.error.HTTPError as exc:
                # The daemon answered, so any continuous connection outage has
                # ended even when this particular resource is unavailable.
                self._mark_connected()
                try:
                    detail = json.loads(exc.read().decode()).get("detail", str(exc))
                except Exception:
                    detail = str(exc)
                if exc.code == 503 and "initializing" in str(detail).lower():
                    if readiness_deadline is None:
                        readiness_deadline = (
                            _time.monotonic() + self._connect_grace
                        )
                    if _time.monotonic() + backoff < readiness_deadline:
                        _time.sleep(backoff)
                        backoff = min(backoff * 2, 1.0)
                        continue
                # A session-scoped 404 can come from the retiring daemon after
                # active.json has flipped (or just before it flips) to the
                # daemon that adopted the session. Follow discovery immediately,
                # then keep checking within the same bounded restart grace.
                session_resource = path.startswith("/api/v1/sessions/")
                if exc.code == 404 and session_resource and self._reresolve is not None:
                    if session_deadline is None:
                        session_deadline = (
                            _time.monotonic() + self._session_settle_grace
                        )
                        backoff = 0.25
                    if not session_replacement_used:
                        if _follow_active_endpoint():
                            session_replacement_used = True
                            continue
                    if _time.monotonic() + backoff < session_deadline:
                        _time.sleep(backoff)
                        backoff = min(backoff * 2, 1.0)
                        continue
                raise BridgeClientError(exc.code, detail) from exc
            except (urllib.error.URLError, ConnectionResetError) as exc:
                reset = (
                    isinstance(exc, ConnectionResetError)
                    or (
                        isinstance(exc, urllib.error.URLError)
                        and isinstance(exc.reason, ConnectionResetError)
                    )
                )
                if reset and method not in ("GET", "HEAD"):
                    raise BridgeConnectionError(
                        f"Connection to agent-bridge at {self._base} reset "
                        f"during non-idempotent {method}; request was not retried"
                    ) from exc
                if self._outage_deadline is None:
                    self._outage_deadline = (
                        _time.monotonic() + self._connect_grace
                    )
                # A connection rejection against the remembered endpoint. Before
                # spending the grace window retrying the SAME port, follow the
                # listener-verified routing table. Re-resolve after every
                # failed attempt because a cutover may publish only after an
                # earlier retry; the deadline still bounds a genuinely-down
                # service.
                now = _time.monotonic()
                if now < self._outage_deadline:
                    if _follow_active_endpoint():
                        continue
                elif not self._outage_replacement_used:
                    if _follow_active_endpoint():
                        self._outage_replacement_used = True
                        continue
                # Stage 1 (CONNECT_BRIDGE): the service may be mid-restart
                # (e.g. a plugin self-update bounced the daemon). Retry within
                # the grace window, then raise BridgeConnectionError -- never
                # sys.exit. A hard exit here was a BaseException that tunneled
                # straight through the streaming engine's `except Exception`
                # reconnect guards (_turn_settled / _ack), killing a live
                # dispatch on a brief restart instead of reconnecting (#23).
                # One-shot command handlers surface this as a clean message via
                # the top-level guard in main(); the streaming engine catches it
                # and resumes from the caller's acked cursor.
                if _time.monotonic() + backoff < self._outage_deadline:
                    _time.sleep(backoff)
                    backoff = min(backoff * 2, 1.0)
                    continue
                raise BridgeConnectionError(
                    f"Cannot connect to agent-bridge at {self._base}: {exc}"
                ) from exc

    def refresh_endpoint(self) -> bool:
        """Re-resolve the daemon endpoint from the routing table.

        Follows a ZDD cutover to a **new dynamic port** (post-#694): the
        streaming path (:meth:`_stream_sse`) pins ``self._base`` at construction
        and, unlike :meth:`_request`, does not re-resolve mid-flight, so a
        streaming caller (``wait``/``read``) that loses the connection across a
        daemon restart must call this to follow the daemon to its new port
        before reconnecting -- otherwise it retries a dead port forever. Returns
        True when the base actually changed. Safe/no-op when the client was built
        without a re-resolver.
        """
        self._daemon_proto = None
        if self._reresolve is None:
            return False
        new_base = self._reresolve()
        if not new_base or new_base.rstrip("/") == self._base:
            return False
        self._base = new_base.rstrip("/")
        return True

    def _stream_sse(
        self, path: str, *, params: dict[str, str] | None = None
    ) -> SseStream:
        """Stream SSE events from an endpoint. Yields parsed event dicts.

        Raises ``BridgeConnectionError`` if the service is unreachable so the
        streaming engine can reconnect (rather than killing the process). A
        successful SSE connection resets request outage state, but stream retry
        duration remains owned by the streaming engine.
        """
        url = f"{self._base}{path}"
        if params:
            qs = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
            if qs:
                url = f"{url}?{qs}"

        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Bearer {self._token}")
        req.add_header("Accept", "text/event-stream")

        try:
            resp = urllib.request.urlopen(req, timeout=120)
        except urllib.error.HTTPError as exc:
            self._mark_connected()
            try:
                detail = json.loads(exc.read().decode()).get("detail", str(exc))
            except Exception:
                detail = str(exc)
            raise BridgeClientError(exc.code, detail) from exc
        except urllib.error.URLError as exc:
            raise BridgeConnectionError(
                f"Cannot connect to agent-bridge at {self._base}: {exc}"
            ) from exc

        self._mark_connected()
        return SseStream(resp)

    # -- API methods ---------------------------------------------------------

    def health(self) -> dict[str, Any]:
        """GET /health"""
        # Health endpoint is public (no auth needed), but we send it anyway
        return self._request("GET", "/health") or {}

    def daemon_protocol(self, *, refresh: bool = False) -> tuple[int, int]:
        """The ``(protocol_version, min_protocol_version)`` the daemon advertises.

        Reads the HTTP wire-contract version + supported range from ``/health``
        (dotfiles #632). A daemon that predates protocol advertisement omits the
        fields, so we report ``(UNVERSIONED, UNVERSIONED)`` == ``(0, 0)`` — every
        versioned-capability check then degrades **off** rather than assuming a
        support it cannot confirm. Memoized for this client's lifetime unless
        ``refresh`` is set.
        """
        from .protocol import UNVERSIONED

        if self._daemon_proto is None or refresh:
            h = self.health()
            try:
                self._daemon_proto = (
                    int(h.get("protocol_version", UNVERSIONED)),
                    int(h.get("min_protocol_version", UNVERSIONED)),
                )
            except (TypeError, ValueError):
                self._daemon_proto = (UNVERSIONED, UNVERSIONED)
        return self._daemon_proto

    def daemon_supports(self, min_version: int) -> bool:
        """Whether the live daemon speaks at least HTTP protocol ``min_version``.

        The capability gate for a client **newer** than the daemon it calls:
        check this before using a feature introduced at protocol ``min_version``
        and fall back gracefully when it is ``False``, instead of blind-sending a
        request an older daemon will ignore or reject (dotfiles #632). An
        unreachable or unversioned daemon reports version ``0`` → ``False``.
        """
        version, _min_supported = self.daemon_protocol()
        return version >= min_version

    def assert_client_supported(self) -> None:
        """Fail fast when THIS client is older than the daemon's support floor.

        The counterpart to :meth:`daemon_supports` (which gates a *newer* client
        against an *older* daemon): here we detect a client whose HTTP contract
        version is **below** the daemon's advertised ``min_protocol_version`` — a
        genuine past-the-support-window incompatibility where the tolerant-reader
        contract can no longer carry correctness — and raise a clear, actionable
        :class:`BridgeClientError` (426 Upgrade Required) instead of blind-sending
        requests the daemon has stopped serving (dotfiles #632).

        Symmetric, self-gating design consistent with the version-skew-tolerant
        stance: each side checks the peer's advertised bounds; the daemon still
        only *advertises* its floor (it does not refuse to operate). Degrade-safe:
        an unreachable or unversioned daemon advertises ``min == UNVERSIONED (0)``,
        so this never raises against it. Latent while
        ``HTTP_PROTOCOL_MIN_SUPPORTED`` stays at its current value; it activates
        automatically the day the floor is raised past this client's version.
        """
        from .protocol import HTTP_PROTOCOL_VERSION

        _version, min_supported = self.daemon_protocol()
        if HTTP_PROTOCOL_VERSION < min_supported:
            raise BridgeClientError(
                426,
                f"agent-bridge client HTTP protocol v{HTTP_PROTOCOL_VERSION} is "
                f"older than this daemon's minimum supported v{min_supported}. "
                f"Update the agent-bridge plugin + runtime on this machine — the "
                f"daemon has moved past this client's contract.",
            )

    def list_agents(self) -> list[dict[str, Any]]:
        """GET /api/v1/agents"""
        agents, _errors = self.list_agents_with_diagnostics()
        return agents

    def list_agents_with_diagnostics(
        self,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """GET /api/v1/agents, including invalid-topology diagnostics."""
        resp = self._request("GET", "/api/v1/agents")
        if not resp:
            return [], []
        errors = [str(e) for e in resp.get("topology_errors", [])]
        return resp.get("agents", []), errors

    def get_agent(self, name: str) -> dict[str, Any]:
        """GET /api/v1/agents/{name}"""
        return self._request("GET", f"/api/v1/agents/{name}") or {}

    def list_machines(self) -> list[dict[str, Any]]:
        """GET /api/v1/machines"""
        machines, _errors = self.list_machines_with_diagnostics()
        return machines

    def list_machines_with_diagnostics(
        self,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """GET /api/v1/machines, including invalid-topology diagnostics."""
        resp = self._request("GET", "/api/v1/machines")
        if not resp:
            return [], []
        errors = [str(e) for e in resp.get("topology_errors", [])]
        return resp.get("machines", []), errors

    def list_sessions(self, *, status: str | None = None) -> list[dict[str, Any]]:
        """GET /api/v1/sessions"""
        params = {"status": status} if status else None
        resp = self._request("GET", "/api/v1/sessions", params=params)
        return resp.get("sessions", []) if resp else []

    def get_session(self, session_id: str) -> dict[str, Any]:
        """GET /api/v1/sessions/{id}"""
        return self._request("GET", f"/api/v1/sessions/{session_id}") or {}

    def get_live_session(self, session_id: str) -> dict[str, Any]:
        """GET /api/v1/live-sessions/{id}; {} if not a registered live session.

        Used by ``send`` to detect an interactive-CLI target (delivered via the
        message queue) vs. a bridge-owned session (delivered as an ACP turn).
        """
        try:
            return self._request(
                "GET", f"/api/v1/live-sessions/{session_id}"
            ) or {}
        except BridgeClientError as exc:
            if exc.status == 404:
                return {}
            raise

    def list_live_sessions(
        self, *, worktree_id: str | None = None, include_dead: bool = False
    ) -> list[dict[str, Any]]:
        """GET /api/v1/live-sessions (optionally ?worktree_id=...).

        Returns the registered live interactive-CLI sessions -- the registry
        that feeds task-coordination tracking of a CLI-embodied task. Terminal
        ``expired`` / ``taken-over`` rows are hidden unless ``include_dead`` is
        set (#3144); ``wedged`` sessions are shown (#3145).
        """
        params: dict[str, Any] = {}
        if worktree_id:
            params["worktree_id"] = worktree_id
        if include_dead:
            params["include_dead"] = "true"
        resp = self._request(
            "GET", "/api/v1/live-sessions", params=params or None
        )
        return resp.get("live_sessions", []) if resp else []

    def record_live_progress(
        self,
        handle: str,
        *,
        summary: str,
        phase: str = "",
        blocker: str | None = None,
        pr: str | None = None,
    ) -> dict[str, Any]:
        """POST /api/v1/live-sessions/{handle}/progress -- an operator session's
        progress beat. ``handle`` is a session id or a worktree handle."""
        return self._request(
            "POST",
            f"/api/v1/live-sessions/{handle}/progress",
            {"summary": summary, "phase": phase, "blocker": blocker, "pr": pr},
        ) or {}

    def resolve_live_session(self, handle: str) -> dict[str, Any]:
        """GET /api/v1/live-sessions/resolve?handle=...; {} if unresolvable.

        Resolves a handle (an exact ``session_id`` OR a **worktree handle**) to
        its current live session -- the D3 addressing primitive that lets a peer
        address an agent by worktree and reach whichever session is live now, so
        ``reply-to`` survives a handoff. Used by ``send`` to detect a live target
        (worktree handle or session id) before falling back to an ACP agent.
        """
        try:
            return self._request(
                "GET", "/api/v1/live-sessions/resolve",
                params={"handle": handle},
            ) or {}
        except BridgeClientError as exc:
            if exc.status == 404:
                return {}
            raise

    def resolve_live_result_target(self, handle: str) -> dict[str, Any]:
        """Resolve a represented live or wedged target for result inspection."""
        self._require_represented_result_snapshots()
        try:
            return self._request(
                "GET",
                "/api/v1/live-sessions/result-target",
                params={"handle": handle},
            ) or {}
        except BridgeClientError as exc:
            if exc.status == 404:
                return {}
            raise

    def send_live_message(
        self, session_id: str, *, sender: str, body: str,
        reply_to: str | None = None, kind: str = "prompt",
        wait: bool = False, wait_timeout: float | None = None,
        idempotency_key: str | None = None,
        expected_session_id: str | None = None,
    ) -> dict[str, Any]:
        """POST /api/v1/live-sessions/{id}/messages -- deliver into a live session.

        ``kind`` is the D2 intent tag (``prompt`` vs ``notify``/``status-check``).
        When ``wait`` is set (D1), the bridge also watches the target's
        represented stream and the result carries the reply turn's assistant
        text (``replied``/``reply``/``stop_reason``). The HTTP request blocks for
        up to ``wait_timeout`` while the receiver processes the message, so the
        client read timeout is widened to cover it.
        """
        payload: dict[str, Any] = {"sender": sender, "body": body}
        if reply_to:
            payload["reply_to"] = reply_to
        if kind and kind != "prompt":
            payload["kind"] = kind
        if idempotency_key:
            payload["idempotency_key"] = idempotency_key
        if expected_session_id:
            payload["expected_session_id"] = expected_session_id
        request_timeout = None
        if wait:
            payload["wait"] = True
            if wait_timeout is not None:
                payload["wait_timeout"] = wait_timeout
            # Give the HTTP read a margin beyond the server-side reply wait.
            request_timeout = (wait_timeout or 120.0) + 15.0
        return self._request(
            "POST", f"/api/v1/live-sessions/{session_id}/messages", payload,
            request_timeout=request_timeout,
        ) or {}

    def get_session_usage(self, session_id: str) -> dict[str, Any]:
        """GET /api/v1/sessions/{id}/usage"""
        return self._request("GET", f"/api/v1/sessions/{session_id}/usage") or {}

    def get_session_status(
        self, session_id: str, *, caller_id: str | None = None
    ) -> dict[str, Any]:
        """GET /api/v1/sessions/{id}/status -- compact dispatch status.

        Includes the in-flight tool (with ``elapsed_s``) and the caller's
        cursor position vs head, so a watcher can check progress without
        dumping the whole feed.
        """
        params = {"caller_id": caller_id} if caller_id else None
        return self._request(
            "GET", f"/api/v1/sessions/{session_id}/status", params=params
        ) or {}

    def _require_result_snapshots(self) -> None:
        from .protocol import RESULT_SNAPSHOT_PROTOCOL_VERSION

        if not self.daemon_supports(RESULT_SNAPSHOT_PROTOCOL_VERSION):
            version, _minimum = self.daemon_protocol()
            raise BridgeClientError(
                426,
                "bounded result snapshots require agent-bridge HTTP protocol "
                f"v{RESULT_SNAPSHOT_PROTOCOL_VERSION}; the daemon advertises "
                f"v{version}. Update the agent-bridge plugin + runtime.",
            )

    def get_result_snapshot(
        self,
        session_ref: str,
        *,
        position: str | None = None,
        max_items: int | None = None,
        max_text_chars: int | None = None,
    ) -> dict[str, Any]:
        """GET /api/v1/sessions/{ref}/result after a protocol capability gate."""
        self._require_result_snapshots()
        params: dict[str, Any] = {}
        if position:
            params["position"] = position
        if max_items is not None:
            params["max_items"] = max_items
        if max_text_chars is not None:
            params["max_text_chars"] = max_text_chars
        return self._request(
            "GET", f"/api/v1/sessions/{session_ref}/result",
            params=params or None,
        ) or {}

    def expand_result_ref(
        self, session_ref: str, ref: str
    ) -> dict[str, Any]:
        """GET /api/v1/sessions/{ref}/result/detail for one opaque reference."""
        self._require_result_snapshots()
        return self._request(
            "GET",
            f"/api/v1/sessions/{session_ref}/result/detail",
            params={"ref": ref},
        ) or {}

    def _require_attention_waits(self) -> None:
        from .protocol import ATTENTION_WAIT_PROTOCOL_VERSION

        if not self.daemon_supports(ATTENTION_WAIT_PROTOCOL_VERSION):
            version, _minimum = self.daemon_protocol()
            raise BridgeClientError(
                426,
                "attention waits require agent-bridge HTTP protocol "
                f"v{ATTENTION_WAIT_PROTOCOL_VERSION}; the daemon advertises "
                f"v{version}. Update the agent-bridge plugin + runtime.",
            )

    def wait_for_attention(
        self,
        session_ref: str,
        *,
        reasons: list[str],
        position: str | None = None,
        timeout_seconds: float = 30.0,
    ) -> dict[str, Any]:
        """Return one cursor-neutral bounded attention wait result."""
        self._require_attention_waits()
        params: list[tuple[str, Any]] = [
            ("reason", reason) for reason in reasons
        ]
        params.append(("timeout_seconds", timeout_seconds))
        if position:
            params.append(("position", position))
        return self._request(
            "GET",
            f"/api/v1/sessions/{session_ref}/attention",
            params=params,
            request_timeout=max(self._timeout, timeout_seconds + 5.0),
        ) or {}

    def answer_permission(
        self, session_id: str, request_id: str, option_id: str
    ) -> dict[str, Any]:
        """Resolve one correlated permission request."""
        self._require_attention_waits()
        return self._request(
            "POST",
            f"/api/v1/sessions/{session_id}/permission",
            body={"request_id": request_id, "option_id": option_id},
        ) or {}

    def _require_represented_result_snapshots(self) -> None:
        from .protocol import REPRESENTED_RESULT_SNAPSHOT_PROTOCOL_VERSION

        if not self.daemon_supports(REPRESENTED_RESULT_SNAPSHOT_PROTOCOL_VERSION):
            version, _minimum = self.daemon_protocol()
            raise BridgeClientError(
                426,
                "represented result snapshots require agent-bridge HTTP "
                f"protocol v{REPRESENTED_RESULT_SNAPSHOT_PROTOCOL_VERSION}; "
                f"the daemon advertises v{version}. Update the agent-bridge "
                "plugin + runtime.",
            )

    def get_live_result_snapshot(
        self,
        session_ref: str,
        *,
        position: str | None = None,
        max_items: int | None = None,
        max_text_chars: int | None = None,
    ) -> dict[str, Any]:
        """GET /api/v1/live-sessions/{ref}/result after a capability gate."""
        self._require_represented_result_snapshots()
        params: dict[str, Any] = {}
        if position:
            params["position"] = position
        if max_items is not None:
            params["max_items"] = max_items
        if max_text_chars is not None:
            params["max_text_chars"] = max_text_chars
        return self._request(
            "GET", f"/api/v1/live-sessions/{session_ref}/result",
            params=params or None,
        ) or {}

    def expand_live_result_ref(
        self, session_ref: str, ref: str
    ) -> dict[str, Any]:
        """GET represented result detail for one process-lifetime reference."""
        self._require_represented_result_snapshots()
        return self._request(
            "GET",
            f"/api/v1/live-sessions/{session_ref}/result/detail",
            params={"ref": ref},
        ) or {}

    def answer_ask_user(
        self,
        session_id: str,
        tool_call_id: str,
        content: dict[str, Any] | None = None,
        *,
        action: str = "accept",
    ) -> dict[str, Any]:
        """POST /api/v1/sessions/{id}/ask-user -- answer a parked ask_user.

        Resolves the dispatched agent's blocked ``ask_user`` elicitation so its
        turn continues (the host acting as the human the agent reached for;
        dotfiles#1275). ``action`` is ``accept`` (with ``content``), ``decline``,
        or ``cancel``. Raises ``BridgeClientError`` (409) when no matching
        question is outstanding.
        """
        return self._request(
            "POST", f"/api/v1/sessions/{session_id}/ask-user",
            body={
                "tool_call_id": tool_call_id,
                "content": content or {},
                "action": action,
            },
        ) or {}

    def start_session(
        self,
        *,
        agent: str | None = None,
        target_dir: str | None = None,
        caller_id: str | None = None,
        sender_repo: str | None = None,
        caller_owner_ref: str | None = None,
        force_new: bool = False,
        parity_fault: str | None = None,
        worktree_id: str | None = None,
        reclaim: bool = False,
        env: dict[str, str] | None = None,
        model: str | None = None,
        effort: str | None = None,
        request_timeout: float | None = None,
    ) -> dict[str, Any]:
        """POST /api/v1/sessions

        ``worktree_id`` targets an *existing* worktree (a session roll). When it
        is set, the server enforces the session-lifecycle head guard: a create
        into a worktree whose ground-layer head is active or whose numbered
        handoff is pending is refused (409 ``worktree_head_active`` /
        ``worktree_head_pending``) unless ``reclaim=true`` -- the
        break-glass take-over (sibling of ``resume_worktree(reclaim=...)``).

        ``env`` sets per-session environment overrides merged onto the resolved
        agent's declared env and applied to the spawned Copilot CLI -- e.g. BYOK
        provider selection (``COPILOT_PROVIDER_BASE_URL`` / ``COPILOT_MODEL``).
        """
        body: dict[str, Any] = {}
        if agent:
            body["agent"] = agent
        if target_dir:
            body["target_dir"] = target_dir
        if caller_id:
            body["caller_id"] = caller_id
        if sender_repo:
            body["sender_repo"] = sender_repo
        if caller_owner_ref:
            body["caller_owner_ref"] = caller_owner_ref
        if force_new:
            body["force_new"] = True
        if parity_fault:
            from .protocol import FAILED_ACP_HANDSHAKE_PROTOCOL_VERSION

            if not self.daemon_supports(
                FAILED_ACP_HANDSHAKE_PROTOCOL_VERSION
            ):
                raise BridgeClientError(
                    426,
                    "The active agent-bridge daemon does not support failed "
                    "ACP handshake parity injection. Update the agent-bridge "
                    "runtime before running this fault scenario.",
                )
            body["parity_fault"] = parity_fault
        if worktree_id:
            body["worktree_id"] = worktree_id
        if reclaim:
            body["reclaim"] = True
        if env:
            body["env"] = env
        if model:
            body["model"] = model
        if effort:
            body["effort"] = effort
        # Always declare this client's HTTP contract version so a (cross-host)
        # receiver can gate capability across version skew (dotfiles #632).
        from .protocol import HTTP_PROTOCOL_VERSION

        body["protocol_version"] = HTTP_PROTOCOL_VERSION
        return self._request(
            "POST",
            "/api/v1/sessions",
            body,
            request_timeout=request_timeout,
        ) or {}

    def submit_prompt(
        self,
        session_id: str,
        prompt: str,
        *,
        queue: bool = False,
        caller_id: str | None = None,
        request_timeout: float | None = None,
    ) -> dict[str, Any]:
        """POST /api/v1/sessions/{id}/turns.

        ``queue=True`` opts into durable send-or-queue: if the session is busy
        the prompt is persisted server-side and delivered FIFO on settle
        (surviving a caller remount / bridge restart) rather than 409'd. The
        response then carries ``queued: true`` with the queue position.
        """
        payload: dict[str, Any] = {"prompt": prompt}
        if queue:
            payload["queue"] = True
            if caller_id:
                payload["caller_id"] = caller_id
        return self._request(
            "POST",
            f"/api/v1/sessions/{session_id}/turns",
            payload,
            request_timeout=request_timeout,
        ) or {}

    def stop_session(
        self, session_id: str, *, force: bool = False, reap_host: bool = False
    ) -> None:
        """POST /api/v1/sessions/{id}/stop

        ``force`` maps to the route's ``?force=true`` — tear down even with
        active background sub-agent tasks (they are killed). See #191.

        ``reap_host`` maps to ``?reap_host=true`` — additionally FREE the
        Session-Host child immediately instead of only detaching it (the
        idle-reaper primitive). The session stays STOPPED and resumable via
        ``load_session`` replay; use it when the caller never reattaches over
        the bridge and wants the ~280 MB child reclaimed on the spot rather than
        after the idle-reaper TTL (#2960).
        """
        params: dict[str, str] = {}
        if force:
            params["force"] = "true"
        if reap_host:
            params["reap_host"] = "true"
        self._request(
            "POST",
            f"/api/v1/sessions/{session_id}/stop",
            params=params or None,
        )

    def interrupt_relays_for_parity(
        self,
        session_id: str,
        *,
        timeout: float = 90.0,
    ) -> dict[str, Any]:
        """Interrupt one harness-owned session's supervised credential relay."""
        from .protocol import RELAY_INTERRUPT_PROTOCOL_VERSION

        if not self.daemon_supports(RELAY_INTERRUPT_PROTOCOL_VERSION):
            raise BridgeClientError(
                426,
                "The active agent-bridge daemon does not support parity relay "
                "interruption. Update the agent-bridge runtime before running "
                "this fault scenario.",
            )
        return self._request(
            "POST",
            f"/api/v1/sessions/{session_id}/parity/interrupt-relays",
            params={"timeout": str(timeout)},
            request_timeout=timeout + 15.0,
        ) or {}

    def recreate_container_for_parity(
        self,
        session_id: str,
        *,
        timeout: float = 600.0,
    ) -> dict[str, Any]:
        """Recreate one harness-owned container session and replace it."""
        from .protocol import CONTAINER_RECREATE_PROTOCOL_VERSION

        if not self.daemon_supports(CONTAINER_RECREATE_PROTOCOL_VERSION):
            raise BridgeClientError(
                426,
                "The active agent-bridge daemon does not support parity "
                "container recreation. Update the runtime before running "
                "this fault scenario.",
            )
        return self._request(
            "POST",
            f"/api/v1/sessions/{session_id}/parity/recreate-container",
            params={"timeout": str(timeout)},
            # The provider recreation consumes ``timeout``; the same request
            # then waits through a complete cold replacement ACP launch.
            request_timeout=timeout + 3600.0,
        ) or {}

    def resume_session(
        self,
        session_id: str,
        *,
        request_timeout: float | None = None,
    ) -> dict[str, Any]:
        """POST /api/v1/sessions/{id}/resume"""
        return self._request(
            "POST",
            f"/api/v1/sessions/{session_id}/resume",
            request_timeout=request_timeout,
        ) or {}

    def resume_worktree(
        self,
        worktree_id: str,
        *,
        reclaim: bool = False,
        request_timeout: float | None = None,
    ) -> dict[str, Any]:
        """POST /api/v1/worktrees/{id}/resume -- ensure a worktree has a live
        owned session (resume its latest, or start a fresh one if the worktree
        still exists on disk but has no resumable session).

        ``reclaim`` is the break-glass take-over: a *fresh live* interactive CLI
        holding the worktree normally yields a 409
        (``reason: live_cli_holds_worktree``); ``reclaim=true`` bypasses that
        guard so the caller can own a worktree it has just freed.
        """
        params = {"reclaim": "true"} if reclaim else None
        return (
            self._request(
                "POST",
                f"/api/v1/worktrees/{worktree_id}/resume",
                params=params,
                request_timeout=request_timeout,
            )
            or {}
        )

    def end_session(
        self,
        session_id: str,
        *,
        force: bool = False,
        if_idle: bool = False,
    ) -> None:
        """DELETE /api/v1/sessions/{id}

        ``force`` maps to the route's ``?force=true`` — tear down even with
        active background sub-agent tasks (they are killed). See #191.
        """
        if if_idle:
            from .protocol import CONDITIONAL_IDLE_END_PROTOCOL_VERSION

            if not self.daemon_supports(CONDITIONAL_IDLE_END_PROTOCOL_VERSION):
                raise BridgeClientError(
                    426,
                    "Conditional idle end requires agent-bridge HTTP protocol "
                    f"{CONDITIONAL_IDLE_END_PROTOCOL_VERSION} or newer.",
                )
        params: dict[str, str] = {}
        if force:
            params["force"] = "true"
        if if_idle:
            params["if_idle"] = "true"
        self._request(
            "DELETE",
            f"/api/v1/sessions/{session_id}",
            params=params or None,
        )

    def handoff_session(
        self, session_id: str, *, reason: str | None = None, seed: bool = True
    ) -> dict[str, Any]:
        """POST /api/v1/sessions/{id}/handoff -- retire a session and continue
        in a fresh successor in the SAME worktree. Returns the successor's
        SessionInfo."""
        params: dict[str, str] = {}
        if reason:
            params["reason"] = reason
        if not seed:
            params["seed"] = "false"
        return self._request(
            "POST",
            f"/api/v1/sessions/{session_id}/handoff",
            params=params or None,
        ) or {}

    def handoff_worktree(
        self, worktree_id: str, *, reason: str | None = None, seed: bool = True
    ) -> dict[str, Any]:
        """POST /api/v1/worktrees/{id}/handoff -- hand a worktree's current
        session off to a fresh successor in place (the worktree-handle path for
        UI consumers with no session id). Returns the successor's SessionInfo."""
        params: dict[str, str] = {}
        if reason:
            params["reason"] = reason
        if not seed:
            params["seed"] = "false"
        return self._request(
            "POST",
            f"/api/v1/worktrees/{worktree_id}/handoff",
            params=params or None,
        ) or {}

    def gc(self) -> dict[str, Any]:
        """POST /api/v1/gc -- prune aged terminal sessions and compact the DB."""
        return self._request("POST", "/api/v1/gc") or {}

    def drain(
        self,
        *,
        timeout: float = 300.0,
        poll: float = 1.0,
        force: bool = False,
        source: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """POST /api/v1/drain -- stop accepting new work and wait for in-flight
        sessions to settle (the zero-downtime pre-swap step).

        If this process is a descendant of a Copilot session (its
        ``AGENT_BRIDGE_SESSION_ID`` env is set -- e.g. an agent running an
        in-session ``test-chamber services agent-bridge update``), that session
        is passed as ``exclude_session_id`` so the redeploy's graceful-cancel
        does not cancel the very turn driving the update (#1790).
        """
        import os as _os

        body: dict[str, Any] = {"timeout": timeout, "poll": poll, "force": force}
        if source:
            body["source"] = source
        if reason:
            body["reason"] = reason
        self_sid = _os.environ.get("AGENT_BRIDGE_SESSION_ID")
        if self_sid:
            body["exclude_session_id"] = self_sid
        return self._request(
            "POST", "/api/v1/drain",
            body=body,
            request_timeout=timeout + 30.0,
        ) or {}

    def undrain(self) -> dict[str, Any]:
        """POST /api/v1/undrain -- release the drain gate (rollback)."""
        return self._request("POST", "/api/v1/undrain") or {}

    def adopt_relay(self) -> dict[str, Any]:
        """POST /api/v1/relay/adopt -- bind the shared credential relay here."""
        return self._request("POST", "/api/v1/relay/adopt") or {}

    def shutdown(self) -> dict[str, Any]:
        """POST /api/v1/shutdown -- request graceful daemon shutdown."""
        return self._request("POST", "/api/v1/shutdown") or {}

    def stream_events(
        self,
        session_id: str,
        *,
        after: int | None = None,
        caller_id: str | None = None,
        controlled: bool = False,
        continuity_id: str | None = None,
        transient: bool = False,
    ) -> SseStream:
        """GET /api/v1/sessions/{id}/events (SSE stream).

        ``after=None`` + ``caller_id`` resumes from the caller's last-acked
        delivery cursor (server-side). Pass an explicit ``after`` for a fixed
        start point.
        """
        params: dict[str, str] = {}
        if after is not None:
            params["after"] = str(after)
        if caller_id:
            params["caller_id"] = caller_id
        if controlled:
            params["controlled"] = "true"
        if continuity_id is not None:
            params["continuity_id"] = continuity_id
        if transient:
            params["transient"] = "true"
        return self._stream_sse(
            f"/api/v1/sessions/{session_id}/events",
            params=params or None,
        )

    def get_cursor(
        self, session_id: str, *, caller_id: str | None = None
    ) -> int:
        """GET /api/v1/sessions/{id}/cursor -- caller's last-acked event id."""
        params = {"caller_id": caller_id} if caller_id else None
        resp = self._request(
            "GET", f"/api/v1/sessions/{session_id}/cursor", params=params
        )
        return resp.get("last_acked_id", 0) if resp else 0

    def get_cursor_info(
        self, session_id: str, *, caller_id: str | None = None
    ) -> dict[str, Any]:
        """GET /api/v1/sessions/{id}/cursor -- full cursor info.

        Returns ``{"last_acked_id", "head_id", ...}`` so a caller can tell
        whether it is behind unseen history (``last_acked_id == 0 < head_id``)
        without reading the whole backlog.
        """
        params = {"caller_id": caller_id} if caller_id else None
        resp = self._request(
            "GET", f"/api/v1/sessions/{session_id}/cursor", params=params
        )
        return resp or {"last_acked_id": 0, "head_id": 0}

    def ack_cursor(
        self,
        session_id: str,
        last_id: int,
        *,
        caller_id: str | None = None,
        continuity_id: str | None = None,
    ) -> int:
        """POST /api/v1/sessions/{id}/cursor -- confirm delivery up to last_id.

        Returns the effective (monotonic) cursor after the ack.
        """
        body: dict[str, Any] = {"last_id": last_id}
        if caller_id:
            body["caller_id"] = caller_id
        if continuity_id is not None:
            body["continuity_id"] = continuity_id
        resp = self._request(
            "POST", f"/api/v1/sessions/{session_id}/cursor", body
        )
        return resp.get("last_acked_id", last_id) if resp else last_id

    @staticmethod
    def _remote_path(host: str, suffix: str) -> str:
        return (
            "/api/v1/remote/"
            + urllib.parse.quote(host, safe="")
            + suffix
        )

    def _require_remote_operations(self) -> None:
        from .protocol import REMOTE_OPERATIONS_PROTOCOL_VERSION

        if not self.daemon_supports(REMOTE_OPERATIONS_PROTOCOL_VERSION):
            version, _minimum = self.daemon_protocol()
            raise BridgeClientError(
                426,
                "remote Bridge operations require agent-bridge HTTP protocol "
                f"v{REMOTE_OPERATIONS_PROTOCOL_VERSION}; the daemon advertises "
                f"v{version}. Update the agent-bridge plugin + runtime.",
            )

    def get_remote_session_status(
        self, host: str, session_id: str, *, caller_id: str
    ) -> dict[str, Any]:
        """Read exact session status through the local carrier owner."""
        self._require_remote_operations()
        return self._request(
            "GET",
            self._remote_path(
                host,
                "/sessions/"
                + urllib.parse.quote(session_id, safe="")
                + "/status",
            ),
            params={"caller_id": caller_id},
        ) or {}

    def resolve_remote_live_session(
        self, host: str, session_id: str
    ) -> dict[str, Any]:
        """Resolve one exact represented session on a hosting Bridge."""
        self._require_remote_operations()
        return self._request(
            "GET",
            self._remote_path(
                host,
                "/live-sessions/"
                + urllib.parse.quote(session_id, safe=""),
            ),
        ) or {}

    def stream_remote_events(
        self,
        host: str,
        session_id: str,
        *,
        caller_id: str,
        after: int | None = None,
        continuity_id: str | None = None,
    ) -> SseStream:
        """Stream exact hosting-Bridge events through the shared carrier."""
        self._require_remote_operations()
        params = {"caller_id": caller_id}
        if after is not None:
            params["after"] = str(after)
        if continuity_id is not None:
            params["continuity_id"] = continuity_id
        return self._stream_sse(
            self._remote_path(
                host,
                "/sessions/"
                + urllib.parse.quote(session_id, safe="")
                + "/events",
            ),
            params=params,
        )

    def ack_remote_cursor(
        self,
        host: str,
        session_id: str,
        last_id: int,
        *,
        caller_id: str,
        continuity_id: str | None,
    ) -> int:
        """Acknowledge a remote event only after local delivery is accepted."""
        self._require_remote_operations()
        response = self._request(
            "POST",
            self._remote_path(
                host,
                "/sessions/"
                + urllib.parse.quote(session_id, safe="")
                + "/cursor",
            ),
            body={
                "caller_id": caller_id,
                "last_id": last_id,
                "continuity_id": continuity_id,
            },
        )
        return response.get("last_acked_id", last_id) if response else last_id

    def read_range(
        self, session_id: str, *, start: int = 0, end: int | None = None
    ) -> list[dict[str, Any]]:
        """GET /api/v1/sessions/{id}/events/range -- random-access read.

        Does not move the delivery cursor.
        """
        params: dict[str, str] = {"start": str(start)}
        if end is not None:
            params["end"] = str(end)
        resp = self._request(
            "GET", f"/api/v1/sessions/{session_id}/events/range", params=params
        )
        return resp.get("events", []) if resp else []
