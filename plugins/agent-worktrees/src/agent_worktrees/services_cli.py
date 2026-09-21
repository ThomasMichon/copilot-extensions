"""Service/worktree namespace CLI dispatch extracted from ``__main__``."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from . import config as cfg
from . import git_ops, output
from . import services as svc


def _core():
    from . import __main__ as core

    return core


def add_parsers(sub) -> None:
    sub.add_parser("services", help="Service discovery and management (run 'services' for usage)")


def _resolve_environment(config: cfg.Config) -> str:
    """Build the environment key from config (e.g. ``myhost-wsl``)."""
    plat = config.platform
    if plat in ("wsl", "windows"):
        return f"{config.machine}-{plat}"
    return config.machine


def _services_usage() -> None:
    """Print services subcommand usage."""
    project = cfg.project_name()
    print(f"Usage: {project} services <command>")
    print()
    print("Discovery:")
    print("  list [--json]                      List services for this environment")
    print("  status [--json]                    Show service deployment staleness")
    print()
    print("Single service:")
    print("  <name> [action] [flags...]         Run action via service installer")
    print("                                     (default action: status)")
    print()
    print("Batch:")
    print("  --all <action> [flags...]          Run action across all services")
    print("    --force                          Include up-to-date services")
    print("    --dry-run                        Show what would run")
    print()
    print("Examples:")
    print(f"  {project} services list")
    print(f"  {project} services permanent-record status")
    print(f"  {project} services permanent-record install")
    print(f"  {project} services --all update")
    print(f"  {project} services --all install --dry-run")
    print()
    print("Legacy:")
    print("  check-stale <install_dir> <repo>   Machine-readable staleness check")


def _installer_cmd(installer: Path, args: list[str]) -> list[str] | None:
    """Build the command to run an installer with the given args."""
    if installer.suffix == ".sh":
        return ["bash", str(installer), *args]
    if installer.suffix == ".ps1":
        return ["pwsh", "-File", str(installer), *args]
    return None


def _service_is_installed(service: svc.ServiceInfo) -> bool:
    """Check if a service's install directory exists on disk."""
    if not service.install_dir:
        return False
    return Path(service.install_dir).exists()


_WORKTREE_VERBS = {
    "create": "create",
    "run": "run",
    "claims": "claims",
    "follow-ups": "follow-ups",
    "claimant-liveness": "claimant-liveness",
    "codename-lookup": "codename-lookup",
    "remove-system": "remove-system",
    "conclude-disposable": "conclude-disposable",
    "list": "list",
    "status": "status",
    "status-segment": "status-segment",
    "status-context": "status-context",
    "status-updater": "status-updater",
    "status-monitor": "status-monitor",
    "reconcile-sessions": "reconcile-sessions",
    "push": "push-changes",
    "push-changes": "push-changes",
    "create-pr": "create-pr",
    "pr-ready": "pr-ready",
    "finalize": "finalize",
    "cleanup": "cleanup",
}


def _worktree_usage() -> None:
    out = sys.stderr
    print("Usage: <project> worktree <command> [args...]", file=out)
    print(file=out)
    print("Non-launching worktree lifecycle commands:", file=out)
    print("  create [--json]        Create a worktree; print id + dir (no launch)", file=out)
    print(
        "  create --system --name N [--owner O]  "
        "Create a daemon-owned worktree (hidden from Picker)",
        file=out,
    )
    print("  remove-system <id> [--json]  Tear down a system worktree by id", file=out)
    print(
        "  conclude-disposable --worktree ID --policy disposable-cli --owner NAME  "
        "Prime an exact dead CLI worker for managed GC",
        file=out,
    )
    print("  list [--json]          List this project's worktrees", file=out)
    print("  status <id>            Show a worktree's git status", file=out)
    print("  push <id> [--title T]  Squash, rebase, and push to the default branch", file=out)
    print(
        "  create-pr [id] [--title T] [--branch B]  PR mode: squash + push a feature branch",
        file=out,
    )
    print("  pr-ready [id]          Move a PR out of draft (ready-for-review)", file=out)
    print("  finalize [id]          Validate content on upstream and clean up", file=out)
    print("  cleanup                List and remove orphaned/finalized worktrees", file=out)


def cmd_worktree_dispatch(argv: list[str]) -> int:
    """Route ``worktree`` subcommands to the canonical lifecycle handlers."""
    if not argv or argv[0] in ("-h", "--help", "help"):
        _worktree_usage()
        return 0 if argv and argv[0] in ("-h", "--help", "help") else 1

    verb = argv[0]
    canonical = _WORKTREE_VERBS.get(verb)
    if not canonical:
        output.err(f"Unknown worktree subcommand: {verb}")
        _worktree_usage()
        return 1

    parser = _core().build_parser()
    try:
        args = parser.parse_args([canonical, *argv[1:]])
    except SystemExit as exc:
        return int(exc.code or 0)
    handler = _core().COMMAND_MAP.get(args.command)
    if not handler:
        _worktree_usage()
        return 1
    return handler(args)


