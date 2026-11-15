"""Controller observation does not inherit the execution target's write scope."""

from contextlib import contextmanager
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent_bridge import acquisition_authority as authority
from agent_bridge import acquisition_worktree_writer as writers
from agent_bridge.acquisition_contract import AcquisitionRefused, WorktreeWriterRequirement
from agent_bridge.creation_worktree_writer import CreationWriterScope
from agent_bridge.venue_plugins import resolve_controller_cwd

pytestmark = pytest.mark.contract("agent_bridge.venue_plugins")


@pytest.fixture
def controller_writer(monkeypatch, tmp_path):
    controller = tmp_path / "controller"
    controller.mkdir()
    target = tmp_path / "target"
    target.mkdir()
    requirement = WorktreeWriterRequirement.model_validate({
        "identity": {
            "installation_root": str(tmp_path / "original-owner"),
            "runtime_generation": "a" * 32,
        },
        "creation_nonce": "original-target-nonce",
    })
    writer = writers.RetainedWriter(
        CreationWriterScope("b" * 64, "execution", "target", str(target), requirement),
        ("original-owner", "original-build"),
    )
    row = {
        "id": "controller", "repo": "control", "machine": "example-host",
        "path": str(controller), "status": "active",
    }
    response = {"worktrees": [row]}

    async def communicate():
        return json.dumps(response).encode(), b""

    process = SimpleNamespace(returncode=0, communicate=communicate)
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(authority.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(authority, "_agent_worktrees_environment", lambda: {})
    monkeypatch.setattr(
        "agent_bridge.session_lifecycle_cli._agent_worktrees_selection",
        lambda **kw: pytest.fail("controller read selected a replacement owner"),
    )
    caller = SimpleNamespace(
        caller_worktree=str(controller),
        caller_owner_ref="example-host/control/controller",
    )

    @contextmanager
    def retained():
        token = writers._current.set(writer)
        try:
            yield
        finally:
            writers._current.reset(token)

    return SimpleNamespace(
        caller=caller, writer=writer, retained=retained, response=response,
        row=row, spawn=spawn,
    )


@pytest.mark.asyncio
async def test_controller_read_uses_original_owner_without_releasing_target_scope(controller_writer):
    fixture = controller_writer
    with fixture.retained():
        assert await resolve_controller_cwd(fixture.caller) == fixture.caller.caller_worktree
        assert writers.current() is fixture.writer
        for operation in ("register-session", "bind-session", "link-succession"):
            with pytest.raises(AcquisitionRefused, match="changed workspace"):
                await authority.worktree_command(
                    [operation, "--worktree-id", "controller"], "control",
                )
        with pytest.raises(AcquisitionRefused, match="changed workspace"):
            await authority.worktree_command(
                ["bind-session", "--worktree-id", "controller"], "execution",
            )
        writers.require_scope(["bind-session", "--worktree-id", "target"], project="execution")
    assert writers.current() is None
    assert fixture.spawn.await_count == 1
    assert fixture.spawn.await_args.args == (
        "original-owner", "original-build", "--project", "control",
        "list", "--json", "--cache-only", "--worktree-id", "controller",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["missing", "duplicate", "project", "machine", "terminal", "path"])
async def test_retained_controller_read_keeps_identity_refusals(controller_writer, fault):
    fixture = controller_writer
    if fault == "missing":
        fixture.response["worktrees"] = []
    elif fault == "duplicate":
        fixture.response["worktrees"].append(dict(fixture.row))
    else:
        field, value = {
            "project": ("repo", "other"),
            "machine": ("machine", "other-host"),
            "terminal": ("status", "finalized"),
            "path": ("path", fixture.caller.caller_worktree + "/missing"),
        }[fault]
        fixture.row[field] = value
    with fixture.retained():
        with pytest.raises(RuntimeError, match="controller context could not be verified"):
            await resolve_controller_cwd(fixture.caller)
        assert writers.current() is fixture.writer
    assert fixture.spawn.await_count == 1


@pytest.mark.asyncio
async def test_unavailable_original_controller_owner_refuses_without_reselection(controller_writer):
    fixture = controller_writer
    fixture.spawn.side_effect = FileNotFoundError("original owner unavailable")
    with fixture.retained():
        with pytest.raises(RuntimeError, match="controller context could not be verified"):
            await resolve_controller_cwd(fixture.caller)
        assert writers.current() is fixture.writer
    assert fixture.spawn.await_count == 1
