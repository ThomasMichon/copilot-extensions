"""Selected-runtime support and preservation admission precede owner writes."""

from __future__ import annotations

import copy
import io
import json
import os
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from agent_procutil import _windows_caller_owned, owned_process
from agent_procutil.owned_budget import OwnedExecutionBudget
from agent_procutil.owned_process import OwnedCleanupError, _communicate_windows_bounded

from agent_dispatch import assignment_relationship_owner as module
from agent_dispatch import procutil
from agent_dispatch.assignment_workspace import AssignmentWorkspace
from agent_dispatch.queue_records import TaskError
from agent_dispatch.queue_task_relationships import WRITER

WRITER_IDENTITY = {"installation_root": "/selected/installation", "runtime_generation": "1" * 32}


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
        "expected_writer_identity": True,
        "writer_identity": dict(WRITER_IDENTITY),
        "writer_scope": "executing-process",
        "writer_contract": WRITER,
        "metadata_version": 2,
    }


def receipt(workspace):
    return {
        "ok": True, "owner": workspace.owner_ref,
        "creation_nonce": workspace.creation_nonce,
        "writer_contract": WRITER, "writer_identity": dict(WRITER_IDENTITY),
    }


def test_selected_factory_retains_command_and_observes_actual_resident_each_time(monkeypatch):
    from agent_dispatch import worktree_owner_launch

    workspace = AssignmentWorkspace("host", "project", "worktree", "/workspace", "original")
    prefix = ("retained-python", "original-build")
    monkeypatch.setattr(worktree_owner_launch, "retained_selection", lambda: prefix)
    calls = []
    current_generation = "resident-first"

    def capture(argv, **kwargs):
        nonlocal current_generation
        assert tuple(argv[:2]) == prefix
        calls.append(argv)
        if "capabilities" in argv:
            result = {**support(), "resident_inspect": 1}
        elif "--resident-only" in argv:
            payload = json.loads(kwargs["input_text"])
            assert payload == {
                "request_id": "original-request", "allow_missing": True,
                "expected_creation_nonce": workspace.creation_nonce,
            }
            result = {
                **receipt(workspace), "schema": "copilot-extensions.resource-relationship",
                "version": 1, "request_id": "original-request", "absent": True,
                "writer_identity": {**WRITER_IDENTITY, "runtime_generation": "2" * 32},
                "resident": {"generation": current_generation, "runtime_generation": "resident-runtime"},
            }
        elif "record" in argv:
            result = {"ok": True, "worktree": {
                "id": workspace.worktree, "repo": workspace.project,
                "path": workspace.path, "creation_nonce": workspace.creation_nonce,
            }}
        else:
            assert calls[-3][-1] == "--json" and "--resident-only" in calls[-3]
            result = receipt(workspace)
        return subprocess.CompletedProcess(argv, 0, json.dumps(result), "")

    monkeypatch.setattr(module, "run_background_capture", capture)
    admissions = []
    writer = module.WorktreeRelationshipWriter.selected(
        admit_workspace=lambda workspace, identity: admissions.append((workspace, identity)),
        admit_request=lambda workspace, identity, request: None,
    )
    # Exercise the actual write boundary; full request matching has separate controls.
    for generation in ("resident-first", "resident-requalified"):
        current_generation = generation
        writer._write(workspace, "inspect", {"request_id": "original-request"})
    assert len(admissions) == 2
    assert sum("--resident-only" in argv for argv in calls) == 2
    with pytest.raises(TaskError, match="bound original request"):
        writer(workspace, "inspect", {"request_id": "original-request"})


