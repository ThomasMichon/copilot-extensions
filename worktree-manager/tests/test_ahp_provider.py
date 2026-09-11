from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from worktree_manager import ahp_provider
from worktree_manager.engine_client import LaunchPlan
from worktree_manager.manager_config import AhpConfig, ManagerConfig


class FakeSocket:
    def __init__(self) -> None:
        self.responses: list[str] = []
        self.sessions: list[dict[str, object]] = []
        self.sent: list[dict[str, object]] = []

    def send(self, raw: str) -> None:
        request = json.loads(raw)
        self.sent.append(request)
        method = request["method"]
        if method == "initialize":
            result = {"protocolVersion": "0.7.0"}
        elif method == "authenticate":
            result = {}
        elif method == "listSessions":
            result = {"items": list(self.sessions)}
        elif method == "createSession":
            params = request["params"]
            self.sessions.append({
                "resource": params["channel"],
                "workingDirectories": params["workingDirectories"],
            })
            result = {}
        else:
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


def _config() -> AhpConfig:
    return AhpConfig(
        endpoint_url="ws://127.0.0.1:8765",
        account="example-user",
        connect_timeout_seconds=2,
        lifecycle_timeout_seconds=3,
    )


def _plan(tmp_path: Path) -> LaunchPlan:
    return LaunchPlan(
        action="exec",
        cmd=["copilot", "--resume=old", "--ahp", "ws://127.0.0.1:1"],
        work_dir=str(tmp_path),
        status_path=str(tmp_path),
        env={},
        worktree_id="host-win-20260909-abcd",
        post_exit=True,
        no_mux=True,
        exit_code=0,
        raw={},
    )


def test_controller_initializes_authenticates_and_targets_exact_workspace(
    monkeypatch,
    tmp_path,
):
    socket = FakeSocket()
    monkeypatch.setitem(
        sys.modules,
        "websocket",
        SimpleNamespace(create_connection=lambda *_args, **_kwargs: socket),
    )
    worktree = tmp_path / "worktree with space"
    worktree.mkdir()

    with ahp_provider.AhpController(_config(), "secret") as controller:
        session_id = controller.create_session(str(worktree))

    methods = [request["method"] for request in socket.sent]
    assert methods[:2] == ["initialize", "authenticate"]
    create = next(r for r in socket.sent if r["method"] == "createSession")
    assert create["params"]["config"]["target"] == "workspace"
    assert create["params"]["workingDirectories"] == [worktree.resolve().as_uri()]
    assert create["params"]["channel"] == f"ahp-session:/{session_id}"


def test_controller_rejects_session_bound_to_other_path(monkeypatch, tmp_path):
    socket = FakeSocket()
    socket.sessions.append({
        "resource": "ahp-session:/session-1",
        "workingDirectories": [(tmp_path / "other").resolve().as_uri()],
    })
    monkeypatch.setitem(
        sys.modules,
        "websocket",
        SimpleNamespace(create_connection=lambda *_args, **_kwargs: socket),
    )

    with ahp_provider.AhpController(_config(), "secret") as controller:
        with pytest.raises(ahp_provider.AhpProviderError, match="different"):
            controller.require_session("session-1", str(tmp_path / "expected"))


