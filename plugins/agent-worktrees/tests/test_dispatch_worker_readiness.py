"""Readiness keeps exact task custody without weakening normal finalization."""

import argparse
import json
import os
import subprocess
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
from agent_procutil import no_window_kwargs
from test_dispatch_worker_closeout import NATIVE
from test_dispatch_worker_closeout import worker as worker

from agent_worktrees import dispatch_worker_closeout as closeout
from agent_worktrees import dispatch_worker_readiness as ready
from agent_worktrees import finalize_obligations, finalized_checkout, output, record_cli, tracking

TASK = "a" * 32


def _git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=path,
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
        **no_window_kwargs(),
    ).stdout.strip()


@pytest.fixture
def prepared(worker, monkeypatch):
    for key in tuple(os.environ):
        if key.upper() in ready.git_ops._REPOSITORY_CONTEXT_ENV or key.upper().startswith(
            ("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")
        ):
            monkeypatch.delenv(key)
    record = worker.record
    record.dispatch_attempt = replace(record.dispatch_attempt, task_id=TASK)
    worker.binding = replace(worker.binding, task_id=TASK, allocation=record.dispatch_attempt)
    identity = {"assignment_event_id": 7, "worker_id": "host/worker"}
    metadata = {
        "version": 2,
        "lifetime": "retained-relationship",
        "phase": "bound",
        "token": "c" * 32,
        "namespace": "dispatch-local-task",
        "creator": "host/project/worker",
        "request_id": "d" * 32,
        "identity": identity,
        "recovery": {
            "schema": "dispatch-task-assignment",
            "version": 2,
            "authority": "local",
            "queue_identity": "b" * 32,
            "task_id": TASK,
            **identity,
        },
    }
    ref = ready.resource_relationship.claim_ref(metadata)
    record.resources.append(tracking.ResourceClaim(kind="task", ref=ref, producer=metadata))
    worker.ref = ref
    _git(worker.binding.path, "init", "-q", "--template=", "-b", record.branch)
    _git(
        worker.binding.path,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "Published baseline",
    )
    _git(worker.binding.path, "update-ref", "refs/remotes/origin/main", "HEAD")
    worker.config = replace(
        worker.config,
        repos={
            "project": replace(worker.config.repos["project"], default_branch="main"),
        },
    )
    monkeypatch.setattr(ready.cfg, "load_config", lambda: worker.config)
    monkeypatch.setattr(ready.cfg, "tracking_dir", lambda: worker.root)
    monkeypatch.setattr(tracking, "load_record", lambda path: record)
    worker.lock_depth = 0
    worker.lock_entries = 0

    @contextmanager
    def lock(*args, **kwargs):
        worker.lock_depth += 1
        worker.lock_entries += 1
        try:
            yield
        finally:
            worker.lock_depth -= 1

    monkeypatch.setattr(tracking, "_RecordLock", lock)
    monkeypatch.setattr(
        ready,
        "read_exact_record",
        lambda *a: {
            "path": record.worktree_path,
            "creation_nonce": record.creation_nonce,
        },
    )
    monkeypatch.setattr(
        ready,
        "read_workspace_record",
        lambda *a: {
            "worktree": {
                "id": record.worktree_id,
                "path": record.worktree_path,
                "creation_nonce": record.creation_nonce,
            }
        },
    )
    monkeypatch.setattr(ready.claim_handoffs, "active_bundle_ids_for_source", lambda ref: [])
    monkeypatch.setattr(
        closeout.finalize,
        "validate_and_finalize",
        lambda *a, **k: pytest.fail("Readiness finalized a workspace"),
    )
    monkeypatch.setattr(
        tracking, "save_record", lambda *a, **k: pytest.fail("Readiness saved a record")
    )
    return worker


def _observe(worker, **kwargs):
    return ready.observe_ready(
        "worker",
        TASK,
        "reservation",
        NATIVE,
        creation_nonce="created-worker",
        head_revision=0,
        task_claim_ref=worker.ref,
        **kwargs,
    )


def _record(argv):
    parser = argparse.ArgumentParser()
    record_cli.add_parsers(parser.add_subparsers(dest="command"))
    args = parser.parse_args(["record", *argv, "--json"])
    with output.capture_json_output() as captured:
        rc = record_cli.cmd_record(args)
    return rc, json.loads(captured.getvalue())


