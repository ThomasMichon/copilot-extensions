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

MIXED_BOOTSTRAP = BOOTSTRAP.replace(
    "status_monitor_runtime._status_monitor_enabled = lambda: False",
    r"""
import atexit, os
from agent_worktrees import locks, tracking_write
os.environ.pop("PYTEST_CURRENT_TEST", None)
assert status_monitor_runtime._status_monitor_enabled()
def resident(kind, payload):
    with (state / "resident-calls").open("a") as stream:
        stream.write(json.dumps(payload) + "\n")
    return {"error": "lifecycle", "message": "unqualified resident received session write"}
server = tracking_write.start_server(resident)
server.start()
atexit.register(server.close)
rv = server.rendezvous()
lock = state / "status-monitor.lock"
lock.write_text(json.dumps({
    "pid": os.getpid(), "start_time": locks.process_start_time(os.getpid()),
    "runtime_generation": "b" * 32,
    "tracking_write_transport": rv["transport"],
    "tracking_write_endpoint": rv["endpoint"],
    "tracking_write_token": rv["token"],
    "tracking_write_verbs": ["session_register", "session_link_succession"],
    "tracking_write_verb_versions": {"session_register": 3, "session_link_succession": 2},
}))
status_monitor_runtime._monitor_lock_path = lambda: lock
""",
)


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


@pytest.mark.asyncio
async def test_retained_command_is_used_for_qualification_and_later_lineage(
    selected_owner: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    prefix = tuple(selected_owner)
    snapshot(selected_owner, "protect")
    before = snapshot(selected_owner)
    monkeypatch.setattr(acquisition, "_agent_worktrees_launch_prefix", lambda: pytest.fail("reselected owner"))
    monkeypatch.setattr(lineage, "_agent_worktrees_launch_prefix", lambda: pytest.fail("reselected owner"))
    capabilities = await acquisition.worktree_command(
        ["resource-relationship", "capabilities", "--json"], None, owner_prefix=prefix,
    )
    assert capabilities["writer_contract"]
    checkout = str(tmp_path / "authority" / "trees" / "host")
    assert lineage.register_session("host", "native-one", worktree_dir=checkout, owner_prefix=prefix)
    assert lineage.register_session("host", "native-two", worktree_dir=checkout, owner_prefix=prefix)
    assert lineage.link_succession(
        "host", "native-one", "native-two", worktree_dir=checkout, owner_prefix=prefix,
    )
    assert lineage.note_handoff("host", "native-one", worktree_dir=checkout, owner_prefix=prefix)
    assert lineage.session_role("host", "native-two", worktree_dir=checkout, owner_prefix=prefix)["is_head"]
    assert lineage.history_digest("host", worktree_dir=checkout, owner_prefix=prefix)
    after = snapshot(selected_owner)
    assert after["resources"][:len(before["resources"])] == before["resources"]
    assert after["creation_nonce"] == before["creation_nonce"]


@pytest.mark.parametrize("changed", ["creation_nonce", "identity", "worktree_path", "project"])
@pytest.mark.parametrize("command", ["register-session", "bind-session", "link-succession"])
def test_retained_session_receiver_refuses_changed_scope_without_resident_fallback(
    tmp_path, changed, command,
):
    root = Path(__file__).resolve().parents[3]
    state = tmp_path / "authority"
    identity = {"installation_root": str(state / "owner"), "runtime_generation": "a" * 32}
    bootstrap = MIXED_BOOTSTRAP.replace(
        'project = state / "project"',
        "relationship.writer_identity = lambda: " + repr(identity) + "\n"
        'project = state / "project"',
    )
    prefix = [sys.executable, "-c", bootstrap, str(root), str(state)]
    snapshot(prefix, "protect")
    before = snapshot(prefix)
    scope = {
        "version": 1, "identity": identity, "creation_nonce": before["creation_nonce"],
        "worktree_id": "host", "project": "example",
        "worktree_path": str(state / "trees" / "host"),
    }
    scope[changed] = (
        {**identity, "runtime_generation": "c" * 32} if changed == "identity" else "changed"
    )
    arguments = (
        ["--predecessor", "native-one", "--successor", "native-two"]
        if command == "link-succession" else ["--session-id", "native-one"]
    )
    result = subprocess.run(
        [*prefix, command, "--worktree-id", "host", *arguments,
         "--retained-record-writer", json.dumps(scope)],
        capture_output=True, text=True, timeout=15, **no_window_kwargs(),
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert snapshot(prefix) == before
    assert not (state / "resident-calls").exists()
