"""HTTP client for the agent-bridge REST API.

Used by CLI commands to talk to a running agent-bridge service.
Uses only stdlib (urllib) to avoid adding runtime dependencies.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterator

import yaml


class BridgeClientError(Exception):
    """Raised when the API returns an error."""

    def __init__(self, status: int, detail: str) -> None:
        self.status = status
        self.detail = detail
        super().__init__(f"HTTP {status}: {detail}")


class BridgeConnectionError(Exception):
    """Raised when the service is unreachable (e.g. mid-restart).

    Unlike the one-shot command path (which exits), the streaming engine
    catches this and retries -- so a service restart mid-workflow is
    survivable: the client reconnects and resumes from its acked cursor.
    """


class BridgeClient:
    """Sync HTTP client for the agent-bridge REST API."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: int = 120,
        connect_grace: float = 5.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout
        # Grace window (seconds) to retry an initial connection refusal -- the
        # service may be briefly down mid-restart (stage 1, transient).
        self._connect_grace = max(0.0, connect_grace)

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
                port = data.get("port", port)
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
        if explicit:
            # Highest priority: the deploy orchestrator dials a *specific*
            # daemon (old or passive) by URL, bypassing the table entirely.
            base_url = explicit.rstrip("/")
        elif os.environ.get("AGENT_BRIDGE_NO_ROUTING_TABLE") not in ("1", "true"):
            try:
                from .routing import read_active_endpoint

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

        return cls(base_url, str(token), timeout=timeout)

    # -- HTTP helpers --------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        params: dict[str, str] | None = None,
        request_timeout: float | None = None,
    ) -> dict[str, Any] | None:
        """Make an authenticated HTTP request. Returns parsed JSON or None for 204."""
        url = f"{self._base}{path}"
        if params:
            qs = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
            if qs:
                url = f"{url}?{qs}"

        data = json.dumps(body).encode() if body else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self._token}")
        if data:
            req.add_header("Content-Type", "application/json")

        import time as _time

        sock_timeout = request_timeout if request_timeout is not None else self._timeout
        deadline = _time.monotonic() + self._connect_grace
        backoff = 0.25
        while True:
            try:
                with urllib.request.urlopen(req, timeout=sock_timeout) as resp:
                    if resp.status == 204:
                        return None
                    return json.loads(resp.read().decode())
            except urllib.error.HTTPError as exc:
                try:
                    detail = json.loads(exc.read().decode()).get("detail", str(exc))
                except Exception:
                    detail = str(exc)
                raise BridgeClientError(exc.code, detail) from exc
            except urllib.error.URLError:
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
                if _time.monotonic() + backoff < deadline:
                    _time.sleep(backoff)
                    backoff = min(backoff * 2, 1.0)
                    continue
                raise BridgeConnectionError(
                    f"Cannot connect to agent-bridge at {self._base}"
                )

    def _stream_sse(
        self, path: str, *, params: dict[str, str] | None = None
    ) -> Iterator[dict[str, Any]]:
        """Stream SSE events from an endpoint. Yields parsed event dicts.

        Raises ``BridgeConnectionError`` if the service is unreachable so the
        streaming engine can reconnect (rather than killing the process).
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
            try:
                detail = json.loads(exc.read().decode()).get("detail", str(exc))
            except Exception:
                detail = str(exc)
            raise BridgeClientError(exc.code, detail) from exc
        except urllib.error.URLError as exc:
            raise BridgeConnectionError(
                f"Cannot connect to agent-bridge at {self._base}: {exc}"
            ) from exc

        try:
            event_type = ""
            event_id = ""
            data_lines: list[str] = []

            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")

                if line.startswith(":"):
                    # SSE comment. ``: tool_progress <json>`` carries quiet-
                    # period liveness (the in-flight tool call the remote is
                    # blocked on); any other comment is a bare heartbeat. Both
                    # are cursor-neutral (no id) -- they let the streaming
                    # engine show progress and check for turn completion during
                    # silence, without touching the durable event stream.
                    body = line[1:].strip()
                    if body.startswith("tool_progress"):
                        raw = body[len("tool_progress"):].strip()
                        try:
                            data = json.loads(raw) if raw else {}
                        except json.JSONDecodeError:
                            data = {}
                        yield {"id": "", "event": "tool_progress", "data": data}
                    else:
                        yield {"id": "", "event": "_heartbeat", "data": {}}
                    continue
                elif line.startswith("id: "):
                    event_id = line[4:]
                elif line.startswith("event: "):
                    event_type = line[7:]
                elif line.startswith("data: "):
                    data_lines.append(line[6:])
                elif line == "":
                    # End of event block
                    if data_lines:
                        raw_data = "\n".join(data_lines)
                        try:
                            parsed = json.loads(raw_data)
                        except json.JSONDecodeError:
                            parsed = {"raw": raw_data}
                        yield {
                            "id": event_id,
                            "event": event_type or parsed.get("event", ""),
                            "data": parsed.get("data", parsed),
                        }
                    event_type = ""
                    event_id = ""
                    data_lines = []
        finally:
            resp.close()

    # -- API methods ---------------------------------------------------------

    def health(self) -> dict[str, Any]:
        """GET /health"""
        # Health endpoint is public (no auth needed), but we send it anyway
        return self._request("GET", "/health") or {}

    def list_agents(self) -> list[dict[str, Any]]:
        """GET /api/v1/agents"""
        resp = self._request("GET", "/api/v1/agents")
        return resp.get("agents", []) if resp else []

    def get_agent(self, name: str) -> dict[str, Any]:
        """GET /api/v1/agents/{name}"""
        return self._request("GET", f"/api/v1/agents/{name}") or {}

    def list_machines(self) -> list[dict[str, Any]]:
        """GET /api/v1/machines"""
        resp = self._request("GET", "/api/v1/machines")
        return resp.get("machines", []) if resp else []

    def list_sessions(self, *, status: str | None = None) -> list[dict[str, Any]]:
        """GET /api/v1/sessions"""
        params = {"status": status} if status else None
        resp = self._request("GET", "/api/v1/sessions", params=params)
        return resp.get("sessions", []) if resp else []

    def get_session(self, session_id: str) -> dict[str, Any]:
        """GET /api/v1/sessions/{id}"""
        return self._request("GET", f"/api/v1/sessions/{session_id}") or {}

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

    def start_session(
        self,
        *,
        agent: str | None = None,
        target_dir: str | None = None,
        caller_id: str | None = None,
        force_new: bool = False,
    ) -> dict[str, Any]:
        """POST /api/v1/sessions"""
        body: dict[str, Any] = {}
        if agent:
            body["agent"] = agent
        if target_dir:
            body["target_dir"] = target_dir
        if caller_id:
            body["caller_id"] = caller_id
        if force_new:
            body["force_new"] = True
        return self._request("POST", "/api/v1/sessions", body) or {}

    def submit_prompt(self, session_id: str, prompt: str) -> dict[str, Any]:
        """POST /api/v1/sessions/{id}/turns"""
        return self._request(
            "POST", f"/api/v1/sessions/{session_id}/turns", {"prompt": prompt}
        ) or {}

    def stop_session(self, session_id: str) -> None:
        """POST /api/v1/sessions/{id}/stop"""
        self._request("POST", f"/api/v1/sessions/{session_id}/stop")

    def resume_session(self, session_id: str) -> dict[str, Any]:
        """POST /api/v1/sessions/{id}/resume"""
        return self._request("POST", f"/api/v1/sessions/{session_id}/resume") or {}

    def end_session(self, session_id: str) -> None:
        """DELETE /api/v1/sessions/{id}"""
        self._request("DELETE", f"/api/v1/sessions/{session_id}")

    def gc(self) -> dict[str, Any]:
        """POST /api/v1/gc -- prune aged terminal sessions and compact the DB."""
        return self._request("POST", "/api/v1/gc") or {}

    def drain(
        self, *, timeout: float = 300.0, poll: float = 1.0, force: bool = False
    ) -> dict[str, Any]:
        """POST /api/v1/drain -- stop accepting new work and wait for in-flight
        sessions to settle (the zero-downtime pre-swap step)."""
        return self._request(
            "POST", "/api/v1/drain",
            body={"timeout": timeout, "poll": poll, "force": force},
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
    ) -> Iterator[dict[str, Any]]:
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
        self, session_id: str, last_id: int, *, caller_id: str | None = None
    ) -> int:
        """POST /api/v1/sessions/{id}/cursor -- confirm delivery up to last_id.

        Returns the effective (monotonic) cursor after the ack.
        """
        body: dict[str, Any] = {"last_id": last_id}
        if caller_id:
            body["caller_id"] = caller_id
        resp = self._request(
            "POST", f"/api/v1/sessions/{session_id}/cursor", body
        )
        return resp.get("last_acked_id", last_id) if resp else last_id

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