def test_ensure_session_uses_only_public_engine_boundary(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        ahp_provider,
        "load_config",
        lambda: ManagerConfig(ahp=_config()),
    )
    monkeypatch.setattr(
        ahp_provider.engine_client,
        "repository_account",
        lambda project: calls.append(("account", project)) or "example-user",
    )
    monkeypatch.setattr(
        ahp_provider.engine_client,
        "repository_token",
        lambda project, account: calls.append(("token", project, account)) or "secret",
    )
    monkeypatch.setattr(
        ahp_provider.engine_client,
        "execution_leg_reserve",
        lambda project, worktree_id, **kwargs: calls.append(
            ("reserve", project, worktree_id, kwargs)
        ) or {
            "reservation_token": "reservation-1",
            "execution_leg": {"binding_revision": 1},
            "previous_execution_leg": None,
        },
    )
    monkeypatch.setattr(
        ahp_provider.engine_client,
        "execution_leg_set",
        lambda project, worktree_id, **kwargs: calls.append(
            ("set", project, worktree_id, kwargs)
        ) or {},
    )

    class Controller:
        endpoint_url = "ws://127.0.0.1:8765"
        protocol_version = "0.7.0"

        def __init__(self, config, token):
            assert config == _config()
            assert token == "secret"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def create_session(self, work_dir):
            assert work_dir == str(tmp_path)
            return "session-1"

    attachment = ahp_provider.ensure_session(
        "example",
        "host-win-20260909-abcd",
        str(tmp_path),
        controller_type=Controller,
    )

    assert attachment.session_id == "session-1"
    set_call = next(call for call in calls if call[0] == "set")
    assert set_call[3]["provider"] == "ahp"
    assert set_call[3]["if_match_revision"] == 1
    assert set_call[3]["reservation_token"] == "reservation-1"
    assert set_call[3]["blob"]["auth_account"] == "example-user"


def test_ensure_session_reattaches_and_verifies_persisted_path(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        ahp_provider,
        "load_config",
        lambda: ManagerConfig(ahp=_config()),
    )
    monkeypatch.setattr(
        ahp_provider.engine_client,
        "repository_account",
        lambda _project: "example-user",
    )
    monkeypatch.setattr(
        ahp_provider.engine_client,
        "repository_token",
        lambda _project, _account: "secret",
    )
    monkeypatch.setattr(
        ahp_provider.engine_client,
        "execution_leg_reserve",
        lambda _project, _worktree_id, **_kwargs: {
            "reservation_token": "reservation-2",
            "execution_leg": {"binding_revision": 5},
            "previous_execution_leg": {
                "provider": "ahp",
                "state": "active",
                "binding_revision": 4,
                "blob": {
                    "endpoint_url": "ws://127.0.0.1:8765",
                    "session_id": "session-1",
                    "auth_account": "example-user",
                    "created_at": "2026-09-09T00:00:00+00:00",
                },
            }
        },
    )
    writes = []
    monkeypatch.setattr(
        ahp_provider.engine_client,
        "execution_leg_set",
        lambda *args, **kwargs: writes.append((args, kwargs)) or {},
    )

    class Controller:
        endpoint_url = "ws://127.0.0.1:8765"
        protocol_version = "0.7.0"

        def __init__(self, _config, _token):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def require_session(self, session_id, work_dir):
            assert session_id == "session-1"
            assert work_dir == str(tmp_path)

        def create_session(self, _work_dir):
            raise AssertionError("reattachment must not create a duplicate session")

    attachment = ahp_provider.ensure_session(
        "example",
        "host-win-20260909-abcd",
        str(tmp_path),
        controller_type=Controller,
    )

    assert attachment.session_id == "session-1"
    assert writes[0][1]["binding_revision"] == 6
    assert writes[0][1]["if_match_revision"] == 5
    assert writes[0][1]["reservation_token"] == "reservation-2"


