"""Own-worker admission adds preservation gates without releasing Dispatch custody."""

from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import select
import subprocess
import sys
from types import SimpleNamespace

import pytest
import yaml

from agent_worktrees import dispatch_worker_closeout as closeout
from agent_worktrees import dispatch_closeout_observation as observation
from agent_worktrees import config as cfg, sessions, tracking
from agent_procutil import no_window_kwargs

NATIVE = "00000000-0000-4000-8000-000000000001"
SOURCE = "native-source-v1:" + "a" * 64


@pytest.fixture
def worker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    workspace = tmp_path / "worker"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    record = tracking.WorktreeRecord(
        worktree_id="worker", branch="worktree/worker", worktree_path=str(workspace),
        repo="project", machine="host", platform="linux", started_at="2026-01-01",
        last_resumed_at="2026-01-01", resume_count=0, title=None,
        status="active", completed_at=None, creation_nonce="created-worker",
        sessions=[tracking.SessionEntry(
            session_id=NATIVE, started_at="2026-01-01", activations=[tracking.SessionActivation(
                ordinal=1, started_at="2026-01-01", start_recorded_at="2026-01-01",
                source_identity=SOURCE,
            )],
        )],
        head_session=NATIVE,
        dispatch_attempt=tracking.DispatchAttempt("task", "reservation", 1, "acp", "pool", "host"),
    )
    config = cfg.Config(
        srcroot=str(tmp_path), machine="host", platform="linux", repo_name="project",
        repos={"project": cfg.RepoConfig(anchor=str(tmp_path / "anchor"), worktree_root=str(tmp_path))},
    )
    binding = closeout.OwnWorker(
        "task", "reservation", NATIVE, "worker", "created-worker", workspace, SOURCE, 0,
        record.dispatch_attempt,
    )
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", NATIVE)
    native_source = closeout._native_source
    monkeypatch.setattr(closeout, "_native_source", lambda native: SOURCE if native == NATIVE else None)
    monkeypatch.setattr(closeout, "require_record_mutation", lambda record, config: None)
    monkeypatch.setattr(sessions, "scan_sessions_fast", lambda records: sessions.SessionContext())
    return SimpleNamespace(
        record=record, binding=binding, config=config, root=tmp_path, native_source=native_source,
    )


def test_own_inbound_allocation_and_session_are_not_an_outbound_closeout_deadlock(worker) -> None:
    record = worker.record
    record.owner_ref = "host/project/creator"
    record.resources.append(tracking.ResourceClaim(
        kind="session", ref=f"host/project/worker#{NATIVE}",
    ))
    worker.binding.require(record, worker.config)


@pytest.mark.parametrize("change", [
    {"task_id": "foreign"}, {"reservation_key": "foreign"}, {"native_id": "foreign"},
    {"worktree_id": "foreign"}, {"creation_nonce": "recreated"}, {"creation_nonce": ""},
    {"source": "foreign"}, {"head_revision": 1},
])
def test_foreign_or_replaced_binding_is_not_own_finalization(worker, change: dict) -> None:
    with pytest.raises(ValueError):
        replace(worker.binding, **change).require(worker.record, worker.config)


@pytest.mark.parametrize("change", [
    {"dispatch_attempt": None}, {"dispatch_attempt_opaque": True},
    {"checkout_managed": False}, {"status": "pushed"}, {"head_session": "foreign"},
    {"follow_up": True}, {"active_effort": "active"},
    {"pair_id": "pair", "pair_role": "harness", "pair_ref": "host/notes/sibling", "pair_kind": "worktree"},
    {"controller_metadata_opaque": True}, {"execution_leg_opaque": True},
    {"session_backend_opaque": True},
])
def test_preservation_states_never_reach_finalization(worker, change: dict) -> None:
    for key, value in change.items():
        setattr(worker.record, key, value)
    with pytest.raises(ValueError):
        worker.binding.require(worker.record, worker.config)


def test_itemized_open_follow_up_blocks_even_without_legacy_flag(worker) -> None:
    tracking.add_follow_up(worker.record, "Finish remaining work", save=False)
    assert worker.record.follow_up is False
    with pytest.raises(ValueError, match="follow-up"):
        worker.binding.require(worker.record, worker.config)


