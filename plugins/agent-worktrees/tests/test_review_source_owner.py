"""Synthetic selected repositories through the actual Git/source/lifetime owners."""

import asyncio
import copy
import io
import json
import subprocess
import sys
import threading
import time

import pytest
from agent_procutil import no_window_kwargs

from agent_worktrees.async_review_source import AsyncReviewSource
from agent_worktrees.git_repository_environment import repository_identity_env
from agent_worktrees.git_review_acquisition import fetch_exact
from agent_worktrees.pr_review_source import LocalReviewSource, SourceIncomplete
from test_pr_review_source import prepared as prepared


@pytest.fixture
def acquisition(prepared, tmp_path):
    source, generation, git, _ = prepared
    revision = generation["revision"]
    git("update-ref", "refs/heads/reviewed", revision["head_sha"])
    git("update-ref", "refs/heads/target", revision["common_sha"])
    root = tmp_path / "selected-shallow"
    root.mkdir()

    def selected_git(*args):
        return subprocess.run(
            ["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=" + str(tmp_path / "no-hooks"), *args],
            cwd=root, env=repository_identity_env(), capture_output=True, timeout=20,
            check=True, **no_window_kwargs(),
        ).stdout.decode().strip()

    selected_git("init", "--quiet")
    selected_git("remote", "add", "origin", source.root.as_uri())
    selected_git("fetch", "--quiet", "--depth=1", "--no-tags", "origin", revision["common_sha"])
    selected_git("update-ref", "refs/retained/previous", revision["common_sha"])
    selected_git("update-ref", "refs/heads/target", revision["common_sha"])
    owner = LocalReviewSource(root.resolve(), source.workspace_id, generation["scope"],
                              checkpoint=source.checkpoint, acquisition=fetch_exact, remote="origin")
    request = {"scope": generation["scope"], "revision": revision,
               "changes": generation["sections"]["changes"]["data"]}
    return owner, generation, request, selected_git


def test_actual_exact_fetch_preserves_refs_and_depth_without_target_or_worktree(acquisition):
    owner, generation, request, git = acquisition
    from agent_pull_requests.source_attachment import attach_source
    old_refs = git("show-ref")
    result = attach_source(generation, owner, prepare=True, budget=60)
    assert result["state"] == "ready", result
    assert old_refs in git("show-ref")
    assert git("rev-parse", "refs/heads/target") == request["revision"]["common_sha"]
    assert git("rev-parse", "--is-shallow-repository") == "true"
    assert set((owner.root / ".git" / "shallow").read_text().split()) == {
        request["revision"]["head_sha"], request["revision"]["common_sha"],
    }
    assert not (owner.root / ".git" / "index").exists()
    assert not git("for-each-ref", "refs/remotes", "refs/tags")
    fetched = (owner.root / ".git" / "FETCH_HEAD").read_bytes()
    second = owner.prepare_and_retain(request, budget=60)
    assert second["retained_refs"] == result["source"]["retained_refs"]
    assert (owner.root / ".git" / "FETCH_HEAD").read_bytes() == fetched
    assert owner.read_retained(second, "/edit.txt", "after", budget=60)["content"] == "after\r\n"


@pytest.mark.parametrize("shape", ["full", "shared", "alternates"])
def test_unsafe_storage_refuses_before_existing_transport(acquisition, monkeypatch, shape):
    owner, _, request, _ = acquisition
    if shape == "full":
        (owner.root / ".git" / "shallow").unlink()
    elif shape == "shared":
        (owner.root / ".git" / "worktrees").mkdir()
    else:
        (owner.root / ".git" / "objects" / "info" / "alternates").write_text("", encoding="utf-8")
    from agent_worktrees import git_ops
    monkeypatch.setattr(git_ops, "_auth_config_args", lambda *a, **k: pytest.fail("credential acquisition entered"))
    with pytest.raises(SourceIncomplete, match="dedicated_shallow"):
        owner.prepare_and_retain(request, budget=60)


def test_owner_refusal_and_scope_drift_never_enter_acquisition(acquisition):
    owner, _, request, _ = acquisition
    owner._acquisition = lambda *a, **k: pytest.fail("transport entered")
    changed = copy.deepcopy(request)
    changed["scope"]["repo"] = "elsewhere"
    with pytest.raises(ValueError, match="selection"):
        owner.prepare_and_retain(changed, budget=60)
    with pytest.raises(ValueError, match="selection"):
        owner.prepare_and_retain(request, budget=60, max_output_bytes=-1)

    def denied():
        raise PermissionError("existing resource owner refused")
    owner.checkpoint = denied
    with pytest.raises(PermissionError, match="existing resource owner"):
        owner.prepare_and_retain(request, budget=60)


def test_native_mapping_drift_after_fetch_is_not_ready(acquisition):
    owner, _, request, _ = acquisition
    request = copy.deepcopy(request)
    request["changes"][0]["item"]["objectId"] = "a" * 40
    result = owner.prepare_and_retain(request, budget=60)
    assert result["complete"] is False
    assert result["retained_refs"] == {}