def test_ensure_session_replaces_confirmed_missing_session(monkeypatch, tmp_path):
    monkeypatch.setattr(
        ahp_provider,
        "load_config",
        lambda: ManagerConfig(ahp=_config()),
    )
    monkeypatch.setattr(
        ahp_provider.engine_client,
        "repository_account",
        lambda _project: "example-user",
    )
    monkeypatch.setattr(
        ahp_provider.engine_client,
        "repository_token",
        lambda _project, _account: "secret",
    )
    monkeypatch.setattr(
        ahp_provider.engine_client,
        "execution_leg_reserve",
        lambda *_args, **_kwargs: {
            "reservation_token": "reservation-replace",
            "execution_leg": {"binding_revision": 5},
            "previous_execution_leg": {
                "provider": "ahp",
                "state": "active",
                "binding_revision": 4,
                "blob": {
                    "endpoint_url": "ws://127.0.0.1:8765",
                    "session_id": "session-missing",
                    "auth_account": "example-user",
                    "created_at": "2026-09-09T00:00:00+00:00",
                },
            },
        },
    )
    writes = []
    monkeypatch.setattr(
        ahp_provider.engine_client,
        "execution_leg_set",
        lambda *args, **kwargs: writes.append(kwargs) or {},
    )

    class Controller:
        endpoint_url = "ws://127.0.0.1:8765"
        protocol_version = "0.7.0"

        def __init__(self, _config, _token):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def require_session(self, session_id, _work_dir):
            assert session_id == "session-missing"
            raise ahp_provider.AhpSessionMissingError("confirmed absent")

        def create_session(self, work_dir):
            assert work_dir == str(tmp_path)
            return "session-replacement"

    attachment = ahp_provider.ensure_session(
        "example",
        "host-win-20260909-abcd",
        str(tmp_path),
        controller_type=Controller,
    )

    assert attachment.session_id == "session-replacement"
    assert writes[0]["blob"]["session_id"] == "session-replacement"
    assert writes[0]["state"] == "active"
    assert writes[0]["reservation_token"] == "reservation-replace"


def test_ensure_session_does_not_replace_unknown_transport_failure(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        ahp_provider,
        "load_config",
        lambda: ManagerConfig(ahp=_config()),
    )
    monkeypatch.setattr(
        ahp_provider.engine_client,
        "repository_account",
        lambda _project: "example-user",
    )
    monkeypatch.setattr(
        ahp_provider.engine_client,
        "repository_token",
        lambda _project, _account: "secret",
    )
    monkeypatch.setattr(
        ahp_provider.engine_client,
        "execution_leg_reserve",
        lambda *_args, **_kwargs: {
            "reservation_token": "reservation-transport",
            "execution_leg": {"binding_revision": 5},
            "previous_execution_leg": {
                "provider": "ahp",
                "state": "unknown",
                "binding_revision": 4,
                "blob": {
                    "endpoint_url": "ws://127.0.0.1:8765",
                    "session_id": "session-1",
                    "auth_account": "example-user",
                },
            },
        },
    )
    releases = []
    monkeypatch.setattr(
        ahp_provider.engine_client,
        "execution_leg_release",
        lambda *args, **kwargs: releases.append(kwargs) or {},
    )

    class Controller:
        endpoint_url = "ws://127.0.0.1:8765"
        protocol_version = "0.7.0"

        def __init__(self, _config, _token):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def require_session(self, _session_id, _work_dir):
            raise ahp_provider.AhpProviderError("transport unavailable")

        def create_session(self, _work_dir):
            raise AssertionError("unknown failure must not create a replacement")

    with pytest.raises(ahp_provider.AhpProviderError, match="transport unavailable"):
        ahp_provider.ensure_session(
            "example",
            "host-win-20260909-abcd",
            str(tmp_path),
            controller_type=Controller,
        )
    assert releases[0]["reservation_token"] == "reservation-transport"


def test_attach_plan_rewrites_identity_without_token_in_argv(tmp_path):
    attachment = ahp_provider.AhpAttachment(
        endpoint_url="ws://127.0.0.1:8765",
        session_id="session-1",
        protocol_version="0.7.0",
        account="example-user",
        token="secret",
    )

    plan = ahp_provider.attach_plan(_plan(tmp_path), attachment)

    assert plan.cmd == [
        "copilot",
        "--experimental",
        "--ahp",
        "ws://127.0.0.1:8765",
        "--resume=session-1",
    ]
    assert "secret" not in plan.cmd
    assert plan.env["GH_TOKEN"] == "secret"
    assert "AHP_CLIENT" in plan.env["COPILOT_CLI_ENABLED_FEATURE_FLAGS"]
    assert plan.post_exit is False


