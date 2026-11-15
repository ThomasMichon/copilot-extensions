"""Declared and evaluator admission through the actual registrar and HTTP owner."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient
import pytest

from agent_dispatch.client import DispatchClient, DispatchUpgradeRequired
from agent_dispatch.coordinator import create_app
from agent_dispatch.overrides import set_override
from agent_dispatch.queue import Status
from agent_dispatch.registrar_discovery import RegistrarSources, add_pointer
from agent_dispatch.task_relevance_contract import relevance_contract
from tests._helpers import TEST_REPO
from tests.test_pool_catalog_transport import _headers
from tests.test_task_relevance_registration import SPEC, emitted, emitter, q as q
from tests.test_task_relevance_transport import transport


@pytest.fixture
def venue(q, tmp_path, monkeypatch):
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    base = tmp_path / "registrar"
    add_pointer("trusted", trusted, base=base)
    sources = RegistrarSources(
        base=base, dropins=tmp_path / "absent-dropins", activation_source=lambda: None,
    )
    override_path = tmp_path / "overrides.json"
    monkeypatch.setattr("agent_dispatch.remote_dispatch.local_machine", lambda: "host-a")
    monkeypatch.setenv("AGENT_DISPATCH_ENV", "default")
    monkeypatch.setenv("AGENT_DISPATCH_OVERRIDES", str(override_path))
    api = TestClient(create_app(
        q, token="data", control_token="control", enable_mcp=False,
        verification_interval=0, cohort_sources=sources, wake_is_active=lambda: True,
    ))
    return api, trusted, override_path


def declaration(trusted, name: str, kind: str, spec: dict, **fields) -> str:
    (trusted / f"{name}.json").write_text(json.dumps({
        "name": name, "owner": "example", "kind": kind, "spec": spec, **fields,
    }), encoding="utf-8")
    return f"declared:example:{name}"


def evaluator(q, venue, *, declared: bool = True, **changes) -> str:
    spec = {
        "repo": TEST_REPO, "evaluator_ref": "review-loop",
        "evaluator_spec": {"rules": []}, **changes,
    }
    if declared:
        return declaration(venue[1], "review-evaluator", "evaluator", spec)
    q.register_registration("evaluator", spec, reg_id="evaluator-a", machine="host-a")
    return "evaluator-a"


def request(api: TestClient, task_id: str, registration_id: str) -> dict:
    response = api.get(
        f"/task-relevance/tasks/{task_id}", headers=_headers(),
        params={"registration_id": registration_id},
    )
    assert response.status_code == 200, response.text
    snapshot = response.json()["result"]
    return {
        "version": 1, "registration_id": registration_id,
        "registration_revision": snapshot["registration_revision"],
        "effective_revision": snapshot["effective_revision"],
        "expected_generation": snapshot["generation"], "expected_revision": snapshot["revision"],
        "request_id": "closed-object", "verdict": "obsolete",
        "reason": "Backing object is closed", "observation_ref": "provider:revision-2",
    }


@pytest.mark.parametrize("legacy_row", [False, True])
def test_declared_emitter_uses_fresh_source_not_its_mirror(q, venue, legacy_row) -> None:
    api, trusted, _ = venue
    rid = declaration(trusted, "review-source", "emitter", SPEC)
    if legacy_row:
        emitter(q)
    t = emitted(q)
    assert q.get_registration(rid) is None
    report = request(api, t.id, rid)
    accepted = api.post(f"/task-relevance/tasks/{t.id}", headers=_headers(), json=report)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["result"]["decision"] == "abandoned"
    assert q.get(t.id).status == Status.ABANDONED
    assert q.steer_log(t.id) == q.list_wakes(t.id) == []
    declaration(trusted, "review-source", "emitter", {**SPEC, "id": "different-source"})
    assert api.post(
        f"/task-relevance/tasks/{t.id}", headers=_headers(), json=report,
    ).json() == accepted.json()


@pytest.mark.parametrize("declared_emitter", [False, True])
@pytest.mark.parametrize("declared_evaluator", [False, True])
def test_companion_evaluator_uses_repo_ref_and_emitter_provenance(
    q, venue, declared_emitter, declared_evaluator,
) -> None:
    api, trusted, _ = venue
    if declared_emitter:
        declaration(trusted, "review-source", "emitter", {**SPEC, "evaluator_ref": "review-loop"})
    else:
        emitter(q, evaluator_ref="review-loop")
    rid = evaluator(q, venue, declared=declared_evaluator)
    t = emitted(q, evaluator_ref="review-loop")
    report = request(api, t.id, rid)
    response = api.post(f"/task-relevance/tasks/{t.id}", headers=_headers(), json=report)
    assert response.status_code == 200, response.text
    assert response.json()["result"]["decision"] == "abandoned"
    assert q.get(t.id).status == Status.ABANDONED


@pytest.mark.parametrize("change", ["spec", "remove", "incomplete", "override", "logical_override"])
def test_declared_movement_or_hold_cannot_reuse_source_observation(q, venue, change) -> None:
    api, trusted, override_path = venue
    rid = declaration(trusted, "review-source", "emitter", SPEC)
    t = emitted(q)
    report = request(api, t.id, rid)
    if change == "spec":
        declaration(trusted, "review-source", "emitter", {**SPEC, "command": ["new-reader"]})
    elif change == "remove":
        (trusted / "review-source.json").unlink()
    elif change == "incomplete":
        (trusted / "review-source.json").write_text("{invalid", encoding="utf-8")
    else:
        key = rid if change == "override" else "logical:example:review-source"
        set_override(override_path, key, reason="source held")
    response = api.post(f"/task-relevance/tasks/{t.id}", headers=_headers(), json=report)
    assert response.status_code == 409, response.text
    assert q.get(t.id).status == Status.QUEUED
    assert q.relevance_receipt(t.id, "closed-object") is None


@pytest.mark.parametrize("mismatch", [
    "manual", "origin", "task_ref", "emitter_ref", "evaluator_repo", "duplicate_evaluator",
    "emitter_hold", "evaluator_hold", "foreign_emitter",
])
def test_evaluator_cannot_expand_beyond_its_companion_source(q, venue, mismatch) -> None:
    api, trusted, override_path = venue
    declaration(trusted, "review-source", "emitter", {
        **SPEC, "evaluator_ref": "other-loop" if mismatch == "emitter_ref" else "review-loop",
    }, **({"filters": {"permit": {"machine": ["host-b"]}}} if mismatch == "foreign_emitter" else {}))
    rid = evaluator(q, venue, **({"repo": "other/repo"} if mismatch == "evaluator_repo" else {}))
    if mismatch == "duplicate_evaluator":
        evaluator(q, venue, declared=False, evaluator_spec={
            "rules": [{"on": "task.submitted", "confirm": True}],
        })
    if mismatch in {"emitter_hold", "evaluator_hold"}:
        held = "declared:example:review-source" if mismatch == "emitter_hold" else rid
        set_override(override_path, held, reason="held")
    t = emitted(q, **{
        "evaluator_ref": "other-loop" if mismatch == "task_ref" else "review-loop",
        **({"source": "manual"} if mismatch == "manual" else {}),
        **({"origin_ref": "unrelated-source"} if mismatch == "origin" else {}),
    })
    response = api.get(
        f"/task-relevance/tasks/{t.id}", headers=_headers(), params={"registration_id": rid},
    )
    assert response.status_code == 409, response.text
    assert q.get(t.id).status == Status.QUEUED


def test_evaluator_closeout_keeps_existing_owner_and_survives_response_replay(q, venue) -> None:
    api, trusted, _ = venue
    declaration(trusted, "review-source", "emitter", {**SPEC, "evaluator_ref": "review-loop"})
    rid = evaluator(q, venue)
    t = emitted(q, evaluator_ref="review-loop")
    q.claim_one("host/worker", task_id=t.id)
    q.start(t.id, "host/worker", owner_session_id="original-native")
    report = request(api, t.id, rid)
    first = api.post(f"/task-relevance/tasks/{t.id}", headers=_headers(), json=report)
    assert first.status_code == 200, first.text
    assert first.json()["result"]["decision"] == "closeout_pending"
    assert api.post(
        f"/task-relevance/tasks/{t.id}", headers=_headers(), json=report,
    ).json() == first.json()
    [wake] = q.list_wakes(t.id)
    assert (wake.owner, wake.owner_session_id) == ("host/worker", "original-native")
    assert len(q.steer_log(t.id)) == 1
    assert q.get(t.id).status == Status.STARTED


def test_extended_scope_requires_effective_revision_without_breaking_prior_direct_body(q, venue) -> None:
    api, trusted, _ = venue
    rid = declaration(trusted, "review-source", "emitter", SPEC)
    t = emitted(q)
    report = request(api, t.id, rid)
    report.pop("effective_revision")
    response = api.post(f"/task-relevance/tasks/{t.id}", headers=_headers(), json=report)
    assert response.status_code == 409, response.text
    assert q.get(t.id).status == Status.QUEUED


def test_evaluator_precedence_reuses_verification_selection_and_rejects_stale_config(q, venue) -> None:
    api, trusted, _ = venue
    declaration(trusted, "review-source", "emitter", {**SPEC, "evaluator_ref": "review-loop"})
    global_id = evaluator(q, venue, declared=False, all_repos=True)
    exact_id = evaluator(q, venue)
    t = emitted(q, evaluator_ref="review-loop")
    response = api.get(
        f"/task-relevance/tasks/{t.id}", headers=_headers(), params={"registration_id": global_id},
    )
    assert response.status_code == 409, response.text
    report = request(api, t.id, exact_id)
    evaluator(q, venue, evaluator_spec={"rules": [{"on": "task.submitted", "confirm": True}]})
    response = api.post(f"/task-relevance/tasks/{t.id}", headers=_headers(), json=report)
    assert response.status_code == 409, response.text
    assert "effective registration revision changed" in response.json()["detail"]
    assert q.get(t.id).status == Status.QUEUED


def test_current_client_passes_effective_observation_to_current_receiver(q, venue) -> None:
    api, trusted, _ = venue
    declaration(trusted, "review-source", "emitter", {**SPEC, "evaluator_ref": "review-loop"})
    rid = evaluator(q, venue)
    t = emitted(q, evaluator_ref="review-loop")
    with DispatchClient(
        "http://testserver", token="data", control_token="control", transport=transport(api, []),
    ) as client:
        snapshot = client.relevance_snapshot(t.id, registration_id=rid, effective_scope=True)
        report = request(api, t.id, rid)
        assert snapshot["effective_revision"] == report["effective_revision"]
        accepted = client.report_task_relevance(t.id, report=report)
        assert accepted["decision"] == "abandoned"
        assert client.relevance_receipt(
            t.id, report["request_id"], registration_id=rid,
        )["acceptance"] == accepted


@pytest.mark.parametrize("bound", [False, True])
@pytest.mark.parametrize("held", ["source-a", "logical:local:review-source", "evaluator-a"])
def test_replacement_retains_predecessor_holds_for_emitter_and_evaluator(
    q, venue, bound, held,
) -> None:
    api, trusted, override_path = venue
    emitter(q, evaluator_ref="review-loop")
    declaration(trusted, "review-source", "emitter", {**SPEC, "evaluator_ref": "review-loop"})
    evaluator(q, venue, declared=False)
    rid = evaluator(q, venue)
    t = emitted(q, evaluator_ref="review-loop")
    if bound:
        q.claim_one("host/worker", task_id=t.id)
        q.start(t.id, "host/worker", owner_session_id="original-native")
    report = request(api, t.id, rid)
    set_override(override_path, held, reason="predecessor held")
    response = api.post(f"/task-relevance/tasks/{t.id}", headers=_headers(), json=report)
    assert response.status_code == 409, response.text
    assert "operator override" in response.json()["detail"]
    assert q.get(t.id).status == (Status.STARTED if bound else Status.QUEUED)
    assert q.steer_log(t.id) == q.list_wakes(t.id) == []
    assert q.relevance_receipt(t.id, "closed-object") is None


@pytest.mark.parametrize("reporter", ["emitter", "evaluator"])
@pytest.mark.parametrize("mirrored", [False, True])
def test_fresh_foreign_declaration_provenance_cannot_look_unique(q, venue, reporter, mirrored) -> None:
    api, trusted, _ = venue
    spec = {**SPEC, "evaluator_ref": "review-loop"}
    rid = declaration(trusted, "review-source", "emitter", spec)
    if reporter == "evaluator":
        rid = evaluator(q, venue)
    t = emitted(q, evaluator_ref="review-loop")
    report = request(api, t.id, rid)
    foreign_id = declaration(
        trusted, "foreign-source", "emitter", spec,
        filters={"permit": {"machine": ["host-b"]}},
    )
    if mirrored:
        q.register_registration("emitter", spec, reg_id=foreign_id, machine="host-b")
    snapshot = api.get(
        f"/task-relevance/tasks/{t.id}", headers=_headers(), params={"registration_id": rid},
    )
    assert snapshot.status_code == 409, snapshot.text
    assert "ambiguous" in snapshot.json()["detail"]
    response = api.post(f"/task-relevance/tasks/{t.id}", headers=_headers(), json=report)
    assert response.status_code == 409, response.text
    assert q.get(t.id).status == Status.QUEUED
    assert q.relevance_receipt(t.id, "closed-object") is None


@pytest.mark.parametrize("held", [False, True])
@pytest.mark.parametrize("bound", [False, True])
@pytest.mark.parametrize("predecessor,reporter", [
    ("source-a", "emitter"), ("source-a", "evaluator"), ("evaluator-a", "evaluator"),
])
def test_paused_predecessor_carries_hold_without_becoming_eligible(
    q, venue, held, bound, predecessor, reporter,
) -> None:
    api, trusted, override_path = venue
    emitter(q, evaluator_ref="review-loop")
    emitter_id = declaration(
        trusted, "review-source", "emitter", {**SPEC, "evaluator_ref": "review-loop"},
    )
    evaluator(q, venue, declared=False)
    evaluator_id = evaluator(q, venue)
    q.set_registration_status(predecessor, "paused")
    rid = emitter_id if reporter == "emitter" else evaluator_id
    t = emitted(q, evaluator_ref="review-loop")
    if bound:
        q.claim_one("host/worker", task_id=t.id)
        q.start(t.id, "host/worker", owner_session_id="original-native")
    report = request(api, t.id, rid)
    if held:
        set_override(override_path, predecessor, reason="paused predecessor held")
    response = api.post(f"/task-relevance/tasks/{t.id}", headers=_headers(), json=report)
    assert response.status_code == (409 if held else 200), response.text
    if held:
        assert "operator override" in response.json()["detail"]
        assert q.get(t.id).status == (Status.STARTED if bound else Status.QUEUED)
        assert q.steer_log(t.id) == q.list_wakes(t.id) == []
        assert q.relevance_receipt(t.id, "closed-object") is None
    else:
        assert response.json()["result"]["decision"] == ("closeout_pending" if bound else "abandoned")


@pytest.mark.parametrize("hold", ["declared:example:review-evaluator", "evaluator-a"])
def test_held_repo_evaluator_does_not_authorize_all_repos_fallback(q, venue, hold) -> None:
    api, trusted, override_path = venue
    declaration(trusted, "review-source", "emitter", {**SPEC, "evaluator_ref": "review-loop"})
    exact_id = evaluator(q, venue)
    evaluator(q, venue, declared=False)
    global_id = declaration(trusted, "global-evaluator", "evaluator", {
        "all_repos": True, "evaluator_ref": "review-loop", "evaluator_spec": {"rules": []},
    })
    t = emitted(q, evaluator_ref="review-loop")
    set_override(override_path, hold, reason="repo evaluator held")
    for rid in (exact_id, global_id):
        response = api.get(
            f"/task-relevance/tasks/{t.id}", headers=_headers(), params={"registration_id": rid},
        )
        assert response.status_code == 409, response.text
    assert q.get(t.id).status == Status.QUEUED


@pytest.mark.parametrize("machine,env,status,expected", [
    (None, "default", "active", 409),
    ("host-b", "default", "active", 200),
    (None, "other", "active", 200),
    (None, "default", "paused", 200),
])
def test_applicable_unscoped_evaluator_keeps_repo_precedence_without_caller_authority(
    q, venue, machine, env, status, expected,
) -> None:
    api, trusted, _ = venue
    declaration(trusted, "review-source", "emitter", {**SPEC, "evaluator_ref": "review-loop"})
    global_id = declaration(trusted, "global-evaluator", "evaluator", {
        "all_repos": True, "evaluator_ref": "review-loop", "evaluator_spec": {"rules": []},
    })
    q.register_registration("evaluator", {
        "repo": TEST_REPO, "evaluator_ref": "review-loop", "evaluator_spec": {"rules": []},
    }, reg_id="exact-evaluator", machine=machine, env=env)
    if status == "paused":
        q.set_registration_status("exact-evaluator", status)
    t = emitted(q, evaluator_ref="review-loop")
    response = api.get(
        f"/task-relevance/tasks/{t.id}", headers=_headers(), params={"registration_id": global_id},
    )
    assert response.status_code == expected, response.text
    if machine is None:
        unscoped = api.get(
            f"/task-relevance/tasks/{t.id}", headers=_headers(),
            params={"registration_id": "exact-evaluator"},
        )
        assert unscoped.status_code == 409, unscoped.text
    assert q.get(t.id).status == Status.QUEUED


def test_new_client_refuses_prior_scope_before_report_and_keeps_old_reads(q, venue) -> None:
    api, trusted, _ = venue
    rid = declaration(trusted, "review-source", "emitter", SPEC)
    t = emitted(q)
    report = request(api, t.id, rid)
    contract = relevance_contract("control")
    contract["registration_scopes"] = ["local-direct-command-emitter"]
    calls = []
    with DispatchClient(
        "http://testserver", token="data", control_token="control",
        transport=transport(api, calls, contract),
    ) as client:
        with pytest.raises(DispatchUpgradeRequired, match="local-effective-emitter-evaluator"):
            client.report_task_relevance(t.id, report=report)
        with pytest.raises(DispatchUpgradeRequired):
            client.relevance_snapshot(t.id, registration_id=rid)
        assert client.get(t.id)["status"] == Status.QUEUED
    assert not any(method == "POST" for method, _path in calls)