def _request(worker, operation="observe-ready"):
    return [
        "--worktree-id", "worker",
        "--readiness-operation", operation,
        "--readiness-task", TASK,
        "--readiness-reservation", "reservation",
        "--readiness-native", NATIVE,
        "--readiness-creation-nonce", "created-worker",
        "--readiness-head-revision", "0",
        "--readiness-task-claim-ref", worker.ref,
    ]


def test_record_descriptors_are_distinct_and_never_inspect_a_checkout(monkeypatch):
    from agent_worktrees import dispatch_closeout_observation

    def forbidden(*args, **kwargs):
        pytest.fail("Contract discovery inspected or changed a workspace")

    for module, name in (
        (ready, "observe_ready"), (ready, "read_exact_record"),
        (record_cli, "read_exact_record"), (record_cli, "read_workspace_record"),
        (tracking, "load_record"), (tracking, "save_record"),
    ):
        monkeypatch.setattr(module, name, forbidden)
    rc, descriptor = _record(["--describe-readiness-contract"])
    assert rc == 0 and descriptor == ready.describe_contract()
    assert descriptor["contract"] == ready.CONTRACT and descriptor["version"] == 1
    assert descriptor["operations"] == ["observe-ready", "check-own-ready"]
    assert descriptor["platforms"] == ["linux", "wsl"]
    assert all(descriptor[key] is False for key in (
        "paired_readiness", "finalized", "task_release", "process_retirement",
    ))
    rc, legacy = _record(["--describe-closeout-contract"])
    assert rc == 0 and legacy == dispatch_closeout_observation.describe_contract()
    assert legacy["contract"] != descriptor["contract"]
    assert legacy["operations"] == ["observe-finalized"] and legacy["version"] == 1


@pytest.mark.parametrize("operation", ["observe-ready", "check-own-ready"])
def test_record_readiness_traverses_real_owner_without_concluding_workspace(
    prepared, monkeypatch, operation,
):
    before = deepcopy(prepared.record)
    if operation == "observe-ready":
        monkeypatch.chdir(prepared.root)
        monkeypatch.delenv("COPILOT_AGENT_SESSION_ID")
    rc, result = _record(_request(prepared, operation))
    assert rc == 0 and result["ok"] is True and result["ready"] is True
    assert result["contract"] == ready.CONTRACT and result["version"] == 1
    assert result["operation"] == operation
    assert result["allocation"] == prepared.record.dispatch_attempt.to_dict()
    assert result["head_revision"] == 0 and result["creation_nonce"] == "created-worker"
    assert result["task_claim_ref"] == prepared.ref
    assert result["checkout_retained"] is True
    assert result["finalized"] is result["task_release"] is result["process_retirement"] is False
    assert prepared.record == before


@pytest.mark.parametrize("case", ["dirty", "head", "claim", "own-native"])
def test_record_refuses_changed_readiness_and_preserves_task_claim(prepared, monkeypatch, case):
    request = _request(prepared)
    if case == "dirty":
        (prepared.binding.path / "unfinished.txt").write_text("WIP", encoding="utf-8")
    elif case == "head":
        request[request.index("--readiness-head-revision") + 1] = "1"
    elif case == "claim":
        request[request.index("--readiness-task-claim-ref") + 1] = "foreign"
    else:
        request = _request(prepared, "check-own-ready")
        monkeypatch.setattr(closeout, "_native_source", lambda native: "foreign")
    before = deepcopy(prepared.record)
    rc, result = _record(request)
    assert rc == 1 and result["ok"] is False and result["error"]
    assert "ready" not in result and prepared.record == before
    assert prepared.record.resources[0].is_unsettled


@pytest.mark.parametrize("probe", ["snapshot", "refs"])
@pytest.mark.parametrize("failure", ["git", "timeout"])
def test_record_normalizes_failed_readiness_probes_without_releasing_custody(
    prepared, monkeypatch, probe, failure,
):
    error = (
        ready.git_ops.GitError(["git", "status"], 128, "index unreadable")
        if failure == "git"
        else subprocess.TimeoutExpired(["git", "status"], 5)
    )

    def failed(*args, **kwargs):
        raise error

    if probe == "snapshot":
        monkeypatch.setattr(ready.finalized_checkout, "snapshot", failed)
    else:
        monkeypatch.setattr(ready, "_refs", failed)
    before = deepcopy(prepared.record)
    rc, result = _record(_request(prepared))
    assert rc == 1 and result == {"version": 1, "ok": False, "error": str(error)}
    assert prepared.record == before and prepared.record.resources[0].is_unsettled
    assert prepared.lock_depth == 0


