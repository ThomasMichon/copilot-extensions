"""Context/introspection CLI surfaces extracted from ``__main__``.

This module holds read-only or configuration-resolution command handlers and
their parser registration so ``__main__.py`` can stay a thin composition root.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from . import config as cfg
from . import git_ops, output, sessions, state_root as state_root_mod, tracking
from . import installer as inst


def _core():
    from . import __main__ as core

    return core


_GET_KEYS: dict[str, str] = {
    "repo-dir": "Anchor repo directory",
    "worktree-dir": "Current worktree root (the worktree you are in; empty if not inside one)",
    "worktree-id": "Current worktree id (empty if not inside one)",
    "worktree-state-dir": "Per-worktree or adopted-anchor state directory outside the repo checkout",
    "worktrees-root": "Parent directory that holds all worktrees (formerly 'worktree-dir')",
    "src-dir": "Source root (parent of repos)",
    "config-dir": "Per-project config directory (~/.{project})",
    "machine": "Machine name from config",
    "platform": "Platform (win/wsl/linux)",
    "project": "Project name",
    "owner-ref": "This worktree's qualified claim ref "
    "(machine/project/worktree_id[#session]) -- the cross-machine "
    "holder identity for resource leases; empty if not in a worktree",
    "repo-remote": "Canonical remote URL of this repo (registry remote; falls "
    "back to git origin) -- the device-independent repo key",
    "lease-origin": "Resolved explicit/private Git-ref lease store origin URL "
    "-- the **harness identity** shared by every agent of this "
    "harness (the in-CodeSpace cross-harness fence key); empty "
    "if no store is configured",
    "pr-enabled": "Whether PR mode is enabled (true/false)",
    "pr-required": "Whether PRs are required, blocking direct-to-master (true/false)",
    "pr-provider": "PR provider (gitea|github|azure-devops) when PR mode is on",
    "pr-profile": "PR-flow profile: direct|pr-human-merge|pr-agent-merge (check first)",
}


def add_parsers(sub) -> None:
    sub.add_parser(
        "install-status",
        help="Show installation status",
    )
    sub.add_parser(
        "installer-readiness",
        help="Emit the plugin-owned installer/readiness contract state as JSON",
    )

    p = sub.add_parser(
        "deploy-instructions",
        help="Retire migrated managed instruction files (machine identity now via the session-machine hook)",
    )
    p.add_argument(
        "--machine", default=None, help="Machine name (auto-detected from config if omitted)"
    )

    sub.add_parser(
        "machine-context",
        help="Emit machine identity as sessionStart additionalContext (hook entrypoint; cwd-gated)",
    )

    p = sub.add_parser("get", help="Query project paths and config values")
    p.add_argument("key", help="Key to query (use 'keys' to list available keys)")
    p.add_argument(
        "--session-id",
        dest="session_id",
        default=None,
        help="Resolve worktree-scoped keys (worktree-dir) from this "
        "session when cwd is HOME (bare resume) -- binding-first, "
        "not cwd inference",
    )

    sp = sub.add_parser(
        "state-root",
        help="Resolve where efforts/visions/logs are written (stateless-harness "
        "aware; --json / --repo NAME)",
    )
    sp.add_argument("--json", action="store_true", help="Emit the full resolution as JSON")
    sp.add_argument(
        "--repo",
        default=None,
        metavar="NAME",
        help="Explicit override: resolve this registered repo",
    )

    sub.add_parser(
        "coordination-readiness",
        help="Emit versioned claim-coordination readiness as JSON",
    )

    sp = sub.add_parser(
        "config-root",
        help="Resolve the guarded machine-local config root (run 'config-root --help')",
    )
    sp.add_argument(
        "--destination",
        default=None,
        metavar="PATH",
        help="Validate an explicit root instead of resolving the default root",
    )
    sp.add_argument(
        "--json",
        action="store_true",
        help="Emit config_root/source/repo/stateless/bound/error as JSON",
    )

    sub.add_parser(
        "knowledge",
        help="Harness/knowledge pairing utilities (run 'knowledge --help')",
    )


def _resolve_lease_origin() -> str:
    """Resolve the Git-ref lease store origin URL -- the **harness identity**."""
    try:
        from . import lease_config

        return lease_config.load_lease_settings().origin
    except Exception:
        return ""


def _pr_reminder_for(
    config,
    verb: str,
    *,
    ok: bool = True,
    state: str = "",
    reason: str = "",
):
    """Build this repo's stay-on-rails PR reminder for ``verb`` (or ``None``)."""
    from . import pr_contract as pc

    try:
        flow = _core()._pr_flow_profile(config.default_repo)
        return pc.pr_reminder(flow, verb, state, ok=ok, reason=reason)
    except Exception:
        return None


