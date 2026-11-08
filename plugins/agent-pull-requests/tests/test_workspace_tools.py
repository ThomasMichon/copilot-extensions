"""Actual CLI/tool dispatch, atomic owners and local exact-Git source fixtures."""

from __future__ import annotations

import asyncio
import copy
import io
import json
from pathlib import Path
import sys

import pytest
from agent_procutil.owned_process import run_caller_owned

from test_review_executor import environment as environment
from test_workspace_integration import integration as integration, prepared_workspace as prepared_workspace
from test_review_owner_store import prepared_intent as prepared_intent

from agent_pull_requests.workspace_tools import WorkspaceTools, dispatch_tool
from agent_pull_requests import __main__ as cli

pytestmark = pytest.mark.parametrize("environment", [True], indirect=True)


@pytest.fixture
def execution_pool():
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=1) as pool:
        yield pool


@pytest.fixture
def tools(environment, integration, tmp_path):
    _, _, _, _, backend, _ = environment
    _, _, native, evidence, _, _ = integration
    root = tmp_path / "selected-source"
    root.mkdir()

    def git(*args):
        from agent_worktrees.git_repository_environment import _NO_HOOKS_PATH, repository_identity_env
        return run_caller_owned(
            ["git", "-c", "core.fsmonitor=false", "-c", f"core.hooksPath={_NO_HOOKS_PATH}",
             "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *args],
            cwd=root, env=repository_identity_env(),
            timeout=20, max_stdout_bytes=65536, max_stderr_bytes=8192,
        ).stdout.strip()

    git("init", "--quiet")
    git("config", "user.name", "Synthetic Reviewer")
    git("config", "user.email", "reviewer@example.test")
    (root / "note.txt").write_text("before", encoding="utf-8")
    (root / "gone.txt").write_text("deleted", encoding="utf-8")
    git("add", ".")
    git("commit", "--quiet", "-m", "synthetic common")
    common = git("rev-parse", "HEAD")
    before_note = git("rev-parse", common + ":note.txt")
    gone = git("rev-parse", common + ":gone.txt")
    (root / "note.txt").write_text("after", encoding="utf-8")
    (root / "gone.txt").unlink()
    (root / "binary.dat").write_bytes(b"\0binary")
    git("add", ".")
    git("commit", "--quiet", "-m", "synthetic reviewed head")
    head = git("rev-parse", "HEAD")
    after_note = git("rev-parse", head + ":note.txt")
    binary = git("rev-parse", head + ":binary.dat")
    latest = max(backend.iterations, key=lambda item: item["id"])
    latest["sourceRefCommit"]["commitId"] = backend.pr["lastMergeSourceCommit"]["commitId"] = head
    latest["commonRefCommit"]["commitId"] = backend.pr["lastMergeTargetCommit"]["commitId"] = common
    changes = [
        {"changeTrackingId": 1, "changeType": "edit", "item": {
            "path": "/note.txt", "objectId": after_note, "originalObjectId": before_note}},
        {"changeTrackingId": 2, "changeType": "add", "item": {
            "path": "/binary.dat", "objectId": binary, "originalObjectId": "0" * 40}},
        {"changeTrackingId": 3, "changeType": "delete", "item": {
            "path": "/gone.txt", "objectId": gone, "originalObjectId": gone}},
    ]
    evidence["sections"]["changes"].update(
        data=changes, revision={"head_sha": head, "common_sha": common, "iteration": latest["id"]},
    )
    from agent_worktrees.pr_review_source import LocalReviewSource
    scope = {**native.operations.scope, "number": 42}
    source = LocalReviewSource(root.resolve(), "synthetic-owned-workspace", scope, checkpoint=backend.checkpoint)
    return WorkspaceTools(native, source_owner=source), git, root


def invoke_cli(host, command, payload, monkeypatch, capsys):
    if command == "capabilities":
        args = ["workspace", command]
    else:
        monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(payload).encode())))
        args = ["workspace", command, "--request", "-"]
    code = cli.main(args, workspace_host=host)
    output = capsys.readouterr().out
    return code, json.loads(output)


def test_actual_cli_frontdoor_load_source_read_and_explicit_submit(environment, integration, tools, monkeypatch, capsys):
    _, selected, initial, _, backend, _ = environment
    host, _, _ = tools
    code, capabilities = invoke_cli(host, "capabilities", {}, monkeypatch, capsys)
    assert code == 0 and "source-read" in capabilities["operations"]
    assert capabilities["source_acquisition"] is False
    code, loaded = invoke_cli(host, "load", {
        "number": 42, "expected_current": initial["id"], "budget_seconds": 60,
    }, monkeypatch, capsys)
    assert code == 0 and loaded["result"]["publication"]["status"] == "published"
    generation_id = loaded["result"]["descriptor"]["id"]
    _, generation = invoke_cli(host, "materialize", {
        "number": 42, "generation_id": generation_id,
    }, monkeypatch, capsys)
    code, attachment = invoke_cli(host, "source", {
        "number": 42, "generation_id": generation_id, "budget_seconds": 60,
    }, monkeypatch, capsys)
    assert code == 0 and attachment["state"] == "ready", attachment
    code, source = invoke_cli(host, "source-read", {
        "number": 42, "generation_id": generation_id, "source_attachment": attachment,
        "path": "/note.txt", "side": "before", "budget_seconds": 60,
    }, monkeypatch, capsys)
    assert code == 0 and source["content"] == "before" and source["effects"] == "none"
    request = copy.deepcopy(selected)
    request.update(generation_id=generation_id, source_id=attachment["source"]["id"])
    request["revision"] = {k: generation["revision"][k] for k in ("head_sha", "common_sha", "iteration")}
    code, submitted = invoke_cli(host, "submit", {
        "number": 42, "intent": request, "source_attachment": attachment, "budget_seconds": 60,
    }, monkeypatch, capsys)
    assert code == 0 and submitted["result"]["status"] == "complete"
    assert len(backend.threads) == 1 and backend.vote == 10
    code, retained = invoke_cli(host, "inspect", {
        "number": 42, "operation_id": request["operation_id"],
    }, monkeypatch, capsys)
    assert code == 0 and all(item["state"] == "confirmed" for item in retained["result"]["actions"])


