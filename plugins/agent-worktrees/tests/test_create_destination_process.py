"""Ordinary CLI -> protected origin -> finite receiving CLI interpreter boundary."""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from agent_procutil.owned_process import run_caller_owned
from machine_transport.registry import MachineEntry

from agent_worktrees import __main__ as main, allocation_link_demand, config as cfg
from agent_worktrees import create_destination, output, tracking
from agent_worktrees import owned_worktree_remote as remote
from test_receiving_allocation import receiver as receiver


_RECEIVER = """
import sys
from pathlib import Path
import pytest
from agent_worktrees import __main__ as main, config as cfg
tests, root, anchor, trees, state, project, operation = sys.argv[1:]
sys.path.insert(0, tests)
from test_worktree_creation_seed import _stub_create_worktree_core_internals
config = cfg.Config(srcroot=root, machine="test", platform=cfg.detect_platform(), repo_name=project,
 repos={project:cfg.RepoConfig(anchor=anchor, worktree_root=trees)})
patch = pytest.MonkeyPatch()
_stub_create_worktree_core_internals(patch, Path(root), config)
cfg.load_project_config = lambda *a, **k:config
cfg.project_dir = lambda *a, **k:Path(state)
cfg.tracking_dir = lambda *a, **k:Path(state)/"worktrees"
sys.exit(main.main(["--project", project, "worktree-allocation-receive", operation, "--json"]))
"""


@pytest.mark.parametrize("same_repository", [False, True])
@pytest.mark.parametrize("bridge", [False, True])
def test_ordinary_cli_returns_actual_child_from_finite_receiver_before_claim_ack(
    receiver, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, same_repository: bool,
    bridge: bool,
) -> None:
    receiving_config, _, receiving_records = receiver
    if same_repository:
        receiving_config = replace(
            receiving_config, repo_name="parent", repos={"parent": receiving_config.default_repo},
        )
    origin_state = tmp_path / "origin-state"
    origin_records = origin_state / "worktrees"
    origin_records.mkdir(parents=True)
    origin_checkout = tmp_path / "origin-checkout"
    origin_checkout.mkdir()
    origin_anchor = tmp_path / "origin-anchor"
    origin_anchor.mkdir()
    origin_config = replace(
        receiving_config, machine="origin", repo_name="parent",
        repos={"parent": replace(receiving_config.default_repo, anchor=str(origin_anchor))},
    )
    tracking.create_new_record(
        "creator", "worktree/creator", str(origin_checkout), "parent", "origin",
        cfg.detect_platform(), origin_records,
    )
    monkeypatch.chdir(origin_checkout)
    monkeypatch.setattr(cfg, "project_dir", lambda name=None: (
        origin_state if (name or cfg.project_name()) == "parent" else receiving_records.parent
    ))
    monkeypatch.setattr(cfg, "tracking_dir", lambda name=None: (
        origin_records if (name or cfg.project_name()) == "parent" else receiving_records
    ))
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: (
        origin_config if cfg.project_name() == "parent" else receiving_config
    ))
    monkeypatch.setattr(cfg, "load_project_config", lambda project, **k: (
        origin_config if project == "parent" else receiving_config
    ))
    monkeypatch.setattr(create_destination, "_cwd_project", lambda cwd: (
        "parent" if cwd == origin_checkout else pytest.fail("origin CWD was rebound")
    ))
    entry = MachineEntry(key="test", display_name="Receiver", alias="receiver-fixture", ssh_ready=True)
    monkeypatch.setattr(cfg, "load_machines_yaml", lambda *a, **k: {"test": entry})
    monkeypatch.setattr(remote, "resolve_ssh_target", lambda e: ("receiver-fixture", "bash"))
    monkeypatch.setattr(allocation_link_demand, "require_capability", lambda owner: None)
    monkeypatch.setattr(allocation_link_demand, "request", lambda owner: None)
    operations: list[str] = []
    tests = str(Path(__file__).parent)

    def transport(argv: list[str], **kwargs):
        assert argv[:5] == ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
        assert argv[-2] == "receiver-fixture"
        operation = next(
            name for name in ("capabilities", "create", "result")
            if f" {name} " in argv[-1]
        )
        operations.append(operation)
        if operation != "capabilities":
            record = tracking.load_record(origin_records / "creator.yaml")
            assert len(record.resources) == 1 and record.resources[0].is_unsettled
            assert record.resources[0].producer["phase"] == "pending"
            request = json.loads(kwargs["input_text"])
            assert request["token"] == record.resources[0].producer["token"]
            assert request["origin"] == "origin/parent/creator"
        return run_caller_owned([
            sys.executable, "-c", _RECEIVER, tests, receiving_config.srcroot,
            receiving_config.default_repo.anchor, receiving_config.default_repo.worktree_root,
            str(receiving_records.parent), receiving_config.repo_name, operation,
        ], **kwargs)

    monkeypatch.setattr(remote, "run_caller_owned", transport)
    command = [
        "--project", "parent", "create", "--machine=test",
        *([] if same_repository else [f"--repo={receiving_config.repo_name}"]),
        *(["--bridge", "--caller-worktree=creator"] if bridge else []),
        "--no-pair", "--json",
    ]
    with output.capture_json_output() as captured:
        code = main.main(command)
    assert code == 0, captured.getvalue()
    result = json.loads(captured.getvalue())
    records = list(receiving_records.glob("*.yaml"))
    assert len(records) == 1
    child = tracking.load_record(records[0])
    assert result["worktree"]["id"] == child.worktree_id
    assert result["worktree"]["path"] == child.worktree_path
    assert result["worktree"]["creation_nonce"] == child.creation_nonce
    assert result["worktree"]["owner_ref"] == "origin/parent/creator"
    assert result["worktree"]["execution_machine"] == "test"
    if bridge:
        assert result["worktree"]["kind"] == "bridge"
        assert result["worktree"]["caller_worktree"] == "creator"
    assert child.receiving_allocation["phase"] == "created" and child.sessions == []
    assert child.kind == ("bridge" if bridge else "session")
    assert child.caller_worktree == ("creator" if bridge else None)
    assert result["allocation"]["linkage"] == "pending"
    assert result["allocation"]["target"]["project"] == receiving_config.repo_name
    assert child.receiving_allocation["token"] not in captured.getvalue()
    assert operations == ["capabilities", "create", "result"]
    recovery_command = [*command, f"--creation-request-id={result['allocation']['request_id']}"]
    with output.capture_json_output() as captured:
        code = main.main(recovery_command)
    assert code == 0, captured.getvalue()
    recovered = json.loads(captured.getvalue())
    assert recovered["worktree"] == result["worktree"]
    assert recovered["allocation"]["creation_key"] is None
    assert len(list(receiving_records.glob("*.yaml"))) == 1
    assert len(tracking.load_record(origin_records / "creator.yaml").resources) == 1
