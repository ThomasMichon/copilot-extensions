"""Terminal-profile adoption preserves caller reachability and bounded delivery custody."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from fleet_manager.gateway_service_handler import CallerContext, ServiceRequest

from agent_pull_requests.review_workspace import build_generation
from agent_pull_requests.watch_contract import PRSnapshot
from agent_pull_requests.watch_gateway import WatchGateway
from agent_pull_requests.watch_replay import ObservationTarget
from agent_pull_requests.watch_replay_binding import ReplayBinding
from agent_pull_requests.watch_replay_store import ReplayStore
from agent_pull_requests.workspace_operations import WorkspaceOperations
from agent_pull_requests.workspace_terminal_read import SELECTION
from agent_pull_requests_service.config import HostConfig, load_config
from agent_pull_requests_service.terminal_transition import adopt

if TYPE_CHECKING:
    from services.pr.tests.test_host import Configuration, configuration as configuration, workspace_config
    from services.pr.tests.test_terminal_transition_eligibility import complete_evidence
else:
    from test_host import Configuration, configuration as configuration, workspace_config
    from test_terminal_transition_eligibility import complete_evidence


def expires() -> int:
    return int(time.time()) + 60


def configured(
    configuration: Configuration,
) -> tuple[Path, dict[str, Any], HostConfig, ReplayStore, dict[str, Any]]:
    path, value = workspace_config(configuration)
    evidence = complete_evidence()
    generation = build_generation(evidence)["generation"]
    value["workspaces"]["bindings"][0]["scope"] = {
        key: item for key, item in generation["scope"].items() if key != "number"
    }
    value["bindings"].append({
        **value["bindings"][0], "principal": "caller-b", "target": "repository-b", "consumer": "dispatch-b",
    })
    path.write_text(json.dumps(value))
    baseline = load_config(path)
    baseline.provision()
    value["workspaces"]["terminal_reads"] = True
    path.write_text(json.dumps(value))
    return path, value, load_config(path), baseline.store(), evidence


def register(
    store: ReplayStore, config: HostConfig, request: str, *, index: int = 0,
    until: tuple[str, ...] = ("merged",),
) -> dict[str, Any]:
    return store.execute(config.bindings[index].scope, "register", {
        "request_id": request, "issued_at": time.time(),
        "number": 42, "until": until, "deadline_at": time.time() + 60,
    }, expires_at=expires())


def gateway(
    config: HostConfig, evidence: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> tuple[WatchGateway, WorkspaceOperations]:
    store = config.store()
    assert config.workspaces is not None
    operations = config.workspaces.operations(store, lambda: None, 60)
    workspace = operations[("caller-a", "repository-a")]
    monkeypatch.setattr(workspace.read_owner, "read", lambda *args, **kwargs: {
        "generation": build_generation(evidence)["generation"], "acquisition": {},
    })
    return WatchGateway(
        store, fleet=config.fleet, gateway=config.gateway, slot=config.slot,
        bindings=config.bindings, workspaces=operations,
    ), workspace


def request(config: HostConfig, operation: str, payload: dict[str, Any], *, workspace: bool) -> ServiceRequest:
    scope = config.bindings[0].scope
    binding = ReplayBinding(
        config.fleet, config.gateway, config.slot, config.namespace,
        "caller-a", "repository-a", scope.consumer, scope.account, scope.repo,
    )
    body = {
        "binding": binding.document(), "request": payload, "workspace_contract": SELECTION,
    } if workspace else payload
    return ServiceRequest(
        CallerContext(config.fleet, config.gateway, config.slot, "caller-a", expires()),
        "POST", ("/v2/review/" if workspace else "/v1/watch/") + "repository-a/" + operation,
        (("content-type", "application/json"),), json.dumps(body).encode(),
    )


def test_changed_binding_refuses_and_preserves_original_pending_reader(
    configuration: Configuration,
) -> None:
    path, value, config, baseline, _ = configured(configuration)
    receipt = register(baseline, config, "original-binding", until=("head_changed",))
    target = ObservationTarget(
        config.bindings[0].scope.account, config.bindings[0].scope.repo, 42,
        frozenset({receipt["registration_id"]}),
    )
    baseline.observe(target, PRSnapshot(head_sha="a" * 40), expires_at=expires())
    baseline.observe(target, PRSnapshot(head_sha="b" * 40), expires_at=expires())
    pending = baseline.execute(config.bindings[0].scope, "pending", {}, expires_at=expires())
    value["bindings"][0]["consumer"] = "replacement-consumer"
    path.write_text(json.dumps(value))
    before = baseline.path.read_bytes()
    with pytest.raises(ValueError, match="replay scope"):
        adopt(load_config(path))
    assert baseline.path.read_bytes() == before
    assert baseline.execute(config.bindings[0].scope, "pending", {}, expires_at=expires()) == pending


def test_unsupported_retained_predicate_keeps_native_review_transition(
    configuration: Configuration,
) -> None:
    _, _, config, baseline, _ = configured(configuration)
    receipt = register(baseline, config, "retained-review", until=("review_changed",))
    scope = config.bindings[0].scope
    target = ObservationTarget(scope.account, scope.repo, 42, frozenset({receipt["registration_id"]}))
    baseline.observe(target, PRSnapshot(review_decision="REVIEW_REQUIRED"), expires_at=expires())
    before = baseline.path.read_bytes()
    with pytest.raises(ValueError, match="terminal predicates"):
        adopt(config)
    assert baseline.path.read_bytes() == before
    baseline.observe(target, PRSnapshot(review_decision="APPROVED"), expires_at=expires())
    events = baseline.execute(scope, "pending", {}, expires_at=expires())["events"]
    assert events[0]["transitions"] == ["review_changed"]


@pytest.mark.parametrize("completion", ["cancelled", "acknowledged"])
def test_incompatible_completed_receipt_refuses_adoption_and_keeps_exact_retry(
    configuration: Configuration, completion: str,
) -> None:
    _, _, config, baseline, _ = configured(configuration)
    scope = config.bindings[0].scope
    original: dict[str, Any] = {
        "request_id": "original-completed", "issued_at": time.time(),
        "number": 42,
        "until": ("review_changed",) if completion == "acknowledged" else ("merged",),
        "deadline_at": time.time() + 60 if completion == "acknowledged" else None,
    }
    receipt = baseline.execute(scope, "register", original, expires_at=expires())
    target = ObservationTarget(scope.account, scope.repo, 42, frozenset({receipt["registration_id"]}))
    finish = {
        "request_id": original["request_id"], "issued_at": original["issued_at"],
        "registration_id": receipt["registration_id"],
    }
    if completion == "acknowledged":
        baseline.observe(target, PRSnapshot(review_decision="REVIEW_REQUIRED"), expires_at=expires())
        baseline.observe(target, PRSnapshot(review_decision="APPROVED"), expires_at=expires())
        event = baseline.execute(scope, "pending", {}, expires_at=expires())["events"][0]
        finish["event_id"] = event["event_id"]
    completed = baseline.execute(
        scope, "acknowledge" if completion == "acknowledged" else "cancel",
        finish, expires_at=expires(),
    )
    before = baseline.path.read_bytes()
    with pytest.raises(ValueError, match="terminal predicates" if completion == "acknowledged" else "finite deadline"):
        adopt(config)
    assert baseline.path.read_bytes() == before
    assert baseline.execute(scope, "register", original, expires_at=expires()) == completed


@pytest.mark.parametrize("support", ["missing", "incompatible"])
def test_missing_admission_core_refuses_terminal_selection_but_keeps_baseline(
    configuration: Configuration, monkeypatch: pytest.MonkeyPatch, support: str,
) -> None:
    from agent_pull_requests import watch_admission

    path, value, _, baseline, _ = configured(configuration)
    before = baseline.path.read_bytes()
    if support == "missing":
        monkeypatch.delattr(watch_admission, "contract")
    else:
        monkeypatch.setattr(watch_admission, "contract", lambda: {"protocol": "unsupported"})
    with pytest.raises(ValueError, match="core unsupported"):
        load_config(path)
    value["workspaces"]["terminal_reads"] = False
    path.write_text(json.dumps(value))
    owner = load_config(path).store()
    assert owner.observation_targets(expires_at=expires()) == ()
    assert owner.path.read_bytes() == before


def test_frozen_pending_event_is_captured_without_replacement(
    configuration: Configuration, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, config, baseline, evidence = configured(configuration)
    receipt = register(baseline, config, "frozen-head", until=("head_changed",))
    scope = config.bindings[0].scope
    target = ObservationTarget(scope.account, scope.repo, 42, frozenset({receipt["registration_id"]}))
    baseline.observe(target, PRSnapshot(head_sha="a" * 40), expires_at=expires())
    baseline.observe(target, PRSnapshot(head_sha="b" * 40), expires_at=expires())
    pending = baseline.execute(scope, "pending", {}, expires_at=expires())
    adopt(config)
    selected, _ = gateway(config, evidence, monkeypatch)
    evidence["sections"]["pull_request"]["data"]["status"] = "completed"

    async def scenario() -> None:
        response = await selected.handle(request(config, "load", {
            "number": 42, "expected_current": None, "budget_seconds": 10,
        }, workspace=True))
        assert response.status == 200
        await selected.drain(timeout=2)

    asyncio.run(scenario())
    assert selected.store.execute(scope, "pending", {}, expires_at=expires()) == pending
    deliveries = json.loads(selected.store.path.read_bytes())["terminal_retention"]["records"][0]["deliveries"]
    assert len(deliveries) == 1
    assert deliveries[0]["event_id"] == pending["events"][0]["event_id"]
    assert deliveries[0]["deadline_at"] == receipt["deadline_at"]
    assert deliveries[0]["frozen_before_terminal"] is True


def test_high_fanout_terminal_gateway_response_does_not_expose_delivery_custody(
    configuration: Configuration, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, config, baseline, evidence = configured(configuration)
    receipts = [register(baseline, config, "own-request")]
    receipts.extend(register(baseline, config, f"private-{index}-" + "x" * 110, index=1) for index in range(96))
    adopt(config)
    selected, _ = gateway(config, evidence, monkeypatch)
    evidence["sections"]["pull_request"]["data"]["status"] = "completed"

    async def scenario() -> None:
        response = await selected.handle(request(config, "load", {
            "number": 42, "expected_current": None, "budget_seconds": 10,
        }, workspace=True))
        assert response.status == 200 and len(response.body) < 32768
        value = json.loads(response.body)
        retirement = value["result"]["conversation"]["retirement"]
        assert set(retirement) == {"cursor", "drain", "effects"}
        assert all(receipt["registration_id"].encode() not in response.body for receipt in receipts)
        assert b"private-" not in response.body and b"dispatch-b" not in response.body
        await selected.drain(timeout=2)

    asyncio.run(scenario())
    row = json.loads(selected.store.path.read_bytes())["terminal_retention"]["records"][0]
    assert len(row["deliveries"]) == 97 and len(json.dumps(row).encode()) > 32768
    assert not selected.store.execute(
        config.bindings[1].scope, "pending", {"limit": 1}, expires_at=expires(),
    )["events"]


@pytest.mark.parametrize("unsupported", ["deadline", "predicate"])
def test_terminal_gateway_rejects_incompatible_new_registration_before_commit(
    configuration: Configuration, monkeypatch: pytest.MonkeyPatch, unsupported: str,
) -> None:
    _, _, config, _, evidence = configured(configuration)
    adopt(config)
    selected, workspace = gateway(config, evidence, monkeypatch)
    capabilities = workspace.execute("capabilities", {}, expires_at=expires(), checkpoint=lambda: None)
    assert capabilities["watch_registration_policy"]["deadline"] == "finite_original"
    identity = {"request_id": "new-client", "issued_at": time.time()}
    payload = {
        **identity, "number": 42,
        "until": ["review_changed"] if unsupported == "predicate" else ["merged"],
        "deadline_at": None if unsupported == "deadline" else time.time() + 60,
    }
    before = selected.store.path.read_bytes()

    async def scenario() -> None:
        refused = await selected.handle(request(config, "register", payload, workspace=False))
        assert refused.status == 400
        assert selected.store.path.read_bytes() == before
        payload.update(until=["merged"], deadline_at=time.time() + 60)
        admitted = await selected.handle(request(config, "register", payload, workspace=False))
        assert admitted.status == 200
        receipt = json.loads(admitted.body)
        assert receipt["deadline_at"] == payload["deadline_at"]
        evidence["sections"]["pull_request"]["data"]["status"] = "completed"
        terminal = await selected.handle(request(config, "load", {
            "number": 42, "expected_current": None, "budget_seconds": 10,
        }, workspace=True))
        assert terminal.status == 200
        assert not selected.store.execute(
            config.bindings[0].scope, "pending", {}, expires_at=expires(),
        )["events"]
        selected.store.observe(
            selected.store.observation_targets(expires_at=expires())[0],
            PRSnapshot(pr_state="closed", merged=True), expires_at=expires(),
        )
        pending = selected.store.execute(
            config.bindings[0].scope, "pending", {}, expires_at=expires(),
        )["events"]
        assert pending[0]["registration_id"] == receipt["registration_id"]
        assert pending[0]["transitions"] == ["merged"]
        await selected.drain(timeout=2)

    asyncio.run(scenario())