@pytest.mark.parametrize("change", [
    "contract", "nonce", "owner", "request", "identity", "unknown-build",
    "foreign-installation", "resident", "resident-generation", "version", "transport",
])
def test_required_resident_observation_refuses_without_direct_fallback(monkeypatch, change):
    workspace = AssignmentWorkspace("host", "project", "worktree", "/workspace", "original")
    calls = []

    def capture(argv, **kwargs):
        calls.append(argv)
        if "capabilities" in argv:
            value = {**support(), "resident_inspect": 1}
        else:
            assert "--resident-only" in argv
            value = {
                **receipt(workspace), "schema": "copilot-extensions.resource-relationship",
                "version": 1, "request_id": "request", "absent": True,
                "resident": {"generation": "resident", "runtime_generation": "build"},
            }
            if change == "transport":
                return None
            if change == "contract":
                value["writer_contract"] = {}
            elif change in {"nonce", "owner", "request"}:
                field = {"nonce": "creation_nonce", "owner": "owner", "request": "request_id"}[change]
                value[field] = "changed"
            elif change == "identity":
                value["writer_identity"] = None
            elif change == "unknown-build":
                value["writer_identity"]["runtime_generation"] = None
            elif change == "foreign-installation":
                value["writer_identity"]["installation_root"] = "/another/installation"
            elif change == "resident":
                value.pop("resident")
            elif change == "resident-generation":
                value["resident"]["generation"] = ""
            elif change == "version":
                value["version"] = True
        return subprocess.CompletedProcess(argv, 0, json.dumps(value), "")

    monkeypatch.setattr(module, "run_background_capture", capture)
    writer = module.WorktreeRelationshipWriter(
        ["retained"], admit_workspace=lambda workspace, identity: None,
        require_resident=True,
    )
    with pytest.raises(TaskError, match="unconfirmed|unresolved"):
        writer(workspace, "reserve", {"request_id": "request"})
    assert len(calls) == 2
    assert "record" not in calls[-1] and "reserve" not in calls[-1]


