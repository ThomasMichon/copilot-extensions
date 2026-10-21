"""Synthetic coordinator authority and binding tests, not runtime qualification."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_dispatch import coordinator_execution_scopes as routes
from agent_dispatch.coordinator import create_app
from agent_dispatch.execution_effect_adapter import EffectRevocation
from agent_dispatch.execution_scope_binding import resolve_execution_installation
from agent_dispatch.loop_governance import LoopGovernance
from agent_dispatch.queue import TaskQueue
from agent_dispatch.queue_records import TaskError
from agent_dispatch.task_scope import TASK_SCOPE_HEADER, mint_task_scope_token
from tests._helpers import TEST_REPO


def _state() -> dict[str, object]:
    return {
        "status": "ready",
        "baseline": {
            "schema": "copilot-extensions.installation-loop-baseline",
            "version": 1,
            "context": "/synthetic/install/context.json",
            "marketplaceId": "example-marketplace",
            "pluginId": "agent-dispatch",
            "actualMode": "namespaced",
            "namespaceGeneration": 1,
            "installGeneration": 2,
            "activationGeneration": 3,
            "legacy": {},
        },
    }


def _governance(state: dict[str, object] | None = None) -> LoopGovernance:
    return LoopGovernance(helper=lambda _: _state() if state is None else state)


def _bind(queue: TaskQueue, governance: LoopGovernance | None = None, **kwargs):
    return queue.bind_execution_scope(
        resolve_execution_installation(governance or _governance()),
        repo=TEST_REPO,
        source="scheduled",
        expected_producer_generation=kwargs.pop("generation", 1),
        **kwargs,
    )


@pytest.fixture
def queue(tmp_path: Path) -> TaskQueue:
    queue = TaskQueue(tmp_path / "tasks.db")
    queue.handoff_producer_scope(
        TEST_REPO, "scheduled", producer_id="producer-a", expected_generation=0
    )
    return queue


def test_binding_replays_after_restart_without_task_or_execution_mutation(queue):
    first = _bind(queue, now=1)
    replay = _bind(TaskQueue(queue.db_path), now=2)
    assert first.replayed is False and replay.replayed is True
    assert first.scope == replay.scope
    assert first.source == "scheduled"
    with queue._connect() as conn:
        assert conn.execute("SELECT count(*) FROM execution_scope_bindings").fetchone()[0] == 1
        for table in ("tasks", "spawn_reservations", "execution_admission_closures"):
            assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_concurrent_resolution_has_one_initial_binding(queue):
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: _bind(queue), range(2)))
    assert {result.scope for result in results} == {results[0].scope}
    assert sorted(result.replayed for result in results) == [False, True]


def test_producer_rollover_preserves_registration_but_requires_current_generation(queue):
    first = _bind(queue)
    queue.handoff_producer_scope(
        TEST_REPO, "scheduled", producer_id="producer-b", expected_generation=1
    )
    with pytest.raises(TaskError, match="current managed producer"):
        _bind(queue)
    current = _bind(queue, generation=2)
    assert current.scope == first.scope
    assert current.producer_generation == 2


def test_unmanaged_label_or_source_is_not_a_producer_registration(tmp_path):
    queue = TaskQueue(tmp_path / "tasks.db")
    queue.register_registration("supervised-lane", {"repo": TEST_REPO, "labels": ["scheduled"]})
    with pytest.raises(TaskError, match="current managed producer"):
        _bind(queue)


def test_binding_refuses_installation_generation_drift_without_rebinding(queue):
    first = _bind(queue)
    changed = _state()
    changed["baseline"]["installGeneration"] = 4
    with pytest.raises(TaskError, match="provenance changed"):
        _bind(queue, _governance(changed))
    assert _bind(queue).scope == first.scope


def test_independent_queues_installations_and_sources_are_distinct(queue, tmp_path):
    first = _bind(queue)
    second_queue = TaskQueue(tmp_path / "other.db")
    second_queue.handoff_producer_scope(
        TEST_REPO, "scheduled", producer_id="producer-a", expected_generation=0
    )
    second = _bind(second_queue)
    assert first.scope.coordinator != second.scope.coordinator
    queue.handoff_producer_scope(
        TEST_REPO, "other", producer_id="producer-b", expected_generation=0
    )
    other = queue.bind_execution_scope(
        resolve_execution_installation(_governance()),
        repo=TEST_REPO,
        source="other",
        expected_producer_generation=1,
    )
    assert other.scope.producer_registration != first.scope.producer_registration
    state = _state()
    state["baseline"]["context"] = "/synthetic/other/context.json"
    with pytest.raises(TaskError, match="qualified producer scope"):
        _bind(queue, _governance(state))


@pytest.mark.parametrize("before_binding", [False, True])
def test_closure_and_binding_cannot_hide_or_replace_each_other(queue, before_binding):
    binding = _bind(queue)
    if before_binding:
        with queue._connect() as conn:
            conn.execute("DELETE FROM execution_scope_bindings")
    wrong = replace(binding.scope, producer_registration="caller-label")
    request = EffectRevocation(
        wrong if before_binding else binding.scope, "operation-a", 1, "automatic", "manual"
    )
    queue.close_execution_admission(request, source="scheduled", expected_producer_generation=1)
    if before_binding:
        with pytest.raises(TaskError, match="qualified producer scope"):
            _bind(queue)
    else:
        with pytest.raises(TaskError, match="conflicts with the retained operation"):
            queue.close_execution_admission(
                replace(request, scope=wrong), source="scheduled", expected_producer_generation=1
            )
        assert _bind(queue).scope == binding.scope
        assert queue.execution_closure_inventory(binding.scope)["acknowledged"] is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("actualMode", "legacy"),
        ("pluginId", "another-plugin"),
        ("version", True),
        ("namespaceGeneration", None),
        ("installGeneration", True),
        ("activationGeneration", 0),
        ("context", ""),
    ],
)
def test_incomplete_or_legacy_governance_never_qualifies(field, value):
    state = _state()
    state["baseline"][field] = value
    with pytest.raises(TaskError):
        resolve_execution_installation(_governance(state))


def test_environment_identity_without_real_receipts_cannot_qualify(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "COPILOT_EXTENSIONS_CONTEXT",
        json.dumps(
            {
                "installReceipt": str(tmp_path / "missing-install.json"),
                "marketplaceId": "caller-marketplace",
                "pluginId": "agent-dispatch",
            }
        ),
    )
    with pytest.raises((TaskError, ValueError)):
        resolve_execution_installation(LoopGovernance())


def _client(monkeypatch, queue, *, control: str | None = "control") -> TestClient:
    monkeypatch.setattr(routes, "LoopGovernance", _governance)
    return TestClient(
        create_app(
            queue,
            token="data",
            control_token=control,
            enable_mcp=False,
            verification_interval=0,
        )
    )


BODY = {"repo": TEST_REPO, "source": "scheduled", "expected_producer_generation": 1}


def _headers(token: str) -> dict[str, str]:
    scheme = "Be" + "arer"
    return {"Authorization": " ".join((scheme, token))}


def test_auth_header_preserves_the_actual_synthetic_credential():
    value = _headers("synthetic-credential")["Authorization"]
    assert value.split() == ["Be" + "arer", "synthetic-credential"]


def test_binding_prevents_a_different_closure_before_any_closure_exists(queue):
    scope = _bind(queue).scope
    with pytest.raises(TaskError, match="qualified producer scope"):
        queue.close_execution_admission(
            EffectRevocation(
                replace(scope, installation="caller-installation"),
                "operation-a",
                1,
                "automatic",
                "manual",
            ),
            source="scheduled",
            expected_producer_generation=1,
        )


@pytest.mark.parametrize("credential,expected", [(None, 401), ("data", 403), ("control", 200)])
def test_scope_resolution_requires_control_not_data_auth(monkeypatch, queue, credential, expected):
    client = _client(monkeypatch, queue)
    response = client.post(
        "/execution-scopes/resolve",
        json=BODY,
        headers={} if credential is None else _headers(credential),
    )
    assert response.status_code == expected
    if expected == 200:
        assert response.json()["acknowledged"] is False
        assert response.json()["runtime_enrolled"] is False
        assert response.json()["replayed"] is False
        assert (
            client.post(
                "/execution-scopes/resolve", json=BODY, headers=_headers("control")
            ).json()["replayed"]
            is True
        )
    else:
        with queue._connect() as conn:
            assert conn.execute("SELECT count(*) FROM execution_scope_bindings").fetchone()[0] == 0


def test_unconfigured_control_is_explicit_even_with_legacy_open_data_auth(monkeypatch, queue):
    monkeypatch.setattr(routes, "LoopGovernance", _governance)
    client = TestClient(create_app(queue, enable_mcp=False, verification_interval=0))
    response = client.post("/execution-scopes/resolve", json=BODY)
    assert response.status_code == 503
    assert response.json()["detail"]["reason"] == "control_authority_not_configured"


def test_task_scoped_authority_cannot_resolve_scopes(monkeypatch, queue):
    credential = mint_task_scope_token(
        "control", task_id="task-a", worker_id="worker-a", owner_session_id="session-a", epoch=0
    )
    response = _client(monkeypatch, queue).post(
        "/execution-scopes/resolve", json=BODY, headers={TASK_SCOPE_HEADER: credential}
    )
    assert response.status_code == 403


def test_missing_receipts_is_visible_not_unqualified_success(monkeypatch, queue):
    client = _client(monkeypatch, queue)
    monkeypatch.setattr(LoopGovernance, "recheck", lambda *_: None)
    response = client.post("/execution-scopes/resolve", json=BODY, headers=_headers("control"))
    assert response.status_code == 409
    assert "current installation governance" in response.json()["detail"]["reason"]


@pytest.mark.parametrize(
    "field", ["scope", "installation", "coordinator", "producer_registration"]
)
def test_caller_cannot_supply_qualification(monkeypatch, queue, field):
    response = _client(monkeypatch, queue).post(
        "/execution-scopes/resolve", json={**BODY, field: "caller"}, headers=_headers("control")
    )
    assert response.status_code == 422


def test_receipt_change_after_binding_returns_no_authority(monkeypatch, queue):
    state = _state()
    changed = deepcopy(state)
    changed["baseline"]["installGeneration"] += 1
    states = iter([state, changed])
    monkeypatch.setattr(
        routes, "LoopGovernance", lambda: LoopGovernance(helper=lambda _: next(states))
    )
    client = TestClient(
        create_app(queue, control_token="control", enable_mcp=False, verification_interval=0)
    )
    response = client.post("/execution-scopes/resolve", json=BODY, headers=_headers("control"))
    assert response.status_code == 409
    assert "changed during" in response.json()["detail"]["reason"]
    assert _bind(queue).replayed is True
