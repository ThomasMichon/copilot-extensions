"""Actual tracked CLI -> workspace clients -> retained file/native effects."""

from __future__ import annotations

import asyncio
from contextlib import redirect_stdout
import json
from pathlib import Path

import pytest

from agent_pull_requests.tracked_publication import TrackedPublication
from agent_pull_requests.watch_replay_store import ReplayStore
from agent_pull_requests.workspace_tools import WorkspaceTools
from test_review_executor import environment as environment
from test_review_owner_store import prepared_intent as prepared_intent
from test_workspace_comment_preparation import comment_preparer as comment_preparer
from test_workspace_integration import integration as integration, prepared_workspace as prepared_workspace
from test_workspace_selection import selection_workspace as selection_workspace

pytestmark = pytest.mark.parametrize("environment", [True], indirect=True)


@pytest.fixture
def tracked(environment: tuple, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple:
    owner_tests = Path(__file__).resolve().parents[3] / "plugins" / "agent-worktrees" / "tests"
    monkeypatch.syspath_prepend(str(owner_tests))
    from _tracked_publication_fixtures import tracked_state

    return tracked_state(environment[1]["scope"], tmp_path, monkeypatch)


def call(
    host: WorkspaceTools, target: dict, scope: dict, request: dict, phase: str,
    command: str, body: str, event: str, *, use_json: bool = True,
) -> tuple[int, dict]:
    from agent_worktrees import __main__ as cli, output

    binding = TrackedPublication(
        host, phase=phase, worktree_id="selected-worktree",
        target=target, native_scope=scope, request=request,
    )
    argv = [command, "selected-worktree"]
    if command == "pr-comment":
        argv.append(body)
    else:
        argv += [{"APPROVED": "--approve", "CHANGES_REQUESTED": "--request-changes",
                  "COMMENTED": "--comment"}[event], "--body", body]
    if use_json:
        argv.append("--json")
    with output.capture_json_output() as stream:
        with redirect_stdout(stream):
            code = cli.main(argv, publication=binding)
    return code, json.loads(stream.getvalue())


@pytest.mark.parametrize("transport", ["native", "gateway"])
@pytest.mark.parametrize("surface", ["comment", "review", "commented", "vote"])
def test_preview_exact_submit_and_original_recovery_after_restart_and_target_change(
    environment: tuple, prepared_workspace: tuple, selection_workspace: tuple, tracked: tuple,
    monkeypatch: pytest.MonkeyPatch, transport: str, surface: str,
) -> None:
    from agent_worktrees import config as cfg, pr_ops

    store, _, _, attachment, backend, _ = environment
    gateway, client, native, _, _, _ = prepared_workspace
    draft, state = selection_workspace
    target, _, _ = tracked
    command, event = ("pr-comment", "") if surface == "comment" else ("pr-review", "APPROVED")
    if surface in ("comment", "commented"):
        draft["actions"] = draft["actions"][:1]
    if surface == "commented":
        event = "COMMENTED"
    if surface == "vote":
        draft["actions"] = draft["actions"][1:]
    body = "" if surface == "vote" else draft["actions"][0]["body"]
    host = WorkspaceTools(native if transport == "native" else client)
    request = {"number": 42, "intent": draft, "source_attachment": attachment, "budget_seconds": 60}
    code, result = call(host, target, draft["scope"], request, "preview", command, body, event)
    assert code == 0, result
    preview = result["publication"]["response"]["result"]
    chosen = preview["intent"]
    assert preview["effects"] == "none" and preview["approval_required"] is True
    assert backend.calls == []
    assert len(state["results"]) == (0 if surface == "vote" else 1)
    submission = {**request, "intent": chosen}
    final_body = "" if surface == "vote" else chosen["actions"][0]["body"]
    if final_body != body:
        code, rejected = call(host, target, draft["scope"], submission, "submit", command, body, event)
        assert code == 1 and "exact selected body" in rejected["error"] and backend.calls == []
    code, result = call(host, target, draft["scope"], submission, "submit", command, final_body, event)
    assert code == 0 and result["publication"]["response"]["result"]["status"] == "complete", result
    writes = [row for row in backend.calls if row[0] != "GET"]
    assert [row[0] for row in writes] == (
        ["POST", "PUT"] if surface == "review" else ["PUT"] if surface == "vote" else ["POST"]
    )
    code, retry = call(host, target, draft["scope"], submission, "submit", command, final_body, event)
    assert code == 0 and retry["publication"]["response"]["result"]["entered_writes"] == 0
    before = store.path.read_bytes()
    native.operations.store = ReplayStore(store.root, store.namespace, service_identity=store.service_identity)
    native.operations.selection_preparer = lambda *a, **k: pytest.fail("recovery resolved today's profile")
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: pytest.fail("recovery loaded current config"))
    monkeypatch.setattr(pr_ops, "_load_record_or_none", lambda *a, **k: pytest.fail("recovery resolved today's PR"))
    code, recovered = call(
        host, target, draft["scope"], {"number": 42, "operation_id": draft["operation_id"]},
        "recover", command, "Ignored current CLI body", event,
    )
    operation = recovered["publication"]["response"]["result"]
    assert code == 0 and operation["intent"] == chosen and operation["selection"]["stage"] == "admitted"
    assert all(action["state"] == "confirmed" for action in operation["actions"])
    assert store.path.read_bytes() == before
    assert [row for row in backend.calls if row[0] != "GET"] == writes
    asyncio.run(gateway.drain(timeout=2))