def test_dispose_verifies_path_and_persists_terminal_revision(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        ahp_provider,
        "load_config",
        lambda: ManagerConfig(ahp=_config()),
    )
    monkeypatch.setattr(
        ahp_provider.engine_client,
        "repository_account",
        lambda _project: "example-user",
    )
    monkeypatch.setattr(
        ahp_provider.engine_client,
        "repository_token",
        lambda _project, _account: "secret",
    )
    monkeypatch.setattr(
        ahp_provider.engine_client,
        "execution_leg_reserve",
        lambda _project, _worktree_id, **_kwargs: {
            "reservation_token": "reservation-3",
            "execution_leg": {"binding_revision": 8},
            "previous_execution_leg": {
                "provider": "ahp",
                "state": "active",
                "binding_revision": 7,
                "blob": {
                    "endpoint_url": "ws://127.0.0.1:8765",
                    "session_id": "session-1",
                    "auth_account": "example-user",
                },
            }
        },
    )
    writes = []
    monkeypatch.setattr(
        ahp_provider.engine_client,
        "execution_leg_set",
        lambda *args, **kwargs: writes.append(kwargs) or {},
    )

    class Controller:
        endpoint_url = "ws://127.0.0.1:8765"
        protocol_version = "0.7.0"

        def __init__(self, _config, _token):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def require_session(self, session_id, work_dir):
            assert (session_id, work_dir) == ("session-1", str(tmp_path))

        def dispose_session(self, session_id):
            assert session_id == "session-1"
            return True

    assert ahp_provider.dispose_worktree_session(
        "example",
        "host-win-20260909-abcd",
        str(tmp_path),
        controller_type=Controller,
    )
    assert writes[0]["state"] == "disposed"
    assert writes[0]["binding_revision"] == 9
    assert writes[0]["if_match_revision"] == 8
    assert writes[0]["reservation_token"] == "reservation-3"


def test_dispose_terminalizes_confirmed_missing_session(monkeypatch, tmp_path):
    monkeypatch.setattr(
        ahp_provider,
        "load_config",
        lambda: ManagerConfig(ahp=_config()),
    )
    monkeypatch.setattr(
        ahp_provider.engine_client,
        "repository_account",
        lambda _project: "example-user",
    )
    monkeypatch.setattr(
        ahp_provider.engine_client,
        "repository_token",
        lambda _project, _account: "secret",
    )
    monkeypatch.setattr(
        ahp_provider.engine_client,
        "execution_leg_reserve",
        lambda *_args, **_kwargs: {
            "reservation_token": "reservation-missing",
            "execution_leg": {"binding_revision": 8},
            "previous_execution_leg": {
                "provider": "ahp",
                "state": "active",
                "binding_revision": 7,
                "blob": {
                    "endpoint_url": "ws://127.0.0.1:8765",
                    "session_id": "session-missing",
                    "auth_account": "example-user",
                },
            },
        },
    )
    writes = []
    monkeypatch.setattr(
        ahp_provider.engine_client,
        "execution_leg_set",
        lambda *args, **kwargs: writes.append(kwargs) or {},
    )

    class Controller:
        endpoint_url = "ws://127.0.0.1:8765"
        protocol_version = "0.7.0"

        def __init__(self, _config, _token):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def require_session(self, _session_id, _work_dir):
            raise ahp_provider.AhpSessionMissingError("confirmed absent")

        def dispose_session(self, _session_id):
            raise AssertionError("already-absent session must not be disposed")

    assert ahp_provider.dispose_worktree_session(
        "example",
        "host-win-20260909-abcd",
        str(tmp_path),
        controller_type=Controller,
    )
    assert writes[0]["state"] == "disposed"
    assert writes[0]["reservation_token"] == "reservation-missing"


