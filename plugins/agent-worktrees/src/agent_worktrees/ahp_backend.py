"""Same-machine Agent Host Protocol session backend."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from urllib.parse import unquote, urlparse

from . import git_ops, repos
from .config import Config, SessionBackendConfig

ROOT_CHANNEL = "ahp-root://"
AUTH_TOKEN_ENV = "AGENT_WORKTREES_AHP_AUTH_TOKEN"
SESSION_LIFECYCLE_TIMEOUT_SECONDS = 30.0
SESSION_OWNER_TIMEOUT_MESSAGE = (
    "Timed out waiting for this session's Copilot CLI to answer."
)
MAX_SESSION_CATALOG_PAGES = 10_000


class AhpBackendError(RuntimeError):
    """The configured AHP backend could not safely serve the request."""


@dataclass(frozen=True)
class AhpSession:
    session_id: str
    endpoint_url: str
    protocol_version: str
    github_account: str
    working_directory: str


def _loopback_endpoint(endpoint_url: str) -> str:
    parsed = urlparse(endpoint_url)
    if parsed.scheme != "ws":
        raise AhpBackendError(
            "same-machine AHP requires a ws:// loopback endpoint"
        )
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise AhpBackendError("AHP endpoint must not contain credentials or query data")
    host = (parsed.hostname or "").casefold()
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise AhpBackendError(
            "same-machine AHP endpoint must use localhost or a loopback address"
        )
    if parsed.port is None:
        raise AhpBackendError("AHP endpoint must include an explicit port")
    return endpoint_url


def _normalize_path(value: str) -> str:
    raw = str(value or "").strip()
    if raw.startswith("file:"):
        parsed = urlparse(raw)
        raw = unquote(parsed.path)
        if os.name == "nt" and len(raw) >= 3 and raw[0] == "/" and raw[2] == ":":
            raw = raw[1:]
    if raw.startswith("\\\\?\\"):
        raw = raw[4:]
    return os.path.normcase(os.path.normpath(raw))


def _summary_session_id(item: dict[str, object]) -> str:
    for key in ("sessionId", "session_id", "id"):
        value = item.get(key)
        if value:
            return str(value)
    resource = str(
        item.get("resource")
        or item.get("uri")
        or item.get("channel")
        or ""
    )
    return resource.rsplit("/", 1)[-1] if "/" in resource else resource


def _summary_working_directory(item: dict[str, object]) -> str:
    for key in ("workingDirectory", "working_directory", "cwd"):
        value = item.get(key)
        if value:
            return str(value)
    for key in ("workingDirectories", "working_directories"):
        value = item.get(key)
        if isinstance(value, list) and value:
            return str(value[0])
    return ""


def account_for_backend(config: Config) -> str:
    """Resolve the explicit repository-scoped GitHub account for AHP."""
    configured = config.session_backend.github_account
    if configured:
        return configured
    account = repos.resolve_account(repos.find_repo(config.repo_name))
    if not account:
        raise AhpBackendError(
            "session_backend.github_account is required when the active "
            "repository has no configured GitHub account"
        )
    return account


class AhpController:
    """Minimal JSON-RPC controller over an AHP WebSocket connection."""

    def __init__(
        self,
        backend: SessionBackendConfig,
        token: str,
        *,
        client_id: str | None = None,
    ) -> None:
        self.backend = backend
        self.endpoint_url = _loopback_endpoint(backend.endpoint_url)
        self.token = token
        self.client_id = client_id or f"agent-worktrees-{uuid.uuid4()}"
        self.protocol_version = ""
        self._next_id = 1
        self._socket = None

    def __enter__(self) -> AhpController:
        try:
            import websocket
        except ImportError as exc:  # pragma: no cover - install contract
            raise AhpBackendError(
                "websocket-client is required for the AHP session backend"
            ) from exc
        try:
            self._socket = websocket.create_connection(
                self.endpoint_url,
                timeout=self.backend.connect_timeout_seconds,
                http_proxy_host=None,
                http_proxy_port=None,
                http_no_proxy=["localhost", "127.0.0.1", "::1"],
            )
        except Exception as exc:
            raise AhpBackendError(
                f"could not connect to AHP endpoint {self.endpoint_url}: {exc}"
            ) from exc
        initialized, _ = self._request(
            "initialize",
            {
                "channel": ROOT_CHANNEL,
                "protocolVersions": list(self.backend.protocol_versions),
                "clientId": self.client_id,
                "initialSubscriptions": [ROOT_CHANNEL],
            },
        )
        protocol = str(initialized.get("protocolVersion", ""))
        if protocol not in self.backend.protocol_versions:
            raise AhpBackendError(
                f"AHP host selected unsupported protocol {protocol or '<none>'}"
            )
        self.protocol_version = protocol
        self._request(
            "authenticate",
            {
                "channel": ROOT_CHANNEL,
                "resource": self.backend.auth_resource,
                "token": self.token,
            },
        )
        return self

    def __exit__(self, *_args: object) -> None:
        if self._socket is not None:
            try:
                self._socket.close()
            except Exception:
                pass
            self._socket = None

    def _request(
        self,
        method: str,
        params: dict[str, object],
        *,
        timeout_seconds: float | None = None,
    ) -> tuple[dict[str, object], list[dict[str, object]]]:
        if self._socket is None:
            raise AhpBackendError("AHP controller is not connected")
        request_id = self._next_id
        self._next_id += 1
        self._socket.send(json.dumps({
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }))
        deadline = monotonic() + (
            timeout_seconds
            if timeout_seconds is not None
            else self.backend.connect_timeout_seconds
        )
        notifications: list[dict[str, object]] = []
        while monotonic() < deadline:
            self._socket.settimeout(max(0.1, deadline - monotonic()))
            try:
                raw = self._socket.recv()
            except Exception as exc:
                raise AhpBackendError(
                    f"AHP {method} did not complete: {exc}"
                ) from exc
            if not raw:
                raise AhpBackendError(f"AHP connection closed during {method}")
            try:
                message = json.loads(
                    raw.decode("utf-8") if isinstance(raw, bytes) else raw
                )
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(message, dict):
                continue
            if message.get("id") != request_id:
                notifications.append(message)
                continue
            error = message.get("error")
            if isinstance(error, dict):
                code = error.get("code", "unknown")
                detail = error.get("message", "request failed")
                raise AhpBackendError(f"AHP {method} failed ({code}): {detail}")
            result = message.get("result")
            return (
                result if isinstance(result, dict) else {},
                notifications,
            )
        raise AhpBackendError(f"AHP {method} timed out")

    def list_sessions(self) -> list[dict[str, object]]:
        sessions: list[dict[str, object]] = []
        cursor = ""
        seen_cursors: set[str] = set()
        for _page in range(MAX_SESSION_CATALOG_PAGES):
            params: dict[str, object] = {"channel": ROOT_CHANNEL}
            if cursor:
                params["cursor"] = cursor
            result, _ = self._request("listSessions", params)
            items = result.get("items", [])
            if isinstance(items, list):
                sessions.extend(
                    item for item in items if isinstance(item, dict)
                )
            next_cursor = result.get("nextCursor")
            if next_cursor in (None, ""):
                return sessions
            if not isinstance(next_cursor, str):
                raise AhpBackendError(
                    "AHP listSessions returned an invalid nextCursor"
                )
            if next_cursor in seen_cursors:
                raise AhpBackendError(
                    "AHP listSessions repeated a pagination cursor"
                )
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        raise AhpBackendError("AHP session catalog exceeded the pagination limit")

    def _session_summary(self, session_id: str) -> dict[str, object] | None:
        return next(
            (
                item for item in self.list_sessions()
                if _summary_session_id(item) == session_id
            ),
            None,
        )

    def create_session(self, working_directory: str) -> AhpSession:
        working_directory_uri = Path(working_directory).resolve().as_uri()
        lifecycle_timeout = max(
            self.backend.connect_timeout_seconds,
            SESSION_LIFECYCLE_TIMEOUT_SECONDS,
        )
        for attempt in range(2):
            session_id = str(uuid.uuid4())
            session_uri = f"ahp-session:/{session_id}"
            try:
                self._request(
                    "createSession",
                    {
                        "channel": session_uri,
                        "workingDirectories": [working_directory_uri],
                        "config": {"mode": "interactive", "target": "workspace"},
                    },
                    timeout_seconds=lifecycle_timeout,
                )
                break
            except AhpBackendError as exc:
                if attempt == 0 and SESSION_OWNER_TIMEOUT_MESSAGE in str(exc):
                    continue
                raise
        summary = self._session_summary(session_id)
        if summary is None:
            raise AhpBackendError(
                "AHP host created a session but did not list the requested id"
            )
        actual = _summary_working_directory(summary)
        if not actual or _normalize_path(actual) != _normalize_path(
            working_directory
        ):
            raise AhpBackendError(
                "AHP session working directory did not match the worktree"
            )
        return AhpSession(
            session_id=session_id,
            endpoint_url=self.endpoint_url,
            protocol_version=self.protocol_version,
            github_account="",
            working_directory=working_directory,
        )

    def require_session(
        self,
        session_id: str,
        working_directory: str,
    ) -> None:
        summary = self._session_summary(session_id)
        if summary is None:
            raise AhpBackendError(
                f"AHP session {session_id} is not present on the configured host"
            )
        actual = _summary_working_directory(summary)
        if not actual or _normalize_path(actual) != _normalize_path(
            working_directory
        ):
            raise AhpBackendError(
                "AHP session exists but is bound to a different working directory"
            )

    def dispose_session(self, session_id: str) -> bool:
        if self._session_summary(session_id) is None:
            return False
        self._request(
            "disposeSession",
            {"channel": f"ahp-session:/{session_id}"},
            timeout_seconds=max(
                self.backend.connect_timeout_seconds,
                SESSION_LIFECYCLE_TIMEOUT_SECONDS,
            ),
        )
        return True


def connect_controller(config: Config) -> tuple[AhpController, str]:
    """Create an authenticated controller for the configured AHP backend."""
    if not config.session_backend.is_ahp:
        raise AhpBackendError("the active project is not configured for AHP")
    account = account_for_backend(config)
    token = os.environ.get(AUTH_TOKEN_ENV, "").strip()
    if not token:
        token = git_ops.gh_token_for_account(account)
    if not token:
        raise AhpBackendError(
            f"could not mint a GitHub token for configured account {account}"
        )
    return AhpController(config.session_backend, token), account


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_worktree_session(
    config: Config,
    record,
):
    """Create or verify the AHP session bound to one worktree record."""
    from .tracking import SessionBackendBinding

    controller, account = connect_controller(config)
    existing = record.session_backend
    now = _now_iso()
    with controller:
        if existing is not None:
            if existing.kind != "ahp":
                raise AhpBackendError(
                    f"unsupported persisted session backend {existing.kind}"
                )
            if existing.endpoint_url != controller.endpoint_url:
                raise AhpBackendError(
                    "persisted AHP endpoint does not match current configuration"
                )
            if existing.auth_account != account:
                raise AhpBackendError(
                    "persisted AHP account does not match current configuration"
                )
            if existing.state == "disposed":
                session = controller.create_session(record.worktree_path)
                return SessionBackendBinding(
                    kind="ahp",
                    endpoint_url=session.endpoint_url,
                    session_id=session.session_id,
                    protocol_version=session.protocol_version,
                    auth_account=account,
                    created_at=now,
                    last_seen_at=now,
                    state="active",
                    binding_revision=existing.binding_revision + 1,
                )
            controller.require_session(
                existing.session_id,
                record.worktree_path,
            )
            existing.protocol_version = controller.protocol_version
            existing.last_seen_at = now
            existing.state = "active"
            existing.binding_revision += 1
            return existing

        session = controller.create_session(record.worktree_path)
        return SessionBackendBinding(
            kind="ahp",
            endpoint_url=session.endpoint_url,
            session_id=session.session_id,
            protocol_version=session.protocol_version,
            auth_account=account,
            created_at=now,
            last_seen_at=now,
            state="active",
            binding_revision=1,
        )


def dispose_worktree_session(config: Config, record) -> bool:
    """Dispose a bound AHP session and mark the binding terminal."""
    existing = record.session_backend
    if existing is None:
        return False
    if existing.state == "disposed":
        return False
    controller, account = connect_controller(config)
    if existing.endpoint_url != controller.endpoint_url:
        raise AhpBackendError(
            "persisted AHP endpoint does not match current configuration"
        )
    if existing.auth_account != account:
        raise AhpBackendError(
            "persisted AHP account does not match current configuration"
        )
    with controller:
        controller.dispose_session(existing.session_id)
        existing.protocol_version = controller.protocol_version
    existing.state = "disposed"
    existing.last_seen_at = _now_iso()
    existing.binding_revision += 1
    return True
