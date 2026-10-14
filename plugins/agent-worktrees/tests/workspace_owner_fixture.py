"""Fresh synthetic owner CLI for connected allocation/embodiment tests."""

from pathlib import Path
from dataclasses import replace
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
    create = main.git_ops.create_worktree

    def paused_create(*args, **kwargs):
        (root / "creating").write_text("pending")
        deadline = time.monotonic() + 15
        while not (root / "release").exists():
            if time.monotonic() >= deadline:
                raise RuntimeError("fixture release was not received")
            time.sleep(0.05)
        return create(*args, **kwargs)

    main.git_ops.create_worktree = paused_create

if os.environ.get("FIXTURE_REFUSE_PROFILE") == "1":
    from agent_worktrees import resolve_launch_cli

    def refused_profile(_config):
        raise RuntimeError("fixture profile refused before effects")

    resolve_launch_cli._validate_profile_assignment_config = refused_profile

if os.environ.get("FIXTURE_REFUSE_CODENAME") == "1":
    config = replace(config, repos={
        "fixture": replace(
            config.default_repo,
            pr=replace(config.default_repo.pr, enabled=True, source_attribution_configured=False),
            codename=cfg.CodenameConfig(
                wordlist_path=str(root / "custom-wordlist.json"), wordlist_path_configured=True,
            ),
        ),
    })

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
