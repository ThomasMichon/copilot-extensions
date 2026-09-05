from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from agent_index import __main__


def _capture_promoting_routing(monkeypatch, tmp_path, capsys):
    captured = {}

    class Handle:
        pid = 4321

        def __init__(self) -> None:
            self.terminated = False

        def terminate(self) -> None:
            self.terminated = True

        def poll(self):
            return 0 if self.terminated else None

    handle = Handle()

    class FakeResult:
        ok = False
        steps = []
        new_port = 4555
        rolled_back = True
        error = "captured"

        @staticmethod
        def to_dict():
            return {
                "ok": False,
                "new_port": 4555,
                "steps": [],
                "rolled_back": True,
                "error": "captured",
            }

    class FakeOrchestrator:
        def __init__(self, _config_dir, **kwargs):
            captured.update(kwargs)

        def run(self, **_kwargs):
            captured["handle"] = captured["spawn_passive"](4555)
            return FakeResult()

    home = tmp_path / "cell"
    route_root = home / "run" / "zdd"
    home.mkdir(parents=True)
    monkeypatch.setenv("AGENT_INDEX_HOME", str(home))
    monkeypatch.setenv("AGENT_INDEX_RUN_DIR", str(home / "run"))
    monkeypatch.setenv("AGENT_INDEX_ROUTING_DIR", str(route_root))
    monkeypatch.setenv("AGENT_INDEX_ROLE", "host")
    monkeypatch.setenv("AGENT_INDEX_INSTALLATION_ID", "cell-a/agent-index")
    monkeypatch.setattr(
        __main__,
        "_validated_cell_transaction",
        lambda: {"token": "t" * 64, "id": "transaction-id"},
    )
    monkeypatch.setattr(__main__, "_validate_cutover_governance", lambda _value: None)
    monkeypatch.setattr(
        "zdd.breadcrumb.recover_stale_cutover",
        lambda *_args, **_kwargs: {"recovered": False},
    )
    monkeypatch.setattr("zdd.cutover.CutoverOrchestrator", FakeOrchestrator)
    monkeypatch.setattr(__main__, "_routing_endpoint", lambda: None)
    monkeypatch.setattr(__main__.subprocess, "Popen", lambda *_args, **_kwargs: handle)

    rc = __main__.cmd_deploy(
        SimpleNamespace(
            json=True,
            health_timeout=1.0,
            drain_timeout=1.0,
            force=False,
            recover=False,
        )
    )

    assert rc == 1
    capsys.readouterr()
    return captured, handle, route_root


def test_deploy_wires_cutover_orchestrator(monkeypatch, tmp_path, capsys) -> None:
    captured = {}
    spawned = {}

    class FakeResult:
        def __init__(self) -> None:
            self.ok = True
            self.steps = ["ok"]
            self.new_port = 4444
            self.rolled_back = False
            self.error = None

        def to_dict(self):
            return {"ok": True, "new_port": self.new_port, "steps": self.steps}

    class FakeOrchestrator:
        def __init__(self, config_dir, **kwargs):
            captured["config_dir"] = config_dir
            captured.update(kwargs)

        def run(self, **kwargs):
            captured["run"] = kwargs
            return FakeResult()

    monkeypatch.setenv("AGENT_INDEX_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AGENT_INDEX_ROLE", "host")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "malicious"))
    monkeypatch.setenv("PYTHONHOME", str(tmp_path / "malicious-home"))
    monkeypatch.setattr(
        "zdd.breadcrumb.recover_stale_cutover",
        lambda *a, **k: {"recovered": False},
    )
    monkeypatch.setattr("zdd.cutover.CutoverOrchestrator", FakeOrchestrator)
    routes = iter(
        [
            None,
            SimpleNamespace(
                base_url="http://127.0.0.1:4444",
                pid=None,
                version=__main__.__version__,
            ),
        ]
    )
    monkeypatch.setattr(__main__, "_routing_endpoint", lambda: next(routes))
    monkeypatch.setattr(
        __main__,
        "_owned_service_status",
        lambda *_args, **_kwargs: {
            "installationId": "",
            "version": __main__.__version__,
        },
    )

    class Handle:
        pid = 4321

    def popen(command, **kwargs):
        spawned["command"] = command
        spawned["kwargs"] = kwargs
        return Handle()

    monkeypatch.setattr(__main__.subprocess, "Popen", popen)

    rc = __main__.cmd_deploy(__main__.build_parser().parse_args([
        "deploy",
        "--json",
        "--health-timeout",
        "7",
        "--drain-timeout",
        "11",
        "--force",
    ]))

    assert rc == 0
    assert captured["config_dir"] == tmp_path / "home"
    assert captured["bind"] == "127.0.0.1"
    assert captured["version"] == __main__.__version__
    for key in ("spawn_passive", "health_check", "make_client", "pick_free_port"):
        assert callable(captured[key])
    assert captured["run"] == {"health_timeout": 7.0, "drain_timeout": 11.0, "force": True}
    assert json.loads(capsys.readouterr().out)["ok"] is True

    handle = captured["spawn_passive"](4555)
    assert handle.pid == 4321
    assert spawned["command"][1:4] == ["-I", "-X", "utf8"]
    assert "start" in spawned["command"]
    assert "__cell-start" not in spawned["command"]
    assert spawned["kwargs"]["cwd"] == str((tmp_path / "home").resolve())
    assert "PYTHONPATH" not in spawned["kwargs"]["env"]
    assert "PYTHONHOME" not in spawned["kwargs"]["env"]