@pytest.mark.parametrize("declaration", [None, False, True, 0, 2])
def test_required_resident_syntax_is_negotiated_before_probe(monkeypatch, declaration):
    calls = []

    def capture(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(
            argv, 0, json.dumps({**support(), "resident_inspect": declaration}), "",
        )

    monkeypatch.setattr(module, "run_background_capture", capture)
    with pytest.raises(TaskError, match="cannot protect"):
        module.WorktreeRelationshipWriter(
            ["retained"], admit_workspace=lambda workspace, identity: None,
            require_resident=True,
        )
    assert len(calls) == 1 and "capabilities" in calls[0]


def test_selected_factory_keeps_independent_cohort_and_original_request_gates(monkeypatch):
    from agent_dispatch import worktree_owner_launch

    monkeypatch.setattr(worktree_owner_launch, "retained_selection", lambda: ("retained",))
    calls = []

    def capture(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps({**support(), "resident_inspect": 1}), "")

    monkeypatch.setattr(module, "run_background_capture", capture)

    def unqualified(workspace, identity):
        raise TaskError("another participating writer remains unqualified")

    writer = module.WorktreeRelationshipWriter.selected(
        admit_workspace=unqualified,
        admit_request=lambda workspace, identity, request: None,
    )
    with pytest.raises(TaskError, match="another participating writer"):
        writer._write(
            AssignmentWorkspace("host", "project", "tree", "/workspace", "nonce"),
            "reserve", {"request_id": "request"},
        )
    assert len(calls) == 1
    with pytest.raises(ValueError, match="original request admission"):
        module.WorktreeRelationshipWriter.selected(
            admit_workspace=unqualified, admit_request=None,
        )


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
            assert json.loads(kwargs["input_text"]) == {
                "request_id": "original-request", "expected_creation_nonce": "original",
                "expected_writer_identity": WRITER_IDENTITY,
            }
            value = receipt(workspace)
        return subprocess.CompletedProcess(argv, 0, json.dumps(value), "")

    monkeypatch.setattr(module, "run_background_capture", capture)
    admitted = []

    def admit(workspace, identity):
        assert identity == WRITER_IDENTITY
        admitted.append(workspace)
        identity["runtime_generation"] = "2" * 32

    writer = module.WorktreeRelationshipWriter(
        ["/selected/python", "-m", "agent_worktrees"],
        admit_workspace=admit,
    )
    payload = {"request_id": "original-request"}
    assert writer(workspace, "reserve", payload) == receipt(workspace)
    assert payload == {"request_id": "original-request"}
    assert admitted == [workspace] and len(calls) == 3
    assert all(call[0][:3] == ["/selected/python", "-m", "agent_worktrees"] for call in calls)
    assert calls[-1][0][-3:] == ["--owner-ref", workspace.owner_ref, "--json"]


@pytest.mark.parametrize("probe", ["capabilities", "record"])
def test_payload_free_probes_use_owned_windows_capture_and_retain_cleanup(
    monkeypatch, tmp_path, probe,
):
    workspace = AssignmentWorkspace("host", "project", "worktree", str(tmp_path), "original")
    calls = []
    cleanup_allowed = False
    fail_probe = True

    def cleanup(deadline):
        assert deadline > time.monotonic()
        if not cleanup_allowed:
            raise TimeoutError("probe descendant exit is unconfirmed")

    failure = OwnedCleanupError(object(), object(), cleanup_retry=cleanup)

    def run_owned(argv, **kwargs):
        nonlocal fail_probe
        calls.append((argv, kwargs))
        if "capabilities" in argv or "record" in argv:
            assert kwargs["input_text"] == ""
        else:
            assert json.loads(kwargs["input_text"]) == {
                "expected_creation_nonce": "original", "expected_writer_identity": WRITER_IDENTITY,
            }
        if probe in argv and fail_probe:
            fail_probe = False
            raise failure
        value = support() if "capabilities" in argv else {
            "ok": True, "worktree": {
                "id": workspace.worktree, "repo": workspace.project,
                "path": workspace.path, "creation_nonce": workspace.creation_nonce,
            },
        }
        if "reserve" in argv:
            value = receipt(workspace)
        return subprocess.CompletedProcess(argv, 0, json.dumps(value), "")

    monkeypatch.setattr(procutil, "sys", SimpleNamespace(platform="win32", maxsize=2**63 - 1))
    monkeypatch.setattr(procutil, "_run_owned", run_owned)

    def make_writer():
        return module.WorktreeRelationshipWriter(
            ["selected"], admit_workspace=lambda workspace, identity: None,
        )

    writer = None if probe == "capabilities" else make_writer()

    def invoke():
        if writer is None:
            return make_writer()
        return writer(workspace, "reserve", {"expected_creation_nonce": "original"})

    with pytest.raises(TaskError, match="custody retained") as caught:
        invoke()
    assert caught.value.__cause__ is failure and module._pending_cleanup == [failure]
    captured = len(calls)
    with pytest.raises(TaskError, match="custody retained"):
        invoke()
    assert len(calls) == captured
    cleanup_allowed = True
    result = invoke()
    if writer is None:
        result(workspace, "reserve", {"expected_creation_nonce": "original"})
    assert module._pending_cleanup == []
    assert any("record" in argv for argv, _ in calls)
    assert any("reserve" in argv for argv, _ in calls)


@pytest.mark.parametrize("change", [
    "old", "new_payload", "missing_nonce", "unknown", "missing_guard",
    "unknown_build", "package_generation", "missing_identity", "extra_identity", "empty_root",
])
def test_missing_actual_runtime_support_refuses_before_record_or_effect(monkeypatch, change):
    calls = []
    declaration = copy.deepcopy(support())
    if change == "old":
        declaration.pop("writer_contract")
    elif change == "new_payload":
        declaration["writer_scope"] = "installed-payload"
    elif change == "missing_nonce":
        declaration["expected_creation_nonce"] = False
    elif change == "missing_guard":
        declaration.pop("expected_writer_identity")
    elif change == "unknown_build":
        declaration["writer_identity"]["runtime_generation"] = None
    elif change == "package_generation":
        declaration["writer_identity"]["runtime_generation"] = "1.2.3"
    elif change == "missing_identity":
        declaration.pop("writer_identity")
    elif change == "extra_identity":
        declaration["writer_identity"]["other"] = True
    elif change == "empty_root":
        declaration["writer_identity"]["installation_root"] = ""
    else:
        declaration["version"] = True

    def capture(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps(declaration), "")

    monkeypatch.setattr(module, "run_background_capture", capture)
    with pytest.raises(TaskError, match="cannot protect"):
        module.WorktreeRelationshipWriter(
            ["/selected/owner"], admit_workspace=lambda workspace, identity: None
        )
    assert len(calls) == 1 and "capabilities" in calls[0]


@pytest.mark.parametrize("payload", [
    {"expected_creation_nonce": "replacement"},
    {"expected_writer_identity": None},
    {"expected_writer_identity": {**WRITER_IDENTITY, "runtime_generation": "2" * 32}},
    {"expected_writer_identity": {**WRITER_IDENTITY, "installation_root": "/other/installation"}},
])
def test_caller_cannot_replace_the_admitted_writer_or_original_workspace(monkeypatch, tmp_path, payload):
    calls = []

    def capture(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps(support()), "")

    admitted = []
    monkeypatch.setattr(module, "run_background_capture", capture)
    writer = module.WorktreeRelationshipWriter(
        ["selected"], admit_workspace=lambda workspace, identity: admitted.append(workspace),
    )
    workspace = AssignmentWorkspace("host", "project", "worktree", str(tmp_path), "original")
    with pytest.raises(TaskError, match="conflicts"):
        writer(workspace, "reserve", payload)
    assert admitted == [workspace] and len(calls) == 1


@pytest.mark.parametrize("field,value", [
    ("writer_identity", None),
    ("writer_identity", {**WRITER_IDENTITY, "runtime_generation": "2" * 32}),
    ("writer_identity", {**WRITER_IDENTITY, "installation_root": "/other/installation"}),
    ("creation_nonce", "replacement"), ("writer_contract", None), ("ok", 1),
    ("owner", "host/project/other"),
])
def test_unconfirmed_executing_writer_receipt_retains_request_without_replay(monkeypatch, tmp_path, field, value):
    workspace = AssignmentWorkspace("host", "project", "worktree", str(tmp_path), "original")
    calls = []

    def capture(argv, **kwargs):
        calls.append((argv, kwargs))
        if "capabilities" in argv:
            result = support()
        elif "record" in argv:
            result = {"ok": True, "worktree": {
                "id": workspace.worktree, "repo": workspace.project,
                "path": workspace.path, "creation_nonce": workspace.creation_nonce,
            }}
        else:
            result = {**receipt(workspace), field: value}
        return subprocess.CompletedProcess(argv, 0, json.dumps(result), "")

    monkeypatch.setattr(module, "run_background_capture", capture)
    writer = module.WorktreeRelationshipWriter(["selected"], admit_workspace=lambda workspace, identity: None)
    payload = {"request_id": "retained-original"}
    with pytest.raises(TaskError, match="original relationship retained"):
        writer(workspace, "reserve", payload)
    assert len(calls) == 3 and payload == {"request_id": "retained-original"}
    assert json.loads(calls[-1][1]["input_text"]) == {
        **payload, "expected_creation_nonce": "original",
        "expected_writer_identity": WRITER_IDENTITY,
    }


@pytest.mark.parametrize("provider", ["prior", "unknown", "current", "changed"])
def test_selected_provider_process_negotiates_guard_without_an_extra_probe(tmp_path, provider):
    workspace = AssignmentWorkspace("host", "project", "worktree", str(tmp_path), "original")
    declaration = support()
    if provider == "prior":
        declaration.pop("expected_writer_identity")
    elif provider == "unknown":
        declaration["writer_identity"]["runtime_generation"] = None
    script = tmp_path / "selected_provider.py"
    script.write_text(
        "import json,sys\n"
        "from pathlib import Path\n"
        "settings=json.loads(sys.argv[1]); argv=sys.argv[2:]\n"
        "root=Path(settings['root']); log=root/'calls'\n"
        "with log.open('a') as stream: stream.write(json.dumps(argv)+'\\n')\n"
        "if 'capabilities' in argv:\n"
        " print(json.dumps(settings['support'])); sys.exit(0)\n"
        "if 'record' in argv:\n"
        " print(json.dumps({'ok': True, 'worktree': settings['record']})); sys.exit(0)\n"
        "payload=json.load(sys.stdin)\n"
        "identity=dict(settings['receipt']['writer_identity'])\n"
        "if settings['changed']: identity['runtime_generation']='2'*32\n"
        "if payload.get('expected_writer_identity') != identity:\n"
        " print(json.dumps({'error': 'rejected', 'message': 'executing writer changed'})); sys.exit(1)\n"
        "assert payload == {'request_id': 'original-request', 'expected_creation_nonce': 'original',\n"
        "                   'expected_writer_identity': identity}\n"
        "(root/'effect').write_text(json.dumps(payload))\n"
        "print(json.dumps(settings['receipt']))\n",
        encoding="utf-8",
    )
    settings = {
        "root": str(tmp_path), "support": declaration, "changed": provider == "changed",
        "record": {"id": workspace.worktree, "repo": workspace.project,
                   "path": workspace.path, "creation_nonce": workspace.creation_nonce},
        "receipt": receipt(workspace),
    }

    def execute():
        writer = module.WorktreeRelationshipWriter(
            [sys.executable, str(script), json.dumps(settings)],
            admit_workspace=lambda workspace, identity: None,
        )
        return writer(workspace, "reserve", {"request_id": "original-request"})

    if provider == "current":
        assert execute() == receipt(workspace)
        assert json.loads((tmp_path / "effect").read_text())["request_id"] == "original-request"
    else:
        with pytest.raises(TaskError, match="cannot protect|unconfirmed"):
            execute()
        assert not (tmp_path / "effect").exists()
    calls = [json.loads(line) for line in (tmp_path / "calls").read_text().splitlines()]
    assert len(calls) == (1 if provider in {"prior", "unknown"} else 3)
    assert sum("capabilities" in call for call in calls) == 1


@pytest.mark.parametrize("reason", ["preservation", "incarnation", "writer"])
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

    def admit(workspace, identity):
        assert identity == WRITER_IDENTITY
        if reason == "preservation":
            raise TaskError("participating writers are unqualified")
        if reason == "writer" and identity != {**WRITER_IDENTITY, "runtime_generation": "2" * 32}:
            raise TaskError("independently observed writer is unqualified")

    monkeypatch.setattr(module, "run_background_capture", capture)
    writer = module.WorktreeRelationshipWriter(["/selected/owner"], admit_workspace=admit)
    with pytest.raises(TaskError, match=r"unqualified|unconfirmed"):
        writer(workspace, "reserve", {"expected_creation_nonce": "original"})
    assert not any("reserve" in argv for argv in calls)
    assert len(calls) == (2 if reason == "incarnation" else 1)


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
        module.WorktreeRelationshipWriter(["selected"], admit_workspace=lambda workspace, identity: None)
    assert translated.value.__cause__ is failure
    assert module._pending_cleanup == [failure]
    assert failure.process is process and failure.job is job
    assert failure.cleanup_resources == (("pipe-worker", worker),)
    with pytest.raises(TaskError, match="custody retained"):
        module.WorktreeRelationshipWriter(["selected"], admit_workspace=lambda workspace, identity: None)
    assert len(calls) == 1 and len(retried) == 1
    retry_allowed = True
    module.WorktreeRelationshipWriter(["selected"], admit_workspace=lambda workspace, identity: None)
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
        ["selected"], admit_workspace=lambda workspace, identity: None,
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
            module.WorktreeRelationshipWriter(["selected"], admit_workspace=lambda workspace, identity: None)
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
        ["selected"], admit_workspace=lambda workspace, identity: None, timeout=1,
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
            module.WorktreeRelationshipWriter(["selected"], admit_workspace=lambda workspace, identity: None)
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
            module.WorktreeRelationshipWriter(["selected"], admit_workspace=lambda workspace, identity: None)
        assert len(captures) == 1 and module._pending_cleanup == [failure]
        assert process.close_calls == (0 if failed_stage == "retirement" else 1)
        process.allow_retirement = process.allow_close = True
        module.WorktreeRelationshipWriter(["selected"], admit_workspace=lambda workspace, identity: None)
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
    settled = Mock()
    failure = OwnedCleanupError(
        object(), object(), cleanup_retry=cleanup, on_settled=settled,
    )
    resource = object()
    failure.cleanup_resources = (("unknown", resource),)
    with pytest.raises(RuntimeError, match="no complete retry"):
        failure.retry_cleanup(deadline=time.monotonic() + 1)
    cleanup.assert_not_called()
    settled.assert_not_called()
    assert failure.cleanup_resources == (("unknown", resource),)


@pytest.fixture
def owned_windows_capture(monkeypatch: pytest.MonkeyPatch) -> tuple[list[Any], dict]:
    """Keep the owned Windows orchestration, with synthetic native process handles."""
    processes = []
    control = {"allow_retirement": True}

    class Process:
        def __init__(self) -> None:
            self.stdin = io.BytesIO()
            self.stdout = io.BytesIO(json.dumps(support()).encode())
            self.stderr = io.BytesIO()
            self.returncode = 0
            self.allow_retirement = control["allow_retirement"]
            self.retirements = 0
            self.closes = 0

        def poll(self) -> int:
            return 0

        def wait(self, timeout: float) -> int:
            assert timeout > 0
            return 0

        def retire_job(self, job: Any, timeout: float) -> None:
            assert timeout > 0
            self.retirements += 1
            if not self.allow_retirement:
                raise OSError("native exit unconfirmed")
            job.handle = 0

        def close(self) -> None:
            assert self.stdin.closed and self.stdout.closed and self.stderr.closed
            self.closes += 1

    def spawn(*args: Any, **kwargs: Any) -> tuple[Any, Any]:
        assert kwargs["require_job"] is True and kwargs["allow_breakaway"] is False
        process = Process()
        processes.append(process)
        return process, SimpleNamespace(handle=1)

    monkeypatch.setattr(owned_process, "os", SimpleNamespace(**{**vars(os), "name": "nt"}))
    monkeypatch.setattr(owned_process, "no_window_kwargs", lambda: {})
    monkeypatch.setattr(_windows_caller_owned, "spawn", spawn)
    return processes, control


@pytest.mark.parametrize("retirement_fails", [False, True])
def test_definite_stdlib_pre_start_failure_settles_without_an_unjoinable_worker_hold(
    monkeypatch: pytest.MonkeyPatch, owned_windows_capture: tuple[list[Any], dict],
    retirement_fails: bool,
) -> None:
    processes, control = owned_windows_capture
    control["allow_retirement"] = not retirement_fails
    workers = []

    def make_thread(*args: Any, **kwargs: Any) -> threading.Thread:
        worker = threading.Thread(*args, **kwargs)
        if not workers:
            # The stdlib start() rejects this before native thread creation.
            worker._initialized = False
        workers.append(worker)
        return worker

    monkeypatch.setattr(owned_process, "threading", SimpleNamespace(
        Thread=make_thread, Event=threading.Event, Lock=threading.Lock,
    ))
    settled = Mock()
    budget = OwnedExecutionBudget.from_timeout(.5, cleanup_reserve=.25)
    def run() -> None:
        owned_process._run_owned(
            ["synthetic-native-child"], timeout=.5, input_text="request",
            capture_limits=(1024, 1024), budget=budget, on_settled=settled,
        )

    if retirement_fails:
        with pytest.raises(OwnedCleanupError) as caught:
            run()
        settled.assert_not_called()
        processes[0].allow_retirement = True
        caught.value.retry_cleanup(deadline=time.monotonic() + 1)
    else:
        with pytest.raises(RuntimeError, match=r"thread.__init__"):
            run()
    assert processes[0].retirements == (2 if retirement_fails else 1)
    assert processes[0].closes == 1
    settled.assert_called_once_with()
    assert all(not worker._started.is_set() for worker in workers)
    assert module._pending_cleanup == []
    # A fresh capture can run; there is no fabricated incomplete worker to retry.
    control["allow_retirement"] = True
    result = owned_process._run_owned(
        ["synthetic-native-child"], timeout=1, input_text="request",
        capture_limits=(1024, 1024),
    )
    assert json.loads(result.stdout) == support()


@pytest.mark.parametrize("launch_before_error", [True, False])
def test_uncertain_start_retains_worker_until_kickoff_completion_and_settlement(
    monkeypatch: pytest.MonkeyPatch, owned_windows_capture: tuple[list[Any], dict],
    launch_before_error: bool,
) -> None:
    processes, _control = owned_windows_capture
    release = threading.Event()
    workers = []

    class LateWorker(threading.Thread):
        def run(self) -> None:
            release.wait()
            super().run()

        def start(self) -> None:
            if not launch_before_error:
                # This wrapper cannot attest native absence merely by raising.
                raise RuntimeError("can't start new thread")
            super().start()
            raise RuntimeError("post-launch failure")

    def make_thread(*args: Any, **kwargs: Any) -> threading.Thread:
        worker = LateWorker(*args, **kwargs)
        workers.append(worker)
        return worker

    monkeypatch.setattr(owned_process, "threading", SimpleNamespace(
        Thread=make_thread, Event=threading.Event, Lock=threading.Lock,
    ))
    settled = Mock()
    failure = None
    try:
        budget = OwnedExecutionBudget.from_timeout(.2, cleanup_reserve=.1)
        with pytest.raises(OwnedCleanupError) as caught:
            owned_process._run_owned(
                ["synthetic-native-child"], timeout=.2, input_text="request",
                capture_limits=(1024, 1024), budget=budget, on_settled=settled,
            )
        failure = caught.value
        attempted = [
            resource for kind, resource in failure.cleanup_resources if kind == "pipe-worker"
        ]
        assert len(attempted) == 1 and attempted[0][0] is workers[0]
        assert workers[0].is_alive() is launch_before_error
        assert not attempted[0][1].is_set()
        assert not any(kind == "pipe-worker-unstarted" for kind, _ in failure.cleanup_resources)
        settled.assert_not_called()
        with pytest.raises((RuntimeError, TimeoutError)):
            failure.retry_cleanup(deadline=time.monotonic() + .02)
        assert processes[0].closes == 0
        settled.assert_not_called()
        release.set()
        if not launch_before_error:
            # Resolve the held, indeterminate kickoff using this exact worker.
            threading.Thread.start(workers[0])
        failure.retry_cleanup(deadline=time.monotonic() + 1)
        assert not workers[0].is_alive() and attempted[0][1].is_set()
        assert processes[0].retirements == processes[0].closes == 1
        settled.assert_called_once_with()
        failure.retry_cleanup(deadline=time.monotonic() + 1)
        assert processes[0].closes == 1
        settled.assert_called_once_with()
    finally:
        release.set()
        if failure is not None:
            if not launch_before_error and workers[0].ident is None:
                threading.Thread.start(workers[0])
            failure.retry_cleanup(deadline=time.monotonic() + 2)
        for worker in workers:
            if worker.ident is not None:
                worker.join(timeout=1)
                assert not worker.is_alive()


@pytest.mark.parametrize("callback_fails", [False, True])
def test_owned_retry_delivers_original_settlement_once_only_after_native_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    owned_windows_capture: tuple[list[Any], dict], callback_fails: bool,
) -> None:
    processes, control = owned_windows_capture
    control["allow_retirement"] = False
    callback_error = ValueError("delivery outcome is unknown")

    def settled() -> None:
        assert processes[0].closes == 1
        if callback_fails:
            raise callback_error

    notify = Mock(side_effect=settled)
    with pytest.raises(OwnedCleanupError) as caught:
        owned_process._run_owned(
            ["synthetic-native-child"], timeout=1, input_text="request",
            capture_limits=(1024, 1024), on_settled=notify,
        )
    failure = caught.value
    notify.assert_not_called()
    processes[0].allow_retirement = True
    if callback_fails:
        with pytest.raises(ValueError) as delivered:
            failure.retry_cleanup(deadline=time.monotonic() + 1)
        assert delivered.value is callback_error
        with pytest.raises(RuntimeError, match="delivery is unconfirmed"):
            failure.retry_cleanup(deadline=time.monotonic() + 1)
        assert failure._cleanup_complete is True
        module._pending_cleanup.append(failure)
        capture = Mock(side_effect=AssertionError("unconfirmed delivery must hold new capture"))
        monkeypatch.setattr(module, "run_background_capture", capture)
        with pytest.raises(TaskError, match="custody retained"):
            module.WorktreeRelationshipWriter(["selected"], admit_workspace=lambda workspace, identity: None)
        assert module._pending_cleanup == [failure]
        capture.assert_not_called()
    else:
        failure.retry_cleanup(deadline=time.monotonic() + 1)
        failure.retry_cleanup(deadline=time.monotonic() + 1)
    notify.assert_called_once_with()
    assert processes[0].closes == 1


