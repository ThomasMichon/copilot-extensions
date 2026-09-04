from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_worktrees import ahp_backend
from agent_worktrees.config import SessionBackendConfig


class FakeSocket:
    def __init__(self) -> None:
        self.responses: list[str] = []
        self.sessions: list[dict[str, object]] = []
        self.sent: list[dict[str, object]] = []
        self.create_timeouts = 0
        self.page_size = 0

    def send(self, raw: str) -> None:
        request = json.loads(raw)
        self.sent.append(request)
        method = request["method"]
        if method == "initialize":
            result = {"protocolVersion": "0.7.0"}
        elif method == "authenticate":
            result = {}
        elif method == "listSessions":
            cursor = int(request["params"].get("cursor", "0"))
            if self.page_size:
                items = self.sessions[cursor:cursor + self.page_size]
                next_offset = cursor + len(items)
                result = {"items": list(items)}
                if next_offset < len(self.sessions):
                    result["nextCursor"] = str(next_offset)
            else:
                result = {"items": list(self.sessions)}
        elif method == "createSession":
            params = request["params"]
            if self.create_timeouts:
                self.create_timeouts -= 1
                self.responses.append(json.dumps({
                    "jsonrpc": "2.0",
                    "id": request["id"],
                    "error": {
                        "code": -32603,
                        "message": ahp_backend.SESSION_OWNER_TIMEOUT_MESSAGE,
                    },
                }))
                return
            session_id = params["channel"].rsplit("/", 1)[-1]
            self.sessions.append({
                "resource": params["channel"],
                "workingDirectories": params["workingDirectories"],
            })
            result = {}
            assert session_id
        elif method == "disposeSession":
            session_id = request["params"]["channel"].rsplit("/", 1)[-1]
            self.sessions = [
                item for item in self.sessions
                if ahp_backend._summary_session_id(item) != session_id
            ]
            result = {}
        else:  # pragma: no cover - defensive
            raise AssertionError(method)
        self.responses.append(json.dumps({
            "jsonrpc": "2.0",
            "id": request["id"],
            "result": result,
        }))

    def recv(self) -> str:
        return self.responses.pop(0)

    def settimeout(self, _value: float) -> None:
        pass

    def close(self) -> None:
        pass


def backend(**overrides) -> SessionBackendConfig:
    values = {
        "kind": "ahp",
        "endpoint_url": "ws://127.0.0.1:8765",
        "github_account": "octocat",
        "protocol_versions": ("0.7.0",),
        "auth_resource": "https://api.github.com",
        "connect_timeout_seconds": 5,
    }
    values.update(overrides)
    return SessionBackendConfig(**values)


def install_fake_websocket(monkeypatch, socket: FakeSocket) -> None:
    module = SimpleNamespace(create_connection=lambda *_args, **_kwargs: socket)
    monkeypatch.setitem(sys.modules, "websocket", module)


def test_controller_bypasses_proxies_for_loopback(monkeypatch):
    socket = FakeSocket()
    captured = {}
    module = SimpleNamespace(
        create_connection=lambda *_args, **kwargs: (
            captured.update(kwargs) or socket
        )
    )
    monkeypatch.setitem(sys.modules, "websocket", module)

    with ahp_backend.AhpController(backend(), "token"):
        pass

    assert captured["http_proxy_host"] is None
    assert captured["http_proxy_port"] is None
    assert captured["http_no_proxy"] == ["localhost", "127.0.0.1", "::1"]


def test_create_session_uses_exact_workspace(monkeypatch, tmp_path: Path):
    socket = FakeSocket()
    install_fake_websocket(monkeypatch, socket)
    worktree = tmp_path / "worktree with space"
    worktree.mkdir()

    with ahp_backend.AhpController(backend(), "token") as controller:
        session = controller.create_session(str(worktree))

    create = next(
        request for request in socket.sent
        if request["method"] == "createSession"
    )
    assert create["params"]["config"] == {
        "mode": "interactive",
        "target": "workspace",
    }
    assert create["params"]["workingDirectories"] == [worktree.resolve().as_uri()]
    assert create["params"]["channel"] == f"ahp-session:/{session.session_id}"
    assert session.working_directory == str(worktree)


def test_create_session_retries_owner_startup_timeout(monkeypatch, tmp_path: Path):
    socket = FakeSocket()
    socket.create_timeouts = 1
    install_fake_websocket(monkeypatch, socket)

    with ahp_backend.AhpController(backend(), "token") as controller:
        session = controller.create_session(str(tmp_path))

    creates = [
        request for request in socket.sent
        if request["method"] == "createSession"
    ]
    assert len(creates) == 2
    assert creates[0]["params"]["channel"] != creates[1]["params"]["channel"]
    assert creates[1]["params"]["channel"] == (
        f"ahp-session:/{session.session_id}"
    )


def test_summary_working_directory_accepts_legacy_scalar():
    assert ahp_backend._summary_working_directory({
        "workingDirectory": "file:///repo",
    }) == "file:///repo"


def test_require_session_rejects_wrong_worktree(monkeypatch, tmp_path: Path):
    socket = FakeSocket()
    socket.sessions.append({
        "resource": "ahp-session:/11111111-1111-1111-1111-111111111111",
        "workingDirectory": (tmp_path / "other").resolve().as_uri(),
    })
    install_fake_websocket(monkeypatch, socket)

    with ahp_backend.AhpController(backend(), "token") as controller:
        with pytest.raises(
            ahp_backend.AhpBackendError,
            match="different working directory",
        ):
            controller.require_session(
                "11111111-1111-1111-1111-111111111111",
                str(tmp_path / "expected"),
            )


