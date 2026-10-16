"""Fresh synthetic owner CLI for connected allocation/embodiment tests.

Usage: python workspace_owner_fixture.py ROOT [--project fixture] COMMAND...
All user state belongs to ROOT/home or ROOT/state. Existing fixture-provided
Copilot settings are preserved; creation uses the normal permission/trust helper.
FIXTURE_COPILOT_PATH and FIXTURE_COPILOT_ARGS (a JSON string array) optionally
provide the platform's ordinary launch template. Only explicit launch commands
may execute that template; metadata commands never launch a backend.
The caller's synthetic anchor must carry committed owner configuration for
session-registration admission, just like an ordinary managed repository.
"""

from pathlib import Path
from dataclasses import replace
import json
import os
import shutil
import sys
import time

root = Path(sys.argv[1]).resolve()
home = root / "home"
copilot_home = home / ".copilot"
copilot_home.mkdir(parents=True, exist_ok=True)
# Establish isolation before importing modules that cache user-scoped paths.
isolated_environment = {key: str(path) for key, path in {
    "HOME": home,
    "USERPROFILE": home,
    "AGENT_HOME": home,
    "COPILOT_HOME": copilot_home,
    "XDG_CONFIG_HOME": home / ".config",
    "XDG_DATA_HOME": home / ".local" / "share",
    "XDG_STATE_HOME": home / ".local" / "state",
    "APPDATA": home / "AppData" / "Roaming",
    "LOCALAPPDATA": home / "AppData" / "Local",
}.items()}
os.environ.update(isolated_environment)
# An inherited installation cell or Git config must not select production state.
for key in tuple(os.environ):
    if key.startswith("GIT_CONFIG_") or key in {
        "COPILOT_EXTENSIONS_CONTEXT", "COPILOT_PLUGIN_ROOT",
        "AGENT_WORKTREES_PAYLOAD_ROOT",
    }:
        os.environ.pop(key, None)
os.environ["GIT_CONFIG_GLOBAL"] = str(home / ".gitconfig")
os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
os.environ["AGENT_WORKTREES_IDENTITY_KEY"] = str(home / "identity.key")
copilot_config = copilot_home / "config.json"
if not copilot_config.exists():
    copilot_config.write_text("{}\n", encoding="utf-8")

repository = Path(__file__).resolve().parents[3]
sys.path[:0] = [
    str(Path(__file__).resolve().parents[1] / "src"),
    *(str(path) for path in (repository / "libs").glob("*/src")),
]

from agent_worktrees import (
    cleanup_gc_cli, config as cfg, context_cli, handoff_cli, launch_wrapper_assets,
    list_cli, record_cli, resolve_cli,
    session_binding_cli, session_tracking_cli, worktree_ops_cli,
)
import agent_worktrees.__main__ as main

cfg.set_active_project("fixture")
platform = cfg.detect_platform()
launch = {}
if "FIXTURE_COPILOT_PATH" in os.environ or "FIXTURE_COPILOT_ARGS" in os.environ:
    try:
        copilot_args = json.loads(os.environ.get("FIXTURE_COPILOT_ARGS", "[]"))
    except json.JSONDecodeError:
        raise SystemExit("FIXTURE_COPILOT_ARGS must be a JSON string array")
    if not isinstance(copilot_args, list) or any(not isinstance(arg, str) for arg in copilot_args):
        raise SystemExit("FIXTURE_COPILOT_ARGS must be a JSON string array")
    copilot_path = os.environ.get("FIXTURE_COPILOT_PATH", "copilot")
    if not copilot_path:
        raise SystemExit("FIXTURE_COPILOT_PATH must not be empty")
    launch["linux" if platform == "wsl" else platform] = [copilot_path, *copilot_args]
config = cfg.Config(
    srcroot=str(root), machine="fixture", platform=platform,
    repo_name="fixture", auto_fast_forward=False,
    repos={"fixture": cfg.RepoConfig(
        anchor=str(root / "anchor"), worktree_root=str(root / "worktrees"), default_branch="main",
        launch=launch,
        session_env={
            **isolated_environment,
            **{key: os.environ[key] for key in ("PATH", "AGENT_BRIDGE_CONFIG_DIR")
               if key in os.environ},
        },
    )},
)
cfg.load_config = lambda *args, **kwargs: config
cfg.project_dir = lambda name=None: root / "state"
cfg.tracking_dir = lambda name=None: root / "state" / "worktrees"
scripts = home / ".agent-worktrees" / "scripts"
scripts.mkdir(parents=True, exist_ok=True)
for name in ("launch-command.ps1", "launch-command.sh"):
    shutil.copyfile(repository / "plugins" / "agent-worktrees" / "scripts" / name, scripts / name)
assets = launch_wrapper_assets.load_manifest(repository / "plugins" / "agent-worktrees")
asset_source = launch_wrapper_assets.resolve_source_dir(repository, assets)
launchers = home / ".agent-worktrees" / "bin"
launchers.mkdir(parents=True, exist_ok=True)
for name in assets.files:
    shutil.copyfile(asset_source / name, launchers / name)
# record's filesystem-only membership reader intentionally does not load Config.
membership = root / "state" / "config.yaml"
membership.parent.mkdir(parents=True, exist_ok=True)
if not membership.exists():
    membership.write_text(json.dumps({
        "machine": "fixture", "platform": platform,
        "repos": {"fixture": {
            "anchor": str(root / "anchor"), "worktree_root": str(root / "worktrees"),
            "default_branch": "main",
        }},
    }) + "\n", encoding="utf-8")
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
    "record": record_cli.cmd_record,
    "list-sessions": session_tracking_cli.cmd_list_sessions,
    "resolve": resolve_cli.cmd_resolve,
    "embody": handoff_cli.cmd_embody,
    "get": context_cli.cmd_get,
    "cleanup": cleanup_gc_cli.cmd_cleanup,
}
raise SystemExit(handlers[args.command](args))