@pytest.mark.parametrize("kind", ["missing", "ambiguous", "ended", "duplicate"])
def test_canonical_active_native_source_is_required(worker, kind: str) -> None:
    entry = worker.record.sessions[0]
    if kind == "missing":
        entry.activations = []
    elif kind == "duplicate":
        entry.activations *= 2
    elif kind == "ambiguous":
        entry.activations[0].source_ambiguous = True
    else:
        entry.activations[0].ended_at = "2026-01-02"
    with pytest.raises(ValueError, match="canonical active"):
        worker.binding.require(worker.record, worker.config)


@pytest.mark.parametrize("kind", ["recorded", "live", "session-claim", "producer", "controller", "handoff"])
def test_foreign_shared_and_unsettled_responsibility_is_retained(
    worker, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    record = worker.record
    if kind == "recorded":
        record.sessions.append(tracking.SessionEntry(session_id="foreign", started_at="2026-01-01"))
    elif kind == "live":
        monkeypatch.setattr(sessions, "scan_sessions_fast", lambda records: sessions.SessionContext(
            active_sessions={sessions._normalize_path(record.worktree_path): [NATIVE, "foreign"]},
        ))
    elif kind in {"session-claim", "producer"}:
        record.resources.append(tracking.ResourceClaim(
            kind="session" if kind == "session-claim" else "container", ref="foreign",
        ))
    elif kind == "controller":
        record.controllers.append(tracking.ControllerRelation(
            kind="session", source="manual", relation_revision=1,
            created_at="2026-01-01", controller_session_id="foreign",
        ))
    else:
        record.handoffs.append(tracking.SessionHandoff(
            ordinal=1, token="pending", predecessor=NATIVE, state="pending", opened_at="2026-01-01",
        ))
    with pytest.raises(ValueError):
        worker.binding.require(record, worker.config)


def test_environment_label_and_neighboring_cwd_are_not_native_ownership(worker, monkeypatch) -> None:
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "foreign")
    with pytest.raises(ValueError):
        worker.binding.require(worker.record, worker.config)
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", NATIVE)
    monkeypatch.chdir(worker.root)
    with pytest.raises(ValueError):
        worker.binding.require(worker.record, worker.config)


@pytest.mark.parametrize("change", [{"attempt": 2}, {"driver": "cli"}, {"supervisor": "other"}])
def test_same_task_and_key_cannot_replace_the_original_allocation(worker, change: dict) -> None:
    worker.record.dispatch_attempt = replace(worker.record.dispatch_attempt, **change)
    with pytest.raises(ValueError):
        worker.binding.require(worker.record, worker.config)


def _wire(worker, monkeypatch) -> list:
    monkeypatch.setattr(cfg, "load_config", lambda: worker.config)
    monkeypatch.setattr(cfg, "tracking_dir", lambda: worker.root)
    monkeypatch.setattr(closeout, "read_workspace_record", lambda cwd: {"worktree": {
        "id": worker.record.worktree_id, "path": worker.record.worktree_path,
    }})
    monkeypatch.setattr(tracking, "load_record", lambda path: worker.record)
    monkeypatch.setattr(
        closeout.finalized_checkout, "matches",
        lambda record: record.status == "finalized" and record.finalized_checkout is not None,
    )
    calls = []

    def finalize(worktree_id, config, **kwargs):
        calls.append((worktree_id, kwargs))
        kwargs["record_guard"](worker.record)
        worker.record.status = "finalized"
        worker.record.finalized_checkout = {"head": "a" * 40, "branch": worker.record.branch}
        return True

    monkeypatch.setattr(closeout.finalize, "validate_and_finalize", finalize)
    return calls


def test_finalizer_is_additive_and_never_asks_to_abandon_or_release(worker, monkeypatch) -> None:
    calls = _wire(worker, monkeypatch)
    result = closeout.finalize_own("task", "reservation", NATIVE)
    assert len(calls) == 1 and calls[0][0] == "worker"
    assert set(calls[0][1]) == {"record_guard"}
    assert result["status"] == "finalized" and result["checkout_retained"] is True
    assert result["task_release"] is False and result["process_retirement"] is False
    assert result["task_id"] == "task" and result["reservation_key"] == "reservation"
    assert result["creation_nonce"] == "created-worker" and result["native_id"] == NATIVE


