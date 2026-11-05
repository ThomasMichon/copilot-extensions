"""Delegated lineage commands preserve an existing retained relationship."""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path

import pytest
from agent_procutil import no_window_kwargs

from agent_bridge import acquisition_authority as acquisition
from agent_bridge import worktree_lineage as lineage

BOOTSTRAP = r"""
import json, sys
from pathlib import Path
root, state, *argv = sys.argv[1:]
root, state = Path(root), Path(state)
sys.path.insert(0, str(root / "plugins" / "agent-worktrees" / "src"))
for library in ("lazy-cli-dispatch", "work-coalescing-singleton", "update-telemetry"):
    sys.path.insert(0, str(root / "libs" / library / "src"))
from agent_worktrees import __main__ as main, config as cfg, config_authority, tracking
from agent_worktrees import session_binding_cli, session_tracking_cli, status_monitor_runtime
from agent_worktrees import resource_relationship as relationship
from agent_worktrees.tracking_relationship_write import apply_resource_relationship
project = state / "project"
records = project / "worktrees"
records.mkdir(parents=True, exist_ok=True)
config = cfg.Config(srcroot=str(state), machine="local", platform="linux", repo_name="example",
    repos={"example": cfg.RepoConfig(anchor=str(state), worktree_root=str(state / "trees"))})
cfg.detect_platform = lambda: "linux"
cfg.load_project_config = lambda *a, **k: config
cfg.load_config = lambda *a, **k: config
cfg.project_dir = lambda *a, **k: project
cfg.tracking_dir = lambda: records
cfg.active_project = lambda: "example"
config_authority.load_project_authority_config = lambda *a, **k: config
session_tracking_cli._all_tracking_dirs = lambda: [records]
status_monitor_runtime._status_monitor_enabled = lambda: False
session_binding_cli._spawn_status_updater = lambda *a, **k: None
path = records / "host.yaml"
if not path.exists():
    checkout = state / "trees" / "host"
    checkout.mkdir(parents=True, exist_ok=True)
    tracking.create_new_record("host", "worktree/host", str(checkout), "example",
        "local", "linux", records)
if argv == ["protect"]:
    result = apply_resource_relationship({
        "schema": relationship.SCHEMA, "version": 1, "operation": "reserve",
        "input": {"kind": "bridge", "namespace": "example", "request_id": "original-task",
            "recovery": {}, "expected_creation_nonce": tracking.load_record(path).creation_nonce},
        "owner_ref": "local/example/host", "yaml_path": str(path),
    })
    assert result.get("ok"), result
    print(json.dumps(result))
elif argv == ["snapshot"]:
    record = tracking.load_record(path)
    print(json.dumps({
        "creation_nonce": record.creation_nonce,
        "resources": [item.to_dict() for item in record.resources],
        "head": record.resolved_head_session,
        "sessions": [item.session_id for item in record.sessions],
    }))
else:
    sys.exit(main.main(argv))
"""


@pytest.fixture
def selected_owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    root = Path(__file__).resolve().parents[3]
    prefix = [sys.executable, "-c", BOOTSTRAP, str(root), str(tmp_path / "authority")]
    monkeypatch.setattr(acquisition, "_agent_worktrees_launch_prefix", lambda: prefix)
    monkeypatch.setattr(lineage, "_agent_worktrees_launch_prefix", lambda: prefix)
    return prefix


def snapshot(prefix: list[str], operation: str = "snapshot") -> dict:
    result = subprocess.run(
        [*prefix, operation], capture_output=True, text=True, timeout=15,
        **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr + result.stdout
    return json.loads(result.stdout)


@pytest.mark.asyncio
async def test_selected_owner_registration_and_succession_preserve_protected_record(
    selected_owner: list[str], tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger="agent_bridge.worktree_lineage")
    protected = snapshot(selected_owner, "protect")
    before = snapshot(selected_owner)
    capabilities = await acquisition.worktree_command(
        ["resource-relationship", "capabilities", "--json"], None,
    )
    assert capabilities["writer_contract"] == protected["writer_contract"]
    assert before["creation_nonce"] == protected["creation_nonce"]
    checkout = str(tmp_path / "authority" / "trees" / "host")
    assert lineage.register_session("host", "native-one", worktree_dir=checkout)
    assert lineage.register_session("host", "native-two", worktree_dir=checkout)
    assert lineage.link_succession("host", "native-one", "native-two", worktree_dir=checkout)
    after = snapshot(selected_owner)
    assert after["sessions"] == ["native-one", "native-two"]
    assert after["head"] == "native-two"
    assert after["creation_nonce"] == before["creation_nonce"]
    retained = [item for item in after["resources"] if item["ref"] == protected["ref"]]
    assert retained == before["resources"]
    assert {item["ref"] for item in after["resources"] if item["kind"] == "session"} == {
        "local/example/host#native-one", "local/example/host#native-two",
    }
