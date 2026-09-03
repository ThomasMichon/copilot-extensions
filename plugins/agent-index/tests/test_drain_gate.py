from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from fastapi.testclient import TestClient

from agent_index import server
from agent_index.server import build_app


def test_drain_health_and_search(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AGENT_INDEX_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AGENT_INDEX_INSTALLATION_ID", "example/agent-index")
    from agent_index.search import engine as search_engine

    class FakeEngine:
        def search(self, *_args, **_kwargs):
            return [SimpleNamespace(chunk_id="c1", score=1.0, content="hit")]

        def find_similar(self, *_args, **_kwargs):
            return []

    monkeypatch.setattr(search_engine, "create_search_engine", lambda: FakeEngine())

    with TestClient(build_app()) as client:
        identity = client.get("/health").json()
        assert identity["installationId"] == "example/agent-index"
        assert identity["instanceToken"]
        assert identity["pid"] > 0

        refused = client.post(
            "/drain",
            json={"timeout": 1, "poll": 0.05},
            headers={"X-Agent-Index-Installation-Id": "other/agent-index"},
        )
        assert refused.status_code == 409
        missing_instance = client.post(
            "/drain",
            json={"timeout": 1, "poll": 0.05},
            headers={"X-Agent-Index-Installation-Id": "example/agent-index"},
        )
        assert missing_instance.status_code == 409
        assert missing_instance.json()["detail"] == "service instance token required"

        headers = {
            "X-Agent-Index-Installation-Id": "example/agent-index",
            "X-Agent-Index-Instance-Token": identity["instanceToken"],
        }
        drained = client.post(
            "/drain",
            json={"timeout": 1, "poll": 0.05},
            headers=headers,
        )
        assert drained.status_code == 200
        assert drained.json()["drained"] is True

        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "draining"

        search = client.get("/search", params={"q": "needle"})
        assert search.status_code == 503
        assert search.headers["retry-after"] == "1"
        assert search.json()["detail"]["retryable"] is True
        assert client.get("/similar", params={"id": "c1"}).status_code == 503
        assert client.get("/clusters").status_code == 503

        undrained = client.post("/undrain", headers=headers)
        assert undrained.status_code == 200
        assert client.get("/health").json()["status"] == "ok"


def test_drain_closes_admission_and_waits_for_admitted_read(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("AGENT_INDEX_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AGENT_INDEX_INSTALLATION_ID", "example/agent-index")
    from agent_index.search import engine as search_engine

    entered = threading.Event()
    release = threading.Event()

    class BlockingEngine:
        def search(self, *_args, **_kwargs):
            entered.set()
            assert release.wait(timeout=5)
            return []

    monkeypatch.setattr(
        search_engine,
        "create_search_engine",
        lambda: BlockingEngine(),
    )

    with TestClient(build_app()) as client:
        identity = client.get("/health").json()
        headers = {
            "X-Agent-Index-Installation-Id": "example/agent-index",
            "X-Agent-Index-Instance-Token": identity["instanceToken"],
        }
        with ThreadPoolExecutor(max_workers=2) as executor:
            active_read = executor.submit(
                client.get,
                "/search",
                params={"q": "already-admitted"},
            )
            assert entered.wait(timeout=2)
            draining = executor.submit(
                client.post,
                "/drain",
                json={"timeout": 5, "poll": 0.05},
                headers=headers,
            )
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                if client.get("/health").json()["status"] == "draining":
                    break
                time.sleep(0.01)
            assert client.get("/clusters").status_code == 503
            assert not draining.done()
            release.set()
            assert active_read.result(timeout=2).status_code == 200
            drained = draining.result(timeout=2)
        assert drained.json()["drained"] is True
        assert drained.json()["busy_reads"] == 0
        assert client.post("/shutdown", headers=headers).status_code == 200


def test_passive_service_stays_inert_until_owned_promotion(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("AGENT_INDEX_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AGENT_INDEX_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("AGENT_INDEX_INSTALLATION_ID", "example/agent-index")
    runtime_version = f"{server.__version__}+host"
    monkeypatch.setenv("AGENT_INDEX_RUNTIME_VERSION", runtime_version)
    monkeypatch.setenv("AGENT_INDEX_CELL_TRANSACTION_TOKEN", "transaction-token")
    monkeypatch.setenv("AGENT_INDEX_CELL_TRANSACTION_ID", "transaction-id")
    transaction = tmp_path / "home" / "selection-transaction.json"
    transaction.parent.mkdir(parents=True)
    transaction.write_text(
        json.dumps(
            {
                "schema": "copilot-extensions.agent-index.selection-transaction",
                "version": 1,
                "id": "transaction-id",
                "installationId": "example/agent-index",
                "token": "transaction-token",
                "state": "reconciling",
                "target": {"runtimeVersion": runtime_version},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_INDEX_CELL_TRANSACTION", str(transaction))

    starts: list[str] = []
    evidence: list[str] = []

    class FakeRunner:
        async def start(self) -> None:
            starts.append("start")

        async def stop(self) -> None:
            starts.append("stop")

        def status(self):
            return {"running": True}

    monkeypatch.setattr(
        server,
        "_create_task_runner",
        lambda: (object(), FakeRunner()),
    )
    monkeypatch.setattr(
        server,
        "_publish_active_evidence",
        lambda active_app, *, strict: evidence.append(
            f"publish:{strict}:{active_app.state.promoted}:"
            f"{active_app.state.passive}"
        ),
    )

    app = build_app(passive=True)
    app.state.bound_host = "127.0.0.1"
    app.state.bound_port = 4545

    with TestClient(app) as client:
        identity = client.get("/health").json()
        assert identity["version"] == runtime_version
        assert identity["status"] == "passive"
        assert identity["promoted"] is False
        assert starts == []
        assert evidence == []
        passive_read = client.get("/search", params={"q": "not-yet"})
        assert passive_read.status_code == 503
        assert passive_read.json()["detail"]["code"] == "service_passive"

        missing_instance = client.post(
            "/promote",
            headers={
                "X-Agent-Index-Installation-Id": "example/agent-index",
                "X-Agent-Index-Transaction-Token": "transaction-token",
            },
        )
        assert missing_instance.status_code == 409

        wrong_transaction = client.post(
            "/promote",
            headers={
                "X-Agent-Index-Installation-Id": "example/agent-index",
                "X-Agent-Index-Instance-Token": identity["instanceToken"],
                "X-Agent-Index-Transaction-Token": "wrong",
            },
        )
        assert wrong_transaction.status_code == 409

        promotion_headers = {
            "X-Agent-Index-Installation-Id": "example/agent-index",
            "X-Agent-Index-Instance-Token": identity["instanceToken"],
            "X-Agent-Index-Transaction-Token": "transaction-token",
        }
        promoted = client.post("/promote", headers=promotion_headers)
        assert promoted.status_code == 200
        assert promoted.json()["promoted"] is True
        assert starts == ["start"]
        assert evidence == ["publish:True:True:False"]
        assert client.get("/health").json()["status"] == "ok"
        assert client.get("/search", params={"q": "ready"}).status_code == 200
        replayed = client.post("/promote", headers=promotion_headers)
        assert replayed.status_code == 200
        assert starts == ["start"]
        assert evidence == ["publish:True:True:False"]