def test_namespaced_deploy_spawns_private_cell_entrypoint(
    monkeypatch,
    tmp_path,
) -> None:
    captured = {}
    spawned = {}

    class FakeResult:
        ok = True
        steps = []
        new_port = 4555
        rolled_back = False
        error = None

        @staticmethod
        def to_dict():
            return {"ok": True, "new_port": 4555, "steps": []}

    class FakeOrchestrator:
        def __init__(self, _config_dir, **kwargs):
            captured.update(kwargs)

        def run(self, **_kwargs):
            captured["spawn_passive"](4555)
            return FakeResult()

    class Handle:
        pid = 4321

    def popen(command, **kwargs):
        spawned["command"] = command
        spawned["kwargs"] = kwargs
        return Handle()

    home = tmp_path / "cell"
    home.mkdir()
    monkeypatch.setenv("AGENT_INDEX_HOME", str(home))
    monkeypatch.setenv("AGENT_INDEX_ROLE", "host")
    monkeypatch.setenv("AGENT_INDEX_INSTALLATION_ID", "cell-a/agent-index")
    monkeypatch.setattr(
        __main__,
        "_validated_cell_transaction",
        lambda: {"token": "t" * 64},
    )
    monkeypatch.setattr(
        "zdd.breadcrumb.recover_stale_cutover",
        lambda *_args, **_kwargs: {"recovered": False},
    )
    monkeypatch.setattr("zdd.cutover.CutoverOrchestrator", FakeOrchestrator)
    routes = iter(
        [
            None,
            SimpleNamespace(
                base_url="http://127.0.0.1:4555",
                pid=4321,
                version=__main__.__version__,
            ),
        ]
    )
    monkeypatch.setattr(__main__, "_routing_endpoint", lambda: next(routes))
    monkeypatch.setattr(
        __main__,
        "_owned_service_client",
        lambda *_args, **_kwargs: ({}, SimpleNamespace()),
    )
    monkeypatch.setattr(
        __main__,
        "_owned_service_status",
        lambda *_args, **_kwargs: {
            "installationId": "cell-a/agent-index",
            "version": __main__.__version__,
            "pid": 4321,
            "instanceToken": "instance-token",
        },
    )
    monkeypatch.setattr(__main__.subprocess, "Popen", popen)

    rc = __main__.cmd_deploy(
        SimpleNamespace(
            json=True,
            health_timeout=7.0,
            drain_timeout=11.0,
            force=False,
            recover=False,
        )
    )

    assert rc == 0
    assert "__cell-start" in spawned["command"]
    assert "start" not in [
        part for part in spawned["command"] if part != "__cell-start"
    ]
    assert spawned["kwargs"]["cwd"] == str(home.resolve())


