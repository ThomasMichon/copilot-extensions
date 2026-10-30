"""Explicit replacement checks retain the allocation and never send a prompt."""

from dataclasses import replace
from types import SimpleNamespace
import json

import pytest

from agent_bridge import native_replacement as replacement, workspace_creation
from agent_bridge.acquisition_contract import AcquisitionRefused
from agent_bridge.models import SessionStatus
from test_workspace_creation import creation_rig as _creation_rig, rig as _rig

creation_rig = _creation_rig
rig = _rig
pytestmark = pytest.mark.contract("agent_bridge.workspace_creation")
OLD = "11111111-1111-4111-8111-111111111111"
NEW = "22222222-2222-4222-8222-222222222222"


@pytest.fixture
def replacement_rig(creation_rig, monkeypatch):
    rig = creation_rig
    rig.row.update(session_count=1, last_session_id=OLD)
    rig.head["id"] = OLD
    rig.target = replace(rig.target, worktree_id=rig.row["id"])
    rig.client.acp_session_id = NEW
    rig.client.pid = 12345
    rig.grant = {
        "contract": replacement.DISPATCH_CONTRACT, "version": 1,
        "task_id": "task-a", "task_generation": 1, "authorized_reservation_key": "task-a:1",
        "exclusive_key": "group-a", "allocation_reservation_key": "original-task:1",
        "creation_key": "original-allocation-key", "replacement_creation_key": "replacement-key",
        "project": rig.row["repo"], "worktree_id": rig.row["id"],
        "worktree_path": rig.row["path"], "creation_nonce": rig.row["creation_nonce"],
        "expected_head": OLD, "expected_head_revision": 1, "expected_lifecycle_revision": 2,
    }
    rig.source.update(
        provider="agent-dispatch",
        task={"id": "task-a", "status": "queued", "generation": 1, "exclusive_key": "group-a"},
        acquisition_reservation={
            "state": "reserving", "conversation_retired": True,
            "allocation_project": rig.row["repo"], "worktree": rig.row["id"],
            "worktree_path": rig.row["path"], "creation_key": "original-allocation-key",
            "allocation_reservation_key": "original-task:1", "exclusive_key": "group-a",
            "native_replacement_authorization": rig.grant,
        },
    )
    rig.owner_calls = []
    rig.promotions = []
    rig.candidate = {
        **{field: rig.grant[field] for field in replacement.TARGET_FIELDS},
        "expected_lifecycle_revision": 3, "replacement_session": NEW,
        "replacement_source": "native-source-v1:" + "a" * 64,
        "native_source": {"version": 1, "qualified": True},
        "head_transitions": [],
    }

    async def owner(args, project, *, input_json=None):
        assert project == rig.row["repo"]
        rig.owner_calls.append((args, input_json))
        result = {"contract": replacement.OWNER_CONTRACT, "version": 1}
        assert args[0] == "replace-dead-head"
        if args[1] == "capabilities":
            return {**result, "versions": [1], "preflight": 1, "candidate_observation": 1}
        if args[1] == "check":
            return {
                **result, "eligible": True,
                "target": {field: rig.grant[field] for field in replacement.TARGET_FIELDS},
            }
        if args[1] == "observe":
            assert args[-4:] == ["--session-id", NEW, "--pid", "12345"]
            return {**result, "candidate": dict(rig.candidate)}
        assert args[1:] == ["apply", "--permit", "--stdin"]
        assert input_json["expected_head"] == OLD
        assert input_json["replacement_session"] == NEW
        assert "head_transitions" not in input_json
        saved = rig.db.execute_read("SELECT native_session_id FROM workspace_embodiments")
        if saved:
            assert saved[0]["native_session_id"] == NEW
        rig.promotions.append(input_json)
        rig.head["id"] = NEW
        rig.row.update(last_session_id=NEW, session_count=2)
        rig.candidate.update(
            expected_head=NEW, expected_head_revision=4, expected_lifecycle_revision=4,
            head_transitions=[
                {"session_id": OLD, "revision": 1},
                {"session_id": NEW, "revision": 4, "reason": "authorized-dead-head-replacement"},
            ],
        )
        return {**result, "replaced": True, "previous_head": OLD, "head_session": NEW, "head_revision": 4}

    monkeypatch.setattr(replacement, "worktree_command", owner)
    return rig


async def load(rig):
    return await replacement.ReplacementAuthority.load(
        rig.resolver, "task-a", rig.row["repo"], rig.row["id"], "replacement-key",
    )


async def start(rig, **options):
    return await workspace_creation.start(
        rig.manager, rig.target, key="replacement-key", replacement_task="task-a",
        resolver=rig.resolver, **options,
    )


