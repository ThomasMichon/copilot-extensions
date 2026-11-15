"""Worktrees-owned finite CLI fixture with a reachable, unqualified resident."""

import json
import os
from pathlib import Path
import sys

root, state, project_name, worktree_id, *argv = sys.argv[1:]
root, state = Path(root), Path(state)
sys.path.insert(0, str(root / "plugins/agent-worktrees/src"))
for library in ("lazy-cli-dispatch", "work-coalescing-singleton", "update-telemetry"):
    sys.path.insert(0, str(root / "libs" / library / "src"))
from agent_worktrees import config as cfg, tracking

project = state / "project"
records = project / "worktrees"
records.mkdir(parents=True, exist_ok=True)
(project / "config.yaml").write_text(
    "machine: local\nplatform: linux\nrepos:\n  " + project_name
    + ":\n    anchor: " + json.dumps(str(state)) + "\n",
    encoding="utf-8",
)
config = cfg.Config(
    srcroot=str(state), machine="local", platform="linux", repo_name=project_name,
    repos={project_name: cfg.RepoConfig(anchor=str(state), worktree_root=str(state / "trees"))},
)
cfg.detect_platform = lambda: "linux"
cfg.socket.gethostname = lambda: "local"
cfg.load_project_config = lambda *a, **k: config
cfg.load_config = lambda *a, **k: config
cfg.project_dir = lambda *a, **k: project
cfg.tracking_dir = lambda *a, **k: records
cfg.active_project = lambda: project_name
command = argv[2:] if argv[:1] == ["--project"] else argv
if command[:1] not in (["snapshot"], ["record"], ["head-session"]):
    from agent_worktrees import resource_relationship as relationship

    relationship.writer_identity = lambda: json.loads((state / "writer-identity.json").read_text())
with (state / "calls").open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(argv) + "\n")
path = records / f"{worktree_id}.yaml"
if not path.exists():
    checkout = state / "trees" / worktree_id
    checkout.mkdir(parents=True, exist_ok=True)
    tracking.create_new_record(
        worktree_id, f"worktree/{worktree_id}", str(checkout), project_name,
        "local", "linux", records,
    )
if argv == ["protect"]:
    from agent_worktrees.tracking_relationship_write import apply_resource_relationship

    result = apply_resource_relationship({
        "schema": relationship.SCHEMA, "version": 1, "operation": "reserve",
        "input": {"kind": "bridge", "namespace": "example", "request_id": "original-task",
                  "recovery": {}, "expected_creation_nonce": tracking.load_record(path).creation_nonce},
        "owner_ref": f"local/{project_name}/{worktree_id}", "yaml_path": str(path),
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
    if command[:1] in (["record"], ["head-session"]) or command[:2] == ["resource-relationship", "capabilities"]:
        import argparse
        from importlib import import_module

        parser = argparse.ArgumentParser(allow_abbrev=False)
        parser.add_argument("--project", choices=(project_name,))
        sub = parser.add_subparsers(dest="command", required=True)
        module, handler_name = {
            "record": ("record_cli", "cmd_record"),
            "head-session": ("session_tracking_cli", "cmd_head_session"),
            "resource-relationship": ("resource_producer_cli", "cmd_resource_relationship"),
        }[command[0]]
        provider = import_module("agent_worktrees." + module)
        if command[0] == "head-session":
            provider._all_tracking_dirs = lambda: [records]
        if command[0] == "head-session":
            provider.add_head_parser(sub)
        elif command[0] == "resource-relationship":
            provider.add_relationship_parser(sub)
        else:
            provider.add_parsers(sub)
        args = parser.parse_args(argv)
        cfg.set_active_project(args.project or project_name)
        sys.exit(getattr(provider, handler_name)(args))
    from agent_worktrees import config_authority, locks
    from agent_worktrees import status_monitor_runtime, tracking_write

    config_authority.load_project_authority_config = lambda *a, **k: config
    os.environ.pop("PYTEST_CURRENT_TEST", None)
    assert status_monitor_runtime._status_monitor_enabled()

    def resident(kind, payload):
        with (state / "resident-calls").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload) + "\n")
        return {"error": "lifecycle", "message": "unqualified resident received session write"}

    server = tracking_write.start_server(resident)
    server.start()
    try:
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
        }), encoding="utf-8")
        status_monitor_runtime._monitor_lock_path = lambda: lock
        from agent_worktrees import __main__ as main, session_binding_cli, session_tracking_cli

        session_tracking_cli._all_tracking_dirs = lambda: [records]
        session_binding_cli._spawn_status_updater = lambda *a, **k: None
        sys.exit(main.main(argv))
    finally:
        server.close()
