"""Use the real registered writer on a disposable record; never create a Dispatch request."""

import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from test_resource_producer import owner as owner
from test_resource_relationship_incarnation import installed_writer as installed_writer

from agent_worktrees import __main__ as cli
from agent_worktrees import config, output, tracking
from agent_worktrees.tracking_lifecycle import link_succession, open_handoff
from agent_worktrees.tracking_session_registry import register_session

Json = dict[str, Any]


def invoke(monkeypatch: pytest.MonkeyPatch, operation: str, payload: Json) -> Json:
    monkeypatch.setattr(
        sys, "stdin", SimpleNamespace(buffer=io.BytesIO(json.dumps(payload).encode()))
    )
    with output.capture_json_output() as capture:
        code = cli.main(["resource-relationship", operation, "--owner-ref", "m/p/owner", "--json"])
    data: Json = json.loads(capture.getvalue())
    assert code == (1 if data.get("error") else 0)
    return data


@pytest.fixture
def reserved(
    owner: tuple[Path, config.Config], installed_writer: Json, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Json]:
    path, _ = owner
    caps = invoke(monkeypatch, "capabilities", {})
    assert caps["compare_and_bind"] == 1 and "compare-and-bind" in caps["operations"]
    register_session("owner", "original")
    record = tracking.load_record(path)
    transition = record.replayed_head_transition
    assert transition is not None
    pins = {
        "expected_creation_nonce": record.creation_nonce,
        "expected_writer_identity": installed_writer,
    }
    pending = invoke(
        monkeypatch,
        "reserve",
        {
            **pins,
            "kind": "task",
            "namespace": "fixture",
            "request_id": "prospective",
            "recovery": {},
        },
    )
    assert pending["phase"] == "pending"
    return path, {
        **pins,
        "expected_head": {"session_id": "original", "revision": transition.revision},
        "request_id": "prospective",
        "token": pending["token"],
        "identity": {"event": 7},
    }


@pytest.mark.parametrize("drift", ["handoff", "revision", "request", "nonce", "writer"])
def test_refusal_precedes_any_record_write(
    reserved: tuple[Path, Json],
    monkeypatch: pytest.MonkeyPatch,
    drift: str,
) -> None:
    path, payload = reserved
    if drift == "handoff":
        open_handoff(tracking.load_record(path), "original", "incoming")
    elif drift == "revision":
        payload["expected_head"]["revision"] += 1
    elif drift == "request":
        payload["request_id"] = "unrelated"
    elif drift == "nonce":
        payload["expected_creation_nonce"] = "stale"
    else:
        payload["expected_writer_identity"]["runtime_generation"] = "2" * 32
    before = path.read_bytes()
    assert invoke(monkeypatch, "compare-and-bind", payload).get("error")
    assert path.read_bytes() == before


def test_replay_is_historical_even_after_succession(
    reserved: tuple[Path, Json],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, payload = reserved
    first = invoke(monkeypatch, "compare-and-bind", payload)
    assert first["acceptance"] == "bound-at-head" and "head" not in first
    assert first["producer"]["head_bind"]["head"] == payload["expected_head"]
    for advance in (False, True):
        if advance:
            register_session("owner", "successor")
            link_succession(tracking.load_record(path), "original", "successor")
            assert tracking.load_record(path).resolved_head_session == "successor"
        before = path.read_bytes()
        replay = invoke(monkeypatch, "compare-and-bind", payload)
        assert replay == {**first, "acceptance": "previously-bound"}
        assert path.read_bytes() == before
    assert invoke(monkeypatch, "compare-and-bind", {**payload, "identity": {"event": 8}}).get(
        "error"
    )
    assert invoke(
        monkeypatch,
        "compare-and-bind",
        {
            **payload,
            "expected_head": {"session_id": "successor", "revision": 99},
        },
    ).get("error")
    assert path.read_bytes() == before


def test_existing_ordinary_bind_is_not_an_acceptance(
    reserved: tuple[Path, Json],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, payload = reserved
    assert invoke(monkeypatch, "bind", {k: payload[k] for k in ("token", "identity")})["ok"]
    before = path.read_bytes()
    assert invoke(monkeypatch, "compare-and-bind", payload).get("error")
    assert path.read_bytes() == before


def test_lost_reply_after_save_does_not_repeat_the_acceptance(
    reserved: tuple[Path, Json], monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, payload = reserved

    def lost(*args: object, **kwargs: object) -> None:
        raise RuntimeError("synthetic post-save failure")

    with monkeypatch.context() as patch:
        patch.setattr("agent_worktrees.tracking_relationship_write.activity.log_event", lost)
        assert invoke(patch, "compare-and-bind", payload).get("error")
    saved = path.read_bytes()
    result = invoke(monkeypatch, "compare-and-bind", payload)
    assert result["acceptance"] == "previously-bound" and "head" not in result
    assert result["producer"]["head_bind"]["head"] == payload["expected_head"]
    assert path.read_bytes() == saved