def test_new_session_is_disposed_and_reservation_released_when_publish_fails(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        ahp_provider,
        "load_config",
        lambda: ManagerConfig(ahp=_config()),
    )
    monkeypatch.setattr(
        ahp_provider.engine_client,
        "repository_account",
        lambda _project: "example-user",
    )
    monkeypatch.setattr(
        ahp_provider.engine_client,
        "repository_token",
        lambda _project, _account: "secret",
    )
    monkeypatch.setattr(
        ahp_provider.engine_client,
        "execution_leg_reserve",
        lambda *_args, **_kwargs: {
            "reservation_token": "reservation-4",
            "execution_leg": {"binding_revision": 1},
            "previous_execution_leg": None,
        },
    )
    monkeypatch.setattr(
        ahp_provider.engine_client,
        "execution_leg_set",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("publication lost")
        ),
    )
    releases = []
    monkeypatch.setattr(
        ahp_provider.engine_client,
        "execution_leg_release",
        lambda *args, **kwargs: releases.append((args, kwargs)) or {},
    )
    disposed = []

    class Controller:
        endpoint_url = "ws://127.0.0.1:8765"
        protocol_version = "0.7.0"

        def __init__(self, _config, _token):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def create_session(self, _work_dir):
            return "session-new"

        def dispose_session(self, session_id):
            disposed.append(session_id)
            return True

    with pytest.raises(RuntimeError, match="publication lost"):
        ahp_provider.ensure_session(
            "example",
            "host-win-20260909-abcd",
            str(tmp_path),
            controller_type=Controller,
        )

    assert disposed == ["session-new"]
    assert releases[0][1]["reservation_token"] == "reservation-4"


def test_ensure_releases_reservation_when_controller_cannot_connect(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        ahp_provider,
        "load_config",
        lambda: ManagerConfig(ahp=_config()),
    )
    monkeypatch.setattr(
        ahp_provider.engine_client,
        "repository_account",
        lambda _project: "example-user",
    )
    monkeypatch.setattr(
        ahp_provider.engine_client,
        "repository_token",
        lambda _project, _account: "secret",
    )
    monkeypatch.setattr(
        ahp_provider.engine_client,
        "execution_leg_reserve",
        lambda *_args, **_kwargs: {
            "reservation_token": "reservation-5",
            "execution_leg": {"binding_revision": 1},
            "previous_execution_leg": None,
        },
    )
    releases = []
    monkeypatch.setattr(
        ahp_provider.engine_client,
        "execution_leg_release",
        lambda *args, **kwargs: releases.append((args, kwargs)) or {},
    )

    class Controller:
        def __init__(self, _config, _token):
            pass

        def __enter__(self):
            raise ahp_provider.AhpProviderError("connection failed")

        def __exit__(self, *_args):
            pass

    with pytest.raises(ahp_provider.AhpProviderError, match="connection failed"):
        ahp_provider.ensure_session(
            "example",
            "host-win-20260909-abcd",
            str(tmp_path),
            controller_type=Controller,
        )

    assert releases[0][1]["reservation_token"] == "reservation-5"


def test_reservation_heartbeat_renews_and_stops_deterministically(monkeypatch):
    renewed = threading.Event()
    calls = []
    monkeypatch.setattr(
        ahp_provider.engine_client,
        "execution_leg_renew",
        lambda *args, **kwargs: calls.append((args, kwargs)) or renewed.set() or {},
    )
    heartbeat = ahp_provider._ReservationHeartbeat(
        "example",
        "host-win-20260909-abcd",
        "reservation-1",
        lease_seconds=3,
        interval_seconds=0.01,
    )

    heartbeat.start()
    assert renewed.wait(1)
    heartbeat.stop()
    count = len(calls)
    assert count >= 1
    assert calls[0][1]["reservation_token"] == "reservation-1"
    assert calls[0][1]["lease_seconds"] == 3
    assert not heartbeat._thread.is_alive()
    assert not renewed.wait(0.03) or len(calls) == count


# ---------------------------------------------------------------------------
# Client-contributed plugins (AHP client-plugins protocol): repo-own-plugin
# resolution + the resourceList/resourceRead reverse-request responder.
# ---------------------------------------------------------------------------