def _emit_pr_reminder(reminder, *, use_json: bool, result: dict | None = None) -> None:
    """Surface a PR reminder."""
    if reminder is None:
        return
    if use_json:
        if result is not None:
            result["reminder"] = reminder.as_dict()
    else:
        print(reminder.text(), file=sys.stderr)


def cmd_deploy_instructions(args: argparse.Namespace) -> int:
    """Retire migrated managed instruction files for the current machine."""
    project = cfg.project_name()
    repo_dir = _core()._find_repo_dir()
    if not repo_dir:
        output.err("Cannot find repo root")
        return 1

    machine = args.machine
    if not machine:
        try:
            config = cfg.load_config()
            machine = config.machine
        except Exception:
            machine = cfg.detect_machine(repo_dir)

    try:
        registry = cfg.load_machines_yaml(repo_dir)
    except FileNotFoundError:
        output.skipped("No machines.yaml found (optional)")
        _core()._cleanup_stale_instructions(cfg.project_dir(project))
        return 0
    except ValueError as exc:
        output.err(f"Cannot load machines.yaml: {exc}")
        return 1

    if machine not in registry:
        output.err(f"Machine '{machine}' not found in machines.yaml")
        return 1

    proj_dir = cfg.project_dir(project)
    proj_dir.mkdir(parents=True, exist_ok=True)
    _core()._deploy_copilot_instructions(
        proj_dir,
        registry[machine],
        project=project,
    )
    return 0


def cmd_machine_context(args: argparse.Namespace) -> int:
    """sessionStart hook entrypoint: emit machine identity as additionalContext."""
    del args
    import json as _json

    def _empty() -> int:
        print("{}")
        return 0

    try:
        project, _assumed = _core()._resolve_active_project(None)
    except Exception:
        return _empty()
    if not project:
        return _empty()
    try:
        cfg.set_active_project(project)
    except Exception:
        pass

    try:
        config = cfg.load_config()
    except Exception:
        return _empty()

    project = getattr(config, "repo_name", "") or project
    machine = getattr(config, "machine", "") or ""
    if not project or not machine:
        return _empty()

    try:
        repo_dir = config.default_repo.anchor
    except Exception:
        repo_dir = _core()._find_repo_dir()
    if not repo_dir:
        return _empty()

    try:
        registry = cfg.load_machines_yaml(repo_dir)
    except (FileNotFoundError, ValueError):
        return _empty()

    entry = cfg.find_machine_entry(registry, machine)
    if entry is None:
        return _empty()

    try:
        raw = cfg.render_copilot_instructions(entry, project=project).rstrip()
    except Exception:
        return _empty()
    if not raw:
        return _empty()

    print(_json.dumps({"additionalContext": raw}))
    return 0


