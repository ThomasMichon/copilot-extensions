"""Exact owner facts, not roster publication, gate keyed workspace creation."""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_bridge import acquisition_authority, workspace_creation
from agent_bridge.acquisition_contract import AcquisitionRefused
from test_workspace_creation import creation_rig as _creation_rig, rig as _connected_rig

creation_rig = _creation_rig
rig = _connected_rig

_VECTORS = json.loads((
    Path(__file__).resolve().parents[1]
    / "contract/fixtures/http/current/workspace-exact-owner-generation-41.json"
).read_text(encoding="utf-8"))


@pytest.fixture
def exact_owner(creation_rig, monkeypatch):
    rig = creation_rig
    original = workspace_creation.worktree_command
    rig.roster = "unavailable"
    rig.record_changes = {}

    async def command(args, project):
        if args[0] == "record":
            rig.calls.append((args, project))
            row = {
                **copy.deepcopy(rig.row),
                "last_session_id": rig.head["id"],
                "execution_leg": None,
                "execution_leg_opaque": False,
                "session_backend": None,
                "session_backend_opaque": False,
                "sessions_indexed": True,
            }
            row.update(rig.record_changes)
            return {"version": 1, "ok": True, "worktree": row}
        if args[0] == "list":
            rig.calls.append((args, project))
            if rig.roster == "unavailable":
                raise AcquisitionRefused("owner_refused", "resident roster is unready")
            return {
                "worktrees": [] if rig.roster == "empty" else [copy.deepcopy(rig.row)],
                "projection": {"quality": rig.roster},
            }
        return await original(args, project)

    monkeypatch.setattr(workspace_creation, "worktree_command", command)
    return rig


@pytest.mark.asyncio
@pytest.mark.parametrize("roster", ["unavailable", "empty", "advisory", "stale"])
async def test_keyed_creation_uses_current_exact_owner_without_roster(exact_owner, roster):
    rig = exact_owner
    rig.roster = roster
    session, first = await workspace_creation.start(rig.manager, rig.target, key="one-allocation")
    replay, second = await workspace_creation.start(rig.manager, rig.target, key="one-allocation")
    assert replay is session
    assert first["worktree"] == second["worktree"]
    assert first["native_session_id"] == second["native_session_id"]
    assert sum(args[0] == "create" for args, _ in rig.calls) == 1
    assert not any(args[0] == "list" for args, _ in rig.calls)
    assert len(rig.manager.list_sessions()) == 1
    assert not rig.db.execute_read("SELECT * FROM turns")
    await asyncio.sleep(0)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["path", "creation_nonce"])
async def test_exact_owner_identity_mismatch_never_launches(exact_owner, field):
    rig = exact_owner
    rig.roster = "current"
    rig.record_changes[field] = (
        str(Path(rig.row["path"]).parent / "different-owner")
        if field == "path" else "different-owner-identity"
    )
    with pytest.raises(AcquisitionRefused) as refused:
        await workspace_creation.start(rig.manager, rig.target, key="one-allocation")
    assert refused.value.code == "creation_conflict"
    assert not rig.manager.list_sessions()
    assert not rig.db.execute_read("SELECT * FROM turns")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"status": "finalizing"},
        {"status": "finalized"},
        {"execution_leg_opaque": True},
        {"session_backend_opaque": True},
        {"sessions_indexed": False},
        {"session_count": None},
        {"last_session_id": "foreign-history"},
        {"last_session_id": ""},
        {"execution_leg": {}},
        {"session_backend": {}},
    ],
)
async def test_exact_owner_uncertainty_is_not_sessionless(exact_owner, changes):
    rig = exact_owner
    rig.roster = "current"
    rig.record_changes.update(changes)
    with pytest.raises(AcquisitionRefused):
        await workspace_creation.start(rig.manager, rig.target, key="one-allocation")
    assert not rig.manager.list_sessions()
    assert not rig.db.execute_read("SELECT * FROM turns")