def _wire_observation(worker, monkeypatch):
    _wire(worker, monkeypatch)
    worker.record.status = "finalized"
    worker.record.finalized_checkout = {"head": "a" * 40, "branch": worker.record.branch}
    monkeypatch.setattr(observation, "require_record_mutation", lambda *a: None)
    monkeypatch.setattr(observation, "read_exact_record", lambda *a: {
        "path": worker.record.worktree_path, "creation_nonce": worker.record.creation_nonce,
    })
    monkeypatch.setattr(observation, "read_workspace_record", lambda *a: {"worktree": {
        "id": worker.record.worktree_id, "creation_nonce": worker.record.creation_nonce,
    }})
    monkeypatch.setattr(closeout.finalize, "validate_and_finalize",
                        lambda *a, **k: pytest.fail("A read finalized the worker"))


def test_observation_is_separate_from_worker_process_ownership_and_never_finalizes(worker, monkeypatch):
    _wire_observation(worker, monkeypatch)
    monkeypatch.chdir(worker.root)
    monkeypatch.delenv("COPILOT_AGENT_SESSION_ID")
    result = observation.observe("worker", "task", "reservation", NATIVE)
    assert result["operation"] == "observe-finalized" and result["status"] == "finalized"
    assert result["allocation"] == worker.record.dispatch_attempt.to_dict()
    assert result["checkout"] == worker.record.finalized_checkout
    assert result["task_release"] is False and result["process_retirement"] is False


@pytest.mark.parametrize("kind", [
    "active", "checkout", "allocation", "head", "paired", "effort", "follow-up",
    "claim", "foreign", "unknown", "source", "workspace", "windows",
])
def test_observation_refuses_protected_or_replaced_finalized_custody(worker, monkeypatch, kind):
    _wire_observation(worker, monkeypatch)
    record = worker.record
    if kind == "active":
        record.status = "active"
    elif kind == "checkout":
        record.finalized_checkout = None
    elif kind == "allocation":
        record.dispatch_attempt = replace(record.dispatch_attempt, reservation_key="foreign")
    elif kind == "head":
        record.head_session = "foreign"
    elif kind == "paired":
        record.pair_id = "pair"
        record.pair_role = "harness"
        record.pair_ref = "host/notes/paired"
        record.pair_kind = "worktree"
    elif kind == "effort":
        record.active_effort = "active"
    elif kind == "follow-up":
        tracking.add_follow_up(record, "Pending follow-up", save=False)
    elif kind == "claim":
        record.resources.append(tracking.ResourceClaim(kind="pr", ref="https://forge.example/pr/1"))
    elif kind == "foreign":
        monkeypatch.setattr(sessions, "scan_sessions_fast", lambda *a: sessions.SessionContext(
            active_sessions={sessions._normalize_path(record.worktree_path): ["foreign"]},
        ))
    elif kind == "unknown":
        record.execution_leg_opaque = True
    elif kind == "source":
        record.sessions[0].activations = []
    elif kind == "workspace":
        monkeypatch.setattr(observation, "read_workspace_record", lambda *a: {"worktree": None})
    else:
        worker.config = replace(worker.config, platform="win32")
    with pytest.raises(ValueError):
        observation.observe("worker", "task", "reservation", NATIVE)


@pytest.mark.parametrize("fault", ["normal-gate", "missing-stamp", "unresolved", "foreign", "no-worktree"])
def test_failed_or_unconfirmed_closeout_does_not_emit_success(worker, monkeypatch, fault) -> None:
    calls = _wire(worker, monkeypatch)
    if fault == "normal-gate":
        monkeypatch.setattr(closeout.finalize, "validate_and_finalize", lambda *a, **k: False)
    elif fault == "missing-stamp":
        def no_stamp(*args, **kwargs):
            worker.record.status = "finalized"
            return True
        monkeypatch.setattr(closeout.finalize, "validate_and_finalize", no_stamp)
    elif fault == "unresolved":
        worker.record.status = "finalizing"
    elif fault == "foreign":
        worker.record.dispatch_attempt = replace(worker.record.dispatch_attempt, task_id="other")
    else:
        monkeypatch.setattr(closeout, "read_workspace_record", lambda cwd: {"worktree": None})
    with pytest.raises(ValueError):
        closeout.finalize_own("task", "reservation", NATIVE)
    assert calls == []