@pytest.mark.parametrize("kind", ["comment", "reply", "edit_owned_comment"])
def test_preapproved_compact_body_survives_actual_cli_native_write_and_restart(
    environment: tuple, integration: tuple, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], kind: str,
) -> None:
    from types import SimpleNamespace

    root = Path(__file__).resolve().parents[3]
    for source in (root / "libs").glob("*/src"):
        monkeypatch.syspath_prepend(str(source))
    from agent_worktrees import attribution_prepare_cli, tracking
    from agent_pull_requests.review_workspace import _digest, _record
    from agent_pull_requests.watch_replay_store import ReplayStore

    monkeypatch.syspath_prepend(str(root / "plugins/ai-attribution/scripts"))
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
    profile = attribution_profile.validate({
        "version": 1, "rules": [{"header": "[Selected AI] "}],
    })
    context = {"domain": "forge", "visibility": "public", "activity": "comment", "required": True}
    logical = "11111111-1111-4111-8111-111111111111"
    original_write = "22222222-2222-4222-8222-222222222222"
    edit_write = "33333333-3333-4333-8333-333333333333"
    original = comment_preparation.prepare(
        "Original selected note.", profile, context, origin,
        comment_id=logical, write_id=original_write,
    )
    prepared = comment_preparation.prepare(
        "Intentional selected edit.", profile, context, origin,
        comment_id=logical, write_id=edit_write, previous=original,
    ) if kind == "edit_owned_comment" else original
    store, selected, generation, attachment, backend, _ = environment
    _, _, native, _, _, _ = integration
    initial_id = generation["id"]
    action = {"id": "prepared-note", "kind": kind, "body": prepared["body"]}
    if kind != "comment":
        parent_body = original["body"] if kind == "edit_owned_comment" else "Existing parent note."
        thread = {"id": 50, "status": 1, "comments": [{
            "id": 1, "parentCommentId": 0, "commentType": 1,
            "content": parent_body, "author": {"id": selected["scope"]["principal_id"]},
        }]}
        backend.threads = [copy.deepcopy(thread)]
        generation["sections"]["discussions"]["data"] = [_record("discussions", thread)]
        generation["id"] = _digest({key: value for key, value in generation.items() if key != "id"})
        selected["generation_id"] = attachment["generation_id"] = generation["id"]
        native.operations.cache.publish(generation, expected_current=initial_id)
        action["thread_id"] = 50
        if kind == "edit_owned_comment":
            action.update(comment_id=1, expected_content_id=_digest(original["body"]))
    selected["actions"] = [action]
    host = WorkspaceTools(native)
    request = {
        "number": 42, "intent": copy.deepcopy(selected),
        "source_attachment": attachment, "budget_seconds": 60,
    }
    code, result = invoke_cli(host, "submit", request, monkeypatch, capsys)
    assert code == 0 and result["result"]["status"] == "complete", result
    writes = [call for call in backend.calls if call[0] != "GET"]
    assert len(writes) == 1 and backend.vote is None
    payload = writes[0][2]
    assert (payload["comments"][0]["content"] if kind == "comment" else payload["content"]) == prepared["body"]
    assert prepared["body"].startswith("[Selected AI] ")
    assert prepared["body"].count("<!-- agent-comment:v1") == 1
    assert "private-tree" not in prepared["body"] and "agent-worktrees:source" not in prepared["body"]
    assert logical in prepared["body"]
    assert (edit_write if kind == "edit_owned_comment" else original_write) in prepared["body"]
    if kind == "edit_owned_comment":
        assert original_write not in prepared["body"]
    if kind == "reply":
        assert backend.threads[0]["comments"][0]["content"] == parent_body
        assert logical not in parent_body and original_write not in parent_body
    restarted = ReplayStore(store.root, store.namespace, service_identity=store.service_identity)
    native.operations.store = restarted
    retained = restarted.inspect_review(
        selected["operation_id"], selected["scope"], expires_at=native.expires_at(),
    )
    assert retained["intent"] == selected and retained["intent"]["actions"][0]["body"] == prepared["body"]
    assert retained["actions"][0]["state"] == "confirmed"
    code, retry = invoke_cli(host, "submit", request, monkeypatch, capsys)
    assert code == 0 and retry["result"]["status"] == "complete"
    assert retry["result"]["entered_writes"] == 0
    assert len([call for call in backend.calls if call[0] != "GET"]) == 1


