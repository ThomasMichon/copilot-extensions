"""Fresh synthetic owner CLI for connected allocation/embodiment tests."""

from pathlib import Path
import os
import sys
import time

repository = Path(__file__).resolve().parents[3]
sys.path[:0] = [
    str(Path(__file__).resolve().parents[1] / "src"),
    *(str(path) for path in (repository / "libs").glob("*/src")),
]

from agent_worktrees import config as cfg, list_cli, session_binding_cli, session_tracking_cli, worktree_ops_cli
import agent_worktrees.__main__ as main

root = Path(sys.argv[1])
cfg.set_active_project("fixture")
config = cfg.Config(
    srcroot=str(root), machine="fixture", platform=cfg.detect_platform(),
    repo_name="fixture", auto_fast_forward=False,
    repos={"fixture": cfg.RepoConfig(
        anchor=str(root / "anchor"), worktree_root=str(root / "worktrees"), default_branch="main",
    )},
)
cfg.load_config = lambda *args, **kwargs: config
cfg.project_dir = lambda name=None: root / "state"
cfg.tracking_dir = lambda name=None: root / "state" / "worktrees"
# A synthetic owner has no resident service. Keep native binding writes real.
session_binding_cli._status_monitor_enabled = lambda: False
session_binding_cli._register_manager_owned_monitor_session = lambda *args, **kwargs: True
session_binding_cli._spawn_status_updater = lambda *args, **kwargs: None

if os.environ.get("FIXTURE_PAUSE_CREATION") == "1":
    prepare = main._prepare_worktree_source

    def paused_prepare(*args, **kwargs):
        (root / "creating").write_text("pending")
        deadline = time.monotonic() + 15
        while not (root / "release").exists():
            if time.monotonic() >= deadline:
                raise RuntimeError("fixture release was not received")
            time.sleep(0.05)
        return prepare(*args, **kwargs)

    main._prepare_worktree_source = paused_prepare

argv, project = main._extract_project_flag(sys.argv[2:])
if project not in (None, "fixture"):
    raise SystemExit("fixture owner refuses a foreign project")
args = main.build_parser().parse_args(argv)
handlers = {
    "create": worktree_ops_cli.cmd_create,
    "list": list_cli.cmd_list,
    "head-session": session_tracking_cli.cmd_head_session,
    "bind-session": session_binding_cli.cmd_bind_session,
}
raise SystemExit(handlers[args.command](args))
