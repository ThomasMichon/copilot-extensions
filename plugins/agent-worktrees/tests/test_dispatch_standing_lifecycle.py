"""A standing charter's whole lifecycle is held by the real Worktrees owner.

Dispatch's selected writer reaches the actual Worktrees CLI handlers and a
private resident, and admission runs the actual materialization CLI. Native
sessions come only from Worktrees' own session tracking, never a copied receipt.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_worktrees import __main__ as main, claim_providers, config_authority, finalize
from agent_worktrees import config as cfg, git_ops, output, runtime_identity, status_monitor_runtime
from agent_worktrees import installer, repos, resource_producer_cli as family, tracking, tracking_write

ROOT = Path(__file__).resolve().parents[3]
TOPOLOGY = ROOT / "plugins" / "agent-roles" / "skills" / "managing-agent-topology"


def git(checkout, *args):
    subprocess.run(
        ["git", "-C", str(checkout), *args], check=True, capture_output=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


@pytest.fixture(scope="module")
def materialization(tmp_path_factory):
    topology = json.loads((TOPOLOGY / "assets" / "topology.example.json").read_text(encoding="utf-8"))
    source = tmp_path_factory.mktemp("standing-source") / "topology.json"
    source.write_text(json.dumps(topology), encoding="utf-8")
    command = (sys.executable, str(TOPOLOGY / "scripts" / "topology.py"))
    result = subprocess.run(
        [*command, "materialize", str(source), "--request", topology["assignments"][0]["requestId"]],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stderr
    return command, json.loads(result.stdout)


@pytest.fixture
def native(tmp_path, monkeypatch, materialization, _isolate_agent_worktrees_home):
    """Private Worktrees state, writer identity and resident on the materialized host."""
    command, document = materialization
    machine = document["source"]["machine"]
    project = tmp_path / "project"
    records = project / "worktrees"
    records.mkdir(parents=True)
    anchor = tmp_path / "anchor"
    anchor.mkdir()
    config = cfg.Config(
        srcroot=str(tmp_path), machine=machine, platform="linux", repo_name="p",
        repos={"p": cfg.RepoConfig(
            anchor=str(anchor), worktree_root=str(tmp_path / "trees"), default_branch="main",
        )},
    )
    (project / "config.yaml").write_text(json.dumps({
        "platform": "linux", "repos": {"p": {"anchor": str(anchor)}},
    }), encoding="utf-8")
    monkeypatch.setattr(cfg, "detect_platform", lambda: "linux")
    monkeypatch.setattr(cfg, "project_dir", lambda name=None: project)
    monkeypatch.setattr(cfg, "tracking_dir", lambda name=None: records)
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: config)
    monkeypatch.setattr(cfg, "load_project_config", lambda *a, **k: config)
    monkeypatch.setattr(config_authority, "load_project_authority_config", lambda *a, **k: config)
    monkeypatch.setattr(cfg, "install_dir", lambda: tmp_path / "selected-installation")
    registry = installer.projects_yaml_path()
    assert registry.is_relative_to(tmp_path)
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(json.dumps({"projects": {"p": {}}}), encoding="utf-8")
    repo_registry = repos._repos_yaml_path()
    assert repo_registry.is_relative_to(_isolate_agent_worktrees_home)
    repo_registry.write_text(json.dumps({
        "repos": {"p": {"class": "worktree", "linux": str(anchor)}},
    }), encoding="utf-8")
    monkeypatch.setattr(runtime_identity, "PROCESS_GENERATION", "1" * 32)
    for key in list(os.environ):
        if key in git_ops._REPOSITORY_CONTEXT_ENV or key.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")):
            monkeypatch.delenv(key)
    checkout = tmp_path / "trees" / "owner"
    git(anchor, "init", "-q", "--template=", "-b", "main")
    git(anchor, "-c", "user.name=Test", "-c", "user.email=test@example.com",
        "commit", "-q", "--allow-empty", "-m", "Published baseline")
    git(anchor, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(anchor, "worktree", "add", "-q", "-b", "worktree/owner", str(checkout))
    tracking.create_new_record("owner", "worktree/owner", str(checkout), "p", machine, "linux", records)
    lock = tmp_path / "resident.lock"
    monkeypatch.setattr(status_monitor_runtime, "_monitor_lock_path", lambda: lock)
    monkeypatch.setattr(
        status_monitor_runtime, "_ensure_status_monitor", lambda: pytest.fail("resident bootstrapped"),
    )
    server = tracking_write.start_server(tracking_write.compute)
    server.start()
    lock.write_text(json.dumps({
        **tracking_write.rendezvous_fields(server), "runtime_generation": "fixture-runtime",
    }), encoding="utf-8")
    path = tmp_path / "materialization.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    try:
        yield SimpleNamespace(
            path=records / "owner.yaml", machine=machine, command=command,
            document=document, materialization=path, records=records, anchor=anchor,
        )
    finally:
        server.close()


def head(path, session_id, **extra):
    return {
        "session_id": session_id,
        "revision": tracking.load_record(path).replayed_head_transition.revision, **extra,
    }


def test_declaration_to_retirement_uses_only_native_worktree_ownership(
    native, monkeypatch, tmp_path,
):
    monkeypatch.syspath_prepend(str(ROOT / "plugins" / "agent-dispatch" / "src"))
    from agent_dispatch import assignment_relationship_owner as module
    from agent_dispatch import submission_readiness_observation as readiness
    from agent_dispatch.assignment_admission import StandingAdmission
    from agent_dispatch.assignment_workspace import AssignmentWorkspace
    from agent_dispatch.pool_instances import PoolInstanceDefinition
    from agent_dispatch.queue import TaskError, TaskQueue
    from agent_dispatch.queue_standing_charters import StandingCharterDeclaration
    from agent_dispatch.registrar import Filters
    from agent_dispatch.registrar_configuration import ConfiguredSupervisorCohort, SupervisorScopeIntent
    from agent_dispatch.task_obligations import read_snapshot
    from agent_dispatch.task_relationships import AssignmentSubject

    actual_capture = module.run_background_capture
    calls = []

    def capture(argv, *, input_text="", timeout=None, env=None):
        calls.append(list(argv))
        if argv[0] != "selected-worktrees":
            return actual_capture(argv, input_text=input_text, timeout=timeout, env=env)
        stdin = SimpleNamespace(buffer=io.BytesIO(input_text.encode("utf-8")))
        with monkeypatch.context() as scoped, output.capture_json_output() as captured:
            scoped.setattr(family.sys, "stdin", stdin)
            code = main.main(list(argv[1:]))
        if "record" in argv:
            assert code == 0, captured.getvalue()
        return subprocess.CompletedProcess(argv, code, captured.getvalue(), "")

    monkeypatch.setattr(module, "run_background_capture", capture)
    monkeypatch.setattr(readiness.procutil, "run_background_capture", capture)
    monkeypatch.setattr(readiness.procutil, "agent_worktrees_launch_prefix", lambda: ["selected-worktrees"])
    monkeypatch.setattr(readiness.remote_dispatch, "local_machine", lambda: native.machine)
    record = tracking.load_record(native.path)
    workspace = AssignmentWorkspace(
        native.machine, "p", "owner", record.worktree_path, record.creation_nonce,
    )
    admitted_workspaces = [workspace]
    declaration = StandingCharterDeclaration(
        namespace="operator", charter_key="dispatcher", lane_key="primary",
        effort_ref="efforts/dispatcher", materialization_ref="roles/declared-dispatcher",
        source_revision=native.document["sourceRevision"],
        expected_materialization_revision=native.document["materializationRevision"],
    )
    admitted = []

    def admit_request(observed, identity, request):
        assert observed in admitted_workspaces
        assert identity["installation_root"] == str(tmp_path / "selected-installation")
        admitted.append(request["request_id"])

    writer = module.WorktreeRelationshipWriter(
        ["selected-worktrees"],
        admit_workspace=lambda observed, identity: None if observed in admitted_workspaces else pytest.fail(),
        admit_request=admit_request, require_resident=True,
        standing=StandingAdmission(
            native.machine, tmp_path / "overrides.json", native.command,
            lambda ref: native.materialization if ref == declaration.materialization_ref else None,
        ),
    )
    db = tmp_path / "queue.sqlite3"

    def open_queue():
        return TaskQueue(db, relationship_writer=writer, relationship_authority="local")

    queue = open_queue()

    def provider(ref):
        subject = AssignmentSubject.decode(ref.split(":", 1)[1], authority="local")
        return {"available": True, **subject.verification({
            "task_obligation": read_snapshot(
                queue, subject.task_id, assignment_event_id=subject.assignment_event_id,
            ),
        })}

    monkeypatch.setattr(claim_providers, "resolve_claim_status", provider)

    declared = queue.reconcile_standing_charter("Dispatcher", declaration, repo="owner/repo")
    assert open_queue().reconcile_standing_charter(
        "Dispatcher", declaration, repo="owner/repo",
    ).id == declared.id
    reserved = queue.reserve_standing_charter(
        declared.id, workspace, expected_revision=declared.standing_revision,
    )
    original = queue.task_relationship(reserved.id)
    assert reserved.status == "claimed" and reserved.owner_session_id is None
    assert original["state"] == "bound"
    [resource] = [
        claim for claim in tracking.load_record(native.path).resources
        if claim.ref == original["receipt"]["ref"]
    ]
    assert resource.is_unsettled and resource.ref == original["receipt"]["ref"]
    assert not finalize._assert_obligations_settled(tracking.load_record(native.path), "owner", abandon=False)
    assert any("--resident-only" in call for call in calls)

    tracking.register_session("owner", "current")
    current = head(native.path, "current")

    def accept(task, session, revision, **extra):
        return queue.accept_standing_charter(
            task.id, workspace, expected_revision=task.standing_revision,
            expected_generation=task.generation, owner_session_id=session,
            expected_head_revision=revision, **extra,
        )

    with pytest.raises(TaskError):
        accept(reserved, "current", current["revision"] + 1)
    assert queue.get(reserved.id) == reserved
    accepted = accept(reserved, "current", current["revision"])
    assert accepted.status == "started" and accepted.owner_session_id == "current"
    attachment = accepted.standing_charter["accepted_materialization"]
    assert attachment["head"] == current
    assert attachment["materialization_revision"] == declaration.expected_materialization_revision
    assert attachment["relationship_ref"] == resource.ref
    assert attachment["local_policy"]["selector"]["charter_key"] == "dispatcher"
    assert any("inspect-materialization" in call for call in calls)

    child = queue.create(
        "Finite child", repo="owner/repo", standing_parent_id=accepted.id, require_verification=True,
    )
    cohort = ConfiguredSupervisorCohort(
        "complete", hashlib.sha256(b"configured").hexdigest(), 0,
        (SupervisorScopeIntent(native.machine, native.machine, "linux"),),
    )
    queue.observe_pool_configured_cohort(cohort, expected_revision=0)
    scope = queue.begin_pool_publication(
        native.machine, "linux", "publisher", expected_generation=0, expected_cohort_revision=1,
    )
    queue.publish_pool_catalog_scope(
        native.machine, "linux", scope.publisher_id, generation=scope.generation,
        expected_revision=0, registration_revision=queue.pool_registration_revision(),
        expected_cohort_revision=1, input_revision="inputs",
        definitions=(PoolInstanceDefinition("finite", native.machine, "linux", 0, 1, Filters()),),
    )
    reservation, _ = queue.reserve_spawn(
        child.id, reserved_by="allocator", driver="cli",
        pool_catalog_revision=queue.pool_catalog_snapshot().revision,
    )
    checkout = tmp_path / "trees" / "finite"
    git(native.anchor, "worktree", "add", "-q", "-b", "worktree/finite", str(checkout))
    finite_record = tracking.create_new_record(
        "finite", "worktree/finite", str(checkout), "p", native.machine, "linux", native.records,
        dispatch_attempt=tracking.DispatchAttempt(
            task_id=child.id, reservation_key=reservation.key, attempt=reservation.attempt,
            driver="cli", supervisor="allocator", creator_machine=native.machine, ownership="created",
        ),
    )
    finite_workspace = AssignmentWorkspace(
        native.machine, "p", "finite", str(checkout), finite_record.creation_nonce,
    )
    admitted_workspaces.append(finite_workspace)
    session_id = "11111111-1111-4111-8111-111111111111"
    tracking.register_session(
        "finite", session_id, source_identity="native-source-v1:" + "2" * 64,
    )
    queue.record_spawn_worktree(
        reservation.key, "finite", ownership="created", creating_host=native.machine,
        execution_machine=native.machine, driver="cli", allocation_project="p",
        worktree_path=str(checkout), creation_nonce=finite_record.creation_nonce,
    )
    queue.record_spawn(
        reservation.key, session_handle=session_id, startup_complete=True,
        startup_revision=reservation.startup_revision,
    )
    queue.start(
        child.id, finite_workspace.worker_id, owner_session_id=session_id,
        expected_generation=queue.get(child.id).generation,
    )
    finite_relation = queue.task_relationship(child.id)
    assert finite_relation["state"] == "bound"
    assert queue.complete(
        child.id, finite_workspace.worker_id, result={"answer": "done"},
    ).status == "submitted"
    assert queue.confirm(child.id, actor="evaluator").status == "completed"
    assert queue.reconcile_task_relationship(
        child.id, event_id=finite_relation["request"]["event_id"],
    )["state"] == "closed"
    [finite_resource] = [
        claim for claim in tracking.load_record(native.records / "finite.yaml").resources
        if claim.ref == finite_relation["receipt"]["ref"]
    ]
    assert not finite_resource.is_unsettled
    assert any("observe-ready" in call for call in calls)
    assert queue.get(accepted.id) == accepted
    assert queue.get(child.id).standing_parent_id == accepted.id

    tracking.register_session("owner", "successor")
    tracking.link_succession(tracking.load_record(native.path), "current", "successor")
    successor = head(native.path, "successor", predecessor_session_id="current")
    with pytest.raises(TaskError):
        accept(accepted, "successor", successor["revision"])
    succeeded = accept(
        accepted, "successor", successor["revision"], expected_owner_session_id="current",
    )
    assert succeeded.owner_session_id == "successor"
    assert succeeded.generation == accepted.generation + 1 and succeeded.attempts == accepted.attempts
    assert succeeded.standing_charter["accepted_materialization"]["head"] == successor
    assert queue.task_relationship(succeeded.id)["request"] == original["request"]
    assert queue.task_relationship(succeeded.id)["receipt"] == original["receipt"]

    retiring = queue.retire_standing_charter(succeeded.id, expected_revision=succeeded.standing_revision)
    assert retiring.standing_charter["lifecycle"] == "retiring"
    assert retiring.owner_session_id == "successor"
    with pytest.raises(TaskError, match="admits no new finite children"):
        queue.create("Late child", repo="owner/repo", standing_parent_id=retiring.id)
    with pytest.raises(TaskError, match="native owner"):
        queue.settle_standing_retirement(retiring.id, expected_revision=retiring.standing_revision)

    def release(revision):
        return queue.release_standing_attachment(
            retiring.id, workspace, expected_revision=retiring.standing_revision,
            expected_generation=retiring.generation, owner_session_id="successor",
            expected_conclusion_revision=revision,
        )

    with pytest.raises(TaskError):
        release(successor["revision"] + 1)
    assert queue.get(retiring.id) == retiring
    assert any(
        claim.ref == resource.ref and claim.is_unsettled
        for claim in tracking.load_record(native.path).resources
    )
    tracking.conclude_session(tracking.load_record(native.path), "successor")
    released = release(head(native.path, "successor")["revision"])
    assert released.status == "queued" and released.owner is None and released.owner_session_id is None
    assert queue.task_relationship(released.id)["state"] == "closed"
    record = tracking.load_record(native.path)
    [settled_resource] = [claim for claim in record.resources if claim.ref == resource.ref]
    assert not settled_resource.is_unsettled
    assert settled_resource.producer["phase"] == "closed"
    assert finalize._assert_obligations_settled(record, "owner", abandon=False)

    retired = queue.settle_standing_retirement(released.id, expected_revision=released.standing_revision)
    assert retired.standing_charter["lifecycle"] == "retired"
    restarted = open_queue()
    assert restarted.get(retired.id) == retired
    history = restarted.attachment_history(retired.id)
    assert [item.session_id for item in history] == ["successor", "current"]
    assert all(item.detached_at is not None for item in history)
    assert [item.detach_reason for item in history] == ["standing release", "standing succession"]
    actions = [
        event["note"].removeprefix("standing:")
        for event in restarted.events(retired.id) if (event["note"] or "").startswith("standing:")
    ]
    assert actions == ["enroll", "reserve", "accept", "succeed", "retire", "release", "retired"]
    assert set(admitted) == {
        original["request"]["request_id"], finite_relation["request"]["request_id"],
    }
