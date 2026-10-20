"""Strict command receipts and pending outcomes, without a second authority."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, Mock
from types import SimpleNamespace

import pytest

from agent_bridge import relationship_authority as authority
from agent_bridge._peer_launch import ContextRefused


@pytest.fixture
def process(monkeypatch):
    proc = SimpleNamespace(
        returncode=0,
        communicate=AsyncMock(return_value=(
            json.dumps({"schema": authority.SCHEMA, "version": 1, "ok": True}).encode(), b"",
        )),
        kill=Mock(),
        wait=AsyncMock(return_value=0),
        stdin=Mock(),
    )
    job = Mock()
    job.terminate_and_wait.side_effect = lambda timeout: (
        setattr(proc, "returncode", 1) if proc.returncode is None else None
    )
    proc._job = job
    start = AsyncMock(return_value=(proc, job))
    monkeypatch.setattr(authority, "_agent_worktrees_launch_prefix", lambda **kwargs: ["qualified-owner", "-I"])
    monkeypatch.setattr(authority, "spawn_in_kill_on_close_job", start)
    return proc, start


@pytest.mark.asyncio
async def test_command_uses_exact_peer_prefix_stdin_owner_and_scrubbed_environment(process, monkeypatch):
    proc, start = process
    for key in ("AGENT_HOME", "AGENT_RT_ROOT", "VIRTUAL_ENV", "PYTHONPATH", "COPILOT_PLUGIN_ROOT"):
        monkeypatch.setenv(key, "unrelated-owner")
    payload = {"request_id": "same-request"}
    assert (await authority.command("inspect", "local/project/worktree#session", payload))["ok"]
    assert start.call_args.args == (
        "qualified-owner", "-I", "resource-relationship", "inspect",
        "--json", "--owner-ref", "local/project/worktree",
    )
    assert all(key not in start.call_args.kwargs["env"] for key in (
        "AGENT_HOME", "AGENT_RT_ROOT", "VIRTUAL_ENV", "PYTHONPATH", "COPILOT_PLUGIN_ROOT",
    ))
    assert json.loads(proc.communicate.call_args.args[0]) == payload


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [
    b"[]", b"{} {}", b'{"schema":"one","schema":"two"}', b'{"value":NaN}',
    b"\xff", b"x" * 65537,
], ids=["array", "multiple", "duplicate-key", "nonfinite", "invalid-utf8", "oversized"])
async def test_invalid_command_output_remains_an_unknown_transaction(process, raw):
    proc, _ = process
    proc.communicate.return_value = raw, b""
    with pytest.raises(authority.RelationshipUnknown):
        await authority.command("reserve", "local/project/worktree", {})


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [True, 2, "1"])
async def test_protocol_mismatch_never_becomes_permission(process, version):
    proc, _ = process
    proc.communicate.return_value = json.dumps({
        "schema": authority.SCHEMA, "version": version,
    }).encode(), b""
    with pytest.raises(authority.RelationshipRefused):
        await authority.command("capabilities")


@pytest.mark.asyncio
async def test_nonzero_is_refusal_even_with_success_shaped_output(process):
    proc, _ = process
    proc.returncode = 1
    with pytest.raises(authority.RelationshipRefused):
        await authority.command("reserve", "local/project/worktree", {})


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [TimeoutError(), asyncio.CancelledError()])
async def test_timeout_and_cancellation_clean_up_command_without_retrying(process, failure):
    proc, start = process
    proc.returncode = None
    proc.communicate.side_effect = [failure, (b"", b"")]
    expected = asyncio.CancelledError if isinstance(failure, asyncio.CancelledError) else authority.RelationshipUnknown
    with pytest.raises(expected):
        await authority.command("reserve", "local/project/worktree", {})
    proc._job.terminate_and_wait.assert_called_once()
    assert proc.communicate.await_count == 2 and start.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", [None, ContextRefused("wrong installation")])
async def test_unavailable_owning_installation_has_no_ambient_fallback(process, monkeypatch, prefix):
    _, start = process
    resolve = Mock(side_effect=prefix) if isinstance(prefix, Exception) else Mock(return_value=prefix)
    monkeypatch.setattr(authority, "_agent_worktrees_launch_prefix", resolve)
    with pytest.raises(authority.RelationshipRefused):
        await authority.command("capabilities")
    resolve.assert_called_once_with(allow_path_fallback=False)
    start.assert_not_called()


@pytest.mark.asyncio
async def test_missing_legacy_owner_refuses_foreign_compatible_path_command(process, monkeypatch, tmp_path):
    from agent_bridge import session_lifecycle_cli

    _, start = process
    monkeypatch.setattr(session_lifecycle_cli, "_agent_bridge_installation_context", lambda: "")
    monkeypatch.setattr(session_lifecycle_cli, "_agent_bridge_owner_root", lambda: tmp_path / "bridge")
    monkeypatch.setattr(session_lifecycle_cli, "_legacy_worktrees_launch_prefix", lambda root: None)
    foreign = Mock(return_value=str(tmp_path / "foreign" / "agent-worktrees"))
    monkeypatch.setattr(session_lifecycle_cli.shutil, "which", foreign)
    monkeypatch.setattr(authority, "_agent_worktrees_launch_prefix", session_lifecycle_cli._agent_worktrees_launch_prefix)
    with pytest.raises(authority.RelationshipRefused):
        await authority.command("capabilities")
    foreign.assert_not_called()
    start.assert_not_called()
    assert session_lifecycle_cli._agent_worktrees_launch_prefix() == [foreign.return_value]


@pytest.mark.parametrize("changed", [
    {"owner": "local/project/other"}, {"request_id": "other"},
    {"absent": 1}, {"absent": False}, {"ok": 1}, {"version": True},
    {"unexpected": "extra"},
])
def test_absence_confirmation_requires_exact_typed_authority(changed):
    result = {
        "schema": authority.SCHEMA, "version": 1, "ok": True,
        "owner": "local/project/worktree", "request_id": "original", "absent": True,
    }
    with pytest.raises(authority.RelationshipUnknown):
        authority.confirm_absence(
            {**result, **changed}, owner="local/project/worktree", request_id="original",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("support", [None, False, 1])
async def test_new_acquisition_requires_explicit_absence_inspection_capability(monkeypatch, support):
    monkeypatch.setattr(authority, "command", AsyncMock(return_value={
        "lifetime": "retained-relationship", "metadata_version": 2,
        "operations": ["reserve", "inspect", "bind", "close"],
        "ref_encoding": "namespace:l1.base64url", "inspect_absence": support,
    }))
    with pytest.raises(authority.RelationshipRefused):
        await authority.require_capability()
