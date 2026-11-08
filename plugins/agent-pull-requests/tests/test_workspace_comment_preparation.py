"""Selected local preparation through the actual CLI, Gateway and native owner."""

from __future__ import annotations

import asyncio
import copy
from pathlib import Path

import pytest

from test_review_executor import environment as environment
from test_review_owner_store import prepared_intent as prepared_intent
from test_workspace_integration import integration as integration, prepared_workspace as prepared_workspace
from test_workspace_tools import invoke_cli

from agent_pull_requests.workspace_tools import WorkspaceTools

pytestmark = pytest.mark.parametrize("environment", [True], indirect=True)


@pytest.fixture
def comment_preparer(environment: tuple, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple:
    from types import SimpleNamespace

    root = Path(__file__).resolve().parents[3]
    for source in (root / "libs").glob("*/src"):
        monkeypatch.syspath_prepend(str(source))
    monkeypatch.syspath_prepend(str(root / "plugins/ai-attribution/scripts"))
    from agent_worktrees import attribution_prepare_cli, tracking
    import attribution_profile
    import comment_preparation

    record = tracking.parse_record_data({
        "worktree_id": "private-tree", "branch": "worktree/private-tree", "repo": "fixture",
        "machine": "private-machine", "platform": "linux", "codename": "bolted-circuit",
        "codename_source": "built-in", "worktree_path": "fixture-root", "sessions": [],
    }, path=tmp_path / "source.yaml")
    config = SimpleNamespace(default_repo=SimpleNamespace(pr=SimpleNamespace(
        source_attribution="codename", source_attribution_configured=False,
    )))
    origin = attribution_prepare_cli.prepare_comment_origin(record, config)
    state = {"profile": attribution_profile.validate({
        "version": 1, "rules": [{"header": "[Selected AI] "}],
    }), "results": []}

    def prepare(kind: str, body: str, *, scope: dict, **identity: object) -> dict:
        if scope != environment[1]["scope"]:
            raise PermissionError("selected preparation destination differs")
        context = {"domain": "forge", "visibility": "public",
                   "activity": "comment" if kind == "edit_owned_comment" else kind, "required": True}
        result = comment_preparation.prepare(body, state["profile"], context, origin, **identity)
        state["results"].append(copy.deepcopy(result))
        return result

    return prepare, state


@pytest.mark.parametrize("kind", ["comment", "reply", "edit_owned_comment"])
@pytest.mark.parametrize("transport", ["native", "gateway"])
def test_local_prepare_preview_then_explicit_submit_retains_exact_selected_identity(
    environment: tuple, prepared_workspace: tuple, comment_preparer: tuple,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], kind: str, transport: str,
) -> None:
    from agent_pull_requests.review_workspace import _digest, _record
    from agent_pull_requests.watch_replay_store import ReplayStore

    store, selected, generation, attachment, backend, _ = environment
    _, client, native, _, _, _ = prepared_workspace
    prepare, state = comment_preparer
    logical = "11111111-1111-4111-8111-111111111111"
    write = "22222222-2222-4222-8222-222222222222"
    previous = None
    action = {"id": "prepared-note", "kind": kind, "body": "Selected note."}
    if kind == "edit_owned_comment":
        previous = prepare("comment", "Original note.", scope=selected["scope"],
                           comment_id=logical, write_id=write, previous=None)
        write = "33333333-3333-4333-8333-333333333333"
    if kind != "comment":
        thread = {"id": 50, "status": 1, "comments": [{
            "id": 1, "parentCommentId": 0, "commentType": 1,
            "content": previous["body"] if previous else "Parent note.",
            "author": {"id": selected["scope"]["principal_id"]},
        }]}
        backend.threads = [copy.deepcopy(thread)]
        old_id = generation["id"]
        generation["sections"]["discussions"]["data"] = [_record("discussions", thread)]
        generation["id"] = _digest({key: value for key, value in generation.items() if key != "id"})
        selected["generation_id"] = attachment["generation_id"] = generation["id"]
        native.operations.cache.publish(generation, expected_current=old_id)
        action["thread_id"] = 50
        if previous:
            action.update(comment_id=1, expected_content_id=_digest(previous["body"]))
    draft = {**copy.deepcopy(selected), "version": 1, "actions": [action, selected["actions"][-1]]}
    request = {
        "number": 42, "intent": draft, "identities": {
            "prepared-note": {"comment_id": logical, "write_id": write, "previous": previous},
        },
    }
    original = copy.deepcopy(request)
    host = WorkspaceTools(native if transport == "native" else client, comment_preparer=prepare)
    before = store.path.read_bytes()
    code, preview = invoke_cli(host, "prepare", request, monkeypatch, capsys)
    assert code == 0 and preview["approval_required"] is True and preview["effects"] == "none", preview
    assert request == original and store.path.read_bytes() == before and backend.calls == []
    chosen = preview["intent"]
    prepared = state["results"][-1]
    assert chosen["actions"][0]["body"] == prepared["body"]
    assert chosen["actions"][0]["preparation"]["identity"]["write_id"] == write
    assert chosen["actions"][-1] == draft["actions"][-1] and "body" not in chosen["actions"][-1]
    assert "private-tree" not in prepared["body"] and "agent-worktrees:source" not in prepared["body"]
    request["intent"]["actions"][0]["body"] = prepared["body"]
    request["identities"]["prepared-note"]["previous"] = prepared
    code, repeated = invoke_cli(host, "prepare", request, monkeypatch, capsys)
    assert code == 0 and repeated == preview
    submission = {"number": 42, "intent": chosen, "source_attachment": attachment, "budget_seconds": 60}
    code, result = invoke_cli(host, "submit", submission, monkeypatch, capsys)
    assert code == 0 and result["result"]["status"] == "complete", result
    assert backend.threads[0]["comments"][-1]["content"] == prepared["body"]
    writes = len([call for call in backend.calls if call[0] != "GET"])
    reopened = ReplayStore(store.root, store.namespace, service_identity=store.service_identity)
    native.operations.store = reopened
    retained = reopened.inspect_review(chosen["operation_id"], chosen["scope"], expires_at=native.expires_at())
    assert retained["intent"] == chosen
    code, retry = invoke_cli(host, "submit", submission, monkeypatch, capsys)
    assert code == 0 and retry["result"]["entered_writes"] == 0
    assert len([call for call in backend.calls if call[0] != "GET"]) == writes


