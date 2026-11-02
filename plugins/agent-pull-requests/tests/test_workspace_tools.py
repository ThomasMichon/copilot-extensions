"""Actual CLI/tool dispatch, atomic owners and local exact-Git source fixtures."""

import asyncio
import copy
import io
import json
import sys

import pytest
from agent_procutil.owned_process import run_caller_owned

from test_review_executor import environment as environment
from test_workspace_integration import integration as integration

from agent_pull_requests.workspace_tools import WorkspaceTools, dispatch_tool
from agent_pull_requests import __main__ as cli

pytestmark = pytest.mark.parametrize("environment", [True], indirect=True)


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


def test_owned_async_source_through_actual_tool_and_client_frontdoor(environment, tools, integration):
    from agent_worktrees.async_review_source import AsyncReviewSource
    _, _, initial, _, _, _ = environment
    native_host, _, _ = tools
    owner = AsyncReviewSource(native_host.source_owner)
    host = WorkspaceTools(integration[1], source_owner=owner)

    async def scenario():
        loaded = await host.invoke("load", {
            "number": 42, "expected_current": initial["id"], "budget_seconds": 60,
        })
        identity = loaded["result"]["descriptor"]["id"]
        definitions = await host.definitions()
        assert any(value["name"] == "pr_workspace_source_read" for value in definitions)
        assert not any(value["name"] == "pr_workspace_source_prepare" for value in definitions)
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