def cmd_get(args: argparse.Namespace) -> int:
    """Query project paths and config values -- machine-readable output."""
    key: str = args.key

    if key == "keys":
        for k, desc in _GET_KEYS.items():
            print(f"{k:16s}  {desc}")
        return 0

    session_id = getattr(args, "session_id", None)
    session_wt_id = None
    session_cwd = None
    if session_id:
        session_wt_id = _core()._activate_session_binding(session_id)
        if not session_wt_id:
            try:
                session_cwd = sessions.session_cwd(session_id)
            except Exception:
                session_cwd = None
            if session_cwd is not None:
                _core()._activate_project_for_path(str(session_cwd))

    try:
        config = cfg.load_config()
    except Exception as e:
        output.err(f"Cannot load config: {e}")
        return 1

    repo = config.default_repo
    wt_id = _core()._infer_worktree_id_from_cwd(config)
    if not wt_id and session_id:
        if not session_wt_id:
            try:
                session_wt_id = tracking.find_worktree_id_by_session(session_id)
            except Exception:
                session_wt_id = None
        if not session_wt_id and session_cwd is not None:
            try:
                session_wt_id = tracking.find_worktree_id_by_cwd(str(session_cwd))
            except Exception:
                session_wt_id = None
        wt_id = session_wt_id
    identity_cwd = session_cwd if session_cwd is not None else os.getcwd()
    current_worktree = _core()._worktree_path_for_id(
        config,
        wt_id,
        cwd=identity_cwd,
    )
    state_scope_id = wt_id
    session_is_anchor = False
    if session_cwd is not None and not wt_id:
        try:
            proc = subprocess.run(
                ["git", "-C", str(session_cwd), "rev-parse", "--show-toplevel"],
                capture_output=True,
                text=True,
                timeout=5,
                env=git_ops.repository_identity_env(),
                stdin=subprocess.DEVNULL,
            )
            session_is_anchor = proc.returncode == 0 and git_ops._normalize_wt_path(
                proc.stdout.strip()
            ) == git_ops._normalize_wt_path(str(repo.anchor))
        except Exception:
            session_is_anchor = False
    if not state_scope_id and (_core()._cwd_is_inside_project(Path(repo.anchor)) or session_is_anchor):
        state_scope_id = tracking.ANCHOR_ID

    if key == "worktree-state-dir" and session_id and not state_scope_id:
        output.err(
            f"Cannot resolve state directory for session {session_id!r}; "
            "the session is unknown or is not associated with an adopted project."
        )
        return 1

    state_dir = cfg.project_dir() / "worktrees" / state_scope_id if state_scope_id else None
    if key == "worktree-state-dir" and state_dir is not None:
        try:
            state_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            output.err(f"Cannot create worktree state directory: {exc}")
            return 1

    values = {
        "repo-dir": repo.anchor,
        "worktree-dir": current_worktree,
        "worktree-id": wt_id or "",
        "worktree-state-dir": (str(state_dir) if state_dir is not None else ""),
        "worktrees-root": repo.worktree_root,
        "src-dir": config.srcroot,
        "config-dir": str(cfg.project_dir()),
        "machine": config.machine,
        "platform": config.platform,
        "project": config.repo_name,
        "owner-ref": (
            tracking.format_claim_ref(config.machine, config.repo_name, wt_id, session_id)
            if wt_id
            else ""
        ),
        "repo-remote": _core()._resolve_repo_remote(config, repo),
        "lease-origin": _core()._resolve_lease_origin(),
        "pr-enabled": "true" if repo.pr.enabled else "false",
        "pr-required": "true" if repo.pr.required else "false",
        "pr-provider": repo.pr.provider if repo.pr.enabled else "",
        "pr-profile": _core()._pr_flow_profile(repo).profile,
    }

    if key not in values:
        output.err(f"Unknown key: {key!r}. Use 'get keys' to list available keys.")
        return 1

    print(values[key])
    return 0


def cmd_install_status(args: argparse.Namespace) -> int:
    del args
    inst.show_install_status()
    return 0


def cmd_installer_readiness(args: argparse.Namespace) -> int:
    del args
    from .installer_readiness import emit, evaluate

    return emit(evaluate())


def _state_root_pair(json_out: bool) -> int:
    """Resolve the paired (harness/knowledge sibling) worktree of the cwd."""
    try:
        config = cfg.load_config()
    except (OSError, RuntimeError, ValueError):
        config = None
    pair = state_root_mod.resolve_pair(config, cwd=os.getcwd())
    if json_out:
        print(json.dumps(pair.as_dict(), indent=2))
    elif pair.sibling and not pair.error:
        print(pair.sibling.path)
    elif pair.error:
        print(pair.error, file=sys.stderr)
    return 0 if pair.paired and pair.sibling and not pair.error else 3