def test_unsupported_retry_keeps_settlement_callback_undelivered() -> None:
    notify = Mock()
    failure = OwnedCleanupError(object(), object(), on_settled=notify)
    with pytest.raises(RuntimeError, match="no complete retry"):
        failure.retry_cleanup(deadline=time.monotonic() + 1)
    notify.assert_not_called()


@pytest.mark.parametrize(
    ("returncode", "stdout", "stderr", "expected"),
    [
        (1, json.dumps({"ok": False, "error": "relationship token is stale"}), "",
         "(close: relationship token is stale)"),
        (1, json.dumps({"error": "rejected", "message": "token does not match"}), "",
         "(close: rejected: token does not match)"),
        (2, "", "trace\nagent-worktrees: owner lock held\n", "(close: agent-worktrees: owner lock held)"),
    ],
)
def test_refused_relationship_write_retains_the_owners_bounded_reason(
    monkeypatch, tmp_path, returncode, stdout, stderr, expected,
):
    workspace = AssignmentWorkspace("host", "project", "worktree", str(tmp_path), "original")

    def capture(argv, **kwargs):
        if "capabilities" in argv:
            return subprocess.CompletedProcess(argv, 0, json.dumps(support()), "")
        if "record" in argv:
            value = {"ok": True, "worktree": {
                "id": workspace.worktree, "repo": workspace.project,
                "path": workspace.path, "creation_nonce": workspace.creation_nonce,
            }}
            return subprocess.CompletedProcess(argv, 0, json.dumps(value), "")
        return subprocess.CompletedProcess(argv, returncode, stdout, stderr)

    monkeypatch.setattr(module, "run_background_capture", capture)
    writer = module.WorktreeRelationshipWriter(
        ["/selected/python", "-m", "agent_worktrees"], admit_workspace=lambda *_: None,
    )
    with pytest.raises(TaskError) as raised:
        writer(workspace, "close", {"token": "t"})
    assert str(raised.value) == f"selected Worktrees relationship result is unconfirmed {expected}"