@pytest.mark.asyncio
@pytest.mark.parametrize("nonce", _VECTORS["invalid_keyed_nonces"])
@pytest.mark.parametrize("stage", ["fresh", "retained-allocated", "retained-terminal"])
async def test_keyed_creation_refuses_nonpositive_allocation_nonce(
    exact_owner, monkeypatch, nonce, stage,
):
    rig = exact_owner
    command = workspace_creation.worktree_command
    if stage != "fresh":
        async def unavailable_record(args, project):
            if args[0] == "record":
                raise AcquisitionRefused("owner_refused", "retained allocation awaits owner")
            return await command(args, project)

        monkeypatch.setattr(workspace_creation, "worktree_command", unavailable_record)
        with pytest.raises(AcquisitionRefused, match="awaits owner"):
            await workspace_creation.start(rig.manager, rig.target, key="one-allocation")
        operation = dict(rig.db.execute_read("SELECT * FROM workspace_embodiments")[0])
        saved = json.loads(operation["worktree_json"])
        saved["creation_nonce"] = nonce
        rig.db.execute_write(
            "UPDATE workspace_embodiments SET worktree_json=?,state=?",
            (json.dumps(saved), stage.removeprefix("retained-")),
        )
        monkeypatch.setattr(workspace_creation, "worktree_command", command)
    rig.row["creation_nonce"] = nonce
    before = len(rig.calls)
    for _ in range(1 if stage == "fresh" else 2):
        with pytest.raises(AcquisitionRefused) as refused:
            await workspace_creation.start(rig.manager, rig.target, key="one-allocation")
        assert refused.value.code == "creation_unproven"
    assert not rig.manager.list_sessions()
    assert not rig.db.execute_read("SELECT * FROM turns")
    assert not rig.db.execute_read("SELECT * FROM acquisition_launch_gates")
    assert sum(args[0] == "create" for args, _ in rig.calls) == 1
    if stage != "fresh":
        assert len(rig.calls) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["allocation-response", "exact-record"])
async def test_lost_owner_response_reuses_one_allocation_and_first_turn(
    exact_owner, monkeypatch, boundary,
):
    rig = exact_owner
    original = workspace_creation.worktree_command
    allocated = {}
    create_keys = []
    lost = False

    async def command(args, project):
        nonlocal lost
        if args[0] == "create":
            key = args[args.index("--creation-key") + 1]
            create_keys.append(key)
            if key not in allocated:
                allocated[key] = await original(args, project)
            result = allocated[key]
            if boundary == "allocation-response" and not lost:
                lost = True
                raise AcquisitionRefused("owner_operation_pending", "original response lost")
            return result
        if args[0] == "record" and boundary == "exact-record" and not lost:
            lost = True
            raise AcquisitionRefused("owner_refused", "owner temporarily unavailable")
        return await original(args, project)

    monkeypatch.setattr(workspace_creation, "worktree_command", command)
    with pytest.raises(AcquisitionRefused):
        await workspace_creation.start(
            rig.manager, rig.target, key="one-allocation", prompt="First turn",
        )
    before = dict(rig.db.execute_read("SELECT * FROM workspace_embodiments")[0])
    first, first_receipt = await workspace_creation.start(
        rig.manager, rig.target, key="one-allocation", prompt="First turn",
    )
    replay, second_receipt = await workspace_creation.start(
        rig.manager, rig.target, key="one-allocation", prompt="First turn",
    )
    assert first is replay and first.session_id == before["session_id"]
    assert first_receipt["native_session_id"] == second_receipt["native_session_id"]
    assert set(create_keys) == {"one-allocation"} and len(allocated) == 1
    assert len(rig.manager.list_sessions()) == 1
    assert len(rig.db.execute_read("SELECT * FROM turns")) == 1
    await asyncio.sleep(0)


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", [
    {"version": True},
    {"version": 2},
    {"ok": False},
    {"worktree": None},
])
async def test_exact_reader_refuses_invalid_cli_envelopes(mutation):
    async def command(args, project):
        assert args == ["record", "--worktree-id", "worktree-a", "--json"]
        assert project == "project-a"
        return {"version": 1, "ok": True, "worktree": {}, **mutation}

    with pytest.raises(AcquisitionRefused):
        await acquisition_authority.read_worktree_record("worktree-a", "project-a", command=command)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", _VECTORS["cases"], ids=lambda case: case["name"])
async def test_committed_exact_owner_contract_vectors(monkeypatch, case):
    row = {**_VECTORS["record"], **case["record_changes"]}
    head = {**_VECTORS["head"], **case["head_changes"]}
    calls = []

    async def command(args, project):
        assert project == "project-a"
        calls.append(args[0])
        if args[0] == "record":
            return {"version": 1, "ok": True, "worktree": row}
        assert args[0] == "head-session", "roster is not known-workspace authority"
        return head

    monkeypatch.setattr(workspace_creation, "worktree_command", command)
    if case["expected_error"]:
        with pytest.raises(AcquisitionRefused) as refused:
            await workspace_creation._workspace("project-a", "worktree-a", expected=_VECTORS["record"])
        assert refused.value.code == case["expected_error"]
    else:
        actual, native = await workspace_creation._workspace(
            "project-a", "worktree-a", expected=_VECTORS["record"],
        )
        manager = SimpleNamespace(
            db=SimpleNamespace(list_fresh_live_sessions=lambda *a, **k: [],
                               get_worktree_ownership=lambda *a: None),
            list_sessions=lambda: [],
        )
        assert workspace_creation._sessionless(manager, actual, native, "worktree-a") is case["expected_sessionless"]
    assert "list" not in calls