@pytest.mark.parametrize("transport", ["native", "gateway"])
@pytest.mark.parametrize("partial", ["unattempted-vote", "unknown-vote"])
def test_partial_review_preserves_confirmed_text_and_never_resends_it(
    environment: tuple, prepared_workspace: tuple, selection_workspace: tuple, tracked: tuple,
    transport: str, partial: str,
) -> None:
    from agent_pull_requests.review_executor import ReviewCandidateChanged

    _, _, _, attachment, backend, _ = environment
    gateway, client, native, _, _, _ = prepared_workspace
    draft, _ = selection_workspace
    target, _, _ = tracked
    host = WorkspaceTools(native if transport == "native" else client)
    request = {"number": 42, "intent": draft, "source_attachment": attachment, "budget_seconds": 60}
    code, preview = call(host, target, draft["scope"], request, "preview", "pr-review", draft["actions"][0]["body"], "APPROVED")
    assert code == 0, preview
    chosen = preview["publication"]["response"]["result"]["intent"]
    if partial == "unknown-vote":
        backend.vote_reply = "missing"
    else:
        def refuse_vote(owner: object, action: dict) -> None:
            if action["kind"] == "verdict":
                raise ReviewCandidateChanged("synthetic changed policy before vote")
        backend.on_policy = refuse_vote
    request["intent"] = chosen
    body = chosen["actions"][0]["body"]
    code, result = call(
        host, target, draft["scope"], request, "submit", "pr-review", body, "APPROVED",
        use_json=partial != "unknown-vote",
    )
    outcomes = result["publication"]["response"]["result"]
    assert code == 1 and "incomplete" in result["error"]
    assert [action["state"] for action in outcomes["actions"]] == [
        "confirmed", "unknown" if partial == "unknown-vote" else "unattempted",
    ]
    backend.on_policy, backend.vote_reply = None, "complete"
    code, retry = call(host, target, draft["scope"], request, "submit", "pr-review", body, "APPROVED")
    assert code == (1 if partial == "unknown-vote" else 0), retry
    assert len([row for row in backend.calls if row[0] == "POST"]) == 1
    assert len([row for row in backend.calls if row[0] == "PUT"]) == 1
    asyncio.run(gateway.drain(timeout=2))