def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _make_repo_with_one_local_plugin(root: Path) -> None:
    """A repo enabling one plugin resolvable to a local ``.ai``-style marketplace
    directory, mirroring plugin_resolve's own ``_make_ai_marketplace`` fixture.
    """
    _write_json(root / ".github" / "copilot" / "settings.json", {
        "extraKnownMarketplaces": {"mp": {"source": {"source": "directory", "path": "./.ai"}}},
        "enabledPlugins": {"greeter@mp": True},
    })
    _write_json(root / ".ai" / ".claude-plugin" / "marketplace.json", {
        "name": "mp",
        "plugins": [{"name": "greeter", "source": "./greeter"}],
    })
    _write_json(root / ".ai" / "greeter" / ".claude-plugin" / "plugin.json", {"name": "greeter"})
    (root / ".ai" / "greeter" / "SKILL.md").write_text(
        "# Greeter\nSay hello.\n", encoding="utf-8"
    )


def test_plugin_container_customizations_resolves_local_plugin(tmp_path):
    _make_repo_with_one_local_plugin(tmp_path)
    customizations, roots = ahp_provider._plugin_container_customizations(str(tmp_path))
    assert len(customizations) == 1
    entry = customizations[0]
    assert entry["id"] == "greeter@mp"
    assert entry["name"] == "greeter@mp"
    assert entry["enabled"] is True
    assert entry["uri"] in roots
    assert roots[entry["uri"]] == (tmp_path / ".ai" / "greeter").resolve()


def test_plugin_container_customizations_empty_for_repo_without_settings(tmp_path):
    customizations, roots = ahp_provider._plugin_container_customizations(str(tmp_path))
    assert customizations == []
    assert roots == {}


def test_resolve_client_plugin_uri_serves_files_under_its_root(tmp_path):
    _make_repo_with_one_local_plugin(tmp_path)
    plugin_dir = (tmp_path / ".ai" / "greeter").resolve()
    roots = {"ahp-client-plugin:/0": plugin_dir}

    assert ahp_provider._resolve_client_plugin_uri(roots, "ahp-client-plugin:/0") == plugin_dir
    assert (
        ahp_provider._resolve_client_plugin_uri(roots, "ahp-client-plugin:/0/SKILL.md")
        == plugin_dir / "SKILL.md"
    )
    # A URI naming a different root, or nothing registered, resolves to None.
    assert ahp_provider._resolve_client_plugin_uri(roots, "ahp-client-plugin:/9") is None
    assert ahp_provider._resolve_client_plugin_uri({}, "ahp-client-plugin:/0") is None


def test_resolve_client_plugin_uri_refuses_path_escape(tmp_path):
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    roots = {"ahp-client-plugin:/0": plugin_dir}

    # A literal ".." segment must never escape the registered root, whether
    # written raw or percent-encoded ("%2e%2e").
    assert ahp_provider._resolve_client_plugin_uri(roots, "ahp-client-plugin:/0/..") is None
    assert (
        ahp_provider._resolve_client_plugin_uri(roots, "ahp-client-plugin:/0/%2e%2e/secret")
        is None
    )
    # A percent-encoded path separator inside one "segment" must not be
    # treated as a real separator either.
    assert (
        ahp_provider._resolve_client_plugin_uri(roots, "ahp-client-plugin:/0/a%2Fb")
        is None
    )