@pytest.mark.parametrize("missing", [
    "operation", "task", "reservation", "native",
    "creation-nonce", "head-revision", "task-claim-ref",
])
def test_partial_record_readiness_never_reaches_observer(prepared, monkeypatch, missing):
    monkeypatch.setattr(ready, "observe_ready", lambda *a, **k: pytest.fail("Partial request"))
    request = _request(prepared)
    offset = request.index(f"--readiness-{missing}")
    del request[offset:offset + 2]
    rc, result = _record(request)
    assert rc == 1 and result["ok"] is False
    assert "Readiness requires an exact" in result["error"]


@pytest.mark.parametrize("case", [
    "cwd", "describe", "legacy-describe", "closeout-task",
    "closeout-reservation", "closeout-native",
])
def test_mixed_record_operations_refuse_before_observation(prepared, monkeypatch, case):
    from agent_worktrees import dispatch_closeout_observation

    def forbidden(*args, **kwargs):
        pytest.fail("Mixed request reached an owner")

    monkeypatch.setattr(ready, "observe_ready", forbidden)
    monkeypatch.setattr(dispatch_closeout_observation, "observe", forbidden)
    request = _request(prepared)
    if case in {"cwd", "describe", "legacy-describe"}:
        request[:2] = {
            "cwd": ["--cwd", str(prepared.binding.path)],
            "describe": ["--describe-readiness-contract"],
            "legacy-describe": ["--describe-closeout-contract"],
        }[case]
    else:
        request += [f"--{case}", "legacy"]
    rc, result = _record(request)
    assert rc == 1 and result["ok"] is False


@pytest.mark.parametrize("extra", [
    ["--typo"], ["--readiness-head-revision", "not-an-integer"],
    ["--readiness-operation", "finalize"],
    ["--worktree-id", "worker", "--describe-readiness-contract"],
])
def test_readiness_record_keeps_strict_parser_rejection(monkeypatch, extra):
    monkeypatch.setattr(ready, "observe_ready", lambda *a, **k: pytest.fail("Invalid syntax"))
    with pytest.raises(SystemExit) as exc:
        _record(extra)
    assert exc.value.code == 2


def test_independent_observation_retains_exact_claim_and_never_finalizes(prepared, monkeypatch):
    before = deepcopy(prepared.record)
    monkeypatch.chdir(prepared.root)
    monkeypatch.delenv("COPILOT_AGENT_SESSION_ID")
    result = _observe(prepared)
    assert result["ready"] is True and result["finalized"] is False
    assert result["task_claim_ref"] == prepared.ref
    assert result["checkout"] == finalized_checkout.snapshot(prepared.record.worktree_path)
    assert result["task_release"] is False and result["process_retirement"] is False
    assert prepared.record == before and prepared.record.resources[0].is_unsettled
    with pytest.raises(ValueError, match="unsettled"):
        prepared.binding.require(prepared.record, prepared.config)
    assert not finalize_obligations.assert_producers_settled(
        prepared.record,
        "worker",
        emit_error=lambda message: None,
    )


def test_own_readiness_requires_actual_native_caller_but_keeps_claim(prepared, monkeypatch):
    assert _observe(prepared, own_caller=True)["ready"] is True
    monkeypatch.setattr(closeout, "_native_source", lambda native: "foreign")
    with pytest.raises(ValueError, match="native caller"):
        _observe(prepared, own_caller=True)