@pytest.mark.parametrize("changed", ["number", "repo", "endpoint", "provider", "principal", "source", "actions", "vote", "body", "noop", "rollback"])
def test_mismatch_and_unsupported_composition_refuse_without_fallback_or_effects(
    environment: tuple, prepared_workspace: tuple, selection_workspace: tuple, tracked: tuple,
    monkeypatch: pytest.MonkeyPatch, changed: str,
) -> None:
    from agent_worktrees import config as cfg
    from _tracked_publication_fixtures import change_association

    store, _, _, attachment, backend, _ = environment
    gateway, _, native, _, _, _ = prepared_workspace
    draft, _ = selection_workspace
    target, config, home = tracked
    body, event = draft["actions"][0]["body"], "APPROVED"
    request = {"number": 42, "intent": draft, "source_attachment": attachment, "budget_seconds": 60}
    if changed in ("number", "repo", "provider"):
        change_association(home, changed, provider=target["provider"])
    elif changed == "endpoint":
        config.default_repo.pr.api_base = "https://dev.azure.com/another"
        monkeypatch.setattr(cfg, "load_config", lambda *a, **k: config)
    elif changed == "principal":
        draft["scope"]["principal_id"] = "unselected-principal"
    elif changed == "source":
        attachment["source"]["complete"] = False
    elif changed == "actions":
        draft["actions"][0].update(kind="reply", thread_id=10)
    elif changed == "vote":
        event = "CHANGES_REQUESTED"
    elif changed == "body":
        body += " changed"
    elif changed == "noop":
        body, event = "", "COMMENTED"
    else:
        native.operations.prepared_reviews = False
    before = store.path.read_bytes()
    scope = environment[2]["scope"]
    code, result = call(WorkspaceTools(native), target, scope, request, "preview", "pr-review", body, event)
    assert code == 1 and "error" in result
    assert store.path.read_bytes() == before and backend.calls == []
    asyncio.run(gateway.drain(timeout=2))


@pytest.mark.parametrize("transport", ["native", "gateway"])
@pytest.mark.parametrize("lost", ["preview", "submit"])
def test_lost_committed_result_recovers_original_key_without_reallocation_or_resend(
    environment: tuple, prepared_workspace: tuple, selection_workspace: tuple, tracked: tuple,
    monkeypatch: pytest.MonkeyPatch, transport: str, lost: str,
) -> None:
    store, _, _, attachment, backend, _ = environment
    gateway, client, native, _, _, _ = prepared_workspace
    draft, state = selection_workspace
    draft["actions"] = draft["actions"][:1]
    target, _, _ = tracked
    host = WorkspaceTools(native if transport == "native" else client)
    request = {"number": 42, "intent": draft, "source_attachment": attachment, "budget_seconds": 60}
    execute = native.operations.execute

    def drop(operation: str, payload: dict, **kwargs: object) -> dict:
        result = execute(operation, payload, **kwargs)
        if operation == ("allocate" if lost == "preview" else "submit"):
            raise RuntimeError("synthetic lost committed result")
        return result

    if lost == "preview":
        monkeypatch.setattr(native.operations, "execute", drop)
    code, preview = call(host, target, draft["scope"], request, "preview", "pr-comment", draft["actions"][0]["body"], "")
    if lost == "preview":
        assert code == 1 and preview["publication"]["effect_completion"] == "not_inferred"
    else:
        assert code == 0, preview
        chosen = preview["publication"]["response"]["result"]["intent"]
        request["intent"] = chosen
        monkeypatch.setattr(native.operations, "execute", drop)
        code, result = call(host, target, draft["scope"], request, "submit", "pr-comment", chosen["actions"][0]["body"], "")
        assert code == 1 and result["publication"]["effect_completion"] == "not_inferred"
    monkeypatch.setattr(native.operations, "execute", execute)
    before = store.path.read_bytes()
    native.operations.store = ReplayStore(store.root, store.namespace, service_identity=store.service_identity)
    code, recovered = call(
        host, target, draft["scope"], {"number": 42, "operation_id": draft["operation_id"]},
        "recover", "pr-comment", "irrelevant current body", "",
    )
    operation = recovered["publication"]["response"]["result"]
    assert code == 0 and operation["selection"]["stage"] == ("prepared" if lost == "preview" else "admitted")
    assert state["resolutions"] == 1 and len(state["results"]) == 1 and store.path.read_bytes() == before
    request["intent"] = operation["intent"]
    body = operation["intent"]["actions"][0]["body"]
    code, submitted = call(host, target, draft["scope"], request, "submit", "pr-comment", body, "")
    assert code == 0 and submitted["publication"]["response"]["result"]["status"] == "complete"
    assert len([row for row in backend.calls if row[0] == "POST"]) == 1
    asyncio.run(gateway.drain(timeout=2))