def test_selected_provider_discovery_is_read_only_and_parser_is_strict(monkeypatch, capfd) -> None:
    monkeypatch.setattr(closeout, "finalize_own", lambda *a: pytest.fail("Discovery finalized a workspace"))
    assert closeout.main(["--describe-contract"]) == 0
    descriptor = json.loads(capfd.readouterr().out)
    assert descriptor["contract"] == closeout.CONTRACT and descriptor["version"] == 1
    for argv in (
        ["--describe-contract", "--task-id", "task"], ["--typo"],
        ["--task-id", "task", "--reservation-key", "reservation"],
    ):
        with pytest.raises(SystemExit) as exc:
            closeout.main(argv)
        assert exc.value.code == 2


def test_native_process_proof_never_trusts_the_environment_label(monkeypatch) -> None:
    witness = {"qualified": False}
    seen = []
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", NATIVE)
    monkeypatch.setattr(closeout.native_source_context, "capture", lambda: witness)
    monkeypatch.setattr(
        closeout.native_lifecycle_source, "start_identity",
        lambda native, source: seen.append((native, source)),
    )
    with pytest.raises(ValueError, match="actual native caller"):
        closeout._native_source(NATIVE)
    assert seen == [(NATIVE, witness)]


def test_failed_cli_closeout_is_structured_without_claiming_release(monkeypatch, capfd) -> None:
    def blocked(*args):
        print("Source validation remains blocked.")
        raise ValueError("Unmerged source remains")

    monkeypatch.setattr(closeout, "finalize_own", blocked)
    monkeypatch.setattr(closeout, "metadata_project", lambda *a, **k: "project")
    assert closeout.main([
        "--task-id", "task", "--reservation-key", "reservation", "--native-id", NATIVE,
    ]) == 1
    captured = capfd.readouterr()
    result = json.loads(captured.out)
    assert result["ok"] is False and result["error"] == "Unmerged source remains"
    assert result["task_release"] is False and result["process_retirement"] is False
    assert "Source validation remains blocked." in captured.err


@pytest.fixture
def cwd_project(worker, monkeypatch):
    home = worker.root / "home"
    anchor = worker.root / "anchor"
    metadata = anchor / ".git" / "worktrees" / "worker"
    metadata.mkdir(parents=True)
    (metadata / "commondir").write_text("../..\n", encoding="utf-8")
    (worker.binding.path / ".git").write_text(f"gitdir: {metadata}\n", encoding="utf-8")
    project = "closeout-project"
    state = home / f".{project}"
    records = state / "worktrees"
    records.mkdir(parents=True)
    (state / "config.yaml").write_text(yaml.safe_dump({
        "platform": "linux", "machine": "host",
        "repos": {project: {"anchor": str(anchor), "worktree_root": str(worker.root)}},
    }), encoding="utf-8")
    registry = home / ".agent-worktrees"
    registry.mkdir()
    (registry / "repos.yaml").write_text(yaml.safe_dump({
        "repos": {project: {
            "class": "worktree", cfg.detect_platform(): str(anchor),
        }},
    }), encoding="utf-8")
    (records / "worker.yaml").write_text(yaml.safe_dump({
        "worktree_id": "worker", "repo": project, "worktree_path": str(worker.binding.path),
        "branch": "worktree/worker", "status": "active", "sessions": [],
    }), encoding="utf-8")
    monkeypatch.setenv("AGENT_HOME", str(home))
    for key in ("COPILOT_EXTENSIONS_CONTEXT", "COPILOT_PLUGIN_ROOT", "AGENT_WORKTREES_PAYLOAD_ROOT"):
        monkeypatch.delenv(key, raising=False)
    return project


def test_fresh_module_entrypoint_resolves_cwd_and_loads_real_configuration(cwd_project) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "agent_worktrees.dispatch_worker_closeout",
         "--task-id", "task", "--reservation-key", "reservation", "--native-id", NATIVE],
        capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    reply = json.loads(result.stdout)
    assert result.returncode == 1 and reply["ok"] is False
    assert reply["error"] == "Own-worker closeout requires its recorded Dispatch allocation"
    assert reply["task_release"] is False and reply["process_retirement"] is False


def test_fresh_canonical_cli_reaches_cwd_bound_provider_without_module_rewriting(cwd_project) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "agent_worktrees", "finalize",
         "--dispatch-task-id", "task", "--dispatch-reservation-key", "reservation",
         "--dispatch-native-id", NATIVE, "--json"],
        capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    reply = json.loads(result.stdout)
    assert result.returncode == 1 and reply["ok"] is False
    assert reply["error"] == "Own-worker closeout requires its recorded Dispatch allocation"
    assert reply["task_release"] is False and reply["process_retirement"] is False