@pytest.mark.parametrize(
    "kind",
    [
        "missing",
        "duplicate",
        "legacy",
        "pending",
        "closed",
        "opaque",
        "offered",
        "creator",
        "task",
        "queue",
        "event",
        "identity-bool",
        "worker",
        "recovery-version",
    ],
)
def test_only_exact_original_protected_assignment_is_excepted(prepared, kind):
    claim = prepared.record.resources[0]
    if kind == "missing":
        prepared.record.resources.clear()
    elif kind == "duplicate":
        prepared.record.resources.append(deepcopy(claim))
    elif kind == "legacy":
        claim.producer = None
    elif kind in {"pending", "closed"}:
        claim.producer["phase"] = kind
    elif kind == "opaque":
        claim.metadata_opaque = True
    elif kind == "offered":
        claim.handoff_bundle = "offered"
    elif kind == "creator":
        claim.producer["creator"] = "host/project/foreign"
    elif kind == "identity-bool":
        claim.producer["identity"]["assignment_event_id"] = True
    else:
        key, value = {
            "task": ("task_id", "e" * 32),
            "queue": ("queue_identity", ""),
            "event": ("assignment_event_id", 0),
            "worker": ("worker_id", "host/foreign"),
            "recovery-version": ("version", 1),
        }[kind]
        claim.producer["recovery"][key] = value
    if claim.producer is not None and kind not in {"pending", "closed"}:
        claim.ref = ready.resource_relationship.claim_ref(claim.producer)
        prepared.ref = claim.ref
    with pytest.raises(ValueError):
        _observe(prepared)


@pytest.mark.parametrize(
    "kind",
    [
        "task",
        "pr",
        "container",
        "session",
        "unknown",
        "pending",
        "opaque-at-rest",
        "offered-at-rest",
    ],
)
def test_every_other_unsettled_obligation_remains_blocking(prepared, kind):
    prepared.record.resources.append(
        tracking.ResourceClaim(
            kind=kind,
            ref="pending-run:new" if kind == "pending" else "foreign",
            state="at-rest" if kind.endswith("-at-rest") else "active",
            metadata_opaque=kind == "opaque-at-rest",
            handoff_bundle="offered" if kind == "offered-at-rest" else "",
        )
    )
    with pytest.raises(ValueError, match="unsettled"):
        _observe(prepared)


@pytest.mark.parametrize(
    "kind",
    [
        "effort",
        "follow-up",
        "handoff",
        "window",
        "nonce",
        "generation",
        "allocation",
        "foreign-native",
        "head-revision-type",
        "workspace",
    ],
)
def test_changed_or_protected_workspace_is_not_ready(prepared, monkeypatch, kind):
    record = prepared.record
    if kind == "effort":
        record.active_effort = "active"
    elif kind == "follow-up":
        tracking.add_follow_up(record, "Pending work", save=False)
    elif kind == "handoff":
        monkeypatch.setattr(
            ready.claim_handoffs, "active_bundle_ids_for_source", lambda ref: ["pending"]
        )
    elif kind == "window":
        prepared.config = replace(prepared.config, platform="win32")
    elif kind == "nonce":
        record.creation_nonce = "recreated"
    elif kind == "generation":
        record.head_revision = 1
    elif kind == "allocation":
        record.dispatch_attempt = replace(record.dispatch_attempt, reservation_key="foreign")
    elif kind == "foreign-native":
        record.head_session = "foreign"
    elif kind == "head-revision-type":
        record.head_revision = False
    else:
        monkeypatch.setattr(ready, "read_workspace_record", lambda *a: {"worktree": None})
    with pytest.raises(ValueError):
        _observe(prepared)


@pytest.mark.parametrize(
    "kind",
    [
        "untracked",
        "staged",
        "committed",
        "tracked-branch",
        "tracked-branch-tag",
    ],
)
def test_dirty_and_unpublished_content_never_passes_readiness(prepared, kind):
    path = prepared.binding.path
    if kind in {"untracked", "staged"}:
        (path / "unfinished.txt").write_text("unfinished\n")
        if kind == "staged":
            _git(path, "add", "unfinished.txt")
    else:
        if kind.startswith("tracked-branch"):
            _git(path, "checkout", "-q", "--detach")
        _git(
            path,
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "Unpublished",
        )
        if kind.startswith("tracked-branch"):
            _git(path, "update-ref", f"refs/heads/{prepared.record.branch}", "HEAD")
            _git(path, "checkout", "-q", "--detach", "refs/remotes/origin/main")
            if kind == "tracked-branch-tag":
                _git(path, "tag", prepared.record.branch, "HEAD")
    with pytest.raises(ValueError, match=r"uncommitted|committed content"):
        _observe(prepared)