def test_actual_native_cache_client_and_json_prepare_frontdoor(acquisition, tmp_path, monkeypatch, capsys):
    from agent_pull_requests.generation_cache import GenerationCache
    from agent_pull_requests.owner_read import OwnerReadHandler
    from agent_pull_requests.watch_replay_store import ReplayStore
    from agent_pull_requests.workspace_operations import NativeWorkspaceClient, WorkspaceOperations
    from agent_pull_requests.workspace_tools import WorkspaceTools
    from agent_pull_requests.workspace_cli import main
    owner, generation, _, _ = acquisition
    store = ReplayStore((tmp_path / "synthetic-store").resolve(), "source-test")
    store.provision()
    cache = GenerationCache(store.root, store.namespace, max_bytes=1024 * 1024, max_generations=4)
    cache.provision()
    cache.publish(generation, expected_current=None)
    scope = dict(generation["scope"])
    scope.pop("number")
    reader = OwnerReadHandler(lambda *args: pytest.fail("unselected live read"),
                              max_work_seconds=60, max_payload_bytes=1024 * 1024)
    operations = WorkspaceOperations(store, cache, scope=scope, read_owner=reader,
                                    binding_ref="synthetic", checkpoint=owner.checkpoint, max_work_seconds=60)
    client = NativeWorkspaceClient(operations, checkpoint=owner.checkpoint, expires_at=lambda: int(time.time()) + 600)
    host = WorkspaceTools(client, source_owner=owner)
    payload = {"number": generation["scope"]["number"], "generation_id": generation["id"], "budget_seconds": 60}
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(payload).encode())))
    assert main(["workspace", "source-prepare", "--request", "-"], host=host) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "ready" and result["generation_id"] == generation["id"]
    assert cache.lookup(generation["scope"])["generation"] == generation


def test_lost_fetch_completion_does_not_publish_ready_and_retry_verifies(acquisition):
    owner, _, request, git = acquisition

    def lost(*args, **kwargs):
        fetch_exact(*args, **kwargs)
        raise SourceIncomplete("synthetic transport completion lost")

    owner._acquisition = lost
    with pytest.raises(SourceIncomplete, match="completion lost"):
        owner.prepare_and_retain(request, budget=60)
    assert git("rev-parse", request["revision"]["head_sha"]) == request["revision"]["head_sha"]
    assert not git("for-each-ref", "refs/agent-pull-requests")
    owner._acquisition = fetch_exact
    assert owner.prepare_and_retain(request, budget=60)["complete"] is True


def test_async_real_inspection_and_retained_read_under_existing_owner(prepared):
    source, generation, _, _ = prepared
    from agent_pull_requests.source_attachment import attach_source_async
    owner = AsyncReviewSource(source)

    async def scenario():
        attachment = await attach_source_async(generation, owner, budget=60)
        assert attachment["state"] == "ready"
        value = await owner.execute("read_retained", attachment["source"], "/edit.txt", "after", budget=60)
        assert value["content"] == "after\r\n"
        await owner.drain(timeout=2)
        assert owner.status() == {"admitting": False, "pending": 0, "drained": True, "cleanup_unconfirmed": False}
        with pytest.raises(RuntimeError, match="admission stopped"):
            await owner.execute("read_retained", attachment["source"], "/edit.txt", "after", budget=60)

    asyncio.run(scenario())


def test_original_process_cleanup_failure_blocks_drain_and_retains_native_custody(prepared, monkeypatch):
    from agent_procutil.owned_process import OwnedCleanupError
    from agent_worktrees import pr_review_source
    source, generation, _, _ = prepared
    native_custody = object()
    failure = OwnedCleanupError(native_custody)

    def incomplete(*args, **kwargs):
        raise failure

    monkeypatch.setattr(pr_review_source, "_owned_git", incomplete)
    owner = AsyncReviewSource(source)
    request = {"scope": generation["scope"], "revision": generation["revision"],
               "changes": generation["sections"]["changes"]["data"]}

    async def scenario():
        with pytest.raises(OwnedCleanupError) as observed:
            await owner.execute("inspect_and_retain", request, budget=60)
        assert observed.value is failure
        with pytest.raises(OwnedCleanupError):
            await owner.drain(timeout=2)
        assert owner.cleanup_errors == (failure,)
        assert owner.cleanup_errors[0].process is native_custody
        assert owner.status()["cleanup_unconfirmed"] is True
        assert owner.status()["drained"] is False

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel", [True, False])
def test_async_cancellation_or_owner_loss_retains_worker_until_terminal_drain(prepared, monkeypatch, cancel):
    from agent_worktrees import pr_review_source
    source, generation, _, _ = prepared
    request = {"scope": generation["scope"], "revision": generation["revision"],
               "changes": generation["sections"]["changes"]["data"]}
    entered, release, denied = threading.Event(), threading.Event(), threading.Event()
    original = pr_review_source._owned_git

    def delayed(*args, **kwargs):
        entered.set()
        if not release.wait(10):
            raise RuntimeError("fixture failed to release owned Git")
        return original(*args, **kwargs)

    def checkpoint():
        if denied.is_set():
            raise PermissionError("selected owner lost")

    source.checkpoint = checkpoint
    monkeypatch.setattr(pr_review_source, "_owned_git", delayed)
    owner = AsyncReviewSource(source)

    async def scenario():
        task = asyncio.create_task(owner.execute("inspect_and_retain", request, budget=60))
        try:
            for _ in range(1000):
                if entered.is_set():
                    break
                await asyncio.sleep(.001)
            assert entered.is_set()
            if cancel:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                denied.set()
            with pytest.raises(TimeoutError, match="custody retained"):
                await owner.drain(timeout=.001)
            assert owner.status()["pending"] == 1
            assert owner.status()["drained"] is False
            joining = asyncio.create_task(owner.drain(timeout=5))
            await asyncio.sleep(0)
            joining.cancel()
            with pytest.raises(asyncio.CancelledError):
                await joining
            assert owner.status()["pending"] == 1
        finally:
            release.set()
            if not cancel:
                with pytest.raises(PermissionError):
                    await task
            await owner.drain(timeout=5)
        assert owner.status()["drained"] is True

    asyncio.run(scenario())