@pytest.mark.parametrize("extra", [
    ["--dry-run"], ["other-worktree"], ["--worktree-id", "other-worktree"],
    ["--config", "another.yaml"], ["--abandon", "--handoff-to", "other"],
    ["--force-open-pr"], ["--explain-landing"],
])
def test_canonical_closeout_rejects_all_normal_finalize_overrides(monkeypatch, capfd, extra) -> None:
    import argparse
    from agent_worktrees import finalize_cli

    monkeypatch.setattr(closeout, "main", lambda *a: pytest.fail("Override reached finalizer"))
    parser = argparse.ArgumentParser()
    finalize_cli.add_parsers(parser.add_subparsers(dest="command"))
    args = parser.parse_args([
        "finalize", "--dispatch-task-id", "task", "--dispatch-reservation-key", "reservation",
        "--dispatch-native-id", NATIVE, "--json", *extra,
    ])
    assert finalize_cli.cmd_finalize(args) == 2
    assert "without finalize overrides" in json.loads(capfd.readouterr().out)["error"]


def test_canonical_closeout_cannot_chdir_to_an_explicit_project(monkeypatch, tmp_path) -> None:
    from agent_worktrees.invocation_project import resolve_invocation_project

    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="actual cwd project"):
        resolve_invocation_project(
            ["finalize", "--dispatch-task-id=task"], "foreign",
            lambda *a: pytest.fail("Override resolved"), lambda *a: False, lambda *a: False,
        )