def _merged_pr(prepared, monkeypatch):
    from types import SimpleNamespace

    head = _git(prepared.binding.path, "rev-parse", "HEAD")
    prepared.record.pr = tracking.PRRecord(
        number=1,
        repo="owner/project",
        provider="github",
        state="merged",
        head_sha=head,
        branch="pr/change",
    )
    calls = []

    def pull(pr, repo):
        assert prepared.lock_depth == 0, "Provider I/O must not hold the record lock"
        calls.append(pr.number)
        return (
            None,
            "owner/project",
            pr.number,
            None,
            SimpleNamespace(state="merged", merged=True, head_sha=head),
        )

    monkeypatch.setattr(ready.pr_gate, "pull_for", pull)
    monkeypatch.setattr(ready.pr_gate, "pr_base_ref", lambda *a: "main")
    return head, calls


def test_cached_merge_still_needs_fresh_provider_and_checks_real_content(prepared, monkeypatch):
    head, calls = _merged_pr(prepared, monkeypatch)
    prepared.record.pr.head_sha = "0" * 40
    assert _observe(prepared)["landing"]["prs"][0]["head_sha"] == head
    assert prepared.lock_entries == 2 and prepared.lock_depth == 0
    assert calls == [1] and prepared.record.pr.head_sha == "0" * 40
    _git(
        prepared.binding.path,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "Later work",
    )
    with pytest.raises(ValueError, match="unpublished"):
        _observe(prepared)


@pytest.mark.parametrize("branch", ["worktree/worker", "pr/change"])
def test_merged_pr_branch_checks_cannot_be_shadowed_by_tags(prepared, monkeypatch, branch):
    head, _ = _merged_pr(prepared, monkeypatch)
    path = prepared.binding.path
    _git(path, "checkout", "-q", "-B", branch, head)
    _git(
        path,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "Unpublished branch work",
    )
    _git(path, "checkout", "-q", "--detach", head)
    _git(path, "tag", branch, head)
    with pytest.raises(ValueError, match="unpublished"):
        _observe(prepared)


@pytest.mark.parametrize("available", [True, False])
def test_missing_default_base_uses_available_actual_pr_base_or_refuses(
    prepared,
    monkeypatch,
    available,
):
    head, _ = _merged_pr(prepared, monkeypatch)
    path = prepared.binding.path
    _git(path, "update-ref", "-d", "refs/remotes/origin/main")
    monkeypatch.setattr(ready.pr_gate, "pr_base_ref", lambda *a: "dev")
    if available:
        _git(path, "update-ref", "refs/remotes/origin/dev", head)
        assert _observe(prepared)["landing"]["bases"] == ["refs/remotes/origin/dev"]
    else:
        with pytest.raises(ValueError, match="any published landing base"):
            _observe(prepared)


@pytest.mark.parametrize("value", [None, False, "open", "closed", "missing-head"])
def test_unknown_open_or_unmerged_provider_state_blocks_cached_merge(prepared, monkeypatch, value):
    from types import SimpleNamespace

    _merged_pr(prepared, monkeypatch)
    pulled = value
    if isinstance(value, str):
        pulled = (
            None,
            None,
            None,
            None,
            SimpleNamespace(
                state=value,
                merged=value == "missing-head",
                head_sha="",
            ),
        )
    monkeypatch.setattr(ready.pr_gate, "pull_for", lambda *a: pulled)
    with pytest.raises(ValueError, match=r"confirm|unmerged"):
        _observe(prepared)


@pytest.mark.parametrize("kind", ["checkout", "refs", "record", "hold", "config"])
def test_changes_during_external_observation_invalidate_readiness(prepared, monkeypatch, kind):
    original = ready._landed

    def landed(*args):
        result = original(*args)
        if kind == "checkout":
            (prepared.binding.path / "new.txt").write_text("new\n")
        elif kind == "refs":
            _git(prepared.binding.path, "update-ref", "refs/heads/later", "HEAD")
        elif kind == "record":
            prepared.record.title = "changed"
        elif kind == "config":
            prepared.config = replace(prepared.config, platform="win32")
        else:
            prepared.record.execution_leg_opaque = True
        return result

    monkeypatch.setattr(ready, "_landed", landed)
    with pytest.raises(ValueError):
        _observe(prepared)


def test_git_redirection_cannot_certify_an_unrelated_workspace(prepared, monkeypatch):
    monkeypatch.setenv("GIT_DIR", str(prepared.root / "foreign"))
    with pytest.raises(ValueError, match="unredirected"):
        _observe(prepared)