@pytest.mark.parametrize(
    "blocked",
    [
        {
            "status": "maintenance",
            "reason": "maintenance",
            "actualMode": "namespaced",
        },
        {
            "status": "deactivation-required",
            "reason": "deactivation-required",
            "actualMode": "namespaced",
        },
    ],
)
def test_governance_block_after_passive_health_leaves_old_route_and_service(
    monkeypatch,
    tmp_path,
    capsys,
    blocked,
) -> None:
    from zdd import routing

    home = tmp_path / "cell"
    route_root = home / "run" / "zdd"
    home.mkdir(parents=True)
    monkeypatch.setenv("AGENT_INDEX_HOME", str(home))
    monkeypatch.setenv("AGENT_INDEX_RUN_DIR", str(home / "run"))
    monkeypatch.setenv("AGENT_INDEX_ROUTING_DIR", str(route_root))
    monkeypatch.setenv("AGENT_INDEX_ROLE", "host")
    monkeypatch.setenv("AGENT_INDEX_INSTALLATION_ID", "cell-a/agent-index")
    monkeypatch.setattr(routing, "_listening", lambda *_args, **_kwargs: True)
    old = routing.publish_active(
        route_root,
        bind="127.0.0.1",
        port=4111,
        pid=os.getpid(),
        version=__main__.__version__,
        demote_existing=True,
    )
    route_path = routing.routing_table_path(route_root)
    before_route = route_path.read_bytes()
    monkeypatch.setattr(
        __main__,
        "_validated_cell_transaction",
        lambda: {"token": "t" * 64, "id": "transaction-id"},
    )
    monkeypatch.setattr(
        "zdd.breadcrumb.recover_stale_cutover",
        lambda *_args, **_kwargs: {"recovered": False},
    )

    events: list[str] = []

    class Handle:
        pid = 4222

        @staticmethod
        def terminate():
            events.append("passive-retired")

        @staticmethod
        def poll():
            return None

    monkeypatch.setattr(
        __main__.subprocess,
        "Popen",
        lambda *_args, **_kwargs: events.append("passive-spawned") or Handle(),
    )

    def status(_url, *, expected_pid=None, **_kwargs):
        if expected_pid == Handle.pid:
            events.append("passive-healthy")
            return {
                "status": "passive",
                "plugin": "agent-index",
                "installationId": "cell-a/agent-index",
                "version": __main__.__version__,
                "pid": Handle.pid,
                "instanceToken": "passive-token",
                "promoted": False,
            }
        return {
            "status": "ok",
            "plugin": "agent-index",
            "installationId": "cell-a/agent-index",
            "version": __main__.__version__,
            "pid": old.pid,
            "instanceToken": "old-token",
            "promoted": True,
        }

    class Client:
        def drain(self, **_kwargs):
            events.append("old-drained")
            return {"drained": True, "clean": True, "forced": False}

        def undrain(self):
            events.append("old-undrained")
            return {"draining": False}

        def shutdown(self):
            events.append("old-shutdown")
            return {"shutdown": True}

        def adopt_relay(self):
            return {"adopted": False}

        def health(self):
            return {"status": "ok"}

    monkeypatch.setattr(__main__, "_owned_service_status", status)
    monkeypatch.setattr(
        __main__,
        "_owned_service_client",
        lambda *_args, **_kwargs: ({}, Client()),
    )

    def block_governance(_transaction):
        events.append("governance-rechecked")
        raise __main__.CutoverGovernanceBlocked(blocked)

    monkeypatch.setattr(
        __main__,
        "_validate_cutover_governance",
        block_governance,
    )

    rc = __main__.cmd_deploy(
        SimpleNamespace(
            json=True,
            health_timeout=1.0,
            drain_timeout=1.0,
            force=False,
            recover=False,
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload["reason"] == "governance-blocked-before-commit"
    assert payload["governance"] == blocked
    assert route_path.read_bytes() == before_route
    assert events == [
        "passive-spawned",
        "passive-healthy",
        "governance-rechecked",
        "passive-retired",
    ]


def test_route_resolvers_stay_on_prior_service_until_promotion_is_read_ready(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    from zdd import routing

    captured, handle, route_root = _capture_promoting_routing(
        monkeypatch,
        tmp_path,
        capsys,
    )
    old = routing.publish_active(
        route_root,
        bind="127.0.0.1",
        port=4111,
        pid=os.getpid(),
        version="old",
        demote_existing=True,
    )
    promotion_started = threading.Event()
    release_promotion = threading.Event()
    promoted = threading.Event()

    def status(_url, *, expected_pid=None, **_kwargs):
        assert expected_pid == handle.pid
        is_ready = promoted.is_set()
        return {
            "status": "ok" if is_ready else "passive",
            "plugin": "agent-index",
            "installationId": "cell-a/agent-index",
            "version": captured["version"],
            "pid": handle.pid,
            "instanceToken": "passive-token",
            "promoted": is_ready,
        }

    def promote(_client):
        promotion_started.set()
        assert release_promotion.wait(timeout=5)
        promoted.set()
        return {"promoted": True}

    monkeypatch.setattr(__main__, "_owned_service_status", status)
    monkeypatch.setattr(__main__.AgentIndexClient, "promote", promote)
    real_publish = routing.publish_active

    def publish_after_readiness(config_dir, **kwargs):
        if kwargs.get("pid") == handle.pid:
            assert promoted.is_set()
        return real_publish(config_dir, **kwargs)

    monkeypatch.setattr(routing, "publish_active", publish_after_readiness)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            captured["routing_mod"].publish_active,
            route_root,
            bind="127.0.0.1",
            port=4555,
            pid=handle.pid,
            version=captured["version"],
            demote_existing=True,
        )
        assert promotion_started.wait(timeout=2)
        observations = []
        for _ in range(100):
            assert not future.done()
            active = routing.read_active_endpoint(
                route_root,
                verify_listener=False,
            )
            observations.append(active.port if active is not None else None)
            time.sleep(0.001)
        assert observations and set(observations) == {old.port}
        release_promotion.set()
        active = future.result(timeout=5)

    assert active.port == 4555
    resolved = routing.read_active_endpoint(route_root, verify_listener=False)
    assert resolved is not None
    assert resolved.port == 4555


def test_pre_promotion_crash_keeps_prior_route_and_owned_passive_receipt(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    from zdd import routing

    captured, handle, route_root = _capture_promoting_routing(
        monkeypatch,
        tmp_path,
        capsys,
    )
    routing.publish_active(
        route_root,
        bind="127.0.0.1",
        port=4111,
        pid=os.getpid(),
        version="old",
        demote_existing=True,
    )
    route_path = routing.routing_table_path(route_root)
    before_route = route_path.read_bytes()
    receipt_path = route_root.parent / "instances" / f"{handle.pid}.json"
    receipt = {
        "schema": "copilot-extensions.agent-index.service-instance",
        "version": 1,
        "installationId": "cell-a/agent-index",
        "runtimeVersion": captured["version"],
        "pid": handle.pid,
        "instanceToken": "passive-token",
        "host": "127.0.0.1",
        "port": 4555,
        "state": "passive",
        "transactionId": "transaction-id",
    }
    receipt_path.parent.mkdir(parents=True)
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    monkeypatch.setattr(
        __main__,
        "_owned_service_status",
        lambda *_args, **_kwargs: {
            "status": "passive",
            "plugin": "agent-index",
            "installationId": "cell-a/agent-index",
            "version": captured["version"],
            "pid": handle.pid,
            "instanceToken": "passive-token",
            "promoted": False,
        },
    )

    def crash_before_promotion(_client):
        raise SystemExit(__main__.CUTOVER_CRASH_EXIT_CODES["passive"])

    monkeypatch.setattr(
        __main__.AgentIndexClient,
        "promote",
        crash_before_promotion,
    )

    with pytest.raises(SystemExit) as raised:
        captured["routing_mod"].publish_active(
            route_root,
            bind="127.0.0.1",
            port=4555,
            pid=handle.pid,
            version=captured["version"],
            demote_existing=True,
        )

    assert raised.value.code == __main__.CUTOVER_CRASH_EXIT_CODES["passive"]
    assert route_path.read_bytes() == before_route
    active = routing.read_active_endpoint(route_root, verify_listener=False)
    assert active is not None
    assert active.port == 4111
    assert handle.poll() is None
    assert json.loads(receipt_path.read_text(encoding="utf-8")) == receipt


def test_deploy_refuses_foreign_active_endpoint(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setenv("AGENT_INDEX_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AGENT_INDEX_ROLE", "host")
    monkeypatch.setenv("AGENT_INDEX_INSTALLATION_ID", "cell-a/agent-index")
    context = tmp_path / "install.json"
    context.write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(context))
    token = "t" * 64
    transaction = tmp_path / "home" / "selection-transaction.json"
    transaction.parent.mkdir(parents=True)
    transaction.write_text(
        json.dumps(
            {
                "schema": __main__.CELL_TRANSACTION_SCHEMA,
                "version": 1,
                "id": "transaction-id",
                "marketplaceId": "cell-a",
                "pluginId": "agent-index",
                "installationId": "cell-a/agent-index",
                "context": str(context),
                "token": token,
                "state": "reconciling",
                "management": {"path": str(tmp_path), "version": __main__.__version__},
                "target": {
                    "payloadRoot": str(tmp_path),
                    "payloadVersion": __main__.__version__,
                    "snapshotId": __main__.__version__,
                    "runtimeVersion": __main__.__version__,
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(__main__.CELL_TRANSACTION_PATH_ENV, str(transaction))
    monkeypatch.setenv(__main__.CELL_TRANSACTION_TOKEN_ENV, token)
    monkeypatch.setenv("AGENT_INDEX_CELL_TRANSACTION_ID", "transaction-id")
    monkeypatch.setattr(
        __main__,
        "_routing_endpoint",
        lambda: SimpleNamespace(base_url="http://127.0.0.1:4444"),
    )
    monkeypatch.setattr(
        __main__,
        "_owned_service_client",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            __main__.ServiceOwnershipError("foreign installation")
        ),
    )

    rc = __main__.cmd_deploy(__main__.build_parser().parse_args(["deploy", "--json"]))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["reason"] == "ownership-mismatch"


def test_namespaced_deploy_requires_cell_transaction(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    monkeypatch.setenv("AGENT_INDEX_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AGENT_INDEX_ROLE", "host")
    monkeypatch.setenv("AGENT_INDEX_INSTALLATION_ID", "cell-a/agent-index")

    rc = __main__.cmd_deploy(
        __main__.build_parser().parse_args(["deploy", "--recover", "--json"])
    )

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["reason"] == "transaction-unauthorized"
    assert "transaction receipt" in payload["error"]


def test_deploy_recover_undrains_stranded_survivor(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    from zdd import breadcrumb, routing

    home = tmp_path / "cell"
    route_root = home / "run" / "zdd"
    home.mkdir(parents=True)
    monkeypatch.setenv("AGENT_INDEX_HOME", str(home))
    monkeypatch.setenv("AGENT_INDEX_RUN_DIR", str(home / "run"))
    monkeypatch.setenv("AGENT_INDEX_ROUTING_DIR", str(route_root))
    monkeypatch.setenv("AGENT_INDEX_ROLE", "host")
    monkeypatch.setenv("AGENT_INDEX_INSTALLATION_ID", "cell-a/agent-index")
    monkeypatch.setattr(routing, "_listening", lambda *_args, **_kwargs: True)
    active = routing.publish_active(
        route_root,
        bind="127.0.0.1",
        port=4111,
        pid=os.getpid(),
        version=__main__.__version__,
        demote_existing=True,
    )
    breadcrumb.write_breadcrumb(
        route_root,
        state="draining",
        old={"bind": active.bind, "port": active.port},
        new_port=4222,
    )
    monkeypatch.setattr(
        __main__,
        "_validated_cell_transaction",
        lambda: {"token": "t" * 64, "id": "transaction-id"},
    )
    state = {"status": "draining", "undrain_calls": 0}

    class Client:
        def undrain(self):
            state["undrain_calls"] += 1
            state["status"] = "ok"
            return {"draining": False}

    def status(_url, **_kwargs):
        return {
            "status": state["status"],
            "plugin": "agent-index",
            "installationId": "cell-a/agent-index",
            "version": __main__.__version__,
            "pid": active.pid,
            "instanceToken": "exact-token",
            "promoted": True,
        }

    monkeypatch.setattr(__main__, "_owned_service_status", status)
    monkeypatch.setattr(
        __main__,
        "_owned_service_client",
        lambda *_args, **_kwargs: (status(""), Client()),
    )

    rc = __main__.cmd_deploy(
        SimpleNamespace(
            json=True,
            health_timeout=1.0,
            drain_timeout=1.0,
            force=False,
            recover=True,
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["recovered"] is True
    assert state == {"status": "ok", "undrain_calls": 1}


def test_control_client_binds_route_pid_version_and_instance_token(
    monkeypatch,
) -> None:
    observed = {}
    monkeypatch.setenv("AGENT_INDEX_INSTALLATION_ID", "cell-a/agent-index")
    monkeypatch.setattr(
        __main__,
        "_routing_endpoint_for_url",
        lambda _url: SimpleNamespace(
            base_url="http://127.0.0.1:4444",
            pid=123,
            version="9.9.9",
        ),
    )

    def status(_url, **kwargs):
        observed.update(kwargs)
        return {
            "installationId": "cell-a/agent-index",
            "instanceToken": "exact-token",
            "pid": 123,
            "version": "9.9.9",
        }

    monkeypatch.setattr(__main__, "_owned_service_status", status)

    payload, client = __main__._owned_service_client(
        "http://127.0.0.1:4444",
        timeout=9.0,
    )

    assert payload["instanceToken"] == "exact-token"
    assert observed["expected_pid"] == 123
    assert observed["expected_version"] == "9.9.9"
    assert observed["expected_instance_token"] == "exact-token"
    assert client.instance_token == "exact-token"


def test_control_client_rejects_route_change_during_token_validation(
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_INDEX_INSTALLATION_ID", "cell-a/agent-index")
    routes = iter(
        (
            SimpleNamespace(
                base_url="http://127.0.0.1:4444",
                pid=123,
                version="9.9.9",
            ),
            SimpleNamespace(
                base_url="http://127.0.0.1:4444",
                pid=456,
                version="9.9.9",
            ),
        )
    )
    monkeypatch.setattr(
        __main__,
        "_routing_endpoint_for_url",
        lambda _url: next(routes),
    )
    monkeypatch.setattr(
        __main__,
        "_owned_service_status",
        lambda *_args, **_kwargs: {
            "installationId": "cell-a/agent-index",
            "instanceToken": "exact-token",
            "pid": 123,
            "version": "9.9.9",
        },
    )

    with pytest.raises(__main__.ServiceOwnershipError, match="routing ownership changed"):
        __main__._owned_service_client(
            "http://127.0.0.1:4444",
            timeout=9.0,
        )


@pytest.mark.parametrize(
    ("expected_pid", "expected_token", "message"),
    [
        (456, None, "pid does not match"),
        (123, "reused-token", "instance token changed"),
    ],
)
def test_same_cell_stale_endpoint_evidence_is_rejected(
    monkeypatch,
    expected_pid,
    expected_token,
    message,
) -> None:
    monkeypatch.setenv("AGENT_INDEX_INSTALLATION_ID", "cell-a/agent-index")

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "plugin": "agent-index",
                "installationId": "cell-a/agent-index",
                "version": "9.9.9",
                "pid": 123,
                "instanceToken": "fresh-token",
            }

    class Client:
        def __init__(self, *, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, _url):
            return Response()

    monkeypatch.setattr(__main__.httpx, "Client", Client)

    with pytest.raises(__main__.ServiceOwnershipError, match=message):
        __main__._owned_service_status(
            "http://127.0.0.1:4444",
            expected_version="9.9.9",
            expected_pid=expected_pid,
            expected_instance_token=expected_token,
        )