@pytest.mark.asyncio
async def test_grant_is_frozen_and_revalidated(replacement_rig):
    rig = replacement_rig
    authority = await load(rig)
    rig.grant["expected_lifecycle_revision"] += 1
    with pytest.raises(AcquisitionRefused, match="authorization changed"):
        await authority.read()
    assert authority.grant["expected_lifecycle_revision"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("creation_nonce", None), ("expected_head", "not-a-uuid"),
    ("expected_head_revision", True), ("expected_lifecycle_revision", 0),
    ("task_generation", True), ("authorized_reservation_key", ""),
    ("expected_lifecycle_revision", 2**63),
])
async def test_malformed_grant_refuses_before_owner_or_launch(replacement_rig, field, value):
    rig = replacement_rig
    rig.grant[field] = value
    with pytest.raises(AcquisitionRefused):
        await start(rig)
    assert not rig.owner_calls
    assert not rig.manager.list_sessions()
    assert not rig.db.execute_read("SELECT * FROM workspace_embodiments")


@pytest.mark.asyncio
@pytest.mark.parametrize("section,field,value", [
    ("task", "generation", 2), ("task", "status", "claimed"),
    ("task", "owner", "other"), ("task", "hold_reason", "paused"),
    ("acquisition_reservation", "conversation_retired", False),
    ("acquisition_reservation", "state", "deferred"),
    ("acquisition_reservation", "release_requested", True),
    ("acquisition_reservation", "session_handle", {"id": "other"}),
    ("acquisition_reservation", "creation_key", "foreign"),
    ("acquisition_reservation", "ordinary_assignment_error", "ambiguous"),
])
async def test_revoked_authority_never_launches(replacement_rig, section, field, value):
    rig = replacement_rig
    rig.source[section][field] = value
    with pytest.raises(AcquisitionRefused):
        await start(rig)
    assert not rig.manager.list_sessions()
    assert not rig.db.execute_read("SELECT * FROM turns")


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", [1, 2, 3])
async def test_exact_preflight_refusal_never_launches(replacement_rig, monkeypatch, stage):
    rig = replacement_rig
    owner = replacement.worktree_command
    checks = 0

    async def changed(args, project, **kwargs):
        nonlocal checks
        result = await owner(args, project, **kwargs)
        if args[1] == "check":
            checks += 1
            if checks == stage:
                result["eligible"] = False
        return result

    monkeypatch.setattr(replacement, "worktree_command", changed)
    with pytest.raises(AcquisitionRefused, match="stopped predecessor"):
        await start(rig)
    assert checks == stage
    assert not rig.manager.list_sessions()
    assert not rig.db.execute_read("SELECT * FROM sessions")
    assert not rig.db.execute_read("SELECT * FROM acquisition_launch_gates")


@pytest.mark.asyncio
async def test_replacement_and_replay_keep_allocation_and_do_not_prompt(replacement_rig):
    rig = replacement_rig
    session, receipt = await start(rig)
    replay, replay_receipt = await start(rig)
    assert replay is session
    assert receipt["native_replacement"] == replay_receipt["native_replacement"]
    assert receipt["native_session_id"] == NEW
    assert receipt["worktree"]["id"] == rig.row["id"]
    assert replay_receipt["replayed"] is True
    assert len(rig.promotions) == 1
    assert len(rig.manager.list_sessions()) == 1
    assert not rig.db.execute_read("SELECT * FROM turns")
    assert not any(args[0] in {"create", "bind-session"} for args, _ in rig.calls)
    assert sum(args[1] == "check" for args, _ in rig.owner_calls) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [None, "foreign-head", "revoked-grant", "changed-native"])
async def test_completed_replacement_cold_replay_resumes_only_recorded_native(
    replacement_rig, monkeypatch, change,
):
    rig = replacement_rig
    session, first = await start(rig)
    session.status = SessionStatus.STOPPED
    session.client = None
    assert session.pid is None
    resume = rig.manager.resume_session
    calls = []

    async def strict(*args, **kwargs):
        calls.append((args, kwargs))
        result = await resume(*args, **kwargs)
        if change == "changed-native":
            result.acp_session_id = OLD
        return result

    monkeypatch.setattr(rig.manager, "resume_session", strict)
    if change == "foreign-head":
        rig.head["id"] = OLD
    elif change == "revoked-grant":
        rig.source["task"]["generation"] += 1
    if change is not None:
        with pytest.raises(AcquisitionRefused):
            await start(rig)
        assert len(calls) == (1 if change == "changed-native" else 0)
    else:
        recovered, replay = await start(rig)
        assert recovered is session and recovered.status == SessionStatus.IDLE
        assert replay["replayed"] and replay["native_session_id"] == NEW
        assert replay["native_replacement"] == first["native_replacement"]
        assert calls == [((session.session_id,), {"drain": False, "allow_recreate": False})]
    assert len(rig.promotions) == len(rig.manager.list_sessions()) == 1
    assert not rig.db.execute_read("SELECT * FROM turns")
    assert not any(args[0] in {"create", "bind-session"} for args, _ in rig.calls)