def test_fresh_selected_cli_describes_observation_without_relying_on_worker_cwd(cwd_project, worker):
    result = subprocess.run(
        [sys.executable, "-m", "agent_worktrees", "--project", cwd_project,
         "record", "--describe-closeout-contract", "--json"],
        cwd=worker.root, capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    descriptor = json.loads(result.stdout)
    assert result.returncode == 0 and descriptor["operations"] == ["observe-finalized"]
    assert descriptor["own_finalization_cli_versions"] == [1]
    assert descriptor["task_release"] is False and descriptor["process_retirement"] is False


@pytest.mark.parametrize("fails", [False, True])
def test_entrypoint_restores_previous_project_after_real_cwd_config_load(
    cwd_project, monkeypatch, capfd, fails,
) -> None:
    previous = cfg.active_project()
    cfg.set_active_project("another-project")

    def operation(*args):
        assert cfg.project_name() == cfg.load_config().repo_name == cwd_project
        if fails:
            raise ValueError("Closeout refused")
        return {"status": "finalized", "task_release": False, "process_retirement": False}

    monkeypatch.setattr(closeout, "finalize_own", operation)
    try:
        assert closeout.main([
            "--task-id", "task", "--reservation-key", "reservation", "--native-id", NATIVE,
        ]) == int(fails)
        assert cfg.active_project() == "another-project"
        assert json.loads(capfd.readouterr().out)["ok"] is not fails
    finally:
        cfg.set_active_project(previous)


def test_entrypoint_never_inherits_a_neighbor_project_when_cwd_is_unmanaged(
    tmp_path, monkeypatch, capfd,
) -> None:
    previous = cfg.active_project()
    cfg.set_active_project("another-project")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(closeout, "finalize_own", lambda *a: pytest.fail("Used another project"))
    try:
        assert closeout.main([
            "--task-id", "task", "--reservation-key", "reservation", "--native-id", NATIVE,
        ]) == 1
        assert "project resolved from its actual cwd" in json.loads(capfd.readouterr().out)["error"]
        assert cfg.active_project() == "another-project"
    finally:
        cfg.set_active_project(previous)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Native holds require Linux procfs")
@pytest.mark.parametrize("state", ["concluded", "handed-off"])
def test_actual_foreign_hold_blocks_even_without_legacy_lock_or_active_claim(
    worker, monkeypatch, state,
) -> None:
    other = "00000000-0000-4000-8000-000000000002"
    worker.record.sessions.append(tracking.SessionEntry(
        session_id=other, started_at="2026-01-01", state=state,
    ))
    worker.record.resources.append(tracking.ResourceClaim(
        kind="session", ref=f"host/project/worker#{other}", state="at-rest",
    ))
    root = worker.root / "sessions"
    directory = root / other
    directory.mkdir(parents=True)
    monkeypatch.setattr(sessions, "_session_state_dir", lambda: root)
    with (directory / f"inuse.{os.getpid()}.hold").open("w"):
        assert not list(directory.glob("*.lock"))
        with pytest.raises(ValueError, match="foreign native"):
            worker.binding.require(worker.record, worker.config)
    worker.binding.require(worker.record, worker.config)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Native holds require Linux procfs")
def test_same_uuid_resumed_process_cannot_ignore_still_held_prior_activation(worker, monkeypatch) -> None:
    from agent_worktrees import native_source_context as context, tracking_session_registry as registry

    root = worker.root / "sessions"
    directory = root / NATIVE
    directory.mkdir(parents=True)
    monkeypatch.setattr(sessions, "_session_state_dir", lambda: root)
    epoch = {"platform": "linux", "environment_id": "e" * 32, "boot_id": NATIVE}
    monkeypatch.setattr(context, "epoch", lambda: epoch)
    pid = os.getpid()
    birth = context.process(pid)["start_time"]
    witness = {"version": 1, "qualified": True, **epoch, "ancestors": [
        {"pid": pid, "start_time": birth},
    ]}
    monkeypatch.setattr(context, "capture", lambda: witness)
    monkeypatch.setattr(closeout, "_native_source", worker.native_source)
    own_source = context.identity(NATIVE, pid, birth, epoch)
    worker.binding = replace(worker.binding, source=own_source)
    script = (
        "import os, pathlib, select, sys\n"
        "with (pathlib.Path(sys.argv[1]) / f'inuse.{os.getpid()}.hold').open('w'):\n"
        " print('ready', flush=True)\n"
        " select.select([sys.stdin], [], [], 20)\n"
    )
    with (directory / f"inuse.{pid}.hold").open("w"), subprocess.Popen(
        [sys.executable, "-c", script, str(directory)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    ) as prior:
        try:
            assert select.select([prior.stdout], [], [], 5)[0]
            assert prior.stdout.readline() == "ready\n"
            prior_source = context.identity(
                NATIVE, prior.pid, context.process(prior.pid)["start_time"], epoch,
            )
            entry = worker.record.sessions[0]
            entry.activations[0].source_identity = prior_source
            registry._start_session_activation(
                entry, event_at="2026-01-02", recorded_at="2026-01-02",
                source="bind", source_identity=own_source,
            )
            assert entry.activations[0].end_source == "inferred:next-start"
            with pytest.raises(ValueError, match="foreign native"):
                worker.binding.require(worker.record, worker.config)
        finally:
            prior.communicate("exit\n", timeout=10)
        assert prior.returncode == 0
        worker.binding.require(worker.record, worker.config)


@pytest.mark.parametrize("fault", ["unknown", "session-budget", "file-budget", "hold-budget", "unreadable"])
def test_foreign_hold_inspection_never_treats_unknown_or_over_budget_as_absence(
    worker, monkeypatch, fault,
) -> None:
    from agent_worktrees import dispatch_worker_sessions as inspection

    other = "00000000-0000-4000-8000-000000000002"
    worker.record.sessions.append(tracking.SessionEntry(
        session_id=other, started_at="2026-01-01", state="concluded",
    ))
    root = worker.root / "sessions"
    directory = root / other
    directory.mkdir(parents=True)
    (directory / "inuse.123.hold").touch()
    monkeypatch.setattr(sessions, "_session_state_dir", lambda: root)
    monkeypatch.setattr(inspection.native_source_context, "process", lambda pid: {"start_time": "1"})
    monkeypatch.setattr(inspection.session_liveness, "session_process_hold_state", lambda *a: "unknown")
    if fault.endswith("-budget"):
        monkeypatch.setattr(inspection, {
            "session-budget": "MAX_SESSIONS", "file-budget": "MAX_FILES", "hold-budget": "MAX_HOLDS",
        }[fault], 0)
    elif fault == "unreadable":
        def denied(path):
            raise PermissionError("Unreadable native session directory")
        monkeypatch.setattr(inspection.os, "scandir", denied)
    with pytest.raises(ValueError):
        worker.binding.require(worker.record, worker.config)