def test_existing_workspace_cli_does_not_implicitly_select_prepared_native_execution(
    environment: tuple, integration: tuple, prepared_intent: dict,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    store, selected, _, attachment, backend, _ = environment
    _, _, native, _, _, _ = integration
    store.adopt_prepared_reviews(
        lifecycle_checkpoint=lambda: None, expires_at=native.expires_at(),
    )
    selected["version"] = 2
    selected["actions"][0].update({
        key: copy.deepcopy(prepared_intent["actions"][0][key]) for key in ("body", "preparation")
    })
    host = WorkspaceTools(native)
    before = store.path.read_bytes()
    code, response = invoke_cli(host, "submit", {
        "number": 42, "intent": selected, "source_attachment": attachment, "budget_seconds": 60,
    }, monkeypatch, capsys)
    assert code != 0 and response["error"]
    assert backend.calls == [] and store.path.read_bytes() == before


def test_actual_cli_over_gateway_selects_supported_prepared_submit_and_original_receipt(
    environment: tuple, prepared_workspace: tuple, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, selected, _, attachment, backend, _ = environment
    _, client, _, _, _, _ = prepared_workspace
    host = WorkspaceTools(client)
    code, capabilities = invoke_cli(host, "capabilities", {}, monkeypatch, capsys)
    assert code == 0 and "receipt" in capabilities["operations"]
    code, submitted = invoke_cli(host, "submit", {
        "number": 42, "intent": selected, "source_attachment": attachment, "budget_seconds": 60,
    }, monkeypatch, capsys)
    assert code == 0 and submitted["result"]["status"] == "complete", submitted
    before_calls = len(backend.calls)
    code, receipt = invoke_cli(host, "receipt", {
        "number": 42, "operation_id": selected["operation_id"], "action_id": "finding",
    }, monkeypatch, capsys)
    assert code == 0 and receipt["result"]["action_state"] == "confirmed"
    assert receipt["result"]["identity"] == selected["actions"][0]["preparation"]["identity"]
    assert receipt["result"]["fresh_native_read"] is False
    assert len(backend.calls) == before_calls
    code, vote = invoke_cli(host, "receipt", {
        "number": 42, "operation_id": selected["operation_id"], "action_id": "verdict",
    }, monkeypatch, capsys)
    assert code == 0 and vote["result"]["status"] == "unavailable"
    assert len([call for call in backend.calls if call[0] != "GET"]) == 2


@pytest.mark.parametrize("path,side,classification", [
    ("/note.txt", "after", "text"), ("/binary.dat", "after", "binary"), ("/gone.txt", "after", "absent"),
])
def test_tools_read_exact_retained_objects_not_changed_working_files(environment, tools, path, side, classification):
    _, _, initial, _, _, _ = environment
    host, _, root = tools

    async def scenario():
        loaded = await host.invoke("load", {"number": 42, "expected_current": initial["id"], "budget_seconds": 60})
        identity = loaded["result"]["descriptor"]["id"]
        attachment = await host.invoke("source", {"number": 42, "generation_id": identity, "budget_seconds": 60})
        (root / "note.txt").write_text("unreviewed working bytes", encoding="utf-8")
        value = await dispatch_tool(host, "pr_workspace_source_read", {"request": {
            "number": 42, "generation_id": identity, "source_attachment": attachment,
            "path": path, "side": side, "budget_seconds": 60,
        }})
        assert value["classification"] == classification
        if classification == "text":
            assert value["content"] == "after"
        if classification == "absent":
            assert value["available"] is False and value["content"] is None

    asyncio.run(scenario())


def test_unbound_actual_module_cli_is_passive_and_rejects_owner_flags(environment):
    output = run_caller_owned(
        [sys.executable, "-m", "agent_pull_requests", "workspace", "capabilities"],
        timeout=20, max_stdout_bytes=8192, max_stderr_bytes=8192,
    )
    value = json.loads(output.stdout)
    assert output.returncode == 0 and value["owner_binding"] == "unavailable"
    assert value["operations"] == ["capabilities"]
    refused = run_caller_owned(
        [sys.executable, "-m", "agent_pull_requests", "workspace", "capabilities", "--owner", "fabricated"],
        timeout=20, max_stdout_bytes=8192, max_stderr_bytes=8192,
    )
    assert refused.returncode == 2


def test_tool_source_ref_scope_and_path_drift_refuse_without_fetch(environment, tools):
    from agent_worktrees.pr_review_source import SourceIncomplete
    _, _, initial, _, _, _ = environment
    host, git, _ = tools

    async def scenario():
        loaded = await host.invoke("load", {"number": 42, "expected_current": initial["id"], "budget_seconds": 60})
        identity = loaded["result"]["descriptor"]["id"]
        attachment = await host.invoke("source", {"number": 42, "generation_id": identity, "budget_seconds": 60})
        request = {"number": 42, "generation_id": identity, "source_attachment": attachment,
                   "path": "/note.txt", "side": "after", "budget_seconds": 60}
        with pytest.raises(SourceIncomplete):
            await host.invoke("source-read", dict(request, path="/../outside"))
        foreign = copy.deepcopy(attachment)
        foreign["scope"]["principal_id"] = "another-principal"
        with pytest.raises(ValueError):
            await host.invoke("source-read", dict(request, source_attachment=foreign))
        refs = attachment["source"]["retained_refs"]
        git("update-ref", refs["head"]["ref"], refs["common"]["object_id"])
        with pytest.raises(SourceIncomplete, match="retained_ref_conflict"):
            await host.invoke("source-read", request)

    asyncio.run(scenario())


@pytest.mark.parametrize("disposition", ["late-read", "lost-write"])
def test_cli_json_delivery_preserves_unattempted_vs_unknown_and_cache_does_not_erase_receipts(environment, integration, disposition, monkeypatch, capsys):
    _, selected, generation, attachment, backend, _ = environment
    _, _, native, _, _, _ = integration
    host = WorkspaceTools(native)
    backend.read_late = disposition == "late-read"
    backend.lose_reply = disposition == "lost-write"
    code, outcome = invoke_cli(host, "submit", {
        "number": 42, "intent": selected, "source_attachment": attachment, "budget_seconds": 60,
    }, monkeypatch, capsys)
    assert code == 0 and outcome["result"]["status"] == "unknown"
    state = "unattempted" if disposition == "late-read" else "unknown"
    assert outcome["result"]["actions"][0]["state"] == state
    backend.read_late = False
    code, refreshed = invoke_cli(host, "load", {
        "number": 42, "expected_current": generation["id"], "budget_seconds": 60,
    }, monkeypatch, capsys)
    assert code == 0 and refreshed["result"]["publication"]["status"] == "published"
    code, retained = invoke_cli(host, "inspect", {
        "number": 42, "operation_id": selected["operation_id"],
    }, monkeypatch, capsys)
    assert code == 0 and retained["result"]["actions"][0]["state"] == state
    assert len(backend.threads) == int(disposition == "lost-write") and backend.vote is None


def test_tool_descriptors_and_strict_parser_do_not_invent_source_owner_or_accept_paths(environment, integration, monkeypatch, capsys):
    from agent_pull_requests.workspace_tools import WorkspaceToolUnavailable
    _, _, native, _, _, _ = integration
    host = WorkspaceTools(native, source_owner=object())

    async def scenario():
        assert "source" not in (await host.capabilities())["operations"]
        definitions = await host.definitions()
        load = next(value for value in definitions if value["name"] == "pr_workspace_load")
        assert set(load["inputSchema"]["properties"]["request"]["required"]) == {
            "number", "expected_current", "budget_seconds",
        }
        with pytest.raises(WorkspaceToolUnavailable):
            await host.invoke("source", {"number": 42, "generation_id": "f" * 64, "budget_seconds": 60})

    asyncio.run(scenario())
    code, rejected = invoke_cli(host, "load", {
        "number": 42, "expected_current": None, "budget_seconds": 60, "root": "unowned-path",
    }, monkeypatch, capsys)
    assert code == 1 and rejected["effect_completion"] == "not_inferred"


@pytest.mark.parametrize("transition", ["unchanged", "partial", "reply", "head", "target"])
def test_cli_sync_preserves_normalized_observations_and_section_delta(environment, integration, transition, monkeypatch, capsys):
    _, _, generation, _, backend, _ = environment
    _, _, native, evidence, _, _ = integration
    host = WorkspaceTools(native)
    if transition == "partial":
        evidence["sections"]["discussions"].update(complete=False, errors=["synthetic_partial"])
    elif transition == "reply":
        backend.threads = [{"id": 7, "status": 1, "comments": [
            {"id": 1, "content": "Synthetic author response", "author": {"id": "another-author"}}]}]
    elif transition == "head":
        latest = max(backend.iterations, key=lambda item: item["id"])
        latest["sourceRefCommit"]["commitId"] = backend.pr["lastMergeSourceCommit"]["commitId"] = "f" * 40
    elif transition == "target":
        backend.pr["lastMergeTargetCommit"]["commitId"] = "e" * 40
    code, synchronized = invoke_cli(host, "sync", {
        "number": 42, "previous_id": generation["id"], "expected_current": generation["id"], "budget_seconds": 60,
    }, monkeypatch, capsys)
    assert code == 0
    changed = synchronized["result"]["comparison"]["changed_sections"]
    assert (not changed) == (transition == "unchanged")
    identity = synchronized["result"]["descriptor"]["id"]
    code, current = invoke_cli(host, "materialize", {"number": 42, "generation_id": identity}, monkeypatch, capsys)
    assert code == 0
    assert current["sections"]["discussions"]["observed_from"] == generation["sections"]["discussions"]["observed_from"]
    if transition == "partial":
        assert current["sections"]["discussions"]["complete"] is False
        assert current["sections"]["discussions"]["errors"] == ["synthetic_partial"]
    assert backend.vote is None


def test_current_outer_envelope_cannot_relabel_an_old_retained_manifest(environment, integration, tools):
    _, _, initial, _, backend, _ = environment
    _, _, _, evidence, _, _ = integration
    host, git, root = tools

    async def scenario():
        first = await host.invoke("load", {"number": 42, "expected_current": initial["id"], "budget_seconds": 60})
        old_id = first["result"]["descriptor"]["id"]
        old_source = await host.invoke("source", {"number": 42, "generation_id": old_id, "budget_seconds": 60})
        (root / "note.txt").write_text("a newer reviewed source", encoding="utf-8")
        git("add", ".")
        git("commit", "--quiet", "-m", "synthetic next head")
        head = git("rev-parse", "HEAD")
        latest = max(backend.iterations, key=lambda item: item["id"])
        latest["sourceRefCommit"]["commitId"] = backend.pr["lastMergeSourceCommit"]["commitId"] = head
        evidence["sections"]["changes"]["revision"]["head_sha"] = head
        evidence["sections"]["changes"]["data"][0]["item"]["objectId"] = git("rev-parse", head + ":note.txt")
        current = await host.invoke("load", {"number": 42, "expected_current": old_id, "budget_seconds": 60})
        current_id = current["result"]["descriptor"]["id"]
        generation = await host.invoke("materialize", {"number": 42, "generation_id": current_id})
        relabeled = copy.deepcopy(old_source)
        relabeled.update(generation_id=current_id, revision=generation["revision"], scope=generation["scope"])
        with pytest.raises(ValueError, match="differs from selected generation"):
            await host.invoke("source-read", {
                "number": 42, "generation_id": current_id, "source_attachment": relabeled,
                "path": "/note.txt", "side": "after", "budget_seconds": 60,
            })

    asyncio.run(scenario())


def test_overlapping_service_tool_calls_share_capability_negotiation(environment, integration):
    _, _, generation, _, _, _ = environment
    _, client, _, _, caller, _ = integration
    host = WorkspaceTools(client)

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        caller.pause_capability = (entered, release)
        first = asyncio.create_task(host.invoke("materialize", {"number": 42, "generation_id": generation["id"]}))
        await asyncio.wait_for(entered.wait(), timeout=2)
        second = asyncio.create_task(host.invoke("materialize", {"number": 42, "generation_id": generation["id"]}))
        await asyncio.sleep(0)
        release.set()
        values = await asyncio.gather(first, second)
        assert values == [generation, generation]
        assert sum(path.endswith("/capabilities") for path in caller.calls) == 1

    asyncio.run(scenario())


def test_owned_async_source_through_actual_tool_and_client_frontdoor(environment, tools, integration, execution_pool):
    from agent_worktrees.async_review_source import AsyncReviewSource
    _, _, initial, _, _, _ = environment
    native_host, _, _ = tools
    owner = AsyncReviewSource(native_host.source_owner, executor=execution_pool)
    host = WorkspaceTools(integration[2], source_owner=owner)

    async def scenario():
        loaded = await host.invoke("load", {
            "number": 42, "expected_current": initial["id"], "budget_seconds": 60,
        })
        identity = loaded["result"]["descriptor"]["id"]
        definitions = await host.definitions()
        assert any(value["name"] == "pr_workspace_source_read" for value in definitions)
        assert not any(value["name"] == "pr_workspace_source_prepare" for value in definitions)
        assert (await host.capabilities())["source_acquisition"] is False
        attachment = await dispatch_tool(host, "pr_workspace_source", {"request": {
            "number": 42, "generation_id": identity, "budget_seconds": 60,
        }})
        assert attachment["state"] == "ready"
        value = await dispatch_tool(host, "pr_workspace_source_read", {"request": {
            "number": 42, "generation_id": identity, "budget_seconds": 60,
            "source_attachment": attachment, "path": "/note.txt", "side": "after",
        }})
        assert value["content"] == "after"
        await owner.drain(timeout=2)
        assert owner.status()["drained"] is True

    asyncio.run(scenario())


def test_one_shot_cli_refuses_async_source_binding_without_adopting_another_loop(environment, tools, monkeypatch, capsys, execution_pool):
    from agent_worktrees.async_review_source import AsyncReviewSource
    _, _, initial, _, _, _ = environment
    native_host, git, _ = tools
    owner = AsyncReviewSource(native_host.source_owner, executor=execution_pool)
    host = WorkspaceTools(native_host.client, source_owner=owner)
    for _ in range(2):
        code, capabilities = invoke_cli(host, "capabilities", {}, monkeypatch, capsys)
        assert code == 0
        assert not {"source", "source-read", "source-prepare"} & set(capabilities["operations"])
    code, loaded = invoke_cli(host, "load", {
        "number": 42, "expected_current": initial["id"], "budget_seconds": 60,
    }, monkeypatch, capsys)
    assert code == 0
    request = {"number": 42, "generation_id": loaded["result"]["descriptor"]["id"], "budget_seconds": 60}
    materialize = host.client.materialize
    reads = []

    def observed(*args):
        reads.append(args)
        return materialize(*args)

    monkeypatch.setattr(host.client, "materialize", observed)
    for _ in range(2):
        code, generation = invoke_cli(host, "materialize", {
            "number": 42, "generation_id": request["generation_id"],
        }, monkeypatch, capsys)
        assert code == 0 and generation["id"] == request["generation_id"]
    assert len(reads) == 1
    for command in ("source", "source-read"):
        payload = dict(request)
        if command == "source-read":
            payload.update(source_attachment={}, path="/note.txt", side="after")
        code, value = invoke_cli(host, command, payload, monkeypatch, capsys)
        assert code == 3 and value["effect_completion"] == "not_inferred"
    assert owner._loop is None and owner.status()["pending"] == 0
    assert not git("for-each-ref", "refs/agent-pull-requests")
    with pytest.raises(SystemExit) as removed:
        cli.main(["workspace", "source-prepare", "--request", "-"], workspace_host=host)
    assert removed.value.code == 2


def test_async_attachment_snapshot_and_response_preparation_run_off_loop(environment, tools, execution_pool, monkeypatch):
    import threading
    from agent_worktrees.async_review_source import AsyncReviewSource
    from agent_worktrees.pr_review_source import SourceIncomplete
    from agent_pull_requests import source_attachment
    _, _, initial, _, _, _ = environment
    native_host, _, _ = tools
    owner = AsyncReviewSource(native_host.source_owner, executor=execution_pool)
    entered, release = threading.Event(), threading.Event()
    copied_on, finished_on = [], []
    original_finish = source_attachment._finish

    original_selection = source_attachment._selection
    def selection(snapshot):
        copied_on.append(threading.get_ident())
        entered.set()
        assert release.wait(10)
        return original_selection(snapshot)

    def finish(*args):
        finished_on.append(threading.get_ident())
        return original_finish(*args)

    monkeypatch.setattr(source_attachment, "_finish", finish)
    monkeypatch.setattr(source_attachment, "_selection", selection)

    async def scenario():
        loaded = await native_host.invoke("load", {
            "number": 42, "expected_current": initial["id"], "budget_seconds": 60,
        })
        generation = await native_host.invoke("materialize", {
            "number": 42, "generation_id": loaded["result"]["descriptor"]["id"],
        })
        loop_thread = threading.get_ident()
        from agent_pull_requests.workspace_operations import encoded
        data = encoded(generation)
        task = asyncio.create_task(source_attachment.attach_source_async(data, owner, selection=generation, budget=60))
        try:
            for _ in range(1000):
                if entered.is_set():
                    break
                await asyncio.sleep(.001)
            assert entered.is_set()
            await asyncio.wait_for(asyncio.sleep(.001), timeout=.1)
            with pytest.raises(SourceIncomplete, match="source_execution_busy"):
                await source_attachment.attach_source_async(data, owner, selection=generation, budget=60)
            assert len(copied_on) == 1
        finally:
            release.set()
        assert (await task)["state"] == "ready"
        assert all(value != loop_thread for value in copied_on + finished_on)
        await owner.drain(timeout=2)

    asyncio.run(scenario())


def test_async_attachment_timeout_retains_uncertain_refs_and_native_drain(environment, tools, execution_pool, monkeypatch):
    import threading
    from agent_worktrees import pr_review_source
    from agent_worktrees.async_review_source import AsyncReviewSource
    from agent_pull_requests.source_attachment import attach_source_async
    _, _, initial, _, _, _ = environment
    native_host, _, _ = tools
    owner = AsyncReviewSource(native_host.source_owner, executor=execution_pool)
    entered, release = threading.Event(), threading.Event()
    original = pr_review_source._owned_git

    def pause(root, checkpoint, deadline, env, maximum, args, **kwargs):
        if args[0] == "update-ref":
            entered.set()
            assert release.wait(15)
        return original(root, checkpoint, deadline, env, maximum, args, **kwargs)

    monkeypatch.setattr(pr_review_source, "_owned_git", pause)

    async def scenario():
        loaded = await native_host.invoke("load", {
            "number": 42, "expected_current": initial["id"], "budget_seconds": 60,
        })
        generation = await native_host.invoke("materialize", {
            "number": 42, "generation_id": loaded["result"]["descriptor"]["id"],
        })
        from agent_pull_requests.workspace_operations import encoded
        task = asyncio.create_task(attach_source_async(encoded(generation), owner, selection=generation, budget=10))
        try:
            result = await asyncio.wait_for(task, timeout=12)
            assert entered.is_set()
            assert result["generation_id"] == generation["id"] and result["state"] == "incomplete"
            assert result["effects"] == "local_refs_possible"
            assert result["native_settlement"] == "unconfirmed"
            assert owner.status()["pending"] == 1
            with pytest.raises(TimeoutError):
                await owner.drain(timeout=.001)
        finally:
            release.set()
            await owner.drain(timeout=5)

    asyncio.run(scenario())


def test_queued_valid_manifest_mutation_refuses_unreviewed_revision(environment, tools, integration, execution_pool, monkeypatch):
    import threading
    from agent_worktrees.async_review_source import AsyncReviewSource
    from agent_worktrees._review_source_operation import SourceOperation
    from agent_worktrees.pr_review_source import LocalReviewSource, SourceIncomplete
    _, _, initial, _, backend, _ = environment
    _, _, _, evidence, _, _ = integration
    native_host, git, root = tools
    entered, release, queued = threading.Event(), threading.Event(), threading.Event()
    phase = []
    original_submit = execution_pool.submit
    original_execute = SourceOperation.execute
    original_read = LocalReviewSource._read_retained_owned
    reads = []

    def occupied():
        entered.set()
        assert release.wait(10)

    def submit(*args, **kwargs):
        future = original_submit(*args, **kwargs)
        if phase:
            queued.set()
        return future

    async def execute(operation, name, *args, **kwargs):
        if name == "read_retained":
            original_submit(occupied)
            for _ in range(1000):
                if entered.is_set():
                    break
                await asyncio.sleep(.001)
            assert entered.is_set()
            phase.append("read")
        return await original_execute(operation, name, *args, **kwargs)

    def read(source, manifest, *args, **kwargs):
        reads.append(manifest["id"])
        return original_read(source, manifest, *args, **kwargs)

    monkeypatch.setattr(execution_pool, "submit", submit)
    monkeypatch.setattr(SourceOperation, "execute", execute)
    monkeypatch.setattr(LocalReviewSource, "_read_retained_owned", read)

    async def scenario():
        loaded = await native_host.invoke("load", {
            "number": 42, "expected_current": initial["id"], "budget_seconds": 60,
        })
        old_id = loaded["result"]["descriptor"]["id"]
        old = await native_host.invoke("source", {"number": 42, "generation_id": old_id, "budget_seconds": 60})
        (root / "note.txt").write_text("unreviewed second revision", encoding="utf-8")
        git("add", ".")
        git("commit", "--quiet", "-m", "synthetic second revision")
        head = git("rev-parse", "HEAD")
        latest = max(backend.iterations, key=lambda row: row["id"])
        latest["sourceRefCommit"]["commitId"] = backend.pr["lastMergeSourceCommit"]["commitId"] = head
        evidence["sections"]["changes"]["revision"]["head_sha"] = head
        evidence["sections"]["changes"]["data"][0]["item"]["objectId"] = git("rev-parse", head + ":note.txt")
        newer = await native_host.invoke("load", {"number": 42, "expected_current": old_id, "budget_seconds": 60})
        new = await native_host.invoke("source", {
            "number": 42, "generation_id": newer["result"]["descriptor"]["id"], "budget_seconds": 60,
        })
        assert new["state"] == "ready" and new["source"]["revision"] != old["source"]["revision"]
        owner = AsyncReviewSource(native_host.source_owner, executor=execution_pool)
        host = WorkspaceTools(native_host.client, source_owner=owner)
        task = asyncio.create_task(host.invoke("source-read", {
            "number": 42, "generation_id": old_id, "budget_seconds": 60,
            "source_attachment": old, "path": "/note.txt", "side": "after",
        }))
        try:
            for _ in range(1000):
                if queued.is_set():
                    break
                await asyncio.sleep(.001)
            assert queued.is_set()
            old["source"].clear()
            old["source"].update(copy.deepcopy(new["source"]))
            release.set()
            with pytest.raises(SourceIncomplete, match="source_selection_changed"):
                await task
            assert reads == []
        finally:
            release.set()
            await owner.drain(timeout=2)

    asyncio.run(scenario())


def test_source_busy_refuses_before_client_io_and_materialization(environment, tools, integration, execution_pool, monkeypatch):
    import threading
    from agent_worktrees.async_review_source import AsyncReviewSource
    from agent_worktrees.pr_review_source import SourceIncomplete
    from agent_pull_requests import workspace_tools
    _, _, client, _, _, _ = integration
    native_host, _, _ = tools
    _, _, initial, _, _, _ = environment
    entered, release = threading.Event(), threading.Event()
    original = workspace_tools._snapshot_generation
    calls = []
    original_execute = client.operations.execute
    def execute(*args, **kwargs):
        calls.append(args[0])
        return original_execute(*args, **kwargs)
    monkeypatch.setattr(client.operations, "execute", execute)

    def blocked(*args):
        entered.set()
        assert release.wait(10)
        return original(*args)

    monkeypatch.setattr(workspace_tools, "_snapshot_generation", blocked)
    owner = AsyncReviewSource(native_host.source_owner, executor=execution_pool)
    host = WorkspaceTools(client, source_owner=owner)

    async def scenario():
        loaded = await native_host.invoke("load", {
            "number": 42, "expected_current": initial["id"], "budget_seconds": 60,
        })
        request = {"number": 42, "generation_id": loaded["result"]["descriptor"]["id"], "budget_seconds": 60}
        task = asyncio.create_task(host.invoke("source", request))
        try:
            for _ in range(5000):
                if entered.is_set():
                    break
                await asyncio.sleep(.001)
            assert entered.is_set()
            before = len(calls)
            with pytest.raises(SourceIncomplete, match="source_execution_busy"):
                await host.invoke("source", request)
            assert len(calls) == before
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            with pytest.raises(TimeoutError):
                await owner.drain(timeout=.001)
            assert owner.status()["pending"] == 1
        finally:
            release.set()
            await owner.drain(timeout=2)
        assert not host._generations

    asyncio.run(scenario())


def test_unqualified_loop_client_refuses_source_before_pacer_or_admission(environment, tools, integration, execution_pool, monkeypatch):
    from agent_worktrees.async_review_source import AsyncReviewSource
    from agent_pull_requests.workspace_tools import WorkspaceToolUnavailable
    _, client, _, _, caller, _ = integration
    native_host, _, _ = tools
    _, _, initial, _, _, _ = environment
    loops = []
    original = caller.request

    async def request(*args, **kwargs):
        loops.append(asyncio.get_running_loop())
        return await original(*args, **kwargs)

    monkeypatch.setattr(caller, "request", request)
    owner = AsyncReviewSource(native_host.source_owner, executor=execution_pool)
    host = WorkspaceTools(client, source_owner=owner)

    async def scenario():
        loaded = await native_host.invoke("load", {
            "number": 42, "expected_current": initial["id"], "budget_seconds": 60,
        })
        entered = asyncio.Event()
        release = asyncio.Event()

        async def pace():
            entered.set()
            await release.wait()

        client._read_admission = pace
        await client.capabilities()
        await client.exchange("lookup", {"number": 42, "generation_id": loaded["result"]["descriptor"]["id"]})
        loop = asyncio.get_running_loop()
        before = len(caller.calls)
        with pytest.raises(WorkspaceToolUnavailable, match="preparation_binding_unsupported"):
            await asyncio.wait_for(host.invoke("source", {
                "number": 42, "generation_id": loaded["result"]["descriptor"]["id"], "budget_seconds": .2,
            }), timeout=2)
        assert loops and all(value is loop for value in loops)
        assert len(caller.calls) == before and not entered.is_set() and owner.status()["pending"] == 0
        release.set()
        await owner.drain(timeout=2)
        assert owner.status()["drained"] is True

    asyncio.run(scenario())


def test_source_uses_validated_snapshot_not_mutated_client_result(environment, tools, execution_pool, monkeypatch):
    from agent_worktrees.async_review_source import AsyncReviewSource
    from agent_pull_requests import workspace_tools
    _, _, initial, _, _, _ = environment
    native_host, _, _ = tools
    original = workspace_tools._snapshot_generation

    def snapshot(generation, *args):
        validated = original(generation, *args)
        generation["id"] = "f" * 64
        generation["revision"]["head_sha"] = "b" * 40
        return validated

    monkeypatch.setattr(workspace_tools, "_snapshot_generation", snapshot)
    owner = AsyncReviewSource(native_host.source_owner, executor=execution_pool)
    host = WorkspaceTools(native_host.client, source_owner=owner)

    async def scenario():
        loaded = await native_host.invoke("load", {
            "number": 42, "expected_current": initial["id"], "budget_seconds": 60,
        })
        identity = loaded["result"]["descriptor"]["id"]
        result = await host.invoke("source", {"number": 42, "generation_id": identity, "budget_seconds": 60})
        assert result["generation_id"] == identity and result["state"] == "ready"
        assert result["revision"]["head_sha"] != "b" * 40
        await owner.drain(timeout=2)

    asyncio.run(scenario())


@pytest.mark.parametrize("declared,scheduled", [(False, False), (True, False), (True, True), ("legacy", False)])
def test_delegating_async_client_discovery_matches_preparation_support(
    environment, tools, integration, execution_pool, declared, scheduled,
):
    from agent_worktrees.async_review_source import AsyncReviewSource
    from agent_pull_requests.workspace_tools import WorkspaceToolUnavailable
    _, client, _, _, _, _ = integration
    native_host, _, _ = tools
    _, _, initial, _, _, _ = environment
    loops = []

    def returned(coroutine):
        return asyncio.create_task(coroutine) if scheduled else coroutine

    class Delegate:
        def ensure_capabilities(self, **kwargs):
            loops.append(asyncio.get_running_loop())
            return returned(client.ensure_capabilities(**kwargs))

        def exchange(self, *args, **kwargs):
            loops.append(asyncio.get_running_loop())
            return returned(client.exchange(*args, **kwargs))

        def materialize(self, *args, **kwargs):
            loops.append(asyncio.get_running_loop())
            return returned(client.materialize(*args, **kwargs))

    if declared:
        Delegate.preparation_contract = staticmethod(lambda: {
            **client.preparation_contract(), "version": 1 if declared == "legacy" else 2,
        })
    owner = AsyncReviewSource(native_host.source_owner, executor=execution_pool)
    host = WorkspaceTools(Delegate(), source_owner=owner)

    async def scenario():
        loaded = await native_host.invoke("load", {
            "number": 42, "expected_current": initial["id"], "budget_seconds": 60,
        })
        capabilities = await host.capabilities()
        assert "source" not in capabilities["operations"]
        request = {"number": 42, "generation_id": loaded["result"]["descriptor"]["id"], "budget_seconds": 60}
        with pytest.raises(WorkspaceToolUnavailable, match="preparation_binding_unsupported"):
            await host.invoke("source", request)
        assert loops and all(loop is asyncio.get_running_loop() for loop in loops)
        await owner.drain(timeout=2)

    asyncio.run(scenario())


def test_client_preparation_failure_is_not_relabelled_as_corrupt_metadata(environment, integration):
    from agent_worktrees.pr_review_source import SourceIncomplete
    _, client, _, _, _, _ = integration
    _, _, generation, _, _, _ = environment
    failure = SourceIncomplete("source_execution_busy")

    async def prepare(*args, **kwargs):
        raise failure

    async def scenario():
        await client.capabilities()
        with pytest.raises(SourceIncomplete) as observed:
            await client.materialize(42, generation["id"], prepare=prepare)
        assert observed.value is failure

    asyncio.run(scenario())


def test_native_materialization_withdrawal_stops_before_next_chunk(environment, tools, execution_pool, monkeypatch):
    from agent_worktrees.async_review_source import AsyncReviewSource
    _, _, initial, _, backend, _ = environment
    native_host, _, _ = tools
    backend.pr["description"] = "synthetic metadata " * 2048
    owner = AsyncReviewSource(native_host.source_owner, executor=execution_pool)
    original = native_host.client.operations.execute
    chunks = []
    active = []

    def execute(name, request, **kwargs):
        result = original(name, request, **kwargs)
        if name == "chunk":
            chunks.append(request["offset"])
            active[0].cancelled.set()
        return result

    async def scenario():
        loaded = await native_host.invoke("load", {
            "number": 42, "expected_current": initial["id"], "budget_seconds": 60,
        })
        assert loaded["result"]["descriptor"]["bytes"] > 8192
        monkeypatch.setattr(native_host.client.operations, "execute", execute)
        host = WorkspaceTools(native_host.client, source_owner=owner)
        async with owner.operation(60) as operation:
            active.append(operation)
            with pytest.raises(PermissionError, match="withdrawn"):
                await host._materialize(42, loaded["result"]["descriptor"]["id"], operation=operation)
        await owner.drain(timeout=2)
        assert chunks == [0]
        assert not host._generations

    asyncio.run(scenario())


def test_read_budget_failure_is_json_not_a_private_git_traceback(environment, tools, monkeypatch, capsys):
    import subprocess
    from agent_worktrees import pr_review_source
    _, _, initial, _, _, _ = environment
    host, _, _ = tools
    _, loaded = invoke_cli(host, "load", {"number": 42, "expected_current": initial["id"], "budget_seconds": 60}, monkeypatch, capsys)
    identity = loaded["result"]["descriptor"]["id"]
    _, attachment = invoke_cli(host, "source", {"number": 42, "generation_id": identity, "budget_seconds": 60}, monkeypatch, capsys)

    def fail(*args, **kwargs):
        raise subprocess.TimeoutExpired(["git", "private-root"], 1, output=b"buffered")

    monkeypatch.setattr(pr_review_source, "run_caller_owned", fail)
    code, value = invoke_cli(host, "source-read", {
        "number": 42, "generation_id": identity, "source_attachment": attachment,
        "path": "/note.txt", "side": "after", "budget_seconds": 60,
    }, monkeypatch, capsys)
    assert code == 1 and value["effect_completion"] == "not_inferred"
    assert "private-root" not in json.dumps(value)


def test_verified_generation_reuse_rechecks_owner_without_retransferring_chunks(environment, integration):
    from agent_pull_requests.workspace_client import WorkspaceClientError
    _, _, generation, _, backend, _ = environment
    gateway, client, _, _, caller, _ = integration
    host = WorkspaceTools(client)

    async def scenario():
        request = {"number": 42, "generation_id": generation["id"]}
        assert await host.invoke("materialize", request) == generation
        chunks = sum(path.endswith("/chunk") for path in caller.calls)
        assert await host.invoke("materialize", request) == generation
        assert sum(path.endswith("/chunk") for path in caller.calls) == chunks
        gateway.workspaces = {}
        with pytest.raises(WorkspaceClientError, match="unsupported"):
            await host.invoke("materialize", request)
        assert backend.threads == []

    asyncio.run(scenario())


@pytest.mark.parametrize("command", ["source", "source-read"])
def test_async_source_is_unavailable_without_owned_execution_path(environment, integration, command):
    import threading
    from agent_pull_requests.workspace_tools import SOURCE_CONTRACT, READ_CONTRACT, WorkspaceToolUnavailable
    _, _, generation, _, _, _ = environment
    _, client, _, _, _, _ = integration
    entered = threading.Event()

    class DelayedSource:
        def contract(self):
            return SOURCE_CONTRACT

        def read_contract(self):
            return READ_CONTRACT

        def inspect_and_retain(self, *args, **kwargs):
            entered.set()
            raise AssertionError("unowned blocking execution")

        read_retained = inspect_and_retain

    host = WorkspaceTools(client, source_owner=DelayedSource())

    async def scenario():
        capabilities = await host.capabilities()
        assert command not in capabilities["operations"]
        request = {"number": 42, "generation_id": generation["id"], "budget_seconds": 60}
        if command == "source-read":
            request.update(source_attachment={}, path="/note.txt", side="after")
        with pytest.raises(WorkspaceToolUnavailable):
            await asyncio.wait_for(host.invoke(command, request), timeout=2)
        assert not entered.is_set()

    asyncio.run(scenario())


def test_oversized_source_budget_is_a_json_refusal(environment, tools, monkeypatch, capsys):
    _, _, initial, _, _, _ = environment
    host, _, _ = tools
    _, loaded = invoke_cli(host, "load", {"number": 42, "expected_current": initial["id"], "budget_seconds": 60}, monkeypatch, capsys)
    code, value = invoke_cli(host, "source", {
        "number": 42, "generation_id": loaded["result"]["descriptor"]["id"],
        "budget_seconds": 10**400,
    }, monkeypatch, capsys)
    assert code == 1 and value["effect_completion"] == "not_inferred"


@pytest.mark.parametrize("identity", [[], {}, None])
def test_malformed_generation_identity_is_json_before_cache_lookup(environment, integration, identity, monkeypatch, capsys):
    _, _, native, _, _, _ = integration
    code, value = invoke_cli(WorkspaceTools(native), "materialize", {
        "number": 42, "generation_id": identity,
    }, monkeypatch, capsys)
    assert code == 1 and value["effect_completion"] == "not_inferred"


def test_concurrent_cache_eviction_during_scoped_lookup_does_not_break_read(environment, integration):
    _, _, generation, _, _, _ = environment
    _, client, _, _, _, _ = integration
    host = WorkspaceTools(client)

    async def scenario():
        request = {"number": 42, "generation_id": generation["id"]}
        assert await host.invoke("materialize", request) == generation
        original = client.exchange

        async def evict(operation, payload):
            result = await original(operation, payload)
            if operation == "lookup":
                host._generations.clear()
            return result

        client.exchange = evict
        assert await host.invoke("materialize", request) == generation

    asyncio.run(scenario())