def test_optional_prepare_refuses_missing_binding_rollback_and_caller_policy(
    environment: tuple, prepared_workspace: tuple, comment_preparer: tuple,
) -> None:
    from agent_pull_requests.workspace_tools import WorkspaceToolUnavailable

    store, selected, _, _, backend, _ = environment
    _, _, native, _, _, _ = prepared_workspace
    prepare, state = comment_preparer
    draft = copy.deepcopy(selected)
    draft["version"] = 1
    draft["actions"][0].pop("preparation")
    draft["actions"][0]["body"] = "Selected note."
    request = {"number": 42, "intent": draft, "identities": {"finding": {
        "comment_id": "11111111-1111-4111-8111-111111111111",
        "write_id": "22222222-2222-4222-8222-222222222222", "previous": None,
    }}}
    before = store.path.read_bytes()

    async def scenario() -> None:
        unbound = WorkspaceTools(native)
        assert "prepare" not in (await unbound.capabilities())["operations"]
        with pytest.raises(WorkspaceToolUnavailable):
            await unbound.invoke("prepare", request)
        host = WorkspaceTools(native, comment_preparer=prepare)
        definitions = await host.definitions()
        assert any(value["name"] == "pr_workspace_prepare" for value in definitions)
        for field in ("context", "source", "profile"):
            with pytest.raises(ValueError):
                await host.invoke("prepare", {**request, field: {}})
        wrong_scope = copy.deepcopy(request)
        wrong_scope["intent"]["scope"]["principal_id"] = "other-principal"
        with pytest.raises(PermissionError):
            await host.invoke("prepare", wrong_scope)
        native.operations.prepared_reviews = False
        assert "prepare" not in (await host.capabilities())["operations"]
        with pytest.raises(WorkspaceToolUnavailable):
            await host.invoke("prepare", request)

    asyncio.run(scenario())
    assert state["results"] == [] and backend.calls == [] and store.path.read_bytes() == before


def test_prepare_retained_write_refuses_profile_change_or_rebound_output(
    environment: tuple, prepared_workspace: tuple, comment_preparer: tuple,
) -> None:
    store, selected, _, _, backend, _ = environment
    _, _, native, _, _, _ = prepared_workspace
    prepare, state = comment_preparer
    draft = copy.deepcopy(selected)
    draft["version"] = 1
    draft["actions"][0].pop("preparation")
    draft["actions"][0]["body"] = "Selected note."
    request = {"number": 42, "intent": draft, "identities": {"finding": {
        "comment_id": "11111111-1111-4111-8111-111111111111",
        "write_id": "22222222-2222-4222-8222-222222222222", "previous": None,
    }}}
    before = store.path.read_bytes()

    async def scenario() -> None:
        host = WorkspaceTools(native, comment_preparer=prepare)
        preview = await host.invoke("prepare", request)
        request["identities"]["finding"]["previous"] = copy.deepcopy(state["results"][-1])
        request["intent"]["actions"][0]["body"] = preview["intent"]["actions"][0]["body"]
        state["profile"]["rules"][0]["header"] = "[Changed AI] "
        with pytest.raises(ValueError):
            await host.invoke("prepare", request)
        state["profile"]["rules"][0]["header"] = "[Selected AI] "

        def rebind(kind: str, body: str, **kwargs: object) -> dict:
            kwargs["write_id"] = "33333333-3333-4333-8333-333333333333"
            kwargs["previous"] = None
            return prepare(kind, "Different selection.", **kwargs)

        host.comment_preparer = rebind
        with pytest.raises(ValueError, match="changed the selected"):
            await host.invoke("prepare", request)

    asyncio.run(scenario())
    assert backend.calls == [] and store.path.read_bytes() == before
