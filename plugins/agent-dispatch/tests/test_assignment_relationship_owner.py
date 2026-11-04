"""Selected-runtime support and preservation admission precede owner writes."""

from __future__ import annotations

import copy
import io
import json
import os
import subprocess
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from agent_procutil.owned_budget import OwnedExecutionBudget
from agent_procutil.owned_process import OwnedCleanupError, _communicate_windows_bounded

from agent_dispatch import assignment_relationship_owner as module
from agent_dispatch.assignment_workspace import AssignmentWorkspace
from agent_dispatch.queue_records import TaskError
from agent_dispatch.queue_task_relationships import WRITER


@pytest.fixture(autouse=True)
def isolate_cleanup_custody(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "_pending_cleanup", [])


def support() -> dict:
    return {
        "schema": "copilot-extensions.resource-relationship",
        "version": 1,
        "operations": ["reserve", "bind", "inspect", "close"],
        "inspect_absence": True,
        "expected_creation_nonce": True,
        "writer_scope": "executing-process",
        "writer_contract": WRITER,
        "metadata_version": 2,
    }


def test_selected_prefix_and_authoritative_record_keep_original_identity(monkeypatch, tmp_path):
    workspace = AssignmentWorkspace("host", "project", "worktree", str(tmp_path), "original")
    calls = []

    def capture(argv, **kwargs):
        calls.append((argv, kwargs))
        if "capabilities" in argv:
            value = support()
        elif "record" in argv:
            value = {
                "ok": True,
                "worktree": {
                    "id": workspace.worktree,
                    "repo": workspace.project,
                    "path": workspace.path,
                    "creation_nonce": workspace.creation_nonce,
                },
            }
        else:
            assert json.loads(kwargs["input_text"])["expected_creation_nonce"] == "original"
            value = {"ok": True}
        return subprocess.CompletedProcess(argv, 0, json.dumps(value), "")

    monkeypatch.setattr(module, "run_background_capture", capture)
    admitted = []
    writer = module.WorktreeRelationshipWriter(
        ["/selected/python", "-m", "agent_worktrees"],
        admit_workspace=admitted.append,
    )
    assert writer(workspace, "reserve", {"expected_creation_nonce": "original"}) == {"ok": True}
    assert admitted == [workspace] and len(calls) == 3
    assert all(call[0][:3] == ["/selected/python", "-m", "agent_worktrees"] for call in calls)
    assert calls[-1][0][-3:] == ["--owner-ref", workspace.owner_ref, "--json"]


@pytest.mark.parametrize("change", ["old", "new_payload", "missing_nonce", "unknown"])
def test_missing_actual_runtime_support_refuses_before_record_or_effect(monkeypatch, change):
    calls = []
    declaration = copy.deepcopy(support())
    if change == "old":
        declaration.pop("writer_contract")
    elif change == "new_payload":
        declaration["writer_scope"] = "installed-payload"
    elif change == "missing_nonce":
        declaration["expected_creation_nonce"] = False
    else:
        declaration["version"] = True

    def capture(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps(declaration), "")

    monkeypatch.setattr(module, "run_background_capture", capture)
    with pytest.raises(TaskError, match="cannot protect"):
        module.WorktreeRelationshipWriter(
            ["/selected/owner"], admit_workspace=lambda workspace: None
        )
    assert len(calls) == 1 and "capabilities" in calls[0]


@pytest.mark.parametrize("reason", ["preservation", "incarnation"])
def test_unqualified_cohort_or_reused_workspace_cannot_reach_relationship_write(
    monkeypatch,
    tmp_path,
    reason,
):
    workspace = AssignmentWorkspace("host", "project", "worktree", str(tmp_path), "original")
    calls = []

    def capture(argv, **kwargs):
        calls.append(argv)
        value = (
            support()
            if "capabilities" in argv
            else {
                "ok": True,
                "worktree": {
                    "id": workspace.worktree,
                    "repo": workspace.project,
                    "path": workspace.path,
                    "creation_nonce": "replacement",
                },
            }
        )
        return subprocess.CompletedProcess(argv, 0, json.dumps(value), "")

    def admit(workspace):
        if reason == "preservation":
            raise TaskError("participating writers are unqualified")

    monkeypatch.setattr(module, "run_background_capture", capture)
    writer = module.WorktreeRelationshipWriter(["/selected/owner"], admit_workspace=admit)
    with pytest.raises(TaskError, match=r"unqualified|unconfirmed"):
        writer(workspace, "reserve", {"expected_creation_nonce": "original"})
    assert not any("reserve" in argv for argv in calls)


