"""Rename continuity uses receiving allocations, not naming snapshots."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest
import yaml

from agent_worktrees import config as cfg, execution_spaces, finalize, git_ops, sessions, tracking
from agent_worktrees import terminal_conclusion as terminal
from agent_worktrees import tracking_session_deregistration_write as deregistration
from agent_worktrees import tracking_session_registration_write as registration
from test_creation_tail_repair import _creation_fixture


def rename(creation, *, foreign_creator: str | None = None) -> None:
    for name in creation.configs:
        entries = {"renamed": {
            "hostname": cfg.socket.gethostname(), "execution_platform": cfg.detect_platform(),
        }}
        if foreign_creator is not None:
            entries[foreign_creator] = {
                "hostname": "another-host", "execution_platform": cfg.detect_platform(),
            }
        (creation.anchors[name] / "machines.yaml").write_text(
            yaml.safe_dump({"machines": entries}), encoding="utf-8",
        )
        creation.configs[name] = replace(creation.configs[name], machine="renamed")


def ordinary_child(creation):
    parent = tracking.load_record(creation.record.yaml_path)
    identity = "ordinary-child"
    path = creation.roots["knowledge"] / identity
    git_ops.create_worktree(
        str(creation.anchors["knowledge"]), str(path), f"worktree/{identity}", "HEAD",
    )
    child = tracking.create_new_record(
        identity, f"worktree/{identity}", str(path), "knowledge", parent.machine,
        parent.platform, creation.projects["knowledge"] / "worktrees",
        machine_identity=parent.machine_identity,
    )
    original_ref = tracking.format_claim_ref(parent.execution_machine, child.repo, child.worktree_id)
    parent.resources = [
        tracking.ResourceClaim(kind="worktree", ref=original_ref),
        tracking.ResourceClaim(kind="task", ref="unrelated-task"),
    ]
    tracking.save_record(parent)
    child.owner_ref = tracking.format_claim_ref(
        parent.execution_machine, parent.repo, parent.worktree_id,
    )
    tracking.save_record(child)
    return parent, child, original_ref


@pytest.mark.parametrize("retired", [False, True])
def test_ordinary_owned_child_entry_and_finalization_survive_rename(
    tmp_path, monkeypatch, capsys, retired,
):
    creation = _creation_fixture(tmp_path, monkeypatch, native_git=True, tuple_identity=True)
    parent, child, original_ref = ordinary_child(creation)
    original_owner = child.owner_ref
    rename(creation)
    child = tracking.load_record(child.yaml_path)
    execution_spaces.require_record_mutation(child, creation.configs["knowledge"])
    child.status = "finalized"
    tracking.save_record(child)
    if retired:
        git_ops.git("worktree", "remove", child.worktree_path, cwd=creation.anchors["knowledge"])
    finalize._settle_parent_obligation(child, creation.configs["knowledge"], child.worktree_id)
    parent = tracking.load_record(parent.yaml_path)
    assert next(claim for claim in parent.resources if claim.ref == original_ref).is_at_rest
    assert next(claim for claim in parent.resources if claim.ref == "unrelated-task").is_unsettled
    assert tracking.load_record(child.yaml_path).owner_ref == original_owner
    assert "Cannot confirm" not in capsys.readouterr().err


def test_historical_owner_without_original_backclaim_remains_unproven(tmp_path, monkeypatch):
    creation = _creation_fixture(tmp_path, monkeypatch, native_git=True, tuple_identity=True)
    parent, child, _ = ordinary_child(creation)
    parent.resources[0].ref = "custom-space/knowledge/different-child"
    tracking.save_record(parent)
    rename(creation)
    before = child.yaml_path.read_bytes()
    with pytest.raises(execution_spaces.ExecutionSpaceError):
        execution_spaces.require_record_mutation(
            tracking.load_record(child.yaml_path), creation.configs["knowledge"],
        )
    assert child.yaml_path.read_bytes() == before


@pytest.mark.parametrize("disposition", ["released", "abandoned"])
def test_ordinary_child_finalization_does_not_reopen_closed_parent_claim(
    tmp_path, monkeypatch, disposition,
):
    creation = _creation_fixture(tmp_path, monkeypatch, native_git=True, tuple_identity=True)
    parent, child, original_ref = ordinary_child(creation)
    parent.resources[0].state = disposition
    tracking.save_record(parent)
    child.status = "finalized"
    tracking.save_record(child)
    rename(creation)
    finalize._settle_parent_obligation(
        tracking.load_record(child.yaml_path), creation.configs["knowledge"], child.worktree_id,
    )
    current = tracking.load_record(parent.yaml_path)
    assert next(claim for claim in current.resources if claim.ref == original_ref).state == disposition


@pytest.mark.parametrize("reactivate", [False, True])
def test_session_end_releases_original_self_claim_after_rename(
    tmp_path, monkeypatch, reactivate,
):
    creation = _creation_fixture(tmp_path, monkeypatch, native_git=True, tuple_identity=True)
    path = creation.record.yaml_path
    monkeypatch.setattr(
        registration.config_authority, "load_project_authority_config",
        lambda project, **kwargs: creation.configs[project],
    )
    args = {
        "worktree_id": creation.record.worktree_id, "yaml_path": str(path),
        "session_id": "own-session", "started_at": "2026-10-13T10:00:00Z",
    }
    assert registration.apply_session_register(args).get("ok")
    record = tracking.load_record(path)
    original_ref = next(claim.ref for claim in record.resources if claim.kind == "session")
    others = [
        tracking.ResourceClaim(kind="session", ref=tracking.format_claim_ref(
            record.execution_machine, project, identity, session=session,
        ))
        for project, identity, session in (
            ("another-project", record.worktree_id, "own-session"),
            (record.repo, "another-worktree", "own-session"),
            (record.repo, record.worktree_id, "another-session"),
        )
    ]
    others.append(tracking.ResourceClaim(
        kind="session", ref=tracking.format_claim_ref(
            "remote-space", record.repo, record.worktree_id, session="own-session",
        ),
    ))
    record.resources.extend(others)
    tracking.save_record(record)
    rename(creation, foreign_creator="remote-space")
    if reactivate:
        assert registration.apply_session_register(args).get("ok")
    monkeypatch.setattr(deregistration, "stop_fsmonitor_daemon", lambda _: None)
    result = deregistration.apply_session_deregister({
        "yaml_path": str(path), "session_id": "own-session", "ended_at": "2026-10-13T11:00:00Z",
    })
    assert result.get("ok"), result
    current = tracking.load_record(path)
    assert next(claim for claim in current.resources if claim.ref == original_ref).state == "released"
    assert all(next(claim for claim in current.resources if claim.ref == other.ref).is_live for other in others)
    assert len(current.resources) == 5
    assert current.session_entry("own-session").ended_at is not None


@pytest.mark.parametrize("policy", [terminal.BRIDGE_CREATION_POLICY, terminal.DISPATCH_ATTEMPT_POLICY])
@pytest.mark.parametrize("pair_kind", [None, "anchor"])
def test_creator_conclusion_without_companion_survives_rename(
    tmp_path, monkeypatch, policy, pair_kind,
):
    _conclusion_fixture(tmp_path, monkeypatch, policy, pair_kind=pair_kind)


@pytest.mark.parametrize("policy", [terminal.BRIDGE_CREATION_POLICY, terminal.DISPATCH_ATTEMPT_POLICY])
def test_conclusion_preserves_unrelated_claim_with_same_session_suffix(tmp_path, monkeypatch, policy):
    _conclusion_fixture(tmp_path, monkeypatch, policy, unrelated=True)


def test_dispatch_still_refuses_explicit_foreign_creator(tmp_path, monkeypatch):
    _conclusion_fixture(
        tmp_path, monkeypatch, terminal.DISPATCH_ATTEMPT_POLICY, foreign_creator=True,
    )


def _conclusion_fixture(
    tmp_path, monkeypatch, policy, *, pair_kind=None, unrelated=False, foreign_creator=False,
) -> None:
    creation = _creation_fixture(tmp_path, monkeypatch, native_git=True, tuple_identity=True)
    record = tracking.load_record(creation.record.yaml_path)
    old_creator = record.execution_machine
    record.origin = "delegate"
    record.sessions = [tracking.SessionEntry(session_id="own-session", started_at="2026-10-13T10:00:00Z")]
    record.head_session = "own-session"
    record.resources = [tracking.ResourceClaim(kind="session", ref=tracking.format_claim_ref(
        old_creator, record.repo, record.worktree_id, session="own-session",
    ))]
    if unrelated:
        record.resources.append(tracking.ResourceClaim(
            kind="session", ref=tracking.format_claim_ref(
                "remote-space", record.repo, record.worktree_id, session="own-session",
            ),
        ))
    if policy == terminal.DISPATCH_ATTEMPT_POLICY:
        record.dispatch_attempt = tracking.DispatchAttempt(
            task_id="task", reservation_key="reservation", attempt=1,
            driver="dispatcher", supervisor="supervisor", creator_machine=old_creator,
        )
    if pair_kind:
        record.pair_id, record.pair_role, record.pair_kind = "pair", "harness", pair_kind
        record.pair_ref = f"{old_creator}/knowledge/@anchor"
    tracking.save_record(record)
    git_ops.git("update-ref", "refs/remotes/origin/main", "HEAD", cwd=creation.anchors["harness"])
    rename(creation, foreign_creator=old_creator if foreign_creator else "remote-space" if unrelated else None)
    monkeypatch.setattr(sessions, "has_mux_session", lambda _: False)
    monkeypatch.setattr(sessions, "scan_sessions_fast", lambda _: SimpleNamespace(active_sessions={}))
    result = terminal.conclude_disposable_worktree(
        record.yaml_path, creation.configs["harness"].default_repo,
        session_id="own-session", owner="dispatcher", policy=policy,
        reservation_key="reservation", creation_nonce=record.creation_nonce,
    )
    current = tracking.load_record(record.yaml_path)
    if unrelated or foreign_creator:
        assert result.get("reason") == ("creator-machine-mismatch" if foreign_creator else "outstanding-obligations")
        assert all(claim.is_live for claim in current.resources)
    else:
        assert result["managed_gc_eligible"], result
        assert not current.live_resources