@pytest.mark.asyncio
async def test_uncertain_promotion_replays_without_second_native_start(replacement_rig, monkeypatch):
    rig = replacement_rig
    owner = replacement.worktree_command

    async def lost_response(args, project, **kwargs):
        result = await owner(args, project, **kwargs)
        if args[1] == "apply":
            raise AcquisitionRefused("owner_operation_pending", "reply lost")
        return result

    monkeypatch.setattr(replacement, "worktree_command", lost_response)
    with pytest.raises(AcquisitionRefused, match="reply lost"):
        await start(rig)
    saved = rig.db.execute_read("SELECT session_id,native_session_id FROM workspace_embodiments")[0]
    session, result = await start(rig)
    assert session.session_id == saved["session_id"]
    assert result["native_session_id"] == saved["native_session_id"] == NEW
    assert len(rig.promotions) == len(rig.manager.list_sessions()) == 1
    assert not rig.db.execute_read("SELECT * FROM turns")


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["reclaim", "rebind", "initial", "handoff"])
async def test_registered_head_is_accepted_only_for_exact_dead_head_reclaim(
    replacement_rig, monkeypatch, reason,
):
    rig = replacement_rig
    launch = rig.manager.start_session

    async def registered(*args, **kwargs):
        session = await launch(*args, **kwargs)
        rig.head["id"] = NEW
        rig.row.update(last_session_id=NEW, session_count=2)
        rig.candidate.update(
            expected_head=NEW, expected_head_revision=4, expected_lifecycle_revision=4,
            head_transitions=[
                {"session_id": OLD, "revision": 1},
                {"session_id": NEW, "revision": 4, "reason": reason},
            ],
        )
        return session

    monkeypatch.setattr(rig.manager, "start_session", registered)
    if reason == "reclaim":
        session, receipt = await start(rig)
        replay, replay_receipt = await start(rig)
        assert replay is session
        assert receipt["native_replacement"] == replay_receipt["native_replacement"]
        assert replay_receipt["replayed"] is True
    else:
        with pytest.raises(AcquisitionRefused, match="predecessor changed"):
            await start(rig)
    assert not rig.promotions
    assert len(rig.manager.list_sessions()) == 1
    assert not rig.db.execute_read("SELECT * FROM turns")


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("expected_head", OLD), ("expected_head_revision", 5),
    ("head_transitions", []),
    ("head_transitions", [{"session_id": OLD, "revision": 1}, {"session_id": NEW, "revision": 4}]),
])
async def test_changed_promotion_readback_retains_pending_identity(replacement_rig, monkeypatch, field, value):
    rig = replacement_rig
    owner = replacement.worktree_command

    async def changed(args, project, **kwargs):
        result = await owner(args, project, **kwargs)
        if args[1] == "apply":
            rig.candidate[field] = value
        return result

    monkeypatch.setattr(replacement, "worktree_command", changed)
    with pytest.raises(AcquisitionRefused, match="readback changed"):
        await start(rig)
    assert rig.db.execute_read("SELECT state FROM workspace_embodiments")[0]["state"] == "launching"
    assert len(rig.manager.list_sessions()) == 1
    assert not rig.db.execute_read("SELECT * FROM turns")


@pytest.mark.asyncio
@pytest.mark.parametrize("pid,native", [(None, NEW), (True, NEW), (0, NEW), (12345, OLD), (12345, "bad")])
async def test_candidate_requires_exact_native_and_process(replacement_rig, pid, native):
    authority = await load(replacement_rig)
    with pytest.raises(AcquisitionRefused):
        await authority.observe(SimpleNamespace(acp_session_id=native, pid=pid))


@pytest.mark.asyncio
async def test_explicit_replacement_never_accepts_first_prompt(replacement_rig):
    with pytest.raises(AcquisitionRefused, match="no prompt"):
        await start(replacement_rig, prompt="must not run")
    assert not replacement_rig.owner_calls


@pytest.mark.parametrize("supported", [True, False])
def test_readiness_never_starts_daemon(monkeypatch, capsys, supported):
    from agent_bridge import __main__ as core
    from agent_bridge.acquisition_cli import register
    import argparse

    seen = []

    def get_client(*, ensure):
        assert ensure is False
        return SimpleNamespace(daemon_supports=lambda version: seen.append(version) or supported)

    monkeypatch.setattr(core, "_get_client", get_client)
    parser = argparse.ArgumentParser()
    register(parser.add_subparsers())
    args = parser.parse_args(["native-replacement-readiness"])
    if supported:
        args.func(args)
    else:
        with pytest.raises(SystemExit) as error:
            args.func(args)
        assert error.value.code == 1
    assert seen == [52]
    assert json.loads(capsys.readouterr().out) == {
        "contract": replacement.DISPATCH_CONTRACT, "version": 1,
        "supported": supported, "required_protocol": 52,
    }
