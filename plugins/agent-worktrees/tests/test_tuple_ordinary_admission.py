"""Ordinary admission selects tuples while retained receipts own every retry."""

from __future__ import annotations

import argparse
import base64
import inspect
import json

import pytest

import agent_worktrees.__main__ as m
from agent_worktrees import config as cfg, launch_request, launch_seed_state, tracking
from agent_worktrees import worktree_identity
from agent_worktrees.worktree_machine_identity import WorktreeMachineIdentity
from test_launch_request import _intent
from test_launch_request import admission_config as admission_config, authority as authority
from test_tuple_resolve_equivalence import observed_config as observed_config


def _admit(capfd: pytest.CaptureFixture, intent: dict) -> dict:
    args = argparse.Namespace(
        command="resolve", json=True,
        launch_request_b64=base64.b64encode(json.dumps(intent).encode()).decode(),
        launch_request_status=None,
    )
    code = launch_request.cmd_request(args)
    result = json.loads(capfd.readouterr().out)
    assert code == 0, result
    return result


@pytest.mark.parametrize("platform", ["windows", "wsl", "linux"])
@pytest.mark.parametrize("map_kind", [
    "absent", "empty", "unrelated", "matching", "unscoped", "sibling-only",
])
@pytest.mark.parametrize("first_mode", ["ordinary", "legacy", "tuple-v1"])
def test_actual_request_admission_and_completed_replay(
    authority, observed_config, monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture, platform: str, map_kind: str, first_mode: str,
) -> None:
    config = observed_config(platform, map_kind, "ordinary")
    intent = _intent("new")
    if first_mode == "legacy" and map_kind == "sibling-only" and platform != "wsl":
        with pytest.raises(ValueError, match="physical-host metadata"):
            launch_request.execute(config, intent)
        assert not list(cfg.tracking_dir().glob("*.yaml"))
        return
    first = (
        _admit(capfd, intent) if first_mode == "ordinary"
        else launch_request.execute(config, intent, identity_mode=first_mode)
    )
    path = cfg.tracking_dir() / f"{first['worktree_id']}.yaml"
    record = tracking.load_record(path)
    physical = "raw-host" if map_kind in ("absent", "empty", "unrelated") else "friendly-host"
    execution = "local-space" if map_kind == "matching" else physical + (
        "-wsl" if platform == "wsl" else ""
    )
    assert record.machine_identity == (
        None if first_mode == "legacy" else WorktreeMachineIdentity(physical, platform, execution)
    )
    receipt_path = launch_request._admission_path(cfg.tracking_dir(), intent["request_id"])
    paths = (path, receipt_path, launch_seed_state.state_path(path))
    before = {path: path.read_bytes() for path in paths}
    monkeypatch.setattr(
        m.git_ops, "create_worktree", lambda *_a: pytest.fail("replay must not allocate"),
    )
    assert _admit(capfd, intent) == first
    assert all(path.read_bytes() == contents for path, contents in before.items())
    assert launch_request.inspect(intent["request_id"])["ready"]


@pytest.mark.parametrize("producer", [
    m._create_worktree_core, launch_request.execute,
    worktree_identity._adopt_linked_worktree, tracking.load_or_create_anchor_record,
])
def test_all_omitted_low_level_modes_remain_legacy(producer) -> None:
    assert inspect.signature(producer).parameters["identity_mode"].default == "legacy"