def cmd_services_dispatch(argv: list[str]) -> int:
    """Route services subcommands -- built-in aggregates or passthrough."""
    if not argv:
        _services_usage()
        return 1

    sub = argv[0]
    rest = argv[1:]

    if sub == "list":
        return _cmd_services_list(json_output="--json" in rest)
    if sub == "status":
        return _cmd_services_status(json_output="--json" in rest)
    if sub == "check-stale":
        if len(rest) < 2:
            output.err("Usage: services check-stale <install_dir> <repo_dir>")
            return 1
        return _cmd_services_check_stale(rest[0], rest[1])
    if sub in ("--help", "-h"):
        _services_usage()
        return 0

    if sub == "--all":
        if not rest:
            output.err("Usage: services --all <action> [flags...]")
            return 1
        return _cmd_services_batch(rest[0], rest[1:])

    return _cmd_service_passthrough(sub, rest)


def _cmd_services_list(json_output: bool = False) -> int:
    repo_dir = _core()._find_repo_dir()
    if not repo_dir:
        output.err("Cannot find repo root")
        return 1

    try:
        config = cfg.load_config()
    except Exception as e:
        output.err(f"Cannot load config: {e}")
        return 1

    env = _resolve_environment(config)
    services = svc.discover_services(
        repo_dir,
        env,
        service_paths=config.default_repo.service_paths or None,
    )

    if json_output:
        data = [
            {
                "name": s.name,
                "display_name": s.display_name,
                "type": s.service_type,
                "deployment_type": s.deployment_type,
                "install_dir": s.install_dir,
                "installer": s.installer_path,
                "source_dir": s.source_dir,
                "auto_update": s.auto_update,
            }
            for s in services
        ]
        print(json.dumps(data, indent=2))
        return 0

    output.header(f"Services ({env})")
    if not services:
        output.skipped("No services found for this environment")
        return 0

    for s in services:
        label = s.display_name or s.name
        detail = f"{s.service_type}, {s.deployment_type}"
        print(f"  {label:35s}  {output._c('dim', detail)}")

    print()
    output.info(f"{len(services)} service(s)")
    return 0


def _cmd_services_status(json_output: bool = False) -> int:
    repo_dir = _core()._find_repo_dir()
    if not repo_dir:
        output.err("Cannot find repo root")
        return 1

    try:
        config = cfg.load_config()
    except Exception as e:
        output.err(f"Cannot load config: {e}")
        return 1

    env = _resolve_environment(config)
    services = svc.discover_services(
        repo_dir,
        env,
        service_paths=config.default_repo.service_paths or None,
    )

    if json_output:
        data = []
        for s in services:
            st = svc.get_service_status(s, repo_dir)
            data.append(
                {
                    "name": st.service.name,
                    "display_name": st.service.display_name,
                    "staleness": st.staleness,
                    "deployed_commit": st.deployed_commit,
                    "deployed_at": st.deployed_at,
                    "deployed_branch": st.deployed_branch,
                    "dirty": st.dirty,
                    "install_dir": st.service.install_dir,
                    "source_paths": st.source_paths,
                }
            )
        print(json.dumps(data, indent=2))
        return 0

    output.header(f"Service Status ({env})")
    if not services:
        output.skipped("No services found for this environment")
        return 0

    for s in services:
        st = svc.get_service_status(s, repo_dir)
        label = s.display_name or s.name

        if st.staleness == "current":
            commit_short = (st.deployed_commit or "?")[:10]
            output.ok(f"{label:35s}  current @ {commit_short}")
        elif st.staleness.startswith("stale:"):
            count = st.staleness.split(":")[1]
            commit_short = (st.deployed_commit or "?")[:10]
            output.changed(f"{label:35s}  {count} commit(s) behind @ {commit_short}")
        else:
            output.skipped(f"{label:35s}  unknown (no manifest)")

        if st.dirty:
            output.warn(f"{'':35s}  deployed from dirty tree")

    print()
    return 0


def _cmd_services_check_stale(install_dir_str: str, repo_dir_str: str) -> int:
    install_dir = Path(install_dir_str)
    repo_dir = Path(repo_dir_str)
    manifest_path = install_dir / "deploy-manifest.json"
    result = svc.check_staleness(manifest_path, repo_dir)
    print(result)
    return 0


_DEPLOY_ACTIONS = {"install", "update", "copy"}


def _ensure_repo_current(repo_dir: Path, config: cfg.Config) -> None:
    git_path = repo_dir / ".git"
    if not git_path.is_dir():
        return

    remote = config.default_repo.remote or "origin"
    branch = config.default_repo.default_branch or "master"

    output.info(f"Syncing anchor repo ({remote}/{branch})…")
    try:
        git_ops.fetch(remote, cwd=repo_dir)
        result = git_ops.git(
            "merge",
            "--ff-only",
            f"{remote}/{branch}",
            cwd=repo_dir,
            check=False,
        )
        if result.returncode != 0:
            output.warn(
                "Anchor has local commits -- fast-forward failed. "
                "Deploying from current anchor HEAD."
            )
    except Exception as exc:
        output.warn(f"Could not sync anchor: {exc}")