class FakeReversePluginSocket(FakeSocket):
    """Extends FakeSocket to simulate the host's reverse resourceList/
    resourceRead calls during createSession, exactly mirroring copilot-host's
    own client_plugins_over_ahp.rs test harness: refuse nothing, walk the
    declared customization's URI as a real directory tree, and only deliver
    the final createSession result once every reverse read completes.
    """

    def __init__(self) -> None:
        super().__init__()
        self._reverse_next_id = 100_000
        self._pending: dict[str, object] | None = None
        self._create_request: dict[str, object] | None = None
        self._walk_stack: list[str] = []
        self.reverse_calls: list[tuple[str, str]] = []  # (method, uri)

    def send(self, raw: str) -> None:
        message = json.loads(raw)
        if "method" not in message:
            # A reply to one of our own reverse calls.
            self._advance(message)
            return
        self.sent.append(message)
        method = message["method"]
        if method == "createSession":
            params = message["params"]
            active_client = params.get("activeClient") or {}
            customizations = active_client.get("customizations") or []
            self._create_request = message
            if not customizations:
                self._deliver_create_result(message)
                return
            # Kick off the walk at the first customization's root.
            self._walk_stack = [c["uri"] for c in customizations]
            self._issue_next_list()
            return
        if method in ("initialize", "authenticate"):
            result = {"protocolVersion": "0.7.0"} if method == "initialize" else {}
            self.responses.append(json.dumps({
                "jsonrpc": "2.0", "id": message["id"], "result": result,
            }))
            return
        if method == "listSessions":
            self.responses.append(json.dumps({
                "jsonrpc": "2.0",
                "id": message["id"],
                "result": {"items": list(self.sessions)},
            }))
            return
        raise AssertionError(method)

    def _issue_next_list(self) -> None:
        if not self._walk_stack:
            self._deliver_create_result(self._create_request)
            return
        uri = self._walk_stack.pop()
        self._reverse_next_id += 1
        self._pending = {"kind": "list", "uri": uri, "id": self._reverse_next_id}
        self.reverse_calls.append(("resourceList", uri))
        self.responses.append(json.dumps({
            "jsonrpc": "2.0",
            "id": self._reverse_next_id,
            "method": "resourceList",
            "params": {"channel": "ahp-root://", "uri": uri},
        }))

    def _advance(self, reply: dict[str, object]) -> None:
        pending = self._pending
        assert pending is not None and reply.get("id") == pending["id"]
        if pending["kind"] == "list":
            entries = (reply.get("result") or {}).get("entries", [])
            for entry in entries:
                child_uri = pending["uri"].rstrip("/") + "/" + entry["name"]
                if entry["type"] == "directory":
                    self._walk_stack.append(child_uri)
                else:
                    self._reverse_next_id += 1
                    self._pending = {
                        "kind": "read", "uri": child_uri, "id": self._reverse_next_id,
                    }
                    self.reverse_calls.append(("resourceRead", child_uri))
                    self.responses.append(json.dumps({
                        "jsonrpc": "2.0",
                        "id": self._reverse_next_id,
                        "method": "resourceRead",
                        "params": {"channel": "ahp-root://", "uri": child_uri},
                    }))
                    return
            self._issue_next_list()
            return
        if pending["kind"] == "read":
            assert (reply.get("result") or {}).get("data") is not None
            self._issue_next_list()
            return

    def _deliver_create_result(self, create_request: dict[str, object]) -> None:
        params = create_request["params"]
        self.sessions.append({
            "resource": params["channel"],
            "workingDirectories": params["workingDirectories"],
        })
        self.responses.append(json.dumps({
            "jsonrpc": "2.0", "id": create_request["id"], "result": {},
        }))


def test_create_session_contributes_and_serves_repo_plugins(monkeypatch, tmp_path):
    _make_repo_with_one_local_plugin(tmp_path)
    socket = FakeReversePluginSocket()
    monkeypatch.setitem(
        sys.modules,
        "websocket",
        SimpleNamespace(create_connection=lambda *_args, **_kwargs: socket),
    )

    with ahp_provider.AhpController(_config(), "secret") as controller:
        session_id = controller.create_session(str(tmp_path))

    create = next(r for r in socket.sent if r["method"] == "createSession")
    customizations = create["params"]["activeClient"]["customizations"]
    assert [c["id"] for c in customizations] == ["greeter@mp"]

    # The host actually walked the real on-disk plugin tree over the reverse
    # protocol and read the real file content -- not a stub.
    methods = [m for m, _uri in socket.reverse_calls]
    assert "resourceList" in methods
    assert "resourceRead" in methods
    read_uris = [uri for m, uri in socket.reverse_calls if m == "resourceRead"]
    assert any(uri.endswith("SKILL.md") for uri in read_uris)
    assert any(uri.endswith("plugin.json") for uri in read_uris)
    assert session_id