def cmd_state_root_dispatch(argv: list[str]) -> int:
    """Resolve the state root (where efforts/visions/logs should be written)."""
    p = argparse.ArgumentParser(
        prog="agent-worktrees state-root",
        description=(
            "Resolve the repo checkout where personal state (efforts, visions, "
            "logs) should be written for the current launch context."
        ),
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit the full resolution as JSON (state_root/source/repo/"
        "stateless/bound/error) instead of the bare path.",
    )
    p.add_argument(
        "--repo",
        default=None,
        metavar="NAME",
        help="Explicit override: resolve this registered repo's checkout "
        "(target the harness itself or a product repo, ignoring the "
        "stateless binding).",
    )
    p.add_argument(
        "--pair",
        action="store_true",
        help="Resolve the PAIRED worktree (the citadel -harness/-knowledge "
        "sibling of the current worktree): print the sibling's checkout "
        "path, or JSON (pair_id/role/sibling id/role/path/kind) with "
        "--json. Exit 3 when the current worktree is unpaired/untracked.",
    )
    p.add_argument(
        "--conduct",
        action="store_true",
        help='Emit the sessionStart "the user\'s state repo" definition '
        "(Markdown) binding the term to the resolved checkout, for the "
        "session-conduct hook. Always exits 0 (prints an unbound notice "
        "when no state repo is bound).",
    )
    try:
        args = p.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)

    if args.pair:
        return _state_root_pair(args.json)

    config = cfg.load_config()
    res = state_root_mod.resolve_state_root(config, repo_override=args.repo)

    if args.conduct:
        launch_path = state_root_mod._git_toplevel(os.getcwd())
        try:
            launch_anchor = config.default_repo.anchor
        except KeyError:
            launch_anchor = None
        pair = state_root_mod.resolve_pair(config, cwd=os.getcwd())
        print(
            state_root_mod.state_repo_definition(
                res,
                pair=pair,
                launch_path=launch_path,
                launch_anchor=launch_anchor,
            )
        )
        return 0

    if args.json:
        print(json.dumps(res.as_dict(), indent=2))
    else:
        if res.path:
            print(res.path)
        if res.error:
            print(res.error, file=sys.stderr)
    return 0 if res.path else 3


def cmd_coordination_readiness_dispatch(argv: list[str]) -> int:
    """Emit the versioned preflight for claim-producing integrations."""
    p = argparse.ArgumentParser(
        prog="agent-worktrees coordination-readiness",
        description=(
            "Report whether resource-claim coordination has a usable durable state root."
        ),
    )
    try:
        p.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    readiness = state_root_mod.coordination_readiness(cfg.load_config())
    print(json.dumps(readiness.as_dict(), indent=2))
    return 0 if readiness.ready else 3


def cmd_config_root_dispatch(argv: list[str]) -> int:
    """Resolve the guarded machine-local root for supported setup writers."""
    p = argparse.ArgumentParser(
        prog="agent-worktrees config-root",
        description=(
            "Resolve the machine-local configuration root for the current "
            "project and reject an explicit stateless-checkout destination."
        ),
    )
    p.add_argument(
        "--destination",
        default=None,
        metavar="PATH",
        help=(
            "Validate an explicit configuration root instead of resolving the "
            "default machine-local root"
        ),
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit config_root/source/repo/stateless/bound/error as JSON",
    )
    try:
        args = p.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)

    try:
        if args.destination and not cfg.active_project():
            res = state_root_mod.validate_config_destination(args.destination)
        else:
            project = cfg.project_name()
            res = state_root_mod.resolve_config_root(
                cfg.load_config(),
                destination=args.destination,
                project=project,
            )
    except (OSError, RuntimeError, ValueError) as exc:
        res = state_root_mod.ConfigRoot(
            None,
            "explicit" if args.destination else "machine_local",
            "",
            False,
            False,
            error=str(exc),
        )
    if args.json:
        print(json.dumps(res.as_dict(), indent=2))
    else:
        if res.path:
            print(res.path)
        if res.error:
            print(res.error, file=sys.stderr)
    return 0 if res.path else 3


