"""Manager-owned same-machine Agent Host Protocol provider."""

from __future__ import annotations

import json
import os
import platform
import threading
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from urllib.parse import unquote, urlparse

from . import engine_client
from .engine_client import LaunchPlan
from .manager_config import AhpConfig, load_config

ROOT_CHANNEL = "ahp-root://"
SESSION_OWNER_TIMEOUT_MESSAGE = (
    "Timed out waiting for this session's Copilot CLI to answer."
)
MAX_SESSION_CATALOG_PAGES = 10_000
RESERVATION_LEASE_SECONDS = 300


class AhpProviderError(RuntimeError):
    """The configured AHP provider could not safely serve the launch."""


class AhpSessionMissingError(AhpProviderError):
    """The configured host confirmed that an exact AHP session is absent."""


@dataclass(frozen=True)
class AhpAttachment:
    endpoint_url: str
    session_id: str
    protocol_version: str
    account: str
    token: str


def _loopback_endpoint(endpoint_url: str) -> str:
    parsed = urlparse(endpoint_url)
    if parsed.scheme != "ws":
        raise AhpProviderError("same-machine AHP requires a ws:// loopback endpoint")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise AhpProviderError("AHP endpoint must not contain credentials or query data")
    if (parsed.hostname or "").casefold() not in {"127.0.0.1", "localhost", "::1"}:
        raise AhpProviderError(
            "same-machine AHP endpoint must use localhost or a loopback address"
        )
    if parsed.port is None:
        raise AhpProviderError("AHP endpoint must include an explicit port")
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
        if item.get(key):
            return str(item[key])
    resource = str(item.get("resource") or item.get("uri") or item.get("channel") or "")
    return resource.rsplit("/", 1)[-1] if "/" in resource else resource


def _summary_working_directory(item: dict[str, object]) -> str:
    for key in ("workingDirectory", "working_directory", "cwd"):
        if item.get(key):
            return str(item[key])
    for key in ("workingDirectories", "working_directories"):
        value = item.get(key)
        if isinstance(value, list) and value:
            return str(value[0])
    return ""