@pytest.mark.parametrize("transport", ["native", "gateway"])
def test_preparation_only_preview_and_recovery_survive_writer_and_preparer_withdrawal(
    environment: tuple, prepared_workspace: tuple, selection_workspace: tuple, tracked: tuple,
    transport: str,
) -> None:
    store, _, _, attachment, backend, _ = environment
    gateway, client, native, _, _, _ = prepared_workspace
    draft, _ = selection_workspace
    draft["actions"] = draft["actions"][:1]
    target, _, _ = tracked
    native.operations.write_context = None
    host = WorkspaceTools(native if transport == "native" else client)
    request = {"number": 42, "intent": draft, "source_attachment": attachment, "budget_seconds": 60}
    code, preview = call(host, target, draft["scope"], request, "preview", "pr-comment", draft["actions"][0]["body"], "")
    assert code == 0, preview
    chosen = preview["publication"]["response"]["result"]["intent"]
    native.operations.selection_preparer = None
    before = store.path.read_bytes()
    request["intent"] = chosen
    code, refused = call(host, target, draft["scope"], request, "submit", "pr-comment", chosen["actions"][0]["body"], "")
    assert code == 1 and "contract unavailable" in refused["error"]
    code, recovered = call(
        host, target, draft["scope"], {"number": 42, "operation_id": draft["operation_id"]},
        "recover", "pr-comment", "irrelevant current body", "",
    )
    assert code == 0 and recovered["publication"]["response"]["result"]["intent"] == chosen
    assert store.path.read_bytes() == before and backend.calls == []
    asyncio.run(gateway.drain(timeout=2))


@pytest.mark.parametrize("transport", ["native", "gateway"])
def test_submission_requires_the_original_committed_preview_not_only_valid_prepared_bytes(
    environment: tuple, prepared_workspace: tuple, selection_workspace: tuple, tracked: tuple,
    transport: str,
) -> None:
    store, selected, _, attachment, backend, _ = environment
    gateway, client, native, _, _, _ = prepared_workspace
    target, _, _ = tracked
    host = WorkspaceTools(native if transport == "native" else client)
    request = {"number": 42, "intent": selected, "source_attachment": attachment, "budget_seconds": 60}
    before = store.path.read_bytes()
    code, refused = call(
        host, target, selected["scope"], request, "submit",
        "pr-review", selected["actions"][0]["body"], "APPROVED",
    )
    assert code == 1 and "error" in refused
    assert store.path.read_bytes() == before and backend.calls == []
    asyncio.run(gateway.drain(timeout=2))


@pytest.mark.parametrize("transport", ["native", "gateway"])
def test_trailing_slash_endpoint_forms_keep_the_same_native_target(
    environment: tuple, prepared_workspace: tuple, selection_workspace: tuple, tracked: tuple,
    transport: str,
) -> None:
    _, _, _, attachment, backend, _ = environment
    gateway, client, native, _, _, _ = prepared_workspace
    draft, _ = selection_workspace
    draft["actions"] = draft["actions"][:1]
    target, config, _ = tracked
    target["api_base"] += "/"
    config.default_repo.pr.api_base = target["api_base"]
    host = WorkspaceTools(native if transport == "native" else client)
    request = {"number": 42, "intent": draft, "source_attachment": attachment, "budget_seconds": 60}
    code, preview = call(host, target, draft["scope"], request, "preview", "pr-comment", draft["actions"][0]["body"], "")
    assert code == 0 and backend.calls == [], preview
    chosen = preview["publication"]["response"]["result"]["intent"]
    target["api_base"] = target["api_base"].rstrip("/")
    request["intent"] = chosen
    code, submitted = call(host, target, draft["scope"], request, "submit", "pr-comment", chosen["actions"][0]["body"], "")
    assert code == 0 and submitted["publication"]["response"]["result"]["status"] == "complete", submitted
    assert len([row for row in backend.calls if row[0] == "POST"]) == 1
    asyncio.run(gateway.drain(timeout=2))
