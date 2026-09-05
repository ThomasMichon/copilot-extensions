"""Independent local HTTP client for carrier-backed remote Bridge commands."""

from __future__ import annotations

import ipaddress
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import yaml

REMOTE_COMMAND_PROTOCOL_VERSION = 14


class RemoteBridgeUnavailable(RuntimeError):
    """The optional local Bridge remote-command capability is absent."""


class RemoteBridgeOperationError(RuntimeError):
    """The local Bridge accepted the capability boundary but the operation failed."""

    def __init__(self, message: str, *, status: int = 0, code: str = "") -> None:
        self.status = status
        self.code = code
        super().__init__(message)


def normalize_host(host: str) -> str:
    """Return the canonical SSH alias used by remote Bridge operations."""
    return host.strip().lower()


class LocalBridgeRemoteClient:
    """Call the local Agent Bridge daemon without importing its runtime."""

    def __init__(self, *, config_dir: Path | None = None) -> None:
        self._config_dir = config_dir

    @staticmethod
    def _explicit_base_url(value: str) -> str:
        try:
            parsed = urllib.parse.urlsplit(value)
            host = parsed.hostname
            _ = parsed.port
        except ValueError as exc:
            raise RemoteBridgeUnavailable(
                "Agent Bridge explicit endpoint is invalid"
            ) from exc
        if (
            parsed.scheme.casefold() not in {"http", "https"}
            or not host
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise RemoteBridgeUnavailable(
                "Agent Bridge explicit endpoint must be a loopback HTTP or HTTPS URL"
            )
        normalized_host = host.rstrip(".").casefold()
        if normalized_host != "localhost":
            try:
                is_loopback = ipaddress.ip_address(normalized_host).is_loopback
            except ValueError:
                is_loopback = False
            if not is_loopback:
                raise RemoteBridgeUnavailable(
                    "Agent Bridge explicit endpoint must be a loopback HTTP or HTTPS URL"
                )
        return value.rstrip("/")

    def _connection(self) -> tuple[str, str]:
        config_dir = self._config_dir or Path(
            os.environ.get("AGENT_BRIDGE_CONFIG_DIR", "~/.agent-bridge")
        ).expanduser()
        explicit = os.environ.get("AGENT_BRIDGE_BASE_URL")
        if explicit:
            base_url = self._explicit_base_url(explicit)
        else:
            try:
                from zdd.routing import read_active_endpoint

                endpoint = read_active_endpoint(config_dir, verify_listener=True)
            except Exception as exc:
                raise RemoteBridgeUnavailable(
                    "Agent Bridge endpoint discovery is unavailable"
                ) from exc
            if endpoint is None:
                raise RemoteBridgeUnavailable(
                    "Agent Bridge has no active local endpoint"
                )
            base_url = endpoint.base_url.rstrip("/")
        try:
            auth = yaml.safe_load(
                (config_dir / "auth.yaml").read_text(encoding="utf-8")
            ) or {}
        except (OSError, UnicodeDecodeError, ValueError, yaml.YAMLError) as exc:
            raise RemoteBridgeUnavailable(
                "Agent Bridge authentication is unavailable"
            ) from exc
        if not isinstance(auth, dict):
            raise RemoteBridgeUnavailable(
                "Agent Bridge authentication is unavailable"
            )
        token = auth.get("token")
        if not isinstance(token, str) or not token.strip():
            raise RemoteBridgeUnavailable(
                "Agent Bridge authentication is unavailable"
            )
        return base_url, token

    @staticmethod
    def _open(
        url: str,
        token: str,
        *,
        method: str = "GET",
        body: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> Any:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(  # noqa: S310 -- loopback Bridge URL
            url, data=data, method=method
        )
        request.add_header("Authorization", f"Bearer {token}")
        request.add_header("Accept", "application/json")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            return urllib.request.urlopen(  # noqa: S310 -- loopback Bridge URL
                request, timeout=timeout
            )
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8")).get(
                    "detail", str(exc)
                )
            except Exception:
                detail = str(exc)
            code = detail.get("code", "") if isinstance(detail, dict) else ""
            message = (
                detail.get("message", str(detail))
                if isinstance(detail, dict)
                else str(detail)
            )
            raise RemoteBridgeOperationError(
                message, status=exc.code, code=str(code)
            ) from exc
        except (OSError, urllib.error.URLError) as exc:
            raise RemoteBridgeOperationError(
                "Agent Bridge remote command is unreachable"
            ) from exc

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        timeout: float = 30.0,
        required_protocol: int = REMOTE_COMMAND_PROTOCOL_VERSION,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        base_url, token = self._connection()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RemoteBridgeOperationError(
                "Agent Bridge remote command timed out"
            )
        health_response = self._open(
            f"{base_url}/health",
            token,
            timeout=min(5.0, remaining),
        )
        try:
            try:
                health = json.loads(health_response.read().decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RemoteBridgeOperationError(
                    "Agent Bridge returned invalid health JSON"
                ) from exc
        finally:
            health_response.close()
        if not isinstance(health, dict):
            raise RemoteBridgeOperationError(
                "Agent Bridge returned an invalid health response"
            )
        try:
            version = int(health.get("protocol_version", 0))
            minimum = int(health.get("min_protocol_version", 1))
        except (TypeError, ValueError) as exc:
            raise RemoteBridgeOperationError(
                "Agent Bridge returned invalid health protocol versions"
            ) from exc
        required = required_protocol
        if not minimum <= required <= version:
            raise RemoteBridgeUnavailable(
                f"Agent Bridge HTTP protocol {required} is required "
                f"(daemon advertises {minimum}-{version})"
            )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RemoteBridgeOperationError(
                "Agent Bridge remote command timed out"
            )
        response = self._open(
            f"{base_url}{path}",
            token,
            method=method,
            body=body,
            timeout=remaining,
        )
        try:
            raw = response.read()
        finally:
            response.close()
        if not raw:
            return {}
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RemoteBridgeOperationError(
                "Agent Bridge returned invalid JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise RemoteBridgeOperationError(
                "Agent Bridge returned an invalid response"
            )
        return payload

    @staticmethod
    def _host_path(host: str, suffix: str) -> str:
        return (
            "/api/v1/remote/"
            + urllib.parse.quote(normalize_host(host), safe="")
            + suffix
        )

    def session_status(
        self,
        host: str,
        session_id: str,
        *,
        caller_id: str,
        timeout: float,
    ) -> dict[str, Any]:
        query = urllib.parse.urlencode({"caller_id": caller_id})
        return self._request(
            "GET",
            self._host_path(
                host,
                "/sessions/"
                + urllib.parse.quote(session_id, safe="")
                + f"/status?{query}",
            ),
            timeout=timeout,
            required_protocol=11,
        )

    def resolve_live_session(
        self, host: str, target: str, *, timeout: float
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            self._host_path(
                host,
                "/live-sessions/" + urllib.parse.quote(target, safe=""),
            ),
            timeout=timeout,
            required_protocol=11,
        )

    def create_session(
        self,
        host: str,
        *,
        agent: str,
        prompt: str,
        caller_id: str,
        timeout: float,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            self._host_path(host, "/sessions"),
            body={
                "agent": agent,
                "prompt": prompt,
                "caller_id": caller_id,
                "timeout": timeout,
            },
            timeout=timeout + 15.0,
        )

    def stop_session(
        self,
        host: str,
        session_id: str,
        *,
        force: bool = False,
        reap_host: bool = False,
        timeout: float,
    ) -> None:
        self._request(
            "POST",
            self._host_path(
                host,
                "/sessions/"
                + urllib.parse.quote(session_id, safe="")
                + "/stop",
            ),
            body={
                "force": force,
                "reap_host": reap_host,
                "timeout": timeout,
            },
            timeout=timeout + 15.0,
        )

    def end_session(
        self,
        host: str,
        session_id: str,
        *,
        force: bool = False,
        if_idle: bool = False,
        timeout: float,
    ) -> None:
        self._request(
            "POST",
            self._host_path(
                host,
                "/sessions/"
                + urllib.parse.quote(session_id, safe="")
                + "/end",
            ),
            body={
                "force": force,
                "if_idle": if_idle,
                "timeout": timeout,
            },
            timeout=timeout + 15.0,
        )

    def send_live_message(
        self,
        host: str,
        target: str,
        *,
        sender: str,
        message: str,
        kind: str,
        expected_session_id: str | None = None,
        idempotency_key: str | None = None,
        timeout: float,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            self._host_path(
                host,
                "/live-sessions/"
                + urllib.parse.quote(target, safe="")
                + "/messages",
            ),
            body={
                "sender": sender,
                "message": message,
                "kind": kind,
                "expected_session_id": expected_session_id,
                "idempotency_key": idempotency_key,
                "timeout": timeout,
            },
            timeout=timeout + 15.0,
        )