def test_list_sessions_follows_pagination(monkeypatch, tmp_path: Path):
    socket = FakeSocket()
    socket.page_size = 1
    expected = "33333333-3333-3333-3333-333333333333"
    socket.sessions = [
        {
            "resource": f"ahp-session:/{index}{index}",
            "workingDirectories": [(tmp_path / str(index)).resolve().as_uri()],
        }
        for index in ("11", "22")
    ] + [{
        "resource": f"ahp-session:/{expected}",
        "workingDirectories": [tmp_path.resolve().as_uri()],
    }]
    install_fake_websocket(monkeypatch, socket)

    with ahp_backend.AhpController(backend(), "token") as controller:
        controller.require_session(expected, str(tmp_path))

    list_requests = [
        request for request in socket.sent
        if request["method"] == "listSessions"
    ]
    assert [request["params"].get("cursor") for request in list_requests] == [
        None,
        "1",
        "2",
    ]


def test_dispose_session_is_idempotent_when_host_entry_is_absent(
    monkeypatch,
):
    socket = FakeSocket()
    install_fake_websocket(monkeypatch, socket)

    with ahp_backend.AhpController(backend(), "token") as controller:
        assert controller.dispose_session(
            "11111111-1111-1111-1111-111111111111"
        ) is False

    assert not any(
        request["method"] == "disposeSession" for request in socket.sent
    )


def test_disposed_binding_gets_a_fresh_hosted_session(monkeypatch, tmp_path):
    class FakeController:
        endpoint_url = "ws://127.0.0.1:8765"
        protocol_version = "0.7.0"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def create_session(self, working_directory):
            return ahp_backend.AhpSession(
                session_id="22222222-2222-2222-2222-222222222222",
                endpoint_url=self.endpoint_url,
                protocol_version=self.protocol_version,
                github_account="octocat",
                working_directory=working_directory,
            )

    controller = FakeController()
    existing = SimpleNamespace(
        kind="ahp",
        endpoint_url=controller.endpoint_url,
        session_id="11111111-1111-1111-1111-111111111111",
        protocol_version="0.7.0",
        auth_account="octocat",
        created_at="2026-09-03T00:00:00+00:00",
        last_seen_at="2026-09-03T00:00:01+00:00",
        state="disposed",
        binding_revision=4,
    )
    record = SimpleNamespace(
        worktree_path=str(tmp_path),
        session_backend=existing,
    )
    monkeypatch.setattr(
        ahp_backend,
        "connect_controller",
        lambda _config: (controller, "octocat"),
    )

    binding = ahp_backend.ensure_worktree_session(
        SimpleNamespace(session_backend=backend()),
        record,
    )

    assert binding.session_id == "22222222-2222-2222-2222-222222222222"
    assert binding.state == "active"
    assert binding.binding_revision == 5


def test_dispose_marks_binding_terminal_when_host_already_lost_it(
    monkeypatch,
    tmp_path,
):
    class FakeController:
        endpoint_url = "ws://127.0.0.1:8765"
        protocol_version = "0.7.0"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def dispose_session(self, _session_id):
            return False

    existing = SimpleNamespace(
        endpoint_url=FakeController.endpoint_url,
        session_id="11111111-1111-1111-1111-111111111111",
        protocol_version="0.7.0",
        auth_account="octocat",
        last_seen_at="2026-09-03T00:00:01+00:00",
        state="active",
        binding_revision=1,
    )
    record = SimpleNamespace(
        worktree_path=str(tmp_path),
        session_backend=existing,
    )
    monkeypatch.setattr(
        ahp_backend,
        "connect_controller",
        lambda _config: (FakeController(), "octocat"),
    )

    assert ahp_backend.dispose_worktree_session(
        SimpleNamespace(session_backend=backend()),
        record,
    ) is True
    assert existing.state == "disposed"
    assert existing.binding_revision == 2


def test_connect_controller_prefers_launcher_token(monkeypatch):
    config = SimpleNamespace(session_backend=backend())
    monkeypatch.setenv(ahp_backend.AUTH_TOKEN_ENV, "launcher-token")
    monkeypatch.setattr(
        ahp_backend.git_ops,
        "gh_token_for_account",
        lambda _account: pytest.fail("must not mint a second token"),
    )

    controller, account = ahp_backend.connect_controller(config)

    assert account == "octocat"
    assert controller.token == "launcher-token"


@pytest.mark.parametrize(
    "endpoint",
    [
        "ws://example.com:8765",
        "wss://127.0.0.1:8765",
        "ws://127.0.0.1",
        "ws://user@127.0.0.1:8765",
    ],
)
def test_same_machine_endpoint_rejects_unsafe_values(endpoint: str):
    with pytest.raises(ahp_backend.AhpBackendError):
        ahp_backend._loopback_endpoint(endpoint)


def test_protocol_skew_fails_closed(monkeypatch):
    socket = FakeSocket()
    original_send = socket.send

    def send(raw: str) -> None:
        request = json.loads(raw)
        if request["method"] == "initialize":
            socket.sent.append(request)
            socket.responses.append(json.dumps({
                "jsonrpc": "2.0",
                "id": request["id"],
                "result": {"protocolVersion": "0.8.0"},
            }))
        else:
            original_send(raw)

    socket.send = send  # type: ignore[method-assign]
    install_fake_websocket(monkeypatch, socket)
    with pytest.raises(ahp_backend.AhpBackendError, match="unsupported protocol"):
        with ahp_backend.AhpController(backend(), "token"):
            pass