class AhpController:
    def __init__(
        self,
        config: AhpConfig,
        token: str,
        *,
        client_id: str | None = None,
    ) -> None:
        self.config = config
        self.endpoint_url = _loopback_endpoint(config.endpoint_url)
        self.token = token
        self.client_id = client_id or f"worktree-manager-{uuid.uuid4()}"
        self.protocol_version = ""
        self._next_id = 1
        self._socket = None

    def __enter__(self):
        try:
            import websocket
        except ImportError as exc:  # pragma: no cover - install contract
            raise AhpProviderError(
                "websocket-client is required for the AHP provider"
            ) from exc
        try:
            self._socket = websocket.create_connection(
                self.endpoint_url,
                timeout=self.config.connect_timeout_seconds,
                http_proxy_host=None,
                http_proxy_port=None,
                http_no_proxy=["localhost", "127.0.0.1", "::1"],
            )
        except Exception as exc:
            raise AhpProviderError(
                f"could not connect to AHP endpoint {self.endpoint_url}: {exc}"
            ) from exc
        initialized, _ = self._request(
            "initialize",
            {
                "channel": ROOT_CHANNEL,
                "protocolVersions": list(self.config.protocol_versions),
                "clientId": self.client_id,
                "initialSubscriptions": [ROOT_CHANNEL],
            },
        )
        protocol = str(initialized.get("protocolVersion", ""))
        if protocol not in self.config.protocol_versions:
            raise AhpProviderError(
                f"AHP host selected unsupported protocol {protocol or '<none>'}"
            )
        self.protocol_version = protocol
        self._request(
            "authenticate",
            {
                "channel": ROOT_CHANNEL,
                "resource": self.config.auth_resource,
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
            raise AhpProviderError("AHP controller is not connected")
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
            else self.config.connect_timeout_seconds
        )
        notifications: list[dict[str, object]] = []
        while monotonic() < deadline:
            self._socket.settimeout(max(0.1, deadline - monotonic()))
            try:
                raw = self._socket.recv()
            except Exception as exc:
                raise AhpProviderError(f"AHP {method} did not complete: {exc}") from exc
            if not raw:
                raise AhpProviderError(f"AHP connection closed during {method}")
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
                raise AhpProviderError(
                    f"AHP {method} failed "
                    f"({error.get('code', 'unknown')}): "
                    f"{error.get('message', 'request failed')}"
                )
            result = message.get("result")
            return result if isinstance(result, dict) else {}, notifications
        raise AhpProviderError(f"AHP {method} timed out")

    def list_sessions(self) -> list[dict[str, object]]:
        sessions: list[dict[str, object]] = []
        cursor = ""
        seen: set[str] = set()
        for _page in range(MAX_SESSION_CATALOG_PAGES):
            params: dict[str, object] = {"channel": ROOT_CHANNEL}
            if cursor:
                params["cursor"] = cursor
            result, _ = self._request("listSessions", params)
            items = result.get("items", [])
            if isinstance(items, list):
                sessions.extend(item for item in items if isinstance(item, dict))
            next_cursor = result.get("nextCursor")
            if next_cursor in (None, ""):
                return sessions
            if not isinstance(next_cursor, str):
                raise AhpProviderError("AHP listSessions returned an invalid nextCursor")
            if next_cursor in seen:
                raise AhpProviderError("AHP listSessions repeated a pagination cursor")
            seen.add(next_cursor)
            cursor = next_cursor
        raise AhpProviderError("AHP session catalog exceeded the pagination limit")

    def _session_summary(self, session_id: str) -> dict[str, object] | None:
        return next(
            (
                item
                for item in self.list_sessions()
                if _summary_session_id(item) == session_id
            ),
            None,
        )

    def create_session(self, work_dir: str) -> str:
        worktree_uri = Path(work_dir).resolve().as_uri()
        timeout = max(
            self.config.connect_timeout_seconds,
            self.config.lifecycle_timeout_seconds,
        )
        for attempt in range(2):
            session_id = str(uuid.uuid4())
            try:
                self._request(
                    "createSession",
                    {
                        "channel": f"ahp-session:/{session_id}",
                        "workingDirectories": [worktree_uri],
                        "config": {"mode": "interactive", "target": "workspace"},
                    },
                    timeout_seconds=timeout,
                )
                break
            except AhpProviderError as exc:
                if attempt == 0 and SESSION_OWNER_TIMEOUT_MESSAGE in str(exc):
                    continue
                raise
        self.require_session(session_id, work_dir)
        return session_id

    def require_session(self, session_id: str, work_dir: str) -> None:
        summary = self._session_summary(session_id)
        if summary is None:
            raise AhpSessionMissingError(
                f"AHP session {session_id} is not present on the configured host"
            )
        actual = _summary_working_directory(summary)
        if not actual or _normalize_path(actual) != _normalize_path(work_dir):
            raise AhpProviderError(
                "AHP session exists but is bound to a different working directory"
            )

    def dispose_session(self, session_id: str) -> bool:
        if self._session_summary(session_id) is None:
            return False
        self._request(
            "disposeSession",
            {"channel": f"ahp-session:/{session_id}"},
            timeout_seconds=max(
                self.config.connect_timeout_seconds,
                self.config.lifecycle_timeout_seconds,
            ),
        )
        return True


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _reservation_owner(operation: str) -> str:
    return f"worktree-manager:{operation}:{os.getpid()}:{uuid.uuid4()}"


def _process_start_time(pid: int) -> str | None:
    if pid <= 0:
        return None
    if platform.system() == "Windows":
        import ctypes
        from ctypes import wintypes

        query = 0x1000
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.GetProcessTimes.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(query, False, pid)
        if not handle:
            return None
        try:
            creation = wintypes.FILETIME()
            exit_time = wintypes.FILETIME()
            kernel = wintypes.FILETIME()
            user = wintypes.FILETIME()
            if not kernel32.GetProcessTimes(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel),
                ctypes.byref(user),
            ):
                return None
            return str((creation.dwHighDateTime << 32) | creation.dwLowDateTime)
        finally:
            kernel32.CloseHandle(handle)
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return None
    rest = stat[stat.rfind(")") + 1 :].split()
    return rest[19] if len(rest) >= 20 and rest[19].isdigit() else None