def cmd_knowledge_dispatch(argv: list[str]) -> int:
    """Manage the launch-time harness/knowledge composition boundary."""
    p = argparse.ArgumentParser(
        prog="agent-worktrees knowledge",
        description=(
            "Manage machine-local composition between a stateless harness and "
            "its paired private knowledge checkout."
        ),
    )
    sub = p.add_subparsers(dest="command")
    compose_p = sub.add_parser(
        "compose-plugins",
        help=(
            "Compose the paired knowledge checkout's local and remote plugin "
            "settings into the harness settings.local.json"
        ),
    )
    compose_p.add_argument(
        "--cwd",
        default=None,
        help=(
            "Resolve the tracked pair containing this path (defaults to the "
            "current directory; launchers pass the real worktree for Bare resume)"
        ),
    )
    compose_p.add_argument(
        "--harness-path",
        default=None,
        help="Explicit harness checkout (requires --knowledge-path; bypasses pair lookup)",
    )
    compose_p.add_argument(
        "--knowledge-path",
        default=None,
        help="Explicit knowledge checkout (requires --harness-path; bypasses pair lookup)",
    )
    compose_p.add_argument(
        "--json", action="store_true", help="Emit the composition summary as JSON"
    )
    try:
        args = p.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    if args.command != "compose-plugins":
        p.print_help()
        return 2
    if bool(args.harness_path) != bool(args.knowledge_path):
        print(
            "agent-worktrees knowledge compose-plugins: error: --harness-path "
            "and --knowledge-path must be supplied together",
            file=sys.stderr,
        )
        return 2

    from . import knowledge_plugins

    try:
        if args.harness_path:
            summary = knowledge_plugins.compose(args.harness_path, args.knowledge_path)
        else:
            _core()._activate_project_for_path(args.cwd)
            summary = knowledge_plugins.compose_from_pair(cwd=args.cwd)
    except knowledge_plugins.KnowledgePluginError as exc:
        summary = {
            "action": "error",
            "paired": False,
            "sanitized": False,
            "error": str(exc),
        }
        if args.json:
            print(json.dumps(summary, indent=2))
        else:
            print(f"Knowledge plugin preflight failed: {exc}", file=sys.stderr)
        return 3

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        outcome = summary["action"]
        if outcome == "composed":
            action = "Updated" if summary["changed"] else "Verified"
            print(f"{action} knowledge plugin overlay: {summary['settings_local']}")
            print(f"  knowledge: {summary['knowledge_path']}")
            if summary["marketplaces"]:
                print(f"  marketplaces: {', '.join(summary['marketplaces'])}")
            if summary["enabled_plugins"]:
                print(f"  enabled: {', '.join(summary['enabled_plugins'])}")
            conflicts = summary["conflicts"]
            if conflicts["marketplaces"] or conflicts["enabled_plugins"]:
                print(
                    "  preserved conflicting harness/unmanaged settings: "
                    f"{len(conflicts['marketplaces'])} marketplace(s), "
                    f"{len(conflicts['enabled_plugins'])} plugin enable(s)",
                    file=sys.stderr,
                )
        elif outcome == "retired":
            print(f"Retired stale knowledge plugin overlay: {summary['settings_local']}")
            print(f"  pair error: {summary['pair_error']}")
        else:
            print(f"Knowledge plugin preflight: no-op ({summary['pair_error']})")
    return 0


def cmd_reconcile_marketplaces(args: argparse.Namespace) -> int:
    """Deprecated no-op compatibility shim for the retired command."""
    if args.stdin:
        try:
            sys.stdin.read()
        except OSError:
            pass
    if args.session_start:
        print("{}")
    elif args.json:
        print(json.dumps({"action": "no-op", "changed": False, "reason": "retired"}, indent=2))
    else:
        print("Marketplace source overrides were retired; nothing to reconcile.")
    return 0
