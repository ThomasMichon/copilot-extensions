"""Use the actual registrar reader, not its eventually mirrored registration rows."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from agent_dispatch.coordinator import create_app
from agent_dispatch.overrides import set_override
from agent_dispatch.queue import Status
from agent_dispatch.registrar_discovery import RegistrarSources, add_pointer
from tests.test_pool_catalog_transport import _headers
from tests.test_task_relevance_registration import SPEC, emitted, emitter, q as q
from tests.test_task_relevance_transport import body


@pytest.mark.parametrize("source_state", ["empty", "superseded", "ambiguous", "incomplete"])
def test_actual_configured_registrar_is_consulted_before_relevance_effect(
    q, tmp_path, monkeypatch, source_state,
) -> None:
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    document = trusted / "source.json"
    if source_state in {"superseded", "ambiguous"}:
        document.write_text(json.dumps({
            "name": "review-source", "owner": "example", "kind": "emitter",
            "spec": {
                **SPEC,
                **({"command": ["different-source-reader", "tick"]} if source_state == "ambiguous" else {}),
            },
        }), encoding="utf-8")
    elif source_state == "incomplete":
        document.write_text("{invalid", encoding="utf-8")
    base = tmp_path / "registrar"
    add_pointer("trusted", trusted, base=base)
    sources = RegistrarSources(
        base=base, dropins=tmp_path / "absent-dropins", activation_source=lambda: None,
    )
    override_path = tmp_path / "overrides.json"
    set_override(override_path, "declared:example:review-source", reason="source held")
    monkeypatch.setattr("agent_dispatch.remote_dispatch.local_machine", lambda: "host-a")
    monkeypatch.setenv("AGENT_DISPATCH_ENV", "default")
    monkeypatch.setenv("AGENT_DISPATCH_OVERRIDES", str(override_path))
    emitter(q)
    t = emitted(q)
    api = TestClient(create_app(
        q, token="data", control_token="control", enable_mcp=False,
        verification_interval=0, cohort_sources=sources, wake_is_active=lambda: True,
    ))
    assert q.get_registration("declared:example:review-source") is None
    response = api.post(f"/task-relevance/tasks/{t.id}", headers=_headers(), json=body(q, t.id))
    assert sources.last_report is not None
    if source_state == "empty":
        assert response.status_code == 200, response.text
        assert q.get(t.id).status == Status.ABANDONED
    else:
        assert response.status_code == 409, response.text
        assert source_state in response.json()["detail"]
        assert q.get(t.id).status == Status.QUEUED
        assert q.relevance_receipt(t.id, "closed-object") is None