def test_failed_capability_construction_retains_native_custody_across_new_instances(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process, job, worker = object(), object(), object()
    retry_allowed = False
    retried = []
    calls = []

    def cleanup(deadline: float) -> None:
        retried.append(deadline)
        if not retry_allowed:
            raise OSError("native exit unconfirmed")

    failure = OwnedCleanupError(
        process, job, cleanup_resources=(("pipe-worker", worker),), cleanup_retry=cleanup,
    )

    def capture(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        calls.append(argv)
        if len(calls) == 1:
            raise failure
        return subprocess.CompletedProcess(argv, 0, json.dumps(support()), "")

    monkeypatch.setattr(module, "run_background_capture", capture)
    with pytest.raises(TaskError, match="custody retained") as translated:
        module.WorktreeRelationshipWriter(["selected"], admit_workspace=lambda workspace: None)
    assert translated.value.__cause__ is failure
    assert module._pending_cleanup == [failure]
    assert failure.process is process and failure.job is job
    assert failure.cleanup_resources == (("pipe-worker", worker),)
    with pytest.raises(TaskError, match="custody retained"):
        module.WorktreeRelationshipWriter(["selected"], admit_workspace=lambda workspace: None)
    assert len(calls) == 1 and len(retried) == 1
    retry_allowed = True
    module.WorktreeRelationshipWriter(["selected"], admit_workspace=lambda workspace: None)
    assert len(calls) == 2 and len(retried) == 2
    assert module._pending_cleanup == []


def test_unresolved_mutation_is_not_replayed_until_exact_cleanup_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleanup_allowed = False
    cleanup_calls = []
    calls = []

    def cleanup(deadline: float) -> None:
        cleanup_calls.append(deadline)
        if not cleanup_allowed:
            raise TimeoutError("pinned Job has not settled")

    failure = OwnedCleanupError(object(), object(), cleanup_retry=cleanup)

    def capture(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        calls.append((argv, kwargs))
        if len(calls) == 2:
            raise failure
        value = support() if "capabilities" in argv else {"ok": True}
        return subprocess.CompletedProcess(argv, 0, json.dumps(value), "")

    monkeypatch.setattr(module, "run_background_capture", capture)
    writer = module.WorktreeRelationshipWriter(
        ["selected"], admit_workspace=lambda workspace: None,
    )
    arguments = ["resource-relationship", "reserve"]
    payload = {"expected_creation_nonce": "original", "task_id": "original-task"}
    with pytest.raises(TaskError, match="custody retained"):
        writer._run(arguments, payload)
    assert len(calls) == 2 and module._pending_cleanup == [failure]
    with pytest.raises(TaskError, match="custody retained"):
        writer._run(arguments, payload)
    assert len(calls) == 2
    cleanup_allowed = True
    assert writer._run(arguments, payload) == {"ok": True}
    assert len(calls) == 3 and calls[1][1]["input_text"] == calls[2][1]["input_text"]
    assert module._pending_cleanup == [] and len(cleanup_calls) == 2
    assert writer._run(["resource-relationship", "inspect"], payload) == {"ok": True}
    assert len(cleanup_calls) == 2


@pytest.mark.parametrize("kind", ["spawn", "finally"])
def test_cleanup_without_supported_retry_refuses_all_later_captures(
    monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    resources = (("unrecognized-native-resource", object()),) if kind == "spawn" else ()
    failure = OwnedCleanupError(object(), object(), cleanup_resources=resources)
    calls = []

    def capture(argv: list[str], **kwargs: Any) -> None:
        calls.append(argv)
        raise failure

    monkeypatch.setattr(module, "run_background_capture", capture)
    for _ in range(2):
        with pytest.raises(TaskError, match="custody retained"):
            module.WorktreeRelationshipWriter(["selected"], admit_workspace=lambda workspace: None)
    assert len(calls) == 1 and module._pending_cleanup == [failure]
    assert failure.cleanup_resources == resources


@pytest.mark.parametrize("spent", [.25, 2.0])
def test_cleanup_and_next_capture_share_one_call_deadline(
    monkeypatch: pytest.MonkeyPatch, spent: float,
) -> None:
    calls = []

    def capture(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        calls.append(kwargs)
        return subprocess.CompletedProcess(argv, 0, json.dumps(support()), "")

    monkeypatch.setattr(module, "run_background_capture", capture)
    writer = module.WorktreeRelationshipWriter(
        ["selected"], admit_workspace=lambda workspace: None, timeout=1,
    )
    clock = [time.monotonic()]
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    def cleanup(deadline: float) -> None:
        clock[0] += spent

    failure = OwnedCleanupError(object(), object(), cleanup_retry=cleanup)
    module._pending_cleanup.append(failure)
    if spent > 1:
        with pytest.raises(TaskError, match="budget expired"):
            writer._run(["resource-relationship", "capabilities", "--json"])
        assert len(calls) == 1
    else:
        assert writer._run(["resource-relationship", "capabilities", "--json"]) == support()
        assert calls[-1]["timeout"] == pytest.approx(1 - spent)
    assert module._pending_cleanup == []


@pytest.mark.parametrize("failed_stage", ["retirement", "close"])
def test_real_blocked_pipe_custody_survives_writer_translation_and_retry(
    monkeypatch: pytest.MonkeyPatch, failed_stage: str,
) -> None:
    reader, pipe_writer = os.pipe()
    job = object()

    class Process:
        def __init__(self) -> None:
            self.stdin = os.fdopen(pipe_writer, "wb", buffering=0)
            self.stdout = io.BytesIO(b"out")
            self.stderr = io.BytesIO(b"err")
            self.allow_retirement = failed_stage != "retirement"
            self.allow_wait = failed_stage != "close"
            self.allow_close = failed_stage != "close"
            self.retirements = 0
            self.close_calls = 0
            self.reader_closed = False

        def poll(self) -> None:
            return None

        def wait(self, timeout: float) -> int:
            assert timeout > 0
            if not self.allow_wait:
                raise OSError("native wait unconfirmed")
            return 0

        def retire_job(self, actual_job: object, timeout: float) -> None:
            assert actual_job is job and timeout > 0
            self.retirements += 1
            if not self.allow_retirement:
                raise OSError("native exit unconfirmed")
            if not self.reader_closed:
                os.close(reader)
                self.reader_closed = True

        def close(self) -> None:
            self.close_calls += 1
            if not self.allow_close:
                raise OSError("native handle close unconfirmed")

    process = Process()
    failure = None
    captures = []

    def capture(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        captures.append(argv)
        if len(captures) == 1:
            budget = OwnedExecutionBudget.from_timeout(.2, cleanup_reserve=.1)
            _communicate_windows_bounded(
                process, job, input_text="x" * 1_000_000,
                deadline=budget.execution_deadline, argv=["synthetic-native-child"],
                timeout=.2, capture_limits=(1024, 1024), budget=budget,
            )
            pytest.fail("native pipe cleanup failure was lost")
        return subprocess.CompletedProcess(argv, 0, json.dumps(support()), "")

    monkeypatch.setattr(module, "run_background_capture", capture)
    try:
        with pytest.raises(TaskError, match="custody retained") as caught:
            module.WorktreeRelationshipWriter(["selected"], admit_workspace=lambda workspace: None)
        failure = caught.value.__cause__
        assert isinstance(failure, OwnedCleanupError)
        assert module._pending_cleanup == [failure]
        assert failure.process is process and failure.job is job
        workers = [
            resource for kind, resource in failure.cleanup_resources if kind == "pipe-worker"
        ]
        assert len(workers) == 3
        if failed_stage == "retirement":
            assert any(worker.is_alive() for worker, _completed in workers)
        process.allow_wait = True
        with pytest.raises(TaskError, match="custody retained"):
            module.WorktreeRelationshipWriter(["selected"], admit_workspace=lambda workspace: None)
        assert len(captures) == 1 and module._pending_cleanup == [failure]
        assert process.close_calls == (0 if failed_stage == "retirement" else 1)
        process.allow_retirement = process.allow_close = True
        module.WorktreeRelationshipWriter(["selected"], admit_workspace=lambda workspace: None)
        assert len(captures) == 2 and module._pending_cleanup == []
        assert all(completed.is_set() and not worker.is_alive() for worker, completed in workers)
        assert process.stdin.closed and process.stdout.closed and process.stderr.closed
        retirements, closes = process.retirements, process.close_calls
        failure.retry_cleanup(deadline=time.monotonic() + 1)
        assert (process.retirements, process.close_calls) == (retirements, closes)
    finally:
        process.allow_retirement = process.allow_wait = process.allow_close = True
        if failure is not None:
            failure.retry_cleanup(deadline=time.monotonic() + 2)
        else:
            process.retire_job(job, 1)
            process.stdin.close()
            process.stdout.close()
            process.stderr.close()


def test_retry_refuses_new_unknown_resources_without_releasing_custody() -> None:
    cleanup = Mock()
    failure = OwnedCleanupError(object(), object(), cleanup_retry=cleanup)
    resource = object()
    failure.cleanup_resources = (("unknown", resource),)
    with pytest.raises(RuntimeError, match="no complete retry"):
        failure.retry_cleanup(deadline=time.monotonic() + 1)
    cleanup.assert_not_called()
    assert failure.cleanup_resources == (("unknown", resource),)
