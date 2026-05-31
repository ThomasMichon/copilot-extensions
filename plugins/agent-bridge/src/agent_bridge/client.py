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


class BridgeClient:
    """Sync HTTP client for the agent-bridge REST API."""

    def __init__(self, base_url: str, token: str) -> None:
        self._base = base_url.rstrip("/")
        self._token = token

    # -- Factory -------------------------------------------------------------

    @classmethod
    def from_config(cls) -> BridgeClient:
        """Build a client from ~/.agent-bridge/ config and auth files.

        Fails clearly if the auth token is missing (unlike the server
        path which auto-generates one).
        """
        import os

        config_dir = Path(
            os.environ.get("AGENT_BRIDGE_CONFIG_DIR", "~/.agent-bridge")
        ).expanduser()

        # Load config
        cfg_path = config_dir / "config.yaml"
        port = 9280
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

        base_url = f"http://{bind}:{port}"

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

        return cls(base_url, str(token))

    # -- HTTP helpers --------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        params: dict[str, str] | None = None,
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

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                if resp.status == 204:
                    return None
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode()).get("detail", str(exc))
            except Exception:
                detail = str(exc)
            raise BridgeClientError(exc.code, detail) from exc
        except urllib.error.URLError as exc:
            print(
                "[FAIL] Cannot connect to agent-bridge at %s\n"
                "       Is it running? Start it with: agent-bridge start" % self._base,
                file=sys.stderr,
            )
            sys.exit(1)

    def _stream_sse(
        self, path: str, *, params: dict[str, str] | None = None
    ) -> Iterator[dict[str, Any]]:
        """Stream SSE events from an endpoint. Yields parsed event dicts."""
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
            print(
                "[FAIL] Cannot connect to agent-bridge at %s" % self._base,
                file=sys.stderr,
            )
            sys.exit(1)

        try:
            event_type = ""
            event_id = ""
            data_lines: list[str] = []

            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")

                if line.startswith(":"):
                    # Comment / heartbeat
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

    def start_session(
        self, *, agent: str | None = None, target_dir: str | None = None
    ) -> dict[str, Any]:
        """POST /api/v1/sessions"""
        body: dict[str, Any] = {}
        if agent:
            body["agent"] = agent
        if target_dir:
            body["target_dir"] = target_dir
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

    def stream_events(
        self, session_id: str, *, after: int = 0
    ) -> Iterator[dict[str, Any]]:
        """GET /api/v1/sessions/{id}/events (SSE stream)"""
        return self._stream_sse(
            f"/api/v1/sessions/{session_id}/events",
            params={"after": str(after)},
        )