def _is_copilot_plugin_name(name: str) -> bool:
    try:
        root = Path.home() / ".copilot" / "installed-plugins"
        if not root.is_dir():
            return False
        return any((mkt / name).is_dir() for mkt in root.iterdir() if mkt.is_dir())
    except OSError:
        return False


def _plugin_managed_notice(name: str) -> int:
    output.info(
        f"'{name}' is a Copilot plugin that manages its own deployment -- "
        "there is no in-repo installer to run here."
    )
    output.info(
        f"Use the plugin's own binstub (e.g. '{name} update' / '{name} status') "
        f"or 'copilot plugin update {name}'."
    )
    return 0


def _cmd_service_passthrough(name: str, action_args: list[str]) -> int:
    repo_dir = _core()._find_repo_dir()
    if not repo_dir:
        output.err("Cannot find repo root")
        return 1

    try:
        config = cfg.load_config()
    except Exception as e:
        output.err(f"Cannot load config: {e}")
        return 1

    action = action_args[0] if action_args else "status"
    if not action_args:
        action_args = ["status"]

    if action in _DEPLOY_ACTIONS:
        _ensure_repo_current(repo_dir, config)

    env = _resolve_environment(config)
    services = svc.discover_services(
        repo_dir,
        env,
        service_paths=config.default_repo.service_paths or None,
    )

    match = [s for s in services if s.name == name]
    if not match:
        if _is_copilot_plugin_name(name):
            return _plugin_managed_notice(name)
        output.err(f"Service {name!r} not found in {env}")
        if services:
            output.info("Available: " + ", ".join(s.name for s in services))
        return 1

    service = match[0]
    if not service.installer_path:
        if service.ownership_model == "plugin" or _is_copilot_plugin_name(name):
            return _plugin_managed_notice(name)
        output.err(f"{name} has no installer")
        return 1

    installer = repo_dir / service.installer_path
    if not installer.exists():
        output.err(f"Installer not found: {installer}")
        return 1

    cmd = _installer_cmd(installer, action_args)
    if not cmd:
        output.err(f"Unknown installer type: {installer.suffix}")
        return 1

    label = service.display_name or service.name
    output.header(f"{label} → {' '.join(action_args)}")

    result = subprocess.run(cmd, cwd=str(repo_dir))
    return result.returncode


def _cmd_services_batch(action: str, flags: list[str]) -> int:
    repo_dir = _core()._find_repo_dir()
    if not repo_dir:
        output.err("Cannot find repo root")
        return 1

    try:
        config = cfg.load_config()
    except Exception as e:
        output.err(f"Cannot load config: {e}")
        return 1

    if action in _DEPLOY_ACTIONS:
        _ensure_repo_current(repo_dir, config)

    env = _resolve_environment(config)
    services = svc.discover_services(
        repo_dir,
        env,
        service_paths=config.default_repo.service_paths or None,
    )

    force = "--force" in flags
    dry_run = "--dry-run" in flags
    pass_flags = [f for f in flags if f not in ("--dry-run",)]

    output.header(f"Services {action} ({env})")

    if not services:
        output.skipped("No services found for this environment")
        return 0

    errors = 0
    skipped = 0
    completed = 0

    for s in services:
        label = s.display_name or s.name
        st = svc.get_service_status(s, repo_dir)
        is_installed = _service_is_installed(s)

        if not force:
            if action in ("install", "update") and not s.auto_update:
                output.skipped(f"{label} -- managed elsewhere (auto_update: false)")
                skipped += 1
                continue
            if action == "install" and is_installed:
                skipped += 1
                continue
            if action == "update":
                if st.staleness == "current":
                    skipped += 1
                    continue
                if not is_installed:
                    output.warn(f"{label} -- not installed, skipping update")
                    skipped += 1
                    continue

        if not s.installer_path:
            output.skipped(f"{label} -- no installer")
            skipped += 1
            continue

        installer = repo_dir / s.installer_path
        if not installer.exists():
            output.err(f"{label} -- installer missing at {installer}")
            errors += 1
            continue

        cmd_args = [action, *pass_flags]
        cmd = _installer_cmd(installer, cmd_args)
        if not cmd:
            output.err(f"{label} -- unknown installer type: {installer.suffix}")
            errors += 1
            continue

        if dry_run:
            output.dry_run(f"{label} → {installer.name} {' '.join(cmd_args)}")
            continue

        print()
        output.changed(f"{label} → {action}")

        result = subprocess.run(cmd, cwd=str(repo_dir))
        if result.returncode == 0:
            output.ok(f"{label} done")
            completed += 1
        else:
            output.err(f"{label} failed (rc={result.returncode})")
            errors += 1

    print()
    if completed:
        output.ok(f"{completed} service(s) completed")
    if skipped:
        output.info(f"{skipped} service(s) skipped")
    if errors:
        output.err(f"{errors} service(s) failed")
    return 1 if errors else 0