class _ReservationHeartbeat:
    def __init__(
        self,
        project: str,
        worktree_id: str,
        reservation_token: str,
        *,
        lease_seconds: int = RESERVATION_LEASE_SECONDS,
        interval_seconds: float | None = None,
    ) -> None:
        self.project = project
        self.worktree_id = worktree_id
        self.reservation_token = reservation_token
        self.lease_seconds = lease_seconds
        self.interval_seconds = (
            interval_seconds
            if interval_seconds is not None
            else max(1.0, lease_seconds / 3)
        )
        self._stop = threading.Event()
        self._error: Exception | None = None
        self._thread = threading.Thread(
            target=self._run,
            name=f"ahp-reservation-{worktree_id[-8:]}",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                engine_client.execution_leg_renew(
                    self.project,
                    self.worktree_id,
                    reservation_token=self.reservation_token,
                    lease_seconds=self.lease_seconds,
                )
            except Exception as error:
                self._error = error
                self._stop.set()
                return

    def stop(self) -> None:
        self._stop.set()
        self._thread.join()

    def raise_if_failed(self) -> None:
        if self._error is not None:
            raise AhpProviderError(
                f"execution-leg reservation renewal failed: {self._error}"
            ) from self._error


def _configured(project: str) -> tuple[AhpConfig, str, str]:
    config = load_config().ahp
    if not config.endpoint_url:
        raise AhpProviderError(
            "AHP was selected but ahp.endpoint_url is not configured"
        )
    account = engine_client.repository_account(project)
    if not account:
        raise AhpProviderError(
            "AHP requires a repository-scoped account from agent-worktrees"
        )
    if config.account and config.account != account:
        raise AhpProviderError(
            "configured AHP account does not match the repository account"
        )
    token = engine_client.repository_token(project, account)
    if not token:
        raise AhpProviderError(
            f"could not mint a token for repository account {account}"
        )
    return config, account, token


def ensure_session(
    project: str,
    worktree_id: str,
    work_dir: str,
    *,
    controller_type=AhpController,
) -> AhpAttachment:
    """Create or verify the exact worktree's AHP session, then persist it."""
    if not worktree_id or not work_dir:
        raise AhpProviderError("AHP requires an agent-worktrees-created worktree")
    config, account, token = _configured(project)
    reservation = engine_client.execution_leg_reserve(
        project,
        worktree_id,
        provider="ahp",
        operation="ensure",
        owner=_reservation_owner("ensure"),
        owner_pid=os.getpid(),
        owner_start_time=_process_start_time(os.getpid()),
        lease_seconds=RESERVATION_LEASE_SECONDS,
    )
    reservation_token = str(reservation.get("reservation_token") or "")
    reserved = reservation.get("execution_leg")
    current = reservation.get("previous_execution_leg")
    if not reservation_token:
        raise AhpProviderError("agent-worktrees returned an invalid reservation")
    try:
        if not isinstance(reserved, dict):
            raise AhpProviderError("agent-worktrees returned an invalid reservation")
        if current is not None and not isinstance(current, dict):
            raise AhpProviderError(
                "agent-worktrees returned an invalid execution leg"
            )
        revision = int(reserved.get("binding_revision") or 0)
        state = str((current or {}).get("state") or "disposed")
        blob = (current or {}).get("blob") or {}
        if not isinstance(blob, dict):
            raise AhpProviderError("persisted AHP execution-leg blob is invalid")
    except Exception:
        engine_client.execution_leg_release(
            project,
            worktree_id,
            reservation_token=reservation_token,
        )
        raise

    heartbeat = _ReservationHeartbeat(project, worktree_id, reservation_token)
    heartbeat.start()
    entered = False
    try:
        with controller_type(config, token) as controller:
            entered = True
            created = False
            try:
                if current and state != "disposed":
                    if blob.get("endpoint_url") != controller.endpoint_url:
                        raise AhpProviderError(
                            "persisted AHP endpoint does not match current configuration"
                        )
                    if blob.get("auth_account") != account:
                        raise AhpProviderError(
                            "persisted AHP account does not match current configuration"
                        )
                    session_id = str(blob.get("session_id") or "")
                    if not session_id:
                        raise AhpProviderError("persisted AHP session id is missing")
                    try:
                        controller.require_session(session_id, work_dir)
                        created_at = str(blob.get("created_at") or _now_iso())
                    except AhpSessionMissingError:
                        session_id = controller.create_session(work_dir)
                        created = True
                        created_at = _now_iso()
                else:
                    session_id = controller.create_session(work_dir)
                    created = True
                    created_at = _now_iso()
                now = _now_iso()
                next_blob = {
                    "endpoint_url": controller.endpoint_url,
                    "session_id": session_id,
                    "protocol_version": controller.protocol_version,
                    "auth_account": account,
                    "created_at": created_at,
                    "last_seen_at": now,
                }
                heartbeat.stop()
                heartbeat.raise_if_failed()
                engine_client.execution_leg_set(
                    project,
                    worktree_id,
                    provider="ahp",
                    state="active",
                    binding_revision=revision + 1,
                    blob=next_blob,
                    if_match_revision=revision,
                    reservation_token=reservation_token,
                )
            except Exception as error:
                heartbeat.stop()
                rollback_error = None
                if created:
                    try:
                        controller.dispose_session(session_id)
                    except Exception as exc:
                        rollback_error = exc
                if not created or rollback_error is None:
                    try:
                        engine_client.execution_leg_release(
                            project,
                            worktree_id,
                            reservation_token=reservation_token,
                        )
                    except Exception as exc:
                        rollback_error = rollback_error or exc
                if rollback_error is not None:
                    raise AhpProviderError(
                        f"{error}; AHP lifecycle rollback failed: {rollback_error}"
                    ) from error
                raise
    except Exception:
        heartbeat.stop()
        if not entered:
            engine_client.execution_leg_release(
                project,
                worktree_id,
                reservation_token=reservation_token,
            )
        raise
    return AhpAttachment(
        endpoint_url=config.endpoint_url,
        session_id=session_id,
        protocol_version=controller.protocol_version,
        account=account,
        token=token,
    )


def dispose_worktree_session(
    project: str,
    worktree_id: str,
    work_dir: str,
    *,
    controller_type=AhpController,
) -> bool:
    """Verify and dispose one persisted AHP leg, then fence it as terminal."""
    config, account, token = _configured(project)
    reservation = engine_client.execution_leg_reserve(
        project,
        worktree_id,
        provider="ahp",
        operation="dispose",
        owner=_reservation_owner("dispose"),
        owner_pid=os.getpid(),
        owner_start_time=_process_start_time(os.getpid()),
        lease_seconds=RESERVATION_LEASE_SECONDS,
    )
    reservation_token = str(reservation.get("reservation_token") or "")
    reserved = reservation.get("execution_leg")
    current = reservation.get("previous_execution_leg")
    if not reservation_token:
        raise AhpProviderError("agent-worktrees returned an invalid reservation")
    heartbeat = _ReservationHeartbeat(project, worktree_id, reservation_token)
    heartbeat.start()
    disposed = False
    try:
        if not isinstance(reserved, dict):
            raise AhpProviderError("agent-worktrees returned an invalid reservation")
        if current is None:
            heartbeat.stop()
            heartbeat.raise_if_failed()
            engine_client.execution_leg_release(
                project,
                worktree_id,
                reservation_token=reservation_token,
            )
            return False
        if not isinstance(current, dict) or current.get("provider") != "ahp":
            raise AhpProviderError("worktree is not owned by the AHP provider")
        revision = int(reserved.get("binding_revision") or 0)
        state = str(current.get("state") or "unknown")
        if state == "disposed":
            heartbeat.stop()
            heartbeat.raise_if_failed()
            engine_client.execution_leg_release(
                project,
                worktree_id,
                reservation_token=reservation_token,
            )
            return False
        blob = current.get("blob")
        if not isinstance(blob, dict):
            raise AhpProviderError("persisted AHP execution-leg blob is invalid")
        if blob.get("endpoint_url") != config.endpoint_url:
            raise AhpProviderError(
                "persisted AHP endpoint does not match current configuration"
            )
        if blob.get("auth_account") != account:
            raise AhpProviderError(
                "persisted AHP account does not match current configuration"
            )
        session_id = str(blob.get("session_id") or "")
        if not session_id:
            raise AhpProviderError("persisted AHP session id is missing")
        with controller_type(config, token) as controller:
            try:
                controller.require_session(session_id, work_dir)
            except AhpSessionMissingError:
                pass
            else:
                controller.dispose_session(session_id)
                disposed = True
            terminal_blob = dict(blob)
            terminal_blob["protocol_version"] = controller.protocol_version
            terminal_blob["last_seen_at"] = _now_iso()
            heartbeat.stop()
            heartbeat.raise_if_failed()
            engine_client.execution_leg_set(
                project,
                worktree_id,
                provider="ahp",
                state="disposed",
                binding_revision=revision + 1,
                blob=terminal_blob,
                if_match_revision=revision,
                reservation_token=reservation_token,
            )
    except Exception as error:
        heartbeat.stop()
        if not disposed:
            try:
                engine_client.execution_leg_release(
                    project,
                    worktree_id,
                    reservation_token=reservation_token,
                )
            except Exception as rollback_error:
                raise AhpProviderError(
                    f"{error}; AHP lifecycle rollback failed: {rollback_error}"
                ) from error
        raise
    return True


def attach_plan(plan: LaunchPlan, attachment: AhpAttachment) -> LaunchPlan:
    """Rewrite the Copilot child command for one verified AHP session."""
    command: list[str] = []
    skip_next = False
    for arg in plan.cmd:
        if skip_next:
            skip_next = False
            continue
        if arg == "--ahp":
            skip_next = True
            continue
        if arg.startswith("--ahp=") or arg.startswith("--resume="):
            continue
        command.append(arg)
    if "--experimental" not in command:
        command.append("--experimental")
    command += ["--ahp", attachment.endpoint_url, f"--resume={attachment.session_id}"]

    features = [
        value.strip()
        for value in str(
            plan.env.get("COPILOT_CLI_ENABLED_FEATURE_FLAGS")
            or os.environ.get("COPILOT_CLI_ENABLED_FEATURE_FLAGS")
            or ""
        ).split(",")
        if value.strip()
    ]
    if "AHP_CLIENT" not in features:
        features.append("AHP_CLIENT")
    env = dict(plan.env)
    env["COPILOT_CLI_ENABLED_FEATURE_FLAGS"] = ",".join(features)
    env["GH_TOKEN"] = attachment.token
    env.pop("GITHUB_TOKEN", None)
    return replace(plan, cmd=command, env=env, post_exit=False)
