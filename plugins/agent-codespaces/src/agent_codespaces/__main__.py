"""CLI entry point for agent-codespaces.

Subcommands:
  ssh <name>            SSH into a CodeSpace (interactive or --stdio)
  list                  List active CodeSpaces
  config adopt          Register current repo for config
  config init           Scaffold codespaces.yaml from existing CodeSpaces
  config show           Show resolved config
  config validate       Validate config
  delete <name>         Delete a CodeSpace (recovers sessions first)
  finalize <name>       Recover Copilot sessions, then optionally --delete
  borrow <effort> <cs>  Advisory-lease a CodeSpace to an effort (check out)
  release <target>      Release a lease (by CodeSpace or effort name)
  leases                Show active CodeSpace leases
  pool                  Show the CodeSpace pool (disposition + core-budget)
  wait <name>           Patiently wait for Available (fail-fast on dead state)
  status                Show service status
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shlex
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING
from collections.abc import Callable

from .codespace_config import CodespaceSource
from . import relay_launch
from . import pool as pool_mod
from .config import (
    ADOPTED_REPOS_FILE,
    CANONICAL_CONFIG_REL,
    CONFIG_DIR_NAME,
    CONFIG_FILE_IN_DIR,
    CONFIG_FILENAME,
    RUNTIME_DIR,
    AdoptedRepo,
    load_adopted_repos,
    load_merged_config,
    repo_config_path,
    repo_has_config,
    save_adopted_repos,
    validate_config,
)
from .connect import (
    ConnectStage,
    ConnectTracker,
    breadcrumb_prelude,
)
from .lifecycle import (
    cleanup_stale,
    create_codespace,
    delete_codespace,
    list_codespaces,
    stop_codespace,
    wait_for_available,
)
from .sessions import sync_codespace_sessions

log = logging.getLogger("agent-codespaces")

if TYPE_CHECKING:
    from ssh_manager import SSHConfig, SupervisedRelayForward

# Patience budget for the SSH-to-CodeSpace stage -- a Shutdown CodeSpace boots
# on connect, which can take well over a minute. Overridable via env.
_SSH_BOOT_TIMEOUT = float(os.environ.get("AGENT_CODESPACES_BOOT_TIMEOUT", "180"))

# Exit code when an SSH operation is rejected because the target is already in
# use by another live process (see ssh_manager.TargetBusyError). Distinct from
# generic failures (1) and the --remote-cmd timeout (124) so callers can react.
_BUSY_EXIT = 75

# Exit code when the host cannot mint an ADO REST bearer and enforcement is on
# (credentials.enforce_ado_rest_login) -- the connect aborts cleanly rather than
# proceeding into a silent mid-dispatch ADO-REST failure (#77).
_ADO_AUTH_EXIT = 77


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="agent-codespaces",
        description="GitHub Codespaces lifecycle, SSH, and credential relay",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable debug logging"
    )
    parser.add_argument(
        "--project", "-p", dest="project", default=None, metavar="REPO",
        help="Resolve as if the cwd were inside REPO's checkout: repo-root "
             "discovery (e.g. codespaces.yaml) targets REPO instead of the "
             "actual cwd. Injected by the `<repo> <slug>` router (e.g. `<repo> "
             "codespaces …`). A harmless no-op for verbs that take an explicit "
             "CodeSpace name or repo.",
    )
    sub = parser.add_subparsers(dest="command")

    # --- ssh ---
    ssh_parser = sub.add_parser("ssh", help="SSH into a CodeSpace")
    ssh_parser.add_argument("name", help="CodeSpace name")
    ssh_parser.add_argument(
        "--stdio", action="store_true",
        help="Structured stdio mode for agent-bridge transport",
    )
    ssh_parser.add_argument(
        "--remote-cmd", dest="remote_cmd",
        help="Remote command to execute (non-interactive, no PTY). Interactive "
             "prompts (e.g. a sudo password) will hang -- use `sudo -n`. A "
             "backgrounded process must fully detach its stdio "
             "(`nohup <cmd> >/tmp/out 2>&1 </dev/null & disown`) or it holds "
             "the channel open until --timeout.",
    )
    ssh_parser.add_argument(
        "--timeout", dest="timeout", type=float, default=60.0, metavar="SECS",
        help="Timeout in seconds for --remote-cmd execution (default: 60). On "
             "expiry the command is terminated and the CLI exits 124. For a "
             "non-stdio --remote-cmd this also defaults the whole "
             "connect+command budget unless --connect-timeout is set.",
    )
    ssh_parser.add_argument(
        "--connect-timeout", dest="connect_timeout", type=float, default=None,
        metavar="SECS",
        help="Overall timeout in seconds for SSH connect/provisioning and the "
             "requested operation. Defaults to --timeout for a non-stdio "
             "--remote-cmd; otherwise no additional whole-operation deadline.",
    )
    ssh_parser.add_argument(
        "--no-provision", "--minimal", dest="no_provision", action="store_true",
        help="Skip heavyweight dotfiles/harness/plugin/repo provisioning. "
             "Non-stdio --remote-cmd uses this minimal diagnostic path by "
             "default; --stdio and interactive keep full provisioning unless "
             "this flag is supplied.",
    )
    ssh_parser.add_argument(
        "--no-relay", action="store_true",
        help="Skip credential relay tunnel setup",
    )
    ssh_parser.add_argument(
        "--auth-cache-warmup", dest="auth_cache_warmup", action="store_true",
        default=None,
        help="Warm the CodeSpace's short-TTL auth cache after connect. "
             "Defaults on for --stdio dispatch and interactive SSH; defaults "
             "off for minimal diagnostic --remote-cmd.",
    )
    ssh_parser.add_argument(
        "--no-auth-cache-warmup", dest="auth_cache_warmup", action="store_false",
        help="Skip the best-effort CodeSpace auth-cache warm-up.",
    )
    ssh_parser.add_argument(
        "--repo", dest="repo", default=None,
        help="CodeSpace repository (owner/name) -- selects per-repo "
             "provision hooks without an extra lookup",
    )
    ssh_parser.add_argument(
        "--effort", dest="effort", default=None,
        help="Explicit owner for this CodeSpace's EXCLUSIVE claim (#897). When "
             "omitted, the owner is auto-resolved to the calling worktree. A "
             "dispatched ssh (whose cwd is the daemon's, not the caller's "
             "worktree) passes the caller's worktree here. A CodeSpace is "
             "fronted by one bridge, so if a different LIVE worktree already "
             "holds the claim the connect is bounced (exit 75) unless --force; "
             "a claim held by a gone worktree is auto-released.",
    )
    ssh_parser.add_argument(
        "--stage-plugin", dest="stage_plugins", action="append", default=[],
        metavar="SOURCE",
        help="A related-repo plugin source (e.g. name@marketplace) to stage onto "
             "the CodeSpace (egress-free, from the host's installed payload) and "
             "fold into the launch as --plugin-dir. Repeatable. Set by the "
             "agent-bridge dispatch path; dispatch-scoped, not globally enabled.",
    )
    ssh_parser.add_argument(
        "--force", action="store_true",
        help="Take over the target if another SSH operation is already in "
             "progress against it: terminates the in-flight connection and "
             "reclaims the target (discards its in-progress work). Without "
             "this, a busy target is rejected with an explanatory error. This "
             "is the SSH-lock takeover only; it does NOT evict another "
             "worktree's exclusive claim (use --force-claim for that).",
    )
    ssh_parser.add_argument(
        "--force-claim", dest="force_claim", action="store_true",
        help="Evict another live worktree's EXCLUSIVE claim on this CodeSpace "
             "(#897) and take it over. Distinct from --force (the SSH lock): a "
             "bridge dispatch passes --force for the lock but NOT --force-claim, "
             "so a genuine claim conflict bounces rather than silently stealing "
             "another worktree's control.",
    )

    # --- list ---
    list_parser = sub.add_parser("list", help="List active CodeSpaces")
    list_parser.add_argument(
        "--json", dest="json_output", action="store_true",
        help="Output as JSON",
    )

    # --- config ---
    config_parser = sub.add_parser("config", help="Configuration management")
    config_sub = config_parser.add_subparsers(dest="config_command")

    config_sub.add_parser("adopt", help="Register current repo for config")
    config_sub.add_parser("show", help="Show resolved config")
    config_sub.add_parser("validate", help="Validate config")
    config_sub.add_parser(
        "migrate",
        help="Relocate a legacy repo-root codespaces.yaml to "
             ".agent-codespaces/config.yaml",
    )
    config_init_p = config_sub.add_parser(
        "init",
        help="Scaffold .agent-codespaces/config.yaml in the current repo "
             "(supplementary-only; most repos need none)",
    )
    config_init_p.add_argument(
        "--from-codespace", dest="from_codespace", default=None,
        help="Derive defaults from this CodeSpace name (default: auto-pick)",
    )
    config_init_p.add_argument(
        "--force", action="store_true",
        help="Overwrite an existing .agent-codespaces/config.yaml",
    )
    config_init_p.add_argument(
        "--adopt", action="store_true",
        help="(Deprecated no-op) init now always auto-adopts",
    )

    # --- delete ---
    delete_parser = sub.add_parser("delete", help="Delete a CodeSpace")
    delete_parser.add_argument("name", help="CodeSpace name")
    delete_parser.add_argument(
        "--force", action="store_true", help="Force deletion",
    )
    delete_parser.add_argument(
        "--no-sync", action="store_true",
        help="Skip the pre-delete Copilot session recovery",
    )

    # --- finalize ---
    finalize_parser = sub.add_parser(
        "finalize",
        help="Gracefully close out a CodeSpace: recover Copilot sessions, "
             "then optionally delete",
    )
    finalize_parser.add_argument("name", help="CodeSpace name")
    finalize_parser.add_argument(
        "--delete", action="store_true",
        help="Delete the CodeSpace after a successful session recovery",
    )
    finalize_parser.add_argument(
        "--force", action="store_true",
        help="With --delete: delete even if recovery failed -- diagnose the "
             "failure first, do not use for routine hiccups (destroys "
             "unrecovered sessions)",
    )
    finalize_parser.add_argument(
        "--timeout", type=float, default=300.0,
        help="Seconds for the session pull (default: 300)",
    )
    finalize_parser.add_argument(
        "--picker-progress", dest="picker_progress", action="store_true",
        help="Emit the registered-pivot NDJSON progress envelope "
             "({\"type\":\"progress\",\"pct\",\"msg\"} lines -> done/error) so the "
             "Worktree Picker's CodeSpaces Recycle verb can stream live progress "
             "into its modal (D4).",
    )

    # --- stop ---
    stop_parser = sub.add_parser(
        "stop",
        help="Gracefully stop a CodeSpace (recover Copilot sessions, then shut "
             "down) -- PRESERVES it for later resume; never deletes",
    )
    stop_parser.add_argument("name", help="CodeSpace name")
    stop_parser.add_argument(
        "--no-sync", action="store_true",
        help="Skip the pre-stop Copilot session recovery",
    )
    stop_parser.add_argument(
        "--timeout", type=float, default=300.0,
        help="Seconds for the session pull (default: 300)",
    )

    # --- prune ---
    prune_parser = sub.add_parser(
        "prune",
        help="Delete prune-eligible (prunable) CodeSpaces to reclaim quota -- "
             "the worktree-style reclaim pass (never touches active/recovered)",
    )
    prune_parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be pruned without deleting",
    )
    prune_parser.add_argument(
        "--max", type=int, default=0, dest="max_count",
        help="Prune at most N boxes, oldest-first (0 = all, default)",
    )

    # --- mark ---
    mark_parser = sub.add_parser(
        "mark",
        help="Set/clear a CodeSpace's prune-lifecycle marker "
             "(recovered | prunable | active) -- used by cleaning-codespaces to "
             "promote a recovered box to prunable once its PR merges",
    )
    mark_parser.add_argument("name", help="CodeSpace name")
    mark_parser.add_argument(
        "state", choices=["recovered", "prunable", "active"],
        help="Lifecycle state to set ('active' clears the marker)",
    )
    mark_parser.add_argument(
        "--reason", default="",
        help="Optional note (e.g. 'PR 2285861 merged + effort archived')",
    )

    # --- create ---
    create_parser = sub.add_parser(
        "create", help="Create a CodeSpace and run post-create provisioning",
    )
    create_parser.add_argument("repo", help="Repository (owner/name)")
    create_parser.add_argument(
        "--branch", default=None, help="Branch to create the CodeSpace on",
    )
    create_parser.add_argument(
        "--display-name", dest="display_name", default=None,
        help="Display name for the CodeSpace",
    )
    create_parser.add_argument(
        "--devcontainer-path", dest="devcontainer_path", default=None,
        help=(
            "Devcontainer config to build from (e.g. "
            ".devcontainer/devcontainer.json). Only needed when the repo has "
            "multiple devcontainer configs; overrides codespaces.yaml. "
            "Auto-resolved otherwise."
        ),
    )
    create_parser.add_argument(
        "--no-wait", action="store_true",
        help="Don't wait for Available / run provisioning",
    )
    create_parser.add_argument(
        "--timeout", type=float, default=300.0,
        help="Seconds to wait for the CodeSpace to become Available",
    )

    # --- bridge ---
    bridge_parser = sub.add_parser(
        "bridge", help="Agent-bridge provider integration",
    )
    bridge_sub = bridge_parser.add_subparsers(dest="bridge_command")
    bridge_reg = bridge_sub.add_parser(
        "register", help="Register codespace agents with agent-bridge",
    )
    bridge_reg.add_argument(
        "--ttl", type=float, default=300.0,
        help="TTL in seconds (0 = no expiry, default: 300)",
    )
    bridge_reg.add_argument(
        "--bridge-url", default="http://127.0.0.1:9280",
        help="Agent-bridge URL (default: http://127.0.0.1:9280)",
    )
    bridge_unreg = bridge_sub.add_parser(
        "unregister", help="Remove codespace agents from agent-bridge",
    )
    bridge_unreg.add_argument(
        "--bridge-url", default="http://127.0.0.1:9280",
        help="Agent-bridge URL",
    )
    bridge_status = bridge_sub.add_parser(
        "status", help="Show provider registration status",
    )
    bridge_status.add_argument(
        "--bridge-url", default="http://127.0.0.1:9280",
        help="Agent-bridge URL",
    )
    bridge_refresh = bridge_sub.add_parser(
        "refresh", help="Re-register with current live codespace state",
    )
    bridge_refresh.add_argument(
        "--ttl", type=float, default=300.0,
        help="TTL in seconds (default: 300)",
    )
    bridge_refresh.add_argument(
        "--bridge-url", default="http://127.0.0.1:9280",
        help="Agent-bridge URL",
    )

    # --- cleanup ---
    cleanup_parser = sub.add_parser(
        "cleanup", help="Remove stale local state (SSH configs, sockets)",
    )
    cleanup_parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be removed without removing",
    )

    # --- borrow / release / leases (advisory borrow broker) ---
    borrow_p = sub.add_parser(
        "borrow",
        help="Advisory-lease a CodeSpace to an effort (check it out)",
    )
    borrow_p.add_argument("effort", help="Effort/worktree name (lease holder)")
    borrow_p.add_argument("codespace", help="CodeSpace name to borrow")
    borrow_p.add_argument(
        "--force", action="store_true",
        help="Take over even if leased by another effort (stale/buggy holder)",
    )

    release_p = sub.add_parser(
        "release", help="Release a CodeSpace lease (check it in)",
    )
    release_p.add_argument("target", help="CodeSpace name or effort name")

    sub.add_parser("leases", help="Show active CodeSpace leases")

    # --- claim / release-claim (#897: exclusive, worktree-keyed control) ------
    # The process-to-process seam the agent-bridge daemon shells out to (it
    # cannot import agent_codespaces; mirrors the acp-model-flags pattern) to
    # enforce exclusive control of a CodeSpace on the Session-Host dispatch path.
    claim_p = sub.add_parser(
        "claim",
        help="Acquire an EXCLUSIVE, worktree-keyed claim on a CodeSpace (#897)",
    )
    claim_p.add_argument("codespace", help="CodeSpace name to claim")
    claim_p.add_argument(
        "--owner", dest="owner", default=None,
        help="Owning worktree (its worktree-dir). Defaults to the calling "
             "worktree; a dispatched caller (e.g. the bridge daemon) passes the "
             "original caller's worktree here.",
    )
    claim_p.add_argument(
        "--force-claim", dest="force_claim", action="store_true",
        help="Evict another live worktree's claim and take over.",
    )
    claim_p.add_argument(
        "--holder-ref", dest="holder_ref", default=None,
        help="Qualified ClaimRef (machine/project/worktree_id[#session]) of the "
             "cross-machine L2 lease holder. Defaults to the calling worktree; a "
             "dispatched caller (e.g. the bridge daemon) passes the original "
             "caller's ref so the distributed lease is keyed to the real owner.",
    )

    release_claim_p = sub.add_parser(
        "release-claim",
        help="Release this worktree's exclusive claim on a CodeSpace (#897)",
    )
    release_claim_p.add_argument("codespace", help="CodeSpace name")
    release_claim_p.add_argument(
        "--owner", dest="owner", default=None,
        help="Owning worktree (defaults to the calling worktree). Only releases "
             "if the claim is owned by this worktree.",
    )

    # --- pool (finite, budget-bounded pool view: disposition + budget) ---
    pool_p = sub.add_parser(
        "pool",
        help="Show the CodeSpace pool: per-box disposition "
             "(in-use/idle/clean/stale) + allocation + core-budget headroom",
    )
    pool_p.add_argument(
        "--json", dest="json_output", action="store_true",
        help="Emit machine-readable JSON (members + budget)",
    )
    pool_p.add_argument(
        "--picker-json", dest="picker_json", action="store_true",
        help="Emit the Worktree Picker CodeSpaces-pivot shape "
             "({entries, summary}) consumed by the registered-pivot renderer",
    )
    pool_p.add_argument(
        "--stream", dest="stream", action="store_true",
        help="With --picker-json, emit the registered-pivot NDJSON envelope "
             "(begin -> row per CodeSpace -> summary -> done) so the pivot "
             "paints progressively (D2).",
    )
    pool_p.add_argument(
        "--subscribe", dest="subscribe", action="store_true",
        help="With --stream, hold the channel open and emit live delta/removed "
             "frames from a periodic pool re-scan so an open pivot updates in "
             "place (D2).",
    )
    pool_p.add_argument(
        "--interval", dest="interval", type=float, default=5.0,
        help="Seconds between --subscribe re-scans (default: 5).",
    )
    pool_p.add_argument(
        "--budget", type=int, default=None,
        help=f"Account concurrent-core budget (default: {pool_mod.DEFAULT_BUDGET_CORES})",
    )
    pool_p.add_argument(
        "--stale-after", type=float, default=None,
        help="Seconds an unheld running box may idle before it ages to 'stale' "
             f"(default: {int(pool_mod.DEFAULT_STALE_AFTER)})",
    )

    # --- wait (patient, fail-fast, backgroundable) ---
    wait_p = sub.add_parser(
        "wait",
        help="Wait for a CodeSpace to become Available (patient; fails fast on "
             "a genuinely-dead state; safe to run as a background task)",
    )
    wait_p.add_argument("name", help="CodeSpace name")
    wait_p.add_argument(
        "--timeout", type=float, default=1200.0,
        help="Max seconds to wait (default: 1200 = 20 min)",
    )
    wait_p.add_argument(
        "--interval", type=float, default=10.0,
        help="Poll interval in seconds (default: 10)",
    )

    # --- status ---
    sub.add_parser("status", help="Show service status")

    # --- version ---
    sub.add_parser("version", help="Show version")

    # --- acp-model-flags (process-to-process seam for agent-bridge dispatch) ---
    sub.add_parser(
        "acp-model-flags",
        help="Print resolved per-session copilot model flags for an ACP launch "
        "(empty when none / opted out).",
    )

    # --- provision-command / relay-launch-env ---------------------------------
    # Additional process-to-process seams for agent-bridge's CodeSpace dispatch
    # (#892 Increment 1): the daemon shells out to these instead of importing
    # ``agent_codespaces`` in the bridge venv, so an agent-codespaces bugfix
    # reaches the dispatch path from agent-codespaces' OWN venv with no bridge
    # redeploy (retires the #733 class). Mirror the ``acp-model-flags`` seam.
    sub.add_parser(
        "provision-command",
        help="Print the idempotent bash command that (re)installs the CodeSpace "
        "relay/auth helpers (the dispatch-path provision step).",
    )
    relay_env_p = sub.add_parser(
        "relay-launch-env",
        help="Print JSON {prelude, port} for a detached CodeSpace launch's "
        "relay env (mints/reuses the per-codespace relay token).",
    )
    relay_env_p.add_argument("codespace", help="CodeSpace name")
    relay_env_p.add_argument(
        "--relay-port", dest="relay_port", type=int, default=None,
        help="The daemon's actually-bound relay port to inject (defaults to the "
        "published/config port when omitted).",
    )

    # --- namespace-* (process-boundary resolver seam for agent-bridge, #892 Inc 3)
    # The `codespace:` namespace resolver, exposed over a process boundary so
    # agent-bridge drives it via subprocess instead of importing
    # `agent_codespaces.resolver` in the bridge venv. Emit plain JSON/dicts
    # (agent_bridge-free); the bridge shim reconstructs its SpawnTarget /
    # NamespaceAgentInfo. Mirror the acp-model-flags / provision-command seams.
    sub.add_parser(
        "namespace-list",
        help="Print JSON list of codespace agents (name/display/description/"
        "icon/state/aliases) for the `codespace:` namespace.",
    )
    ns_resolve_p = sub.add_parser(
        "namespace-resolve",
        help="Print JSON {type,spawn_command,user} resolving a codespace name "
        "to a spawn spec (KeyError->exit 3, bad state->exit 4).",
    )
    ns_resolve_p.add_argument("name", help="Codespace name (raw or friendly)")
    ns_resolve_p.add_argument("--repo", default=None, help="Requested workspace repo")
    ns_resolve_p.add_argument(
        "--repo-remote", dest="repo_remote", default=None,
        help="Git remote URL for the requested repo (clone-if-missing).",
    )
    ns_resolve_p.add_argument(
        "--stage-plugin", dest="stage_plugin", action="append", default=[],
        help="Related-repo plugin source to stage (repeatable).",
    )
    ns_target_p = sub.add_parser(
        "namespace-target-repo",
        help="Print the workspace repo a codespace hosts (empty if unknown).",
    )
    ns_target_p.add_argument("name", help="Codespace name")
    ns_ready_p = sub.add_parser(
        "namespace-ensure-ready",
        help="Exit 0 if the codespace is reachable/startable, else exit 1.",
    )
    ns_ready_p.add_argument("name", help="Codespace name")

    # --- relay-profile (declarative credential-relay seam for agent-bridge #892 Inc 2)
    sub.add_parser(
        "relay-profile",
        help="Print JSON credential-relay profile (sources/port/ado_host/"
        "azure_resources/gated_actions/token_store) for agent-bridge to apply.",
    )

    sub.add_parser(
        "config-migrate",
        help="Migrate machine-local config schema (adopted-repos.yaml); idempotent",
    )

    args = parser.parse_args(argv)

    # Logging setup
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(levelname)s: %(message)s",
    )

    # A top-level --project (e.g. injected by the `<repo> <slug>` router) means
    # "resolve as if the cwd were inside REPO's checkout" -- chdir to REPO's
    # checkout so repo-root discovery (_resolve_repo_root / codespaces.yaml)
    # targets it. Only the project-consuming verbs (config) actually read from
    # the cwd; on a name/CodeSpace-addressed verb an *explicit* --project bounces
    # (fail loud, #1080) while a router-injected one stays a silent no-op.
    # Best-effort otherwise: an unresolvable project warns but never blocks.
    if _guard_project_scope(parser, args):
        _chdir_to_project(args.project)

    if not args.command:
        parser.print_help()
        return 1

    try:
        if args.command == "ssh":
            return _cmd_ssh(args)
        if args.command == "list":
            return _cmd_list(args)
        if args.command == "config":
            return _cmd_config(args)
        if args.command == "delete":
            return _cmd_delete(args)
        if args.command == "finalize":
            if getattr(args, "picker_progress", False):
                return _cmd_finalize_progress(args)
            return _cmd_finalize(args)
        if args.command == "stop":
            return _cmd_stop(args)
        if args.command == "create":
            return _cmd_create(args)
        if args.command == "prune":
            return _cmd_prune(args)
        if args.command == "mark":
            return _cmd_mark(args)
        if args.command == "bridge":
            return _cmd_bridge(args)
        if args.command == "cleanup":
            return _cmd_cleanup(args)
        if args.command == "borrow":
            return _cmd_borrow(args)
        if args.command == "release":
            return _cmd_release(args)
        if args.command == "leases":
            return _cmd_leases()
        if args.command == "claim":
            return _cmd_claim(args)
        if args.command == "release-claim":
            return _cmd_release_claim(args)
        if args.command == "pool":
            return _cmd_pool(args)
        if args.command == "wait":
            return _cmd_wait(args)
        if args.command == "status":
            return _cmd_status()
        if args.command == "version":
            return _cmd_version()
        if args.command == "acp-model-flags":
            return _cmd_acp_model_flags()
        if args.command == "provision-command":
            return _cmd_provision_command()
        if args.command == "relay-launch-env":
            return _cmd_relay_launch_env(args)
        if args.command == "namespace-list":
            return _cmd_namespace_list()
        if args.command == "namespace-resolve":
            return _cmd_namespace_resolve(args)
        if args.command == "namespace-target-repo":
            return _cmd_namespace_target_repo(args)
        if args.command == "namespace-ensure-ready":
            return _cmd_namespace_ensure_ready(args)
        if args.command == "relay-profile":
            return _cmd_relay_profile()
        if args.command == "config-migrate":
            return _cmd_config_migrate()
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130

    return 0


# The relay launch prelude now lives in ``relay_launch`` (the public seam the
# agent-bridge Session-Host path also imports, so both stay in lockstep). Kept as
# module aliases here for back-compat with existing references.
_SCRUB_ENV_VARS = relay_launch.SCRUB_ENV_VARS
_build_relay_env = relay_launch.build_relay_env


def _relay_listening(port: int, timeout: float = 0.5) -> bool:
    """True if the host credential relay is accepting TCP on 127.0.0.1:<port>.

    ``agent-codespaces ssh`` only sets up the ``-R`` reverse-forward and assumes
    the relay (owned/run by the agent-bridge daemon) is up. If the daemon is
    down or its relay failed to bind, the forward dead-ends and git auth over
    the tunnel silently returns nothing (#122/#112). A quick pre-connect probe
    lets us warn loudly instead of failing silently.
    """
    return relay_launch.relay_listening(port, timeout=timeout)


async def _start_supervised_relay(
    name: str,
    ssh_config: SSHConfig,
    relay_port: int,
    *,
    context: str,
    host_port_resolver: Callable[[], int] | None = None,
) -> SupervisedRelayForward | None:
    """Start the best-effort supervised credential-relay reverse-forward."""
    relay = None
    try:
        from ssh_manager import SupervisedRelayForward

        relay = SupervisedRelayForward(
            ssh_config, relay_port, host_port_resolver=host_port_resolver
        )
        await relay.start()
        return relay
    except Exception as exc:
        log.warning(
            "Credential relay reverse-forward for %s failed to establish: %s",
            name,
            exc,
        )
        relay_launch.warn_if_relay_unavailable(relay_port, name, context=context)
        try:
            if relay is not None:
                await relay.stop()
        except Exception as stop_exc:
            log.debug("Relay cleanup after failed start for %s failed: %s", name, stop_exc)
        return None


def _build_launch_command(
    remote_cmd: str | None,
    plugin_dirs: list[str],
    *,
    is_stdio: bool,
    relay_env: str,
    breadcrumb: str,
) -> str | None:
    """Assemble the remote command string, folding in ``--plugin-dir`` args.

    The ``--plugin-dir`` args are ONLY valid when ``remote_cmd`` *is* the
    ``copilot --acp`` launch -- i.e. the ``--stdio`` transport agent-bridge
    uses, where the copilot invocation is the tail of the command so appending
    is correct. A plain diagnostic ``--remote-cmd`` (non-stdio) is an arbitrary
    shell command, so appending flags to its tail corrupts it (issue #152: the
    last token -- e.g. ``tail``/``ls``/a script -- receives the unexpected
    ``--plugin-dir=...``). Fold them in only for the stdio launch.

    Prepends the relay env + arrival breadcrumb and wraps in ``bash -l -c`` so
    the CodeSpace platform env loads. Returns ``None`` when there is no command.
    """
    if not remote_cmd:
        return None
    acp = remote_cmd
    if is_stdio:
        for d in plugin_dirs:
            acp += f' --plugin-dir="{d}"'
        try:
            from .model_launch import build_model_flags

            acp += build_model_flags()
        except Exception as exc:
            log.debug("Model flag resolution failed for ACP launch: %s", exc)
    inner = relay_env + breadcrumb + "; " + acp
    return f"bash -l -c {shlex.quote(inner)}"


async def _preflight_copilot_platform(manager, name: str) -> None:  # noqa: ANN001
    """Ensure the CodeSpace's copilot has its platform binary before ACP launch.

    #111: a fresh CodeSpace can ship the ``@github/copilot`` loader stub without
    its platform optional-dependency (the private-feed npm default 401s the
    fetch), so ``copilot --acp`` fails at ``stage 7/LAUNCH_ACP`` with a bare
    "Connection closed". This verifies ``copilot --version`` and, if the
    platform package is missing, reinstalls it from public npm. Best-effort: any
    failure only warns; the launch proceeds and surfaces its own error.
    """
    from .platform_preflight import ensure_copilot_platform

    async def _run(cmd: str) -> tuple[int, str]:
        result = await manager.exec_command(name, cmd, timeout=240)
        return result.exit_code, (result.stdout or "") + (result.stderr or "")

    try:
        ok, detail = await ensure_copilot_platform(_run)
    except Exception as exc:
        log.debug("copilot platform preflight raised: %s", exc)
        return
    if not ok:
        print(
            "[WARN] CodeSpace copilot may be missing its platform package and "
            f"auto-repair did not confirm success ({detail}); the ACP launch "
            "may fail at LAUNCH_ACP -- see #111.",
            file=sys.stderr,
        )


def _cmd_ssh(args: argparse.Namespace) -> int:
    """SSH into a CodeSpace using ssh-manager."""
    from ssh_manager import ConnectionManager, TargetBusyError, TargetLock

    from .lifecycle import account_for_codespace

    source = CodespaceSource(args.name, account=account_for_codespace(args.name))
    config = load_merged_config()
    from .relay_launch import effective_relay_port
    relay_port = effective_relay_port(config)

    # Reusing a box (an explicit ssh connect) clears any prune-lifecycle marker
    # -- it is active work again, not a recovered/prunable reclaim candidate.
    _clear_status_quietly(args.name)
    # Exclusive, worktree-keyed claim (#897). A CodeSpace is fronted by exactly
    # one agent-bridge Session Host, so only one worktree may control it at a
    # time. Resolve the owning worktree -- an explicit ``--effort`` (used by a
    # dispatched ``ssh`` whose cwd is the daemon's, not the caller's worktree),
    # else the calling worktree via agent-worktrees -- then acquire the claim,
    # sweeping existing claims and BOUNCING a live different owner (unless
    # ``--force``). A claim held by a gone/finalized worktree is auto-released and
    # taken over. Degrade-safe: when no worktree resolves (not a worktree,
    # agent-worktrees absent), we skip claiming and connect exactly as before.
    from .lease import (
        ClaimConflict,
        active_worktree_ids,
        claim,
        resolve_owner_worktree,
    )

    # Escape hatch: an operator (or a unit test) can disable exclusive-control
    # enforcement entirely. --force remains the per-call takeover.
    if os.environ.get("AGENT_CODESPACES_DISABLE_CLAIM"):
        claim_owner = None
    else:
        claim_owner = resolve_owner_worktree(
            explicit=getattr(args, "effort", None),
            session_id=getattr(args, "session_id", None),
        )
    # Resolve the qualified holder ClaimRef once, for BOTH the cross-machine L2
    # claim (below) and the cross-harness in-CodeSpace fence (in _run). It is the
    # marker's holder identity even when L1/L2 claiming is disabled, so hoist it
    # out of the claim block. Degrade-safe: None when not in a worktree.
    from . import coordination
    fence_holder_ref = coordination.owner_ref(
        session_id=getattr(args, "session_id", None),
    )
    if claim_owner:
        holder_ref = fence_holder_ref
        try:
            claim(
                args.name, claim_owner,
                force=getattr(args, "force_claim", False),
                active=active_worktree_ids(),
                holder_ref=holder_ref,
            )
        except ClaimConflict as exc:
            print(
                f"[BUSY] {exc}\n"
                f"       A CodeSpace is fronted by a single bridge, so a second "
                f"worktree cannot drive it concurrently. Options:\n"
                f"       - let the owner finish, or dispatch to a different "
                f"CodeSpace; or\n"
                f"       - take over with --force-claim (evicts the current "
                f"owner's claim -- its in-flight work may be disrupted).",
                file=sys.stderr,
            )
            return _BUSY_EXIT
        except RuntimeError as exc:
            # Never let a claim-bookkeeping error block a connect.
            print(f"[WARN] CodeSpace claim skipped: {exc}", file=sys.stderr)

        # Journal the CodeSpace as an outbound obligation on the BORROWING
        # worktree so its finalize gate holds it accountable
        # (resource-obligation-settlement Ph3b-wiring/2). Best-effort +
        # degrade-safe: resolves the owner by its qualified holder-ref (not the
        # caller's cwd -- a dispatched ssh runs in the daemon's cwd). Settled to
        # at-rest on a clean disconnect (below). A missing holder-ref / binstub /
        # cross-machine owner is a silent no-op. Journaled for any claimed
        # connect (a clean disconnect immediately settles it to at-rest, so an
        # ephemeral probe leaves only harmless at-rest provenance).
        if fence_holder_ref:
            if coordination.journal_obligation(args.name, fence_holder_ref):
                log.info(
                    "Journaled CodeSpace %s as an obligation on %s",
                    args.name, fence_holder_ref,
                )

    # Credential relay state. The relay reverse-forward now has its own
    # supervised ``ssh -N -R`` channel, so it is not piggybacked on the
    # coordination connection's port_forwards.
    port_forwards: list[str] = []
    # Neutralize static PATs the CodeSpace injects (e.g. MS_ADO_PAT) so a
    # dispatched agent never relies on a stale/expired token instead of the
    # credential relay (#160/#77). This runs in the launch prelude AFTER the
    # login-shell profile loads, so it wins even if a profile re-exports the
    # var, and applies to both the copilot --acp launch and a diagnostic
    # --remote-cmd (not a human's interactive VS Code shell). Kept unconditional
    # -- even with --no-relay we never want an injected PAT relied on. Built via
    # _build_relay_env AFTER the relay token is minted so the scrub is never
    # clobbered by the relay exports.
    relay_token: str | None = None
    if not args.no_relay:
        # #122: the relay is owned/run by the agent-bridge daemon; this command
        # only forwards the port. If the relay isn't listening host-side, the
        # -R forward dead-ends and git auth over the tunnel silently returns
        # nothing (ADO 'could not read Username', GitHub/dotfiles 403). Probe it
        # up front and warn LOUDLY with remediation rather than failing silently.
        if not _relay_listening(relay_port):
            log.warning(
                "Credential relay not listening on 127.0.0.1:%d before connecting "
                "to %s -- git auth over the relay will fail (#122)",
                relay_port, args.name,
            )
            relay_launch.warn_if_relay_unavailable(
                relay_port, args.name, context="CodeSpace connect",
            )
        # Per-codespace relay token: the shared relay gates get-azure-token
        # (it also serves network-reachable containers), so the codespace path
        # must present its own secret for the official azure-auth-helper scope
        # broker. Minted/persisted host-side; injected over SSH as LC_* so it
        # survives the login shell into the relay client.
        from .relay_token import token_for

        relay_token = token_for(args.name)

    # Launch prelude: always scrub injected PATs (#160/#77); add the relay
    # exports only when the relay is in use. Built here (after the token mint)
    # so the PAT scrub can NEVER be clobbered by the relay exports.
    relay_env = _build_relay_env(
        relay_port,
        relay_token,
        use_relay=not args.no_relay,
        ado_host=getattr(config.credentials, "ado_host", None),
    )

    manager = ConnectionManager()
    relay_forward = None

    # The remote command is assembled inside _run() -- AFTER the relay is up and
    # any --stage-plugin payloads are staged -- so their on-CodeSpace
    # --plugin-dir paths can be folded into the copilot invocation. See
    # _finalize_remote_cmd below.
    diagnostic_remote_cmd = bool(args.remote_cmd and not args.stdio)
    minimal_provision = bool(getattr(args, "no_provision", False) or diagnostic_remote_cmd)
    overall_timeout = getattr(args, "connect_timeout", None)
    if overall_timeout is None and diagnostic_remote_cmd:
        overall_timeout = args.timeout

    tracker = ConnectTracker(
        session_id=args.name,
        emit_stderr=diagnostic_remote_cmd,
    )

    def _finalize_remote_cmd(plugin_dirs: list[str]) -> str | None:
        """Wrap args.remote_cmd in a login shell (see _build_launch_command).

        Folds ``--plugin-dir`` args in ONLY for the ``--stdio`` copilot launch,
        never for a plain diagnostic ``--remote-cmd`` (issue #152).
        """
        return _build_launch_command(
            args.remote_cmd,
            plugin_dirs,
            is_stdio=args.stdio,
            relay_env=relay_env,
            breadcrumb=breadcrumb_prelude(args.name),
        )

    async def _run() -> int:
        nonlocal relay_forward

        # Stage 3 (ssh-to-target): a Shutdown CodeSpace boots on connect, so be
        # patient -- retry to the boot deadline, then fail fast with a clear,
        # staged message (never an opaque provider death).
        tracker.started(ConnectStage.SSH_TO_TARGET, f"codespace={args.name}")
        deadline = time.monotonic() + _SSH_BOOT_TIMEOUT
        backoff = 3.0
        while True:
            try:
                connection = await manager.ensure_connected(
                    args.name, source, port_forwards,
                )
                if not args.no_relay and relay_forward is None:
                    relay_forward = await _start_supervised_relay(
                        args.name,
                        connection.config,
                        relay_port,
                        context="CodeSpace connect",
                        host_port_resolver=lambda: relay_launch.effective_relay_port(
                            config
                        ),
                    )
                tracker.reached(ConnectStage.SSH_TO_TARGET, f"codespace={args.name}")
                break
            except (ConnectionError, TimeoutError) as exc:
                if time.monotonic() + backoff >= deadline:
                    tracker.failed(
                        ConnectStage.SSH_TO_TARGET,
                        f"Failed to reach CodeSpace {args.name}: {exc}",
                        retryable=True,
                    )
                    print(
                        f"[FAIL] Could not establish SSH to CodeSpace "
                        f"'{args.name}' within {_SSH_BOOT_TIMEOUT:.0f}s "
                        f"(stage 3/ssh-to-target): {exc}",
                        file=sys.stderr,
                    )
                    return 1
                log.info(
                    "CodeSpace %s not ready (booting?): %s -- retry in %.0fs",
                    args.name, exc, backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 1.5, 20.0)

        # Cross-harness in-CodeSpace lockfile fence (git-ref-resource-leases
        # Phase 4). The repo-ref L2 store is same-harness-scoped by construction
        # -- a *foreign* harness writes to a different store and is invisible to
        # it. Fence that seam on the resource itself: read ~/.agent-lease inside
        # the CodeSpace; a fresh marker from a foreign harness refuses the
        # connect (unless --force-claim), then we drop our own marker. Needs only
        # the raw SSH channel (a cat/mv), so it runs before relay provisioning
        # and independent of --no-relay. Degrade-safe: any read/write/identity
        # failure proceeds.
        if not await _check_cross_harness_fence(
            manager, args.name, fence_holder_ref,
            force=getattr(args, "force_claim", False),
        ):
            await manager.disconnect(args.name)
            return _BUSY_EXIT

        if not args.no_relay:
            # Stage 4 (target-auth-env): deploy the CodeSpace-side relay helpers
            # so remote auth resolves over the tunnel. Auth verification later
            # in this same stage catches missing local credentials up front.
            tracker.started(ConnectStage.TARGET_AUTH_ENV, "credential relay")
            await _provision_relay_helpers(manager, args.name)

        cs_plugin_dirs: list[str] = []
        if minimal_provision:
            tracker.started(
                ConnectStage.TARGET_BINSTUB,
                "heavy provisioning skipped (minimal diagnostic path)",
            )
            tracker.reached(
                ConnectStage.TARGET_BINSTUB,
                "heavy provisioning skipped",
            )
        else:
            tracker.started(ConnectStage.TARGET_BINSTUB, "provisioning")
            # Ensure the account dotfiles repo is cloned + current (universal
            # bootstrap, gated on `dotfiles_repo`). Heals a CodeSpace whose
            # post-start dotfiles clone hasn't run (e.g. first agent-bridge
            # connect) and syncs it forward on reconnect. Needs the relay up for
            # git auth.
            if not args.no_relay:
                await _provision_dotfiles(manager, args.name, config)
                await _provision_harness(manager, args.name, config)

            # Register CodeSpace-scoped plugins (the CodeSpace-scoped axis) via
            # BOTH lanes: (1) the CodeSpace user settings so they load for
            # interactive / `copilot -p` launches (incl. a human opening the
            # CodeSpace in VS Code, where there's no agent-bridge to pass
            # --plugin-dir); and (2) their on-CodeSpace payload dirs, folded into
            # the acp launch below as --plugin-dir -- because `copilot --acp`
            # (the dispatch) ignores enabledPlugins and only surfaces plugin
            # skills via --plugin-dir. Best-effort; needs the relay up for the
            # payload pre-install.
            if not args.no_relay:
                cs_plugin_dirs = await _register_codespace_plugins(
                    manager, args.name, getattr(args, "repo", None), config,
                )

            # Run repo-declared provision hooks (by-convention extras from the
            # adopted repo's codespaces.yaml). Best-effort, idempotent.
            await _provision_repo_hooks(
                manager, args.name, config, getattr(args, "repo", None),
            )
            tracker.reached(ConnectStage.TARGET_BINSTUB)

        # Verify the host has local auth for every domain the session's git
        # remotes use -- the workspace (ADO) AND the dotfiles repo (GitHub).
        # Surfaces missing auth up front rather than letting it fail mid-fetch.
        # The ADO REST bearer preflight inside may abort the connect (#77) when
        # enforcement is on and the host cannot mint the bearer.
        if not args.no_relay:
            from .auth_preflight import AdoRestAuthError

            try:
                await _verify_remote_auth(manager, args.name, config)
            except AdoRestAuthError as exc:
                print(f"[ERROR] {exc}", file=sys.stderr)
                await manager.disconnect(args.name)
                return _ADO_AUTH_EXIT
            tracker.reached(ConnectStage.TARGET_AUTH_ENV)
            if _should_warm_auth_cache(args, minimal_provision):
                await _warm_remote_auth_cache(
                    manager, args.name, config, relay_env=relay_env,
                )

        # Stage related-repo plugins (repo-targeted lane) onto the CodeSpace and
        # fold their --plugin-dir paths into the launch. Best-effort: a staging
        # failure drops that plugin but never blocks the dispatch.
        plugin_dirs: list[str] = list(cs_plugin_dirs)
        if not args.no_relay and not minimal_provision:
            plugin_dirs += await _stage_plugins(
                manager, args.name, getattr(args, "stage_plugins", []),
            )
        remote_cmd = _finalize_remote_cmd(plugin_dirs)

        if args.stdio and remote_cmd:
            # #111: a fresh CodeSpace can ship copilot's loader stub without its
            # platform binary, so `copilot --acp` dies at LAUNCH_ACP ("Connection
            # closed"). Verify + self-repair (public-npm reinstall) up front.
            await _preflight_copilot_platform(manager, args.name)
            # Structured stdio mode for agent-bridge
            tracker.started(ConnectStage.LAUNCH_ACP, "stdio channel")
            proc = await manager.open_stdio_channel(args.name, remote_cmd)
            # Pipe through to our own stdio
            await _pipe_stdio(proc)
            tracker.reached(ConnectStage.LAUNCH_ACP)
            return proc.returncode if proc.returncode is not None else 1

        if remote_cmd:
            # Non-interactive command execution
            tracker.started(ConnectStage.LAUNCH_ACP, "remote command")
            result = await manager.exec_command(
                args.name, remote_cmd, timeout=args.timeout
            )
            tracker.reached(ConnectStage.LAUNCH_ACP)
            return _emit_remote_cmd_result(result, args.timeout)

        # Interactive SSH -- fall through to gh codespace ssh
        await manager.disconnect(args.name)
        return _interactive_ssh(
            args.name,
            port_forwards,
            relay_port=relay_port if not args.no_relay else None,
            relay_token=relay_token,
        )

    # Serialize SSH access to this CodeSpace across processes. All access funnels
    # through one credential-relay reverse-forward (one relay port per host), so
    # a second concurrent connection collides on that port and can collapse a
    # live agent-bridge dispatch. Hold a per-target lock for the operation's
    # lifetime; reject a busy target (or take over with --force).
    op = "stdio" if args.stdio else ("remote-cmd" if args.remote_cmd else "interactive")
    target_lock = TargetLock(args.name, op=op)
    try:
        target_lock.acquire(force=getattr(args, "force", False))
    except TargetBusyError as busy:
        print(busy.user_message(), file=sys.stderr)
        return _BUSY_EXIT

    async def _run_with_cleanup() -> int:
        try:
            if overall_timeout is not None and overall_timeout > 0:
                return await asyncio.wait_for(_run(), timeout=overall_timeout)
            return await _run()
        except TimeoutError:
            print(
                f"[FAIL] SSH operation for CodeSpace '{args.name}' exceeded "
                f"{overall_timeout:g}s; disconnected and cleaned up.",
                file=sys.stderr,
            )
            return 124
        except asyncio.CancelledError:
            print(
                f"[CANCEL] SSH operation for CodeSpace '{args.name}' was "
                "interrupted; disconnecting and cleaning up.",
                file=sys.stderr,
            )
            raise
        finally:
            # Settle the CodeSpace obligation on the borrowing worktree if its
            # work is at-rest (resource-obligation-settlement Ph3b-wiring/2).
            # Runs while the SSH channel is still up (before disconnect), so the
            # read-only cleanliness probe can execute. Best-effort + degrade-safe:
            # un-probeable / dirty / no holder-ref -> the obligation stays active
            # (never settled blind).
            if fence_holder_ref:
                await _settle_codespace_on_disconnect(
                    manager, args.name, fence_holder_ref,
                )
            if relay_forward is not None:
                await relay_forward.stop()
            await manager.disconnect(args.name)

    try:
        return asyncio.run(_run_with_cleanup())
    finally:
        target_lock.release()


async def _settle_codespace_on_disconnect(
    manager, name: str, holder_ref: str | None,
) -> bool:
    """Probe cleanliness + settle the CodeSpace obligation on disconnect.

    The incremental-settlement hook for a borrowed CodeSpace
    (resource-obligation-settlement Ph3b-wiring/2): run the read-only
    ``cleanliness`` probe over the still-open SSH channel and, on a *definitive*
    at-rest verdict (git clean AND no in-flight dispatch), settle the borrowing
    worktree's claim to ``at-rest`` via ``coordination.settle_obligation``
    (``agent-worktrees claims settle <name> --owner-ref <holder_ref>``).

    ``in_flight`` is host-side knowledge: at disconnect this connection is the
    one that was driving the box, and the exclusive claim precludes a concurrent
    driver, so it is ``False`` here. Fully best-effort + degrade-safe (any probe
    / settle failure leaves the obligation ``active`` -- never settled blind,
    never raises). Returns ``True`` only on a confirmed settle.
    """
    from . import cleanliness, coordination
    try:
        gc = await cleanliness.probe_cleanliness(manager, name)
    except Exception:
        return False
    if not cleanliness.at_rest(gc, in_flight=False):
        log.info(
            "CodeSpace %s not at-rest on disconnect (known=%s dirty=%s ahead=%s "
            "unpushed_branches=%s) -- obligation stays active",
            name, gc.known, gc.dirty, gc.ahead, gc.unpushed_branches,
        )
        return False
    if coordination.settle_obligation(name, holder_ref):
        log.info("Settled CodeSpace %s obligation on %s -> at-rest",
                 name, holder_ref)
        return True
    return False


async def _stage_plugins(manager, name: str, sources: list[str]) -> list[str]:
    """Stage related-repo plugin payloads onto the CodeSpace over SSH.

    For each ``name@marketplace`` source, tar+base64 the host's installed
    payload and extract it into a per-plugin dir on the CodeSpace, returning the
    remote ``--plugin-dir`` paths. Egress-free (no marketplace fetch on the
    CodeSpace). Best-effort per source: a missing host payload or a failed
    transfer is logged and skipped, never raised.
    """
    if not sources:
        return []
    from .plugin_staging import build_stage_command, dest_dir, host_payload_dir

    dirs: list[str] = []
    for source in sources:
        payload = host_payload_dir(source)
        if payload is None:
            log.warning(
                "Skipping plugin stage for %s: no host payload under "
                "~/.copilot/installed-plugins (is it installed on the host?)",
                source,
            )
            continue
        dest = dest_dir(source)
        try:
            command = build_stage_command(payload, dest)
            result = await manager.exec_command(name, command, timeout=60.0)
            if result.exit_code == 0:
                dirs.append(dest)
                log.info("Staged plugin %s -> %s on %s", source, dest, name)
            else:
                log.warning(
                    "Staging %s on %s exited %s: %s",
                    source, name, result.exit_code, result.stderr.strip(),
                )
        except Exception as exc:
            log.warning("Staging %s on %s failed: %s", source, name, exc)
    return dirs


async def _check_cross_harness_fence(
    manager, name: str, holder_ref: str | None, *, force: bool = False,
) -> bool:
    """Cross-harness in-CodeSpace lockfile fence (git-ref-resource-leases Ph4).

    Reads the ``~/.agent-lease`` marker inside the CodeSpace and decides whether
    to proceed. A **fresh** marker from a **foreign** harness (a different lease
    store origin) is a genuine cross-harness collision the same-harness-scoped
    ref-CAS store cannot see, so it **refuses** the connect (unless ``force``);
    an absent / stale / same-harness marker proceeds, after which we drop our
    own marker (best-effort). All the harness/marker logic lives in ``fence.py``
    (pure) + ``coordination.harness_identity`` (the identity shell-out); this is
    the thin SSH-executing wrapper.

    Returns ``True`` to **proceed**, ``False`` to **refuse**. Degrade-safe: no
    resolvable harness identity, an unreadable marker, or any exec failure all
    **proceed** -- the fence only *adds* a cross-harness signal, it never becomes
    a new hard dependency. Disable entirely with
    ``AGENT_CODESPACES_DISABLE_FENCE``.
    """
    if os.environ.get("AGENT_CODESPACES_DISABLE_FENCE"):
        return True

    from . import coordination, fence

    try:
        local_harness = coordination.harness_identity()
    except Exception as exc:
        log.debug("Cross-harness fence identity on %s unresolved: %s", name, exc)
        local_harness = None
    if not local_harness:
        # No identity -> we cannot tell foreign from own; fence off (proceed).
        return True

    try:
        read = await manager.exec_command(
            name, fence.read_marker_command(), timeout=30.0,
        )
        text = read.stdout if read.exit_code == 0 else ""
    except Exception as exc:
        log.warning(
            "Cross-harness fence read on %s failed: %s -- proceeding", name, exc,
        )
        return True

    decision = fence.evaluate(local_harness, fence.FenceMarker.parse(text))
    if decision.refuse:
        if not force:
            print(
                f"[BUSY] CodeSpace '{name}' is held by a DIFFERENT harness "
                f"(fence marker holder '{decision.foreign_holder or '?'}', "
                f"harness '{decision.foreign_harness}').\n"
                f"       Our repo-ref lease store cannot arbitrate across "
                f"harnesses, so the in-CodeSpace lockfile fences it. Options:\n"
                f"       - let the other harness finish, or use a different "
                f"CodeSpace; or\n"
                f"       - take over with --force-claim (its in-flight work may "
                f"be disrupted).",
                file=sys.stderr,
            )
            return False
        log.warning(
            "Forced connect on %s over a fresh foreign-harness marker "
            "(holder '%s', harness '%s')",
            name, decision.foreign_holder or "?", decision.foreign_harness,
        )

    our = fence.FenceMarker(
        harness=local_harness, holder=holder_ref or "", written_at=time.time(),
    )
    try:
        await manager.exec_command(
            name, fence.write_marker_command(our), timeout=30.0,
        )
    except Exception as exc:
        log.warning("Cross-harness fence write on %s failed: %s", name, exc)
    return True


async def _provision_relay_helpers(manager, name: str) -> None:
    """Deploy the CodeSpace-side relay helper scripts over SSH.

    Installs ``ado-auth-helper-relay`` and the smart ``ado-auth-helper``
    wrapper into the CodeSpace so ADO auth resolves over the credential
    relay tunnel. Idempotent and best-effort: logs a warning on failure
    but never raises, since the SSH command itself should still proceed.
    """
    from .codespace_assets import build_provision_command

    try:
        command = build_provision_command()
        result = await manager.exec_command(name, command, timeout=30.0)
        if result.exit_code == 0:
            log.debug("Relay helpers provisioned on %s", name)
        else:
            log.warning(
                "Relay helper provisioning on %s exited %s: %s",
                name, result.exit_code, result.stderr.strip(),
            )
    except Exception as exc:
        log.warning("Relay helper provisioning on %s failed: %s", name, exc)


async def _provision_dotfiles(manager, name: str, config) -> None:
    """Ensure the configured dotfiles repo is present + current on a CodeSpace.

    Universal bootstrap for every CodeSpace when ``defaults.dotfiles_repo`` is
    set: clone-if-absent (+ run ``install.sh``) and sync-forward on the default
    branch (re-installing only when ``HEAD`` moved); a checkout parked on a
    feature branch / left dirty is never touched. This used to live in a per-repo
    ``on_create`` hook; making it built-in means a CodeSpace created outside
    agent-codespaces (e.g. via the GitHub UI / VS Code, where the post-start
    dotfiles clone may not have completed) is healed on the first connect.
    Best-effort and idempotent: logs a warning on failure but never raises.
    """
    if not config.dotfiles_repo:
        return

    from .provision import build_dotfiles_command
    from .relay_launch import effective_relay_port

    try:
        command = build_dotfiles_command(
            config.dotfiles_repo, effective_relay_port(config),
        )
        # Run under a LOGIN shell: the dotfiles clone authenticates to GitHub via
        # the CodeSpace's own credential helper (gitcredential_github.sh), which
        # needs the platform env (GITHUB_TOKEN, profile.d) that only a login
        # shell loads. A non-login `exec_command` would clone unauthenticated and
        # fail silently. Mirrors how `_verify_remote_auth` and the remote_cmd
        # path wrap their commands.
        login_command = f"bash -l -c {shlex.quote(command)}"
        # Clone + install.sh can run long on a first connect; be generous.
        result = await manager.exec_command(name, login_command, timeout=900.0)
        if result.exit_code == 0:
            log.debug("Dotfiles provisioned on %s", name)
        else:
            log.warning(
                "Dotfiles provisioning on %s exited %s: %s",
                name, result.exit_code, result.stderr.strip(),
            )
    except Exception as exc:
        log.warning("Dotfiles provisioning on %s failed: %s", name, exc)


async def _provision_harness(manager, name: str, config) -> None:
    """Ensure the configured control-plane *harness* checkout is present +
    current on a venue at ``/workspaces/<basename(harness_repo)>``.

    The harness analogue of :func:`_provision_dotfiles`, kept SEPARATE from the
    dotfiles shim. **Opt-in:** only runs when ``defaults.harness_repo`` is set;
    unset by default, so by default NO harness is placed on the venue and the
    local control-plane agent owns effort / vision updates. Clone-if-absent +
    sync-forward on the default branch (no ``install.sh`` -- the harness is
    referenced in place, not installed) at the **standard** ``/workspaces/<repo>``
    path (#174); a parked feature branch / dirty tree is never touched.
    Best-effort and idempotent: logs a warning, never raises.
    """
    if not config.harness_repo:
        return

    from .provision import build_harness_command
    from .relay_launch import effective_relay_port

    try:
        command = build_harness_command(
            config.harness_repo, effective_relay_port(config),
        )
        # Login shell, same rationale as the dotfiles clone: the harness clone
        # authenticates to GitHub via the CodeSpace's own credential helper,
        # which needs the platform env only a login shell loads.
        login_command = f"bash -l -c {shlex.quote(command)}"
        result = await manager.exec_command(name, login_command, timeout=900.0)
        if result.exit_code == 0:
            log.debug("Harness provisioned on %s", name)
        else:
            log.warning(
                "Harness provisioning on %s exited %s: %s",
                name, result.exit_code, result.stderr.strip(),
            )
    except Exception as exc:
        log.warning("Harness provisioning on %s failed: %s", name, exc)


async def _register_codespace_plugins(
    manager, name: str, repo: str | None, config
) -> list[str]:
    """Register CodeSpace-scoped plugins into the CodeSpace + return their dirs.

    The **CodeSpace-scoped** plugin axis, delivered via BOTH lanes:
    - **user settings (interactive lane):** resolves the harness's
      ``codespacePlugins`` for this CodeSpace's workspace repo -- both those
      swept from installed harness plugins AND the operator-declared
      ``codespaces.yaml`` ``codespace_plugins`` list
      (:func:`codespace_plugins.resolve_codespace_plugins`) -- and writes them
      into the CodeSpace's user ``~/.copilot/settings.json`` + pre-installs the
      payloads (see :mod:`codespace_register`). Honored by interactive /
      ``copilot -p``.
    - **``--plugin-dir`` (dispatch lane):** ``copilot --acp`` (the agent-bridge
      dispatch) does **NOT** honor ``enabledPlugins`` -- only ``--plugin-dir``
      surfaces plugin skills under ``--acp``. So this returns the on-CodeSpace
      payload dirs (the ones the register step just installed) for the caller to
      fold into the acp launch as ``--plugin-dir`` args.

    Best-effort and idempotent: logs a warning on failure but never raises, and
    returns ``[]`` when there is nothing to register.
    """
    from .codespace_plugins import (
        parse_operator_plugins,
        plugin_names_from_enabled,
        resolve_codespace_plugins,
    )
    from .codespace_register import build_register_command, codespace_plugin_dirs
    from .config import repo_copilot_settings

    try:
        # The dispatch path doesn't pass --repo, so resolve the CodeSpace's
        # workspace repo ourselves (needed to apply repo-scoped codespacePlugins
        # entries; global entries apply regardless). A single `gh` lookup, only
        # paid when we don't already know the repo.
        if repo is None:
            repo = _lookup_codespace_repo(name)

        # Repo-scoped config is canonical (NOT the user ~/.copilot/settings.json):
        # the marketplace registry + harness-plugin enablement come from the
        # adopted control-plane repo's .github/copilot/settings.json. Keeps
        # internal marketplace definitions out of user state, and lets the register
        # step carry whichever marketplaces the selected plugins reference.
        repo_settings = repo_copilot_settings(getattr(config, "source_paths", []) or [])
        enabled_names = plugin_names_from_enabled(repo_settings.get("enabledPlugins"))

        # Merge the operator-declared globals (codespaces.yaml `codespace_plugins`)
        # with the set swept from installed harness plugins.
        operator_specs = parse_operator_plugins(
            getattr(config, "codespace_plugins", []) or []
        )
        specs = resolve_codespace_plugins(
            repo, extra_specs=operator_specs, enabled_names=enabled_names
        )
        command = build_register_command(
            specs, marketplaces=repo_settings.get("extraKnownMarketplaces") or {}
        )
        if not command:
            return []

        wrapped = f"bash -l -c {shlex.quote(command)}"
        # Settings merge is quick; the pre-install (`copilot plugin install`)
        # clones the marketplace over the relay, so allow a generous window.
        result = await manager.exec_command(name, wrapped, timeout=240.0)
        if result.exit_code == 0:
            log.info(
                "Registered %d CodeSpace-scoped plugin(s) on %s: %s",
                len(specs), name, ", ".join(s.source for s in specs),
            )
            # Fold the just-installed payloads into the --acp launch: the
            # dispatch ignores enabledPlugins, so --plugin-dir is required.
            return codespace_plugin_dirs(specs)
        log.warning(
            "CodeSpace plugin registration on %s exited %s: %s",
            name, result.exit_code, result.stderr.strip(),
        )
    except Exception as exc:
        log.warning("CodeSpace plugin registration on %s failed: %s", name, exc)
    return []


async def _verify_remote_auth(manager, name: str, config) -> None:
    """Verify host-side auth for the CodeSpace's git remote domains.

    Lists the git remotes of both the workspace/product checkout and the
    dotfiles checkout, extracts their domains, and probes the local credential
    store (the same source the relay uses) for each. When ``dotfiles_repo`` is
    configured, its host (e.g. github.com) is verified explicitly too -- even
    on a first connect before the dotfiles clone exists. Missing domains are
    reported as a warning so the user can fix auth (``az login`` / GCM sign-in)
    before work begins. Best-effort for the git-credential probe: it never
    raises. It DOES, however, run the ADO REST bearer preflight
    (:func:`_preflight_ado_rest_token`) for ADO workspaces, which raises
    :class:`~agent_codespaces.auth_preflight.AdoRestAuthError` when the host
    cannot mint the bearer and ``enforce_ado_rest_login`` is on -- the caller
    catches it to abort the connect cleanly.
    """
    from .auth_preflight import host_from_url, verify_remote_auth

    async def _run_remote(cmd: str) -> str:
        wrapped = f"bash -l -c {shlex.quote(cmd)}"
        result = await manager.exec_command(name, wrapped, timeout=30.0)
        return result.stdout or ""

    # Guarantee the dotfiles repo's host is checked even if its checkout isn't
    # present yet (account dotfiles are always GitHub-hosted).
    extra_hosts: list[str] = []
    if config.dotfiles_repo:
        host = host_from_url(f"https://github.com/{config.dotfiles_repo}")
        if host:
            extra_hosts.append(host)

    try:
        hosts, missing = await verify_remote_auth(
            _run_remote, extra_hosts=extra_hosts,
        )
    except Exception as exc:
        log.debug("Remote auth verification on %s failed: %s", name, exc)
        return

    if not hosts:
        return

    if missing:
        msg = (
            f"Missing local auth for remote domain(s): {', '.join(missing)}. "
            f"Git operations against these in CodeSpace '{name}' will fail "
            f"fast over the relay. Sign in on the host "
            f"(az login / Git Credential Manager) for each domain."
        )
        log.warning(msg)
        print(f"[WARN] {msg}", file=sys.stderr)
    else:
        log.info(
            "Remote auth verified for %s: %s", name, ", ".join(hosts),
        )

    # #77: ADO REST bearer preflight for ADO workspaces. Git-credential auth
    # (above) covers git ops; PR/REST tooling additionally needs an AAD *bearer*
    # the relay mints from the host az identity. Only runs when the session
    # touches an ADO host, so non-ADO CodeSpaces pay no cost.
    if any(_is_ado_host(h) for h in hosts):
        await _preflight_ado_rest_token(name, config)


def _is_ado_host(host: str) -> bool:
    """Whether ``host`` is an Azure DevOps host (ADO REST bearer applies)."""
    h = (host or "").lower()
    return h.endswith(".visualstudio.com") or h == "dev.azure.com"


async def _preflight_ado_rest_token(name: str, config) -> None:
    """#77: ensure the host can mint an ADO REST bearer for dispatched agents.

    A dispatched agent's ``ado-auth-helper get-access-token`` is served by the
    relay from the host az identity (get-azure-token). Verify the host can mint
    it; if not, enforce ``az login`` on the host. When
    ``credentials.enforce_ado_rest_login`` is set, a login that can't complete
    ABORTS the connect (raises); otherwise it is a loud warning and the connect
    proceeds. The relay itself always logs a loud not-logged-in error too.
    """
    from .auth_preflight import (
        ADO_REST_RESOURCE,
        AdoRestAuthError,
        enforce_host_ado_login,
        host_can_mint_ado_token,
    )

    try:
        if await host_can_mint_ado_token():
            log.info("ADO REST bearer available on host for %s", name)
            return
    except Exception as exc:
        log.debug("ADO REST token preflight probe failed: %s", exc)
        return

    enforce = getattr(config.credentials, "enforce_ado_rest_login", True)
    ok = False
    try:
        ok = await enforce_host_ado_login()
    except Exception:
        log.debug("ADO REST login enforcement raised", exc_info=True)

    if ok:
        log.info(
            "ADO REST bearer now available on host after az login (%s)", name
        )
        return

    msg = (
        f"Host cannot mint an ADO REST bearer: `ado-auth-helper "
        f"get-access-token` on CodeSpace '{name}' will fail. Sign in on the "
        f"HOST:  az login --scope {ADO_REST_RESOURCE}/.default  (#77)."
    )
    if enforce:
        raise AdoRestAuthError(msg)
    log.warning(msg)
    print(f"[WARN] {msg}", file=sys.stderr)


def _should_warm_auth_cache(args: argparse.Namespace, minimal_provision: bool) -> bool:
    """Whether this SSH connect should warm the CodeSpace auth cache."""
    _ = minimal_provision  # the default gate is transport-shaped, not provision-shaped
    explicit = getattr(args, "auth_cache_warmup", None)
    if explicit is not None:
        return bool(explicit)
    # Keep the minimal diagnostic --remote-cmd path fast unless explicitly
    # requested. Dispatch (--stdio) and interactive connects warm by default.
    return not (getattr(args, "remote_cmd", None) and not getattr(args, "stdio", False))


async def _warm_remote_auth_cache(
    manager,
    name: str,
    config,
    *,
    relay_env: str,
    timeout: float = 20.0,
) -> None:
    """Best-effort connect-time warm-up for the on-CodeSpace auth cache.

    Warms the cache entries that a headless agent most often needs after the SSH
    reverse-forward drops: git credentials for the workspace/dotfiles remote
    hosts plus the ADO REST/feed bare-token helpers. Failures are debug-only and
    never block the connect.
    """
    from .auth_preflight import REMOTE_LIST_COMMAND, host_from_url, parse_remote_hosts

    async def _run_remote(cmd: str, *, command_timeout: float) -> str:
        wrapped = f"bash -l -c {shlex.quote(cmd)}"
        result = await manager.exec_command(name, wrapped, timeout=command_timeout)
        if getattr(result, "exit_code", 1) != 0:
            return ""
        return getattr(result, "stdout", "") or ""

    hosts: list[str] = []
    try:
        remote_output = await _run_remote(REMOTE_LIST_COMMAND, command_timeout=10.0)
        hosts.extend(parse_remote_hosts(remote_output))
    except Exception as exc:
        log.debug("Auth-cache warm-up remote host discovery on %s failed: %s", name, exc)

    if config.dotfiles_repo:
        dotfiles_host = host_from_url(f"https://github.com/{config.dotfiles_repo}")
        if dotfiles_host:
            hosts.append(dotfiles_host)

    deduped_hosts = list(dict.fromkeys(h for h in hosts if h))
    commands = ["set +e"]
    for host in deduped_hosts:
        commands.append(
            "printf '%s\\n%s\\n\\n' "
            f"{shlex.quote('protocol=https')} {shlex.quote('host=' + host)} "
            "| ado-auth-helper get >/dev/null 2>/dev/null || true"
        )
    commands.extend([
        "azure-auth-helper get-access-token >/dev/null 2>/dev/null || true",
        "ado-auth-helper get-access-token >/dev/null 2>/dev/null || true",
    ])
    command = relay_env + " " + "; ".join(commands)

    try:
        await _run_remote(command, command_timeout=timeout)
        log.debug(
            "Auth-cache warm-up attempted on %s for hosts: %s",
            name, ", ".join(deduped_hosts) if deduped_hosts else "(none)",
        )
    except Exception as exc:
        log.debug("Auth-cache warm-up on %s failed: %s", name, exc)


def _lookup_codespace_repo(name: str) -> str | None:
    """Best-effort lookup of a CodeSpace's repository (owner/name)."""
    try:
        from .lifecycle import list_codespaces

        for cs in list_codespaces():
            if cs.name == name:
                return cs.repository
    except Exception as exc:
        log.debug("Could not resolve repo for %s: %s", name, exc)
    return None


async def _provision_repo_hooks(
    manager, name: str, config, repo: str | None, *,
    include_on_create: bool = False,
) -> None:
    """Run repo-declared provision hooks for a CodeSpace over SSH.

    Applies the adopted repo's ``provision`` block (global + per-repo,
    selected by the CodeSpace's repository) from ``codespaces.yaml``.
    The repo is taken from ``--repo`` when provided (hot path) and only
    looked up when per-repo hooks actually exist. When
    ``include_on_create`` is set, ``on_create`` commands run too (used
    once during ``agent-codespaces create``). Best-effort and idempotent.
    """
    from .provision import build_provision_command

    try:
        # Only pay for a repo lookup when per-repo hooks are declared.
        if repo is None and any(rc.provision for rc in config.repos.values()):
            repo = _lookup_codespace_repo(name)

        provision = config.provision_for_repo(repo)
        command = build_provision_command(
            provision, include_on_create=include_on_create,
        )
        if not command:
            return

        # on_create hooks (e.g. install scripts) can run long; give them
        # a generous timeout. on_connect-only hooks stay snappy.
        timeout = 900.0 if include_on_create else 30.0
        result = await manager.exec_command(name, command, timeout=timeout)
        if result.exit_code == 0:
            log.debug("Repo provision hooks applied on %s", name)
        else:
            log.warning(
                "Repo provision hooks on %s exited %s: %s",
                name, result.exit_code, result.stderr.strip(),
            )
    except Exception as exc:
        log.warning("Repo provision hooks on %s failed: %s", name, exc)


def _emit_remote_cmd_result(result, timeout: float) -> int:  # noqa: ANN001
    """Print a remote command's output and return its exit code.

    Surfaces partial output and a loud, cause-hinting error when the command
    was terminated for exceeding the timeout, instead of returning a silent
    ``-1`` with swallowed output (#47). The remote command runs without a PTY
    (``-T``), so the usual causes of a hang are a backgrounded process that
    keeps the stdout/stderr channel open, or a command waiting for input the
    session cannot provide (e.g. a ``sudo`` password prompt).
    """
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    if result.timed_out:
        print(
            f"[FAIL] Remote command did not finish within {timeout:.0f}s and "
            f"was terminated (no PTY).\n"
            f"       - Backgrounded work must fully detach its stdio, e.g. "
            f"`nohup <cmd> >/tmp/out 2>&1 </dev/null & disown`.\n"
            f"       - sudo cannot prompt here; use passwordless `sudo -n`.\n"
            f"       - For a legitimately long command, raise `--timeout <secs>`.",
            file=sys.stderr,
        )
        return 124
    return result.exit_code


async def _pipe_stdio(proc) -> None:
    """Pipe a subprocess's stdio through to our own stdin/stdout.

    Uses threads for the stdin/stdout relay instead of asyncio pipe
    transports, because Windows ProactorEventLoop cannot wire
    stdin/stdout via ``connect_read_pipe`` (raises
    ``OSError: [WinError 6] The handle is invalid``).

    Threading is simple and works on all platforms.
    """
    import threading

    def _forward_in() -> None:
        """Read from our stdin, write to subprocess stdin (blocking)."""
        try:
            stdin_fd = sys.stdin.buffer.fileno()
            while True:
                # os.read returns as soon as any data is available (no
                # buffering), unlike sys.stdin.buffer.read(n) which can
                # block until n bytes arrive on a pipe.
                data = os.read(stdin_fd, 4096)
                if not data:
                    break
                if proc.stdin:
                    proc.stdin.write(data)
                    # Block until drained -- never time out. A drain timeout
                    # here would close the agent's stdin under backpressure and
                    # wedge the ACP channel (see _forward_out for the symmetric
                    # stdout hazard, #46.6).
                    asyncio.run_coroutine_threadsafe(
                        proc.stdin.drain(), loop
                    ).result()
        except (OSError, ValueError):
            pass
        finally:
            if proc.stdin:
                proc.stdin.close()

    def _forward_out() -> None:
        """Read from subprocess stdout, write to our stdout (blocking).

        Blocks indefinitely on each read -- it must NEVER give up on a merely
        *quiet* channel. A long, output-buffered remote tool call (a multi-
        minute ``rush build``/test, or the agent thinking) emits no ACP stdout
        for well over a minute; a read timeout here would terminate this pump
        thread mid-dispatch and silently collapse the session. On Python 3.11+
        the prior ``fut.result(timeout=30)`` made this worse: the resulting
        ``TimeoutError`` is an ``OSError`` subclass, so it was swallowed by the
        ``except`` below and the relay exited cleanly after 30s of silence --
        the root cause of the ~10-15 min dispatch collapse (#46.6). A genuinely
        dead connection still terminates the relay correctly: SSH's
        ``ServerAliveInterval`` kills the ssh process, closing stdout (EOF),
        which returns empty and breaks the loop.
        """
        try:
            while True:
                # read1 is not available on asyncio streams; use the
                # loop to schedule the async read from this thread.
                fut = asyncio.run_coroutine_threadsafe(
                    proc.stdout.read(4096), loop
                )
                data = fut.result()  # block until data or EOF -- no timeout
                if not data:
                    break
                sys.stdout.buffer.write(data)
                sys.stdout.buffer.flush()
        except (OSError, ValueError):
            pass

    loop = asyncio.get_event_loop()

    in_thread = threading.Thread(target=_forward_in, daemon=True)
    out_thread = threading.Thread(target=_forward_out, daemon=True)
    in_thread.start()
    out_thread.start()

    await proc.wait()

    # Give output thread a moment to flush remaining data
    out_thread.join(timeout=2)


def _interactive_ssh(
    codespace_name: str,
    port_forwards: list[str],
    relay_port: int | None = None,
    relay_token: str | None = None,
) -> int:
    """Fall back to ``gh codespace ssh`` for interactive sessions."""
    import subprocess as sp

    from . import gh_account, lifecycle

    # Pin gh to the account that owns this CodeSpace (multi-account #195/#190),
    # then overlay the relay vars. Start from the account env so GH_TOKEN is set
    # even when no relay vars are present.
    account = lifecycle.account_for_codespace(codespace_name)
    env = gh_account.env_for_account(account) if account else None
    if relay_port is not None:
        env = {
            **(env if env is not None else os.environ),
            "LC_GIT_CREDENTIAL_RELAY": str(relay_port),
            "GIT_TERMINAL_PROMPT": "0",
        }
        if relay_token:
            env["LC_GIT_CREDENTIAL_RELAY_TOKEN"] = relay_token

    args = ["gh", "codespace", "ssh", "-c", codespace_name]
    for fwd in port_forwards:
        # Split "-R port:host:port" into SSH option
        args.extend(["--", fwd])

    return sp.call(args, env=env)


def _cmd_list(args: argparse.Namespace) -> int:
    """List active CodeSpaces (with any prune-lifecycle eligibility marker)."""
    from .status import list_status

    codespaces = list_codespaces()
    marks = {s.codespace: s.state for s in list_status()}

    if args.json_output:
        data = [
            {
                "name": cs.name,
                "display_name": cs.display_name,
                "repository": cs.repository,
                "branch": cs.branch,
                "state": cs.state,
                "machine": cs.machine,
                "account": cs.account,
                "eligibility": marks.get(cs.name, "active"),
            }
            for cs in codespaces
        ]
        print(json.dumps(data, indent=2))
        return 0

    if not codespaces:
        print("No active CodeSpaces")
        return 0

    # Table output
    print(f"{'Name':<40} {'Repo':<35} {'Branch':<20} {'State':<12} {'Elig':<10}")
    print("-" * 118)
    for cs in codespaces:
        elig = marks.get(cs.name, "")
        print(f"{cs.name:<40} {cs.repository:<35} {cs.branch:<20} "
              f"{cs.state:<12} {elig:<10}")

    return 0


def _cmd_config(args: argparse.Namespace) -> int:
    """Configuration subcommands."""
    if args.config_command == "adopt":
        return _config_adopt()
    if args.config_command == "show":
        return _config_show()
    if args.config_command == "validate":
        return _config_validate()
    if args.config_command == "migrate":
        return _config_migrate_file()
    if args.config_command == "init":
        return _config_init(
            from_codespace=args.from_codespace,
            force=args.force,
            also_adopt=args.adopt,
        )
    print(
        "Usage: agent-codespaces config {init|adopt|show|validate|migrate}",
        file=sys.stderr,
    )
    return 1


# Verbs that actually consume the top-level ``--project``: their cwd/repo-root
# discovery (``_resolve_repo_root`` -> ``codespaces.yaml``, exercised by
# ``config init``/``config adopt``) targets the project's checkout. Every other
# verb is name/CodeSpace-addressed or reads the merged adopted-repo config, so it
# ignores ``--project`` -- see ``_guard_project_scope``.
_PROJECT_CONSUMING_VERBS = frozenset({"config"})


def _guard_project_scope(parser: argparse.ArgumentParser,
                         args: argparse.Namespace) -> bool:
    """Decide whether the top-level ``--project`` applies, bouncing misuse.

    Returns True iff ``--project`` should be applied (the caller then chdirs via
    ``_chdir_to_project``); False for a silent no-op.

    ``--project`` is meaningful only for the project-consuming verbs
    (``config``): on a name/CodeSpace-addressed verb it does nothing useful.
    Silently swallowing an *explicitly*-passed ``--project`` is a foot-gun for
    agentic callers, so we bounce instead (mirrors agent-bridge #1080) -- but
    ONLY when the flag was user-typed.

    The ``<repo> <slug>`` router injects ``--project`` *uniformly* for the
    project-consuming slugs (incl. on their name-addressed verbs) and marks it
    ``AGENT_WORKTREES_PROJECT_ROUTED=1``; a *routed* no-op stays silent so the
    uniform ``<repo> codespaces …`` surface (e.g. ``<repo> codespaces ssh
    <name>``) keeps working. The marker is consumed here so it never leaks to
    child processes.
    """
    routed = os.environ.pop("AGENT_WORKTREES_PROJECT_ROUTED", None) == "1"
    if getattr(args, "project", None) is None:
        return False
    command = getattr(args, "command", None)
    if command in _PROJECT_CONSUMING_VERBS:
        return True
    if routed:
        return False
    parser.error(
        f"--project {args.project!r} is not meaningful for "
        f"'{command or '(no command)'}': it scopes only the project-consuming "
        f"verbs ({'/'.join(sorted(_PROJECT_CONSUMING_VERBS))}). Remove "
        f"--project, or use one of those verbs."
    )
    return False  # unreachable: parser.error() exits; keeps type-checkers happy


def _chdir_to_project(project: str) -> bool:
    """Chdir to ``project``'s checkout so cwd-based repo-root discovery targets
    it (the project-addressed `<repo> codespaces …` surface). Resolves the path
    via ``agent-worktrees repos find`` (agent-codespaces runs in its own venv, so
    it shells out rather than importing agent_worktrees). Best-effort: on any
    failure it warns and leaves the cwd unchanged, so a name-addressed verb still
    runs. Returns True iff the cwd was changed."""
    import shutil
    import subprocess as sp

    name = (project or "").strip()
    if not name:
        return False
    # Resolve the binstub via PATHEXT (on Windows it is a .cmd/.ps1, not a bare
    # executable, so a plain ["agent-worktrees", ...] argv fails with WinError 2).
    exe = shutil.which("agent-worktrees")
    if not exe:
        print(f"WARNING: --project {name}: agent-worktrees not found on PATH; "
              f"using current directory", file=sys.stderr)
        return False
    try:
        result = sp.run(
            [exe, "repos", "find", name],
            capture_output=True, text=True, timeout=15,
        )
    except Exception as exc:  # pragma: no cover - environment-dependent
        print(f"WARNING: --project {name}: could not resolve checkout ({exc}); "
              f"using current directory", file=sys.stderr)
        return False
    path = (result.stdout or "").strip().splitlines()
    checkout = path[0].strip() if path else ""
    if result.returncode != 0 or not checkout or not Path(checkout).is_dir():
        print(f"WARNING: --project {name}: no checkout found "
              f"(agent-worktrees repos find returned {result.returncode}); "
              f"using current directory", file=sys.stderr)
        return False
    try:
        os.chdir(checkout)
        return True
    except OSError as exc:
        print(f"WARNING: --project {name}: cannot chdir to {checkout} ({exc}); "
              f"using current directory", file=sys.stderr)
        return False


def _resolve_repo_root() -> Path:
    """Resolve the canonical repo root (worktree-safe), or cwd if not a repo."""
    import subprocess as sp

    cwd = Path.cwd()
    try:
        result = sp.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return Path(result.stdout.strip()).parent.resolve()
    except FileNotFoundError:
        pass
    return cwd


def _list_codespaces_for_init() -> list[dict]:
    """Return `gh codespace list` entries across all mapped accounts, or [].

    Cross-account like :func:`lifecycle.list_codespaces` (#195/#190): lists
    under each account in the agent-worktrees ``account_map`` plus the ambient
    account, tagging each entry with its ``account`` and de-duping by name.
    Falls back to a single ambient list when no map exists.
    """
    import subprocess as sp

    from . import gh_account

    def _one(login: str | None) -> list[dict]:
        env = gh_account.env_for_account(login) if login else None
        try:
            result = sp.run(
                ["gh", "codespace", "list", "--json",
                 "name,repository,machineName,displayName,state,lastUsedAt"],
                capture_output=True, text=True, timeout=30, env=env,
            )
        except (FileNotFoundError, sp.TimeoutExpired):
            return []
        if result.returncode != 0:
            return []
        try:
            data = json.loads(result.stdout or "[]")
        except json.JSONDecodeError:
            return []
        if not isinstance(data, list):
            return []
        for entry in data:
            if isinstance(entry, dict):
                entry.setdefault("account", login or "")
        return data

    accounts = gh_account.mapped_accounts()
    if not accounts:
        return _one(None)
    merged: dict[str, dict] = {}
    for login in (*accounts, None):
        for entry in _one(login):
            name = entry.get("name")
            if name:
                merged.setdefault(name, entry)
    return list(merged.values())


def _discover_workspace_folder(codespaces: list[dict], repository: str) -> str | None:
    """Best-effort: read $WORKING_DIRECTORY from an already-Available CodeSpace.

    Only targets CodeSpaces already in the ``Available`` state so we never pay
    a cold-start. Returns None on any failure (no Available CodeSpace, SSH
    error, timeout) -- callers must treat workspace_folder as unknown, not
    guess it from the repo name (the CodeSpaces repo name often differs from
    the checked-out workspace, e.g. ``<repo>-codespaces`` vs ``<repo>``).
    """
    import subprocess as sp

    from . import gh_account

    available = [
        c for c in codespaces
        if c.get("repository") == repository and c.get("state") == "Available"
    ]
    for c in available:
        name = c.get("name")
        if not name:
            continue
        env = gh_account.env_for_account(c.get("account") or None) if c.get("account") else None
        try:
            result = sp.run(
                ["gh", "codespace", "ssh", "-c", name, "--",
                 "printf %s \"$WORKING_DIRECTORY\""],
                capture_output=True,
                text=True,
                timeout=45,
                env=env,
            )
        except (FileNotFoundError, sp.TimeoutExpired):
            return None
        if result.returncode == 0:
            wd = (result.stdout or "").strip()
            if wd.startswith("/"):
                return wd
    return None


def _derive_codespaces_defaults(
    codespaces: list[dict], from_codespace: str | None
) -> dict | None:
    """Pick a representative CodeSpace and derive scaffold defaults.

    Returns a dict with keys: repository, machine_type, workspace_folder
    (str or None if it could not be reliably discovered), source_name.
    Returns None if no usable CodeSpace is found.
    """
    if not codespaces:
        return None

    chosen: dict | None = None
    if from_codespace:
        chosen = next(
            (c for c in codespaces if c.get("name") == from_codespace), None
        )
        if chosen is None:
            return None
    else:
        # Prefer the most-recently-used CodeSpace (lastUsedAt is ISO-8601, so
        # lexical max works); fall back to the first.
        chosen = max(
            codespaces,
            key=lambda c: c.get("lastUsedAt") or "",
        )

    repository = chosen.get("repository") or ""

    # Use the most common machine type across CodeSpaces of the chosen repo
    # (more representative than a single CodeSpace's machine).
    same_repo = [c for c in codespaces if c.get("repository") == repository]
    machine_counts: dict[str, int] = {}
    for c in same_repo:
        m = c.get("machineName")
        if m:
            machine_counts[m] = machine_counts.get(m, 0) + 1
    machine_type = (
        max(machine_counts, key=machine_counts.get)
        if machine_counts
        else "largePremiumLinux"
    )

    # Discover workspace_folder from a live CodeSpace -- NOT from the repo name,
    # which is unreliable (the CodeSpaces repo often differs from the checkout).
    workspace_folder = _discover_workspace_folder(codespaces, repository)

    return {
        "repository": repository,
        "machine_type": machine_type,
        "workspace_folder": workspace_folder,
        "source_name": chosen.get("displayName") or chosen.get("name") or "",
    }


def _render_codespaces_yaml(defaults: dict | None) -> str:
    """Render a ``.agent-codespaces/config.yaml``.

    The file is **supplementary-only**: it carries just the CodeSpace-specific
    bits convention can't derive. A repo that matches convention (machine
    defaults, ``/workspaces/<basename>`` checkout, git-credential relay for
    github.com + ADO) needs no file at all -- so the template leads with that and
    keeps every block commented unless a discovered CodeSpace supplies a value.
    """
    header = (
        "# .agent-codespaces/config.yaml -- SUPPLEMENTARY CodeSpace config.\n"
        "#\n"
        "# Most repos need NO file here. agent-codespaces derives by convention:\n"
        "#   * machine_type=largePremiumLinux, location=EastUs\n"
        "#   * checkout at /workspaces/<repo-basename>\n"
        "#   * git-credential relay serving github.com AND Azure DevOps (via GCM)\n"
        "# Add a block below ONLY when your repo deviates (e.g. a split\n"
        "# CodeSpaces-vs-product repo, a pinned devcontainer, an ADO host, or a\n"
        "# provision hook). All org/account/URL values live HERE, in your repo --\n"
        "# never in the copilot-extensions plugin.\n"
    )

    if defaults:
        repo = defaults.get("repository")
        machine = defaults["machine_type"]
        ws = defaults.get("workspace_folder")
        derived = (
            f"# Derived from your CodeSpace '{defaults['source_name']}'.\n"
        )
        # Only emit a repos: block when the discovered machine differs from the
        # convention default, or a non-convention workspace folder was found.
        basename = repo.split("/")[-1] if repo else ""
        conv_ws = f"/workspaces/{basename.removesuffix('-codespaces')}" if basename else ""
        lines: list[str] = []
        if machine and machine != "largePremiumLinux":
            lines.append(f"    machine_type: {machine}")
        if ws and ws != conv_ws:
            lines.append(f"    workspace_folder: {ws}")
        elif basename.endswith("-codespaces"):
            # A split repo: record the product mapping so agents land right.
            product = basename.removesuffix("-codespaces")
            lines.append(f"    workspace_repo: {product}   # -> /workspaces/{product}")
        if lines and repo:
            repo_block = f"\nrepos:\n  {repo}:\n" + "\n".join(lines) + "\n"
        else:
            repo_block = (
                f"\n# This repo matches convention -- no overrides needed.\n"
                f"# Add a repos: block only if that changes:\n"
                f"# repos:\n#   {repo or '<org>/<repo>'}:\n"
                f"#     workspace_repo: <product>   # only for split *-codespaces repos\n"
            )
        return header + derived + repo_block

    return header + (
        "\n# No existing CodeSpaces detected. Uncomment and adapt ONLY what you\n"
        "# need; delete the rest.\n"
        "#\n"
        "# repos:\n"
        "#   <org>/<repo>-codespaces:\n"
        "#     workspace_repo: <product>          # split repo -> /workspaces/<product>\n"
        "#     machine_type: largePremiumLinux256gb\n"
        "#     devcontainer_path: .devcontainer/devcontainer.json  # pin if repo ships >1\n"
        "#\n"
        "# credentials:\n"
        "#   ado_host: <your-org>.visualstudio.com   # only for bare ADO get-access-token\n"
    )


def _parse_gh_account_scopes(status_text: str) -> dict[str, set[str]]:
    """Parse ``gh auth status`` into ``{login: {scopes}}``.

    ``gh auth status`` prints a block per authenticated account; each carries a
    ``Token scopes: 'a', 'b', ...`` line. We attribute each scopes line to the
    most recent ``account <login>`` seen so a per-account scope check is
    possible (multi-account #247/#190).
    """
    import re

    accounts: dict[str, set[str]] = {}
    current: str | None = None
    for line in status_text.splitlines():
        m = re.search(r"account\s+([A-Za-z0-9_](?:[A-Za-z0-9_-]*[A-Za-z0-9_])?)", line)
        if m:
            current = m.group(1)
            accounts.setdefault(current, set())
        if current and "token scopes" in line.lower():
            scopes = set(re.findall(r"'([^']+)'", line))
            accounts[current] |= scopes
    return accounts


def _gh_auth_preflight() -> list[str]:
    """Check gh auth + codespace scope. Returns a list of guidance messages
    (empty if all good).

    Beyond the ambient account, verifies every account in the agent-worktrees
    ``account_map`` is logged in with the ``codespace`` scope, surfacing the
    per-account remedy (its ``accounts.yaml`` login flow when recorded) so a
    cross-account list/ssh doesn't fail with a misleading 403/404 (#247/#190).
    """
    import subprocess as sp

    from . import gh_account

    msgs: list[str] = []
    try:
        result = sp.run(
            ["gh", "auth", "status"],
            capture_output=True, text=True, timeout=20,
        )
    except FileNotFoundError:
        return ["gh CLI not found -- install from https://cli.github.com/ "
                "then run: gh auth login"]
    except sp.TimeoutExpired:
        return ["gh auth status timed out -- check your network / gh install."]

    combined = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0 or "not logged" in combined.lower():
        msgs.append("gh is not authenticated -- run: gh auth login")
        return msgs

    # gh prints "Token scopes: 'gist', 'repo', ..." -- the codespace scope is
    # required for `gh codespace` operations.
    if "codespace" not in combined.lower():
        msgs.append(
            "gh token is missing the 'codespace' scope (needed for CodeSpace "
            "operations) -- run: gh auth refresh -h github.com -s codespace"
        )

    # Per-account check for every account the account_map routes to.
    per_account = _parse_gh_account_scopes(combined)
    lowered = {login.casefold(): scopes for login, scopes in per_account.items()}
    for login in gh_account.mapped_accounts():
        scopes = lowered.get(login.casefold())
        if scopes is None:
            remedy = _account_login_remedy(login)
            msgs.append(
                f"mapped gh account '{login}' is not logged in -- {remedy}"
            )
        elif "codespace" not in {s.casefold() for s in scopes}:
            msgs.append(
                f"mapped gh account '{login}' is missing the 'codespace' scope "
                f"-- run: gh auth refresh -h github.com -u {login} -s codespace"
            )
    return msgs


def _account_login_remedy(login: str) -> str:
    """Return the recorded login flow for ``login``, or a sane default."""
    try:
        import shutil
        aw = shutil.which("agent-worktrees")
        if aw:
            import subprocess as sp
            r = sp.run([aw, "accounts", "show", login, "--json"],
                       capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                data = json.loads(r.stdout or "{}")
                flow = (data.get("login_flow") or "").strip()
                if flow:
                    return f"run: {flow}"
    except Exception:
        pass
    return f"run: gh auth login -h github.com (account {login})"


def _config_init(
    *, from_codespace: str | None, force: bool, also_adopt: bool
) -> int:
    """Scaffold ``.agent-codespaces/config.yaml``, deriving from existing CodeSpaces.

    Most repos need no file at all -- the scaffold is supplementary-only. Writing
    it also auto-adopts the repo (so the detached daemon picks it up); pass a
    repo that matches convention and you can simply skip this entirely.
    """
    repo_root = _resolve_repo_root()
    canonical = repo_root / CONFIG_DIR_NAME / CONFIG_FILE_IN_DIR
    legacy = repo_root / CONFIG_FILENAME

    if legacy.exists() and not canonical.exists():
        print(f"A legacy {CONFIG_FILENAME} exists at {legacy}.")
        print(f"Run `agent-codespaces config migrate` to move it to "
              f"{CANONICAL_CONFIG_REL}.")
        return 0

    if canonical.exists() and not force:
        print(f"{CANONICAL_CONFIG_REL} already exists at {canonical}")
        print("Use --force to overwrite, or edit it directly.")
        return 0

    # Preflight: surface gh auth / scope problems explicitly, so an empty
    # `gh codespace list` (auth failure) isn't mistaken for "no CodeSpaces".
    gh_msgs = _gh_auth_preflight()
    for m in gh_msgs:
        print(f"[gh] {m}", file=sys.stderr)

    codespaces = _list_codespaces_for_init()
    defaults = _derive_codespaces_defaults(codespaces, from_codespace)

    if from_codespace and defaults is None:
        print(
            f"ERROR: CodeSpace '{from_codespace}' not found in "
            "`gh codespace list`.",
            file=sys.stderr,
        )
        return 1

    content = _render_codespaces_yaml(defaults)
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_text(content, encoding="utf-8")

    print(f"Wrote {canonical}")
    if defaults:
        print(f"  Derived from CodeSpace: {defaults['source_name']}")
        print(f"  repository:        {defaults['repository']}")
        print(f"  machine_type:      {defaults['machine_type']}")
        ws = defaults.get("workspace_folder")
        if ws:
            print(f"  workspace_folder:  {ws}  (discovered from a live CodeSpace)")
    else:
        print("  No existing CodeSpaces detected -- wrote a supplementary-only "
              "template (most repos can delete it).")

    # Auto-adopt: the file is only consulted by the detached daemon via the
    # adoption manifest, and a manual `config adopt` step was pure friction.
    print()
    return _config_adopt()


def _config_adopt() -> int:
    """Register the current repo for config."""
    repo_root = _resolve_repo_root()

    if not repo_has_config(repo_root):
        print(
            f"ERROR: No {CANONICAL_CONFIG_REL} (or legacy {CONFIG_FILENAME}) "
            f"found in {repo_root}",
            file=sys.stderr,
        )
        print(
            "Standard repos need no config -- adopt only a repo that carries "
            "supplementary CodeSpace config. Run `agent-codespaces config init` "
            "to scaffold one.",
            file=sys.stderr,
        )
        return 1

    repos = load_adopted_repos()
    existing_paths = {str(r.path) for r in repos}

    if str(repo_root) in existing_paths:
        print(f"Already adopted: {repo_root}")
        return 0

    repos.append(AdoptedRepo(
        path=repo_root,
        adopted_at=datetime.now(tz=timezone.utc).isoformat(),
    ))
    save_adopted_repos(repos)
    print(f"Adopted: {repo_root}")
    print(f"Config:   {repo_config_path(repo_root)}")
    print(f"Manifest: {ADOPTED_REPOS_FILE}")
    return 0


def _config_migrate_file() -> int:
    """Relocate a legacy repo-root ``codespaces.yaml`` to the canonical location.

    Moves ``<repo>/codespaces.yaml`` -> ``<repo>/.agent-codespaces/config.yaml``
    (idempotent). Content is copied verbatim; adoption is unaffected (the manifest
    tracks the repo root, not the file). A no-op when the repo already uses the
    canonical location or carries no config.
    """
    repo_root = _resolve_repo_root()
    canonical = repo_root / CONFIG_DIR_NAME / CONFIG_FILE_IN_DIR
    legacy = repo_root / CONFIG_FILENAME

    if canonical.exists():
        if legacy.exists():
            print(f"Both {CANONICAL_CONFIG_REL} and legacy {CONFIG_FILENAME} "
                  f"exist. The canonical file wins; remove {legacy} when ready.")
            return 0
        print(f"Already migrated: {canonical}")
        return 0

    if not legacy.exists():
        print(f"No legacy {CONFIG_FILENAME} to migrate in {repo_root} "
              "(nothing to do).")
        return 0

    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_text(legacy.read_text(encoding="utf-8"), encoding="utf-8")
    legacy.unlink()
    print(f"Migrated {legacy}")
    print(f"      -> {canonical}")
    print("Commit the move; adoption is unchanged (the manifest tracks the repo "
          "root, not the file).")
    return 0


def _config_show() -> int:
    """Show resolved config from all adopted repos."""
    config = load_merged_config()

    print("=== Resolved Configuration ===")
    print(f"Sources: {len(config.source_paths)} adopted repo(s)")
    for p in config.source_paths:
        print(f"  - {p}")

    print("\nDefaults:")
    print(f"  machine_type: {config.default_machine_type}")
    print(f"  location: {config.default_location}")
    if config.dotfiles_repo:
        print(f"  dotfiles_repo: {config.dotfiles_repo}")
    print(f"  ssh_user: {config.ssh_user}")
    if config.workspace_folder:
        print(f"  workspace_folder: {config.workspace_folder}")
    if config.acp_command:
        print(f"  acp_command: {config.acp_command} (explicit override)")
    print(f"  effective_acp_command: {config.effective_acp_command}")

    from .relay_launch import effective_relay_port
    _rp = config.credentials.relay_port
    _eff = effective_relay_port(config)
    _rp_desc = f"{_eff} (dynamic)" if not _rp else str(_eff)
    print(f"\nCredential relay port: {_rp_desc}")
    for name, source in config.credentials.sources.items():
        status = "enabled" if source.enabled else "disabled"
        print(f"  {name}: {status}")
        if source.allowed_hosts:
            for h in source.allowed_hosts:
                print(f"    - {h}")

    if config.repos:
        print(f"\nTarget repos: {len(config.repos)}")
        for repo_key, repo_cfg in config.repos.items():
            mt = repo_cfg.machine_type or config.default_machine_type
            loc = repo_cfg.location or config.default_location
            print(f"  {repo_key}: {mt} / {loc}")

    return 0


def _config_validate() -> int:
    """Validate config from all adopted repos."""
    config = load_merged_config()
    issues = validate_config(config)

    if not issues:
        print("[OK] Configuration is valid")
        return 0

    for issue in issues:
        print(f"[WARN] {issue}")
    return 1


def _cmd_delete(args: argparse.Namespace) -> int:
    """Delete a CodeSpace, recovering its Copilot sessions first (unless
    --no-sync). The recovery is best-effort: a failure warns but does not block
    deletion (use `finalize` for a sync-gated delete)."""
    if not getattr(args, "no_sync", False):
        res = sync_codespace_sessions(args.name, verbose=args.verbose)
        if res.get("ok"):
            print(f"[OK] Recovered {res.get('session_count', 0)} session(s) "
                  f"before delete: {res.get('detail', '')}")
        else:
            print(f"[WARN] Pre-delete session recovery failed (continuing): "
                  f"{res.get('detail')}", file=sys.stderr)
    delete_codespace(args.name, force=args.force)
    print(f"Deleted: {args.name}")
    _release_lease_quietly(args.name)
    return 0


def _cmd_finalize(args: argparse.Namespace) -> int:
    """Gracefully close out a CodeSpace (the worktree-style "done" transition).

    Default (no ``--delete``): **recover -> stop -> mark ``recovered``**. The box
    is preserved (off the active-quota, boots on next connect) and reusable; the
    ``recovered`` marker makes it a candidate the ``cleaning-codespaces`` skill can
    later promote to ``prunable`` (once its PR merges) for ``prune`` to reclaim.
    Recovery is idempotent -- an already-Shutdown box is NOT booted just to
    re-pull (fixes the "too many codespaces running" quota error).

    With ``--delete``: recover then delete (removed only after a successful sync,
    unless ``--force`` overrides a failed one) -- the eager retire path.
    """
    from .status import STATE_RECOVERED, set_status

    # Preserving path may skip booting a Shutdown box; the destructive --delete
    # path must recover first (booting if needed) before the box is gone.
    res = sync_codespace_sessions(
        args.name, timeout=args.timeout, verbose=args.verbose,
        skip_if_shutdown=not args.delete,
    )
    if res.get("ok"):
        if res.get("skipped"):
            print(f"[OK] {args.name}: {res.get('detail', '')}")
        else:
            print(f"[OK] Recovered {res.get('session_count', 0)} session(s) from "
                  f"{args.name}: {res.get('detail', '')}")
    else:
        print(f"[WARN] Session recovery for {args.name} failed: "
              f"{res.get('detail')}", file=sys.stderr)
        if args.delete and not args.force:
            print("Refusing to delete after a failed recovery. Diagnose and "
                  "resolve the error above (often a still-booting CodeSpace or "
                  "an SSH/relay issue), then re-run finalize so the sessions "
                  "are captured. If the CodeSpace is genuinely unbootable (never "
                  "reaches SSH), its session-state is unrecoverable -- retire it "
                  "with `agent-codespaces finalize <name> --delete --force` (or "
                  "`delete <name> --force --no-sync`) to skip recovery.",
                  file=sys.stderr)
            return 1

    if args.delete:
        delete_codespace(args.name, force=args.force)
        print(f"Deleted: {args.name}")
        _release_lease_quietly(args.name)
        _clear_status_quietly(args.name)
        return 0 if res.get("ok") else 1

    # Default preserve path: stop (idempotent) then mark recovered.
    try:
        stopped = stop_codespace(args.name)
        print(f"Stopped: {args.name} (preserved -- boots on next connect)"
              if stopped else f"Already stopped: {args.name}")
    except RuntimeError as exc:
        print(f"[WARN] Could not stop {args.name} (continuing): {exc}",
              file=sys.stderr)

    if res.get("ok"):
        set_status(args.name, STATE_RECOVERED, reason="finalized")
        print(f"[OK] {args.name} marked 'recovered' -- preserved & reusable; "
              f"eligible for prune once its PR merges (reuse clears the mark)")
        _release_lease_quietly(args.name)
        return 0

    print(f"[WARN] Not marking {args.name} 'recovered' (recovery failed; the box "
          f"is preserved, so retry `finalize {args.name}` later)", file=sys.stderr)
    return 1


def _cmd_finalize_progress(args: argparse.Namespace) -> int:
    """``finalize`` in **progress-streaming** mode (D4) -- the same recover-first
    close-out as :func:`_cmd_finalize`, but its stdout is the registered-pivot
    NDJSON progress envelope so the Worktree Picker's CodeSpaces **Recycle** verb
    can stream live progress into its modal.

    Emits ``{"type":"progress","pct":..,"msg":..}`` lines (one per flushed line,
    mirroring ``pool --stream``) then a terminal ``{"type":"done","message":..}``
    or ``{"type":"error","message":..}``. Preserves the safety contract:
    recovery runs first (recycle-rescues-first); with ``--delete`` a failed
    recovery aborts unless ``--force`` (so a stale but unrecovered box is never
    silently destroyed). No stdout ``print`` here -- only the envelope -- so the
    modal reader sees a clean stream.
    """
    from .status import STATE_RECOVERED, set_status

    out = sys.__stdout__

    def emit(obj: dict) -> None:
        try:
            out.write(json.dumps(obj, default=str) + "\n")
            out.flush()
        except (BrokenPipeError, OSError):
            pass

    name = args.name
    try:
        emit({"type": "progress", "pct": 5.0,
              "msg": f"Recovering Copilot sessions from {name}\u2026"})
        res = sync_codespace_sessions(
            name, timeout=args.timeout, verbose=args.verbose,
            skip_if_shutdown=not args.delete,
        )
        if res.get("ok"):
            if res.get("skipped"):
                emit({"type": "progress", "pct": 45.0, "msg": res.get("detail", "")})
            else:
                emit({"type": "progress", "pct": 45.0,
                      "msg": f"Recovered {res.get('session_count', 0)} session(s)"})
        else:
            if args.delete and not args.force:
                emit({"type": "error",
                      "message": f"Session recovery failed ({res.get('detail')}). "
                                 "Not deleting -- diagnose first, or re-run with "
                                 "--force to retire an unrecoverable box."})
                return 1
            emit({"type": "progress", "pct": 45.0,
                  "msg": f"Recovery failed ({res.get('detail')}); continuing"})

        if args.delete:
            emit({"type": "progress", "pct": 70.0, "msg": f"Deleting {name}\u2026"})
            delete_codespace(name, force=args.force)
            _release_lease_quietly(name)
            _clear_status_quietly(name)
            emit({"type": "done",
                  "message": f"Recycled {name} (recovered + deleted)"})
            return 0 if res.get("ok") else 1

        # Preserve path: stop (idempotent) then mark recovered.
        emit({"type": "progress", "pct": 70.0,
              "msg": f"Stopping {name} (preserving)\u2026"})
        try:
            stop_codespace(name)
        except RuntimeError as exc:
            emit({"type": "progress", "pct": 80.0,
                  "msg": f"stop warning: {exc}"})
        if res.get("ok"):
            set_status(name, STATE_RECOVERED, reason="finalized")
            _release_lease_quietly(name)
            emit({"type": "done",
                  "message": f"{name} finalized -- preserved & reusable"})
            return 0
        emit({"type": "error",
              "message": f"Recovery failed; {name} preserved -- retry later"})
        return 1
    except Exception as exc:  # never crash the modal reader
        emit({"type": "error", "message": str(exc)[:200]})
        return 1


def _cmd_stop(args: argparse.Namespace) -> int:
    """Gracefully stop a CodeSpace, PRESERVING it for later resume.

    The pause-and-keep counterpart to ``finalize --delete``: recover Copilot
    session-state into the agent-logger hub (unless --no-sync), then
    ``gh codespace stop``. Unlike ``finalize --delete``, a failed recovery does
    NOT block the stop -- stopping is non-destructive, so the sessions remain on
    the (preserved) CodeSpace and can be recovered on a later connect/finalize.
    Idempotent: a no-op if the CodeSpace is already Shutdown. Never deletes.

    Unlike ``finalize``, ``stop`` does not set the ``recovered`` lifecycle marker
    -- it is the plain pause primitive; ``finalize`` is the mark-eligible "done"
    transition.
    """
    if not getattr(args, "no_sync", False):
        res = sync_codespace_sessions(
            args.name, timeout=args.timeout, verbose=args.verbose,
            skip_if_shutdown=True,
        )
        if res.get("ok"):
            detail = res.get("detail", "")
            if res.get("skipped"):
                print(f"[OK] {args.name}: {detail}")
            else:
                print(f"[OK] Recovered {res.get('session_count', 0)} session(s) "
                      f"before stop: {detail}")
        else:
            print(f"[WARN] Pre-stop session recovery failed (continuing -- the "
                  f"CodeSpace is preserved, so sessions can be recovered "
                  f"later): {res.get('detail')}", file=sys.stderr)

    stopped = stop_codespace(args.name)
    if stopped:
        print(f"Stopped: {args.name} (preserved -- boots on next connect)")
    else:
        print(f"Already stopped: {args.name}")
    return 0


def _release_lease_quietly(codespace: str) -> None:
    """Check a CodeSpace back in on teardown. Best-effort, never raises."""
    try:
        from .lease import release

        if release(codespace):
            print(f"[OK] Released lease on {codespace}")
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("lease release for %s failed: %s", codespace, exc)


def _clear_status_quietly(codespace: str) -> None:
    """Drop any eligibility marker for a CodeSpace. Best-effort, never raises.

    Called when a box is reused (borrow) or gone (delete/prune) so a stale
    ``recovered``/``prunable`` marker never lingers.
    """
    try:
        from .status import clear_status

        clear_status(codespace)
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("status clear for %s failed: %s", codespace, exc)


def _cmd_mark(args: argparse.Namespace) -> int:
    """Set or clear a CodeSpace's prune-lifecycle marker.

    The skill-side promotion lever: ``cleaning-codespaces`` (which has the ADO
    PR-merged context) runs ``mark <name> prunable`` once a box is safe to
    reclaim, so the plugin's ``prune`` can delete it. ``active`` clears the
    marker (e.g. a manual un-mark to reuse a box).
    """
    from .status import STATE_ACTIVE, set_status

    set_status(args.name, args.state, reason=args.reason)
    if args.state == STATE_ACTIVE:
        print(f"[OK] {args.name} marker cleared (active)")
    else:
        suffix = f" ({args.reason})" if args.reason else ""
        print(f"[OK] {args.name} marked '{args.state}'{suffix}")
    return 0


def _cmd_prune(args: argparse.Namespace) -> int:
    """Reclaim ``prunable`` CodeSpaces -- the worktree-style prune pass.

    Deletes only boxes the ``cleaning-codespaces`` skill has promoted to
    ``prunable`` (safe: PR merged + effort archived), oldest-first. A final
    session recovery runs before each delete (booting if needed -- this IS the
    destructive path). ``recovered`` and unmarked boxes are never touched.
    """
    from .status import STATE_PRUNABLE, clear_status, list_by_state

    prunable = list_by_state(STATE_PRUNABLE)
    if not prunable:
        print("No prune-eligible (prunable) CodeSpaces. "
              "(Finalize marks 'recovered'; the cleaning-codespaces skill "
              "promotes to 'prunable' once a PR merges.)")
        return 0

    # Oldest-first: the box marked prunable longest ago is the safest to reclaim.
    prunable.sort(key=lambda s: s.state_at)

    try:
        live_names: set[str] | None = {cs.name for cs in list_codespaces()}
    except RuntimeError:
        live_names = None  # can't list (auth/network) -> don't gate on existence

    pruned = 0
    for rec in prunable:
        if args.max_count and pruned >= args.max_count:
            break
        name = rec.codespace

        # A prunable marker for a box that no longer exists -> drop the marker.
        # (``live_names is None`` means we couldn't list, so don't assume gone.)
        if live_names is not None and name not in live_names:
            print(f"[--] {name}: no longer exists; clearing stale marker")
            if not args.dry_run:
                clear_status(name)
                _release_lease_quietly(name)
            continue

        if args.dry_run:
            print(f"[dry-run] would prune {name} "
                  f"(prunable; {rec.reason or 'no reason'})")
            pruned += 1
            continue

        print(f"Pruning {name} ({rec.reason or 'prunable'})...")
        # Destructive path: recover even a Shutdown box (boot if needed) first.
        res = sync_codespace_sessions(name, skip_if_shutdown=False)
        if not (res.get("ok") or res.get("skipped")):
            print(f"[WARN] Final recovery failed for {name}; skipping delete "
                  f"(diagnose): {res.get('detail')}", file=sys.stderr)
            continue
        try:
            delete_codespace(name, force=False)
        except RuntimeError as exc:
            print(f"[WARN] Delete failed for {name}: {exc}", file=sys.stderr)
            continue
        clear_status(name)
        _release_lease_quietly(name)
        print(f"[OK] Pruned {name}")
        pruned += 1

    verb = "would prune" if args.dry_run else "Pruned"
    print(f"{verb} {pruned} CodeSpace(s).")
    return 0


def _reclaim_for_quota(err: str) -> str | None:
    """On a CodeSpace-create quota error, attempt ONE safe reclaim so a retry
    can succeed. Returns a human note describing what it freed, or None.

    GitHub has two distinct limits needing different reclaims:
    - **TOTAL** ("maximum number of codespaces" / "reached ... limit"): every
      box, even stopped, counts -> DELETE frees room. Prune one ``prunable`` box.
    - **RUNNING** ("too many codespaces running"): only *stopping* a running box
      frees room; deleting a stopped ``prunable`` box does NOT. So stop the oldest
      *eligible* (recovered/prunable) box that is (unusually) still running --
      never auto-stop an in-use/unmarked box.
    """
    from .status import (
        STATE_PRUNABLE,
        STATE_RECOVERED,
        clear_status,
        get_status,
        list_by_state,
    )

    low = err.lower()
    running_limit = "too many codespaces running" in low
    total_limit = (not running_limit) and (
        "maximum number of codespaces" in low
        or "you have reached" in low
        or ("reached" in low and "codespace" in low and "limit" in low)
    )

    if total_limit:
        for rec in sorted(list_by_state(STATE_PRUNABLE), key=lambda s: s.state_at):
            name = rec.codespace
            res = sync_codespace_sessions(name, skip_if_shutdown=False)
            if not (res.get("ok") or res.get("skipped")):
                continue
            try:
                delete_codespace(name, force=False)
            except RuntimeError:
                continue
            clear_status(name)
            _release_lease_quietly(name)
            return f"pruned '{name}' to free total-codespace quota"
        return None

    if running_limit:
        try:
            from .lifecycle import _SHUTDOWN_STATE

            running = [c for c in list_codespaces() if c.state != _SHUTDOWN_STATE]
        except RuntimeError:
            return None
        # Only stop a running box that is already finalized (eligible) -- never
        # an in-use/unmarked one.
        for cs in running:
            st = get_status(cs.name)
            if st and st.state in (STATE_RECOVERED, STATE_PRUNABLE):
                try:
                    stop_codespace(cs.name)
                except RuntimeError:
                    continue
                return f"stopped eligible running box '{cs.name}' to free running quota"
        return None

    return None


def _cmd_create(args: argparse.Namespace) -> int:
    """Create a CodeSpace and run post-create provisioning hooks.

    On a quota error, attempt one safe reclaim (prune a ``prunable`` box for the
    total-quota limit, or stop an eligible running box for the running-quota
    limit) and retry once, so a busy account self-heals instead of hard-failing.
    """
    from ssh_manager import ConnectionManager

    config = load_merged_config()
    print(f"Creating CodeSpace for {args.repo}...")

    def _create():
        return create_codespace(
            args.repo, config, branch=args.branch,
            display_name=getattr(args, "display_name", None),
            devcontainer_path=getattr(args, "devcontainer_path", None),
        )

    try:
        info = _create()
    except RuntimeError as exc:
        note = _reclaim_for_quota(str(exc))
        if not note:
            raise
        print(f"[..] Codespace quota hit; {note}. Retrying create...")
        info = _create()
    print(f"Created: {info.name}")

    if args.no_wait:
        return 0

    print("Waiting for CodeSpace to become Available...")
    if not wait_for_available(info.name, timeout=args.timeout):
        print(
            f"[WARN] {info.name} did not reach Available within "
            f"{args.timeout:.0f}s -- run provisioning later with "
            f"`agent-codespaces ssh {info.name}`",
            file=sys.stderr,
        )
        return 1

    # Provision over SSH: relay helpers + dotfiles bootstrap + repo hooks
    # (including on_create extras).
    from .relay_launch import effective_relay_port
    relay_port = effective_relay_port(config)
    source = CodespaceSource(info.name, account=info.account or None)
    manager = ConnectionManager()

    async def _run() -> int:
        relay_forward = None
        try:
            connection = await manager.ensure_connected(info.name, source, [])
            relay_forward = await _start_supervised_relay(
                info.name,
                connection.config,
                relay_port,
                context="CodeSpace create provisioning",
                host_port_resolver=lambda: relay_launch.effective_relay_port(config),
            )
            await _provision_relay_helpers(manager, info.name)
            await _provision_dotfiles(manager, info.name, config)
            await _provision_harness(manager, info.name, config)
            await _provision_repo_hooks(
                manager, info.name, config, args.repo, include_on_create=True,
            )
            return 0
        finally:
            if relay_forward is not None:
                await relay_forward.stop()
            await manager.disconnect(info.name)

    print("Running post-create provisioning...")
    rc = asyncio.run(_run())
    if rc == 0:
        print(f"[OK] {info.name} created and provisioned")
    return rc


def _cmd_bridge(args: argparse.Namespace) -> int:
    """Agent-bridge provider integration subcommands."""
    from .bridge_provider import (
        get_bridge_status,
        register_with_bridge,
        unregister_from_bridge,
    )

    if args.bridge_command == "register":
        result = register_with_bridge(
            bridge_url=args.bridge_url,
            ttl=args.ttl,
        )
        print(
            f"[OK] Registered {result.get('agents', 0)} agent(s) "
            f"with agent-bridge (ttl={result.get('ttl', 0):.0f}s)"
        )
        return 0

    if args.bridge_command == "unregister":
        unregister_from_bridge(bridge_url=args.bridge_url)
        print("[OK] Unregistered codespace agents from agent-bridge")
        return 0

    if args.bridge_command == "status":
        status = get_bridge_status(bridge_url=args.bridge_url)
        if status is None:
            print("[--] Not registered (or agent-bridge not reachable)")
            return 0
        expired = status.get("expired", False)
        state = "EXPIRED" if expired else "ACTIVE"
        print(f"[{state}] Provider '{status.get('name', 'codespaces')}'")
        print(f"  Agents: {status.get('agents', 0)}")
        print(f"  Active: {status.get('active_agents', 0)}")
        print(f"  TTL: {status.get('ttl', 0):.0f}s")
        print(f"  Age: {status.get('age', 0):.0f}s")
        conflicts = status.get("conflicts", [])
        if conflicts:
            print(f"  Conflicts: {', '.join(conflicts)}")
        return 0

    if args.bridge_command == "refresh":
        # Re-register with fresh codespace state (drops stale agents)
        result = register_with_bridge(
            bridge_url=args.bridge_url,
            ttl=args.ttl,
        )
        print(
            f"[OK] Refreshed: {result.get('agents', 0)} agent(s) "
            f"registered (ttl={result.get('ttl', 0):.0f}s)"
        )
        return 0

    print(
        "Usage: agent-codespaces bridge {register|unregister|status|refresh}",
        file=sys.stderr,
    )
    return 1


def _cmd_borrow(args: argparse.Namespace) -> int:
    """Advisory-lease a CodeSpace to an effort (check it out).

    Reusing a box clears any lingering ``recovered``/``prunable`` eligibility
    marker (it is active work again, not a prune candidate).
    """
    from .lease import borrow

    lease = borrow(args.effort, args.codespace, force=args.force)
    _clear_status_quietly(args.codespace)
    print(lease.codespace)
    return 0


def _cmd_release(args: argparse.Namespace) -> int:
    """Release a CodeSpace lease by CodeSpace name or effort name."""
    from .lease import release

    if release(args.target):
        print(f"Released: {args.target}")
        return 0
    print(f"No lease found for '{args.target}'", file=sys.stderr)
    return 1


def _short_owner(owner: str) -> str:
    """A readable owner label: the basename of a worktree path, else as-is.

    A claim's owner is an absolute worktree path (long); an advisory borrow's
    owner is a short effort name. Show the basename for a path so the ``pool`` /
    ``leases`` tables stay legible (#904).
    """
    if owner and os.path.isabs(owner):
        return os.path.basename(owner.rstrip("/\\")) or owner
    return owner


def _cmd_leases() -> int:
    """Show active CodeSpace leases (advisory borrows and #897 claims)."""
    from .lease import list_leases

    leases = list_leases()
    if not leases:
        print("No active leases.")
        return 0
    print(f"{'CODESPACE':<40} {'OWNER':<28} {'KIND':<7} {'HOST':<16} {'PID'}")
    for lease in leases:
        # A claim keys its owner on ``worktree`` (with ``effort`` empty); an
        # advisory borrow keys on ``effort``. Show the single owner + which
        # flavor recorded it, so a dispatched (claimed) CodeSpace is no longer a
        # blank row (#904).
        owner = lease.worktree or lease.effort
        kind = "claim" if lease.worktree else "borrow"
        print(
            f"{lease.codespace:<40} {_short_owner(owner):<28} {kind:<7} "
            f"{lease.host:<16} {lease.pid}"
        )
    return 0


def _cmd_claim(args: argparse.Namespace) -> int:
    """Acquire an exclusive worktree-keyed claim on a CodeSpace (#897).

    The process-to-process seam the agent-bridge daemon shells out to (it cannot
    import ``agent_codespaces``). Resolves the owner (``--owner`` else the calling
    worktree), sweeps existing claims, and either acquires or **bounces** (exit
    ``_BUSY_EXIT``) on a live different owner. Degrade-safe: an unresolvable owner
    is a no-op success (there is nothing to key a claim on).
    """
    from .lease import (
        ClaimConflict,
        active_worktree_ids,
        claim,
        resolve_owner_worktree,
    )

    # Escape hatch parity with the ``ssh`` direct path: an operator (or a unit
    # test) can disable exclusive-control enforcement entirely. Honored here too
    # so the daemon's shelled ``claim`` is a no-op success when the daemon runs
    # with claiming disabled.
    if os.environ.get("AGENT_CODESPACES_DISABLE_CLAIM"):
        print("[OK] Claim disabled (AGENT_CODESPACES_DISABLE_CLAIM); skipped.")
        return 0

    owner = resolve_owner_worktree(explicit=getattr(args, "owner", None))
    if not owner:
        print(
            "[WARN] No owning worktree resolved (not in a worktree and no "
            "--owner given); claim skipped.",
            file=sys.stderr,
        )
        return 0
    from . import coordination
    # Cross-machine holder identity: an explicit --holder-ref (a dispatched
    # caller, e.g. the bridge daemon, passes the original caller's qualified
    # ClaimRef), else resolved from the calling worktree. None -> L2 skipped
    # (degrade-safe, L1-only).
    holder_ref = coordination.owner_ref(
        explicit=getattr(args, "holder_ref", None),
        session_id=getattr(args, "session_id", None),
    )
    try:
        lease = claim(
            args.codespace, owner,
            force=getattr(args, "force_claim", False),
            active=active_worktree_ids(),
            holder_ref=holder_ref,
        )
    except ClaimConflict as exc:
        print(f"[BUSY] {exc} Use --force-claim to take over.", file=sys.stderr)
        return _BUSY_EXIT
    print(f"[OK] Claimed {lease.codespace} for {owner}")
    return 0


def _cmd_release_claim(args: argparse.Namespace) -> int:
    """Release this worktree's exclusive claim on a CodeSpace (#897)."""
    from .lease import release_claim, resolve_owner_worktree

    if os.environ.get("AGENT_CODESPACES_DISABLE_CLAIM"):
        print("[OK] Claim disabled (AGENT_CODESPACES_DISABLE_CLAIM); skipped.")
        return 0
    owner = resolve_owner_worktree(explicit=getattr(args, "owner", None))
    if not owner:
        print("[WARN] No owning worktree resolved; nothing to release.",
              file=sys.stderr)
        return 0
    if release_claim(args.codespace, owner):
        print(f"[OK] Released claim on {args.codespace} (owner {owner})")
    else:
        print(f"No claim on {args.codespace} owned by {owner}.")
    return 0


def _cmd_pool_stream(
    args: argparse.Namespace,
    members,
    budget,
    banner: str,
) -> int:
    """Emit the CodeSpaces pivot as the registered-pivot NDJSON envelope (D2).

    One-shot: ``begin`` -> a ``row`` per CodeSpace -> ``summary`` -> ``done``,
    one JSON object per flushed line so the pivot paints progressively. With
    ``--subscribe`` the channel is then held open: every ``--interval`` seconds
    the pool is re-scanned and the diff vs. the last snapshot is emitted as
    ``delta`` / ``removed`` frames (whole-row by ``id``), plus a fresh
    ``summary`` when the budget line changes, so an open pivot live-updates. The
    loop exits cleanly when the reader closes the pipe (BrokenPipeError) or on
    interrupt/kill, so the Picker's teardown never orphans it."""
    out = sys.__stdout__

    def emit(obj: dict) -> bool:
        try:
            out.write(json.dumps(obj, default=str) + "\n")
            out.flush()
            return True
        except (BrokenPipeError, OSError):
            return False

    for frame in pool_mod.picker_stream_frames(members, budget, banner=banner):
        if not emit(frame):
            return 0

    if not getattr(args, "subscribe", False):
        return 0

    # --subscribe: periodic re-scan + diff -> live delta/removed frames.
    interval = max(0.5, float(getattr(args, "interval", 5.0) or 5.0))
    prev = pool_mod.picker_payload(members, budget, banner=banner)
    prev_entries = prev["entries"]
    prev_summary = prev["summary"]
    budget_cores = args.budget if args.budget is not None else pool_mod.DEFAULT_BUDGET_CORES
    stale_after = (
        args.stale_after if args.stale_after is not None
        else pool_mod.DEFAULT_STALE_AFTER
    )
    try:
        while True:
            time.sleep(interval)
            curr = None
            try:
                members, budget = pool_mod.build_pool(
                    budget_cores=budget_cores, stale_after=stale_after,
                )
                curr = pool_mod.picker_payload(members, budget, banner=banner)
            except Exception:
                # A transient re-scan failure (e.g. a gh hiccup) must not kill
                # the live channel -- skip this tick and try again next time.
                curr = None
            if curr is None:
                continue
            deltas, removed = pool_mod.diff_entries(prev_entries, curr["entries"])
            for entry in deltas:
                if not emit({"type": "delta", "entry": entry}):
                    return 0
            for rid in removed:
                if not emit({"type": "removed", "id": rid}):
                    return 0
            if curr["summary"] != prev_summary:
                if not emit({"type": "summary", "summary": curr["summary"]}):
                    return 0
            prev_entries = curr["entries"]
            prev_summary = curr["summary"]
    except KeyboardInterrupt:
        return 0


def _cmd_pool(args: argparse.Namespace) -> int:
    """Show the CodeSpace pool: per-box disposition + allocation + core budget.

    A *derived* view (no store of its own) over ``list_codespaces`` + leases +
    the finalize/prune markers. ``--json`` emits ``{"budget":..,"members":[..]}``
    -- the programmatic surface the Picker's CodeSpaces pivot and the
    reuse/recycle policies consume.
    """
    budget_cores = args.budget if args.budget is not None else pool_mod.DEFAULT_BUDGET_CORES
    stale_after = (
        args.stale_after if args.stale_after is not None
        else pool_mod.DEFAULT_STALE_AFTER
    )
    members, budget = pool_mod.build_pool(
        budget_cores=budget_cores, stale_after=stale_after,
    )

    if getattr(args, "picker_json", False):
        # Surface the missing-`codespace`-scope remedy as a prominent, actionable
        # **banner** (rather than an empty, opaque list) so the Picker CodeSpaces
        # pivot renders an unmissable notice with the exact `gh auth refresh`
        # remedy (#980). Best-effort: never let it break the payload.
        try:
            _msgs = _gh_auth_preflight()
            banner = " \u00b7 ".join(_msgs) if _msgs else ""
        except Exception:
            banner = ""
        if getattr(args, "stream", False):
            # D2: NDJSON streaming envelope (optionally held live via --subscribe).
            return _cmd_pool_stream(args, members, budget, banner)
        print(json.dumps(pool_mod.picker_payload(members, budget, banner=banner)))
        return 0

    if args.json_output:
        print(json.dumps({
            "budget": budget.to_dict(),
            "members": [m.to_dict() for m in members],
        }, indent=2))
        return 0

    unknown = (
        f" ({budget.unknown_cores_count} running box"
        f"{'es' if budget.unknown_cores_count != 1 else ''} w/ unknown cores)"
        if budget.unknown_cores_count else ""
    )
    print(
        f"Budget: {budget.spent_cores}/{budget.total_cores} cores in use, "
        f"{budget.headroom_cores} free  "
        f"({budget.running_count} running / {budget.total_count} total){unknown}"
    )
    if not members:
        print("No CodeSpaces in the pool.")
        return 0
    print()
    print(f"{'NAME':<38} {'REPO':<30} {'DISPOSITION':<13} "
          f"{'CORES':<6} {'HOLDER (owner@host)'}")
    print("-" * 110)
    for m in sorted(members, key=lambda x: (x.disposition, x.name)):
        cores = str(m.cores) if m.cores_known else "?"
        owner = m.holder_owner
        if owner:
            holder = f"{_short_owner(owner)}@{m.holder_host or '?'}"
        elif m.beacon:
            holder = f"(beacon #{m.beacon})"
        elif m.l2_live and m.l2_holder:
            holder = f"(L2: {pool_mod._short_claim_ref(m.l2_holder)})"
        else:
            holder = ""
        print(f"{m.name:<38} {m.repository:<30} {m.disposition:<13} "
              f"{cores:<6} {holder}")
    return 0


def _cmd_wait(args: argparse.Namespace) -> int:
    """Patiently wait for a CodeSpace to become Available.

    Exit codes: 0 Available, 2 genuinely-failed state, 124 timeout -- so a
    background caller can distinguish "still slow" from "dead" and never create
    a redundant CodeSpace just because a boot was slow.
    """
    from .lifecycle import WaitOutcome, wait_for_codespace

    print(f"Waiting for CodeSpace '{args.name}' (up to {args.timeout:.0f}s)...")

    def _progress(state: str, remaining: float) -> None:
        print(f"  ... state={state or '?'} ({remaining:.0f}s left)")

    outcome, last_state = wait_for_codespace(
        args.name, timeout=args.timeout, interval=args.interval,
        on_progress=_progress,
    )
    if outcome == WaitOutcome.AVAILABLE:
        print(f"[OK] {args.name} is Available")
        return 0
    if outcome == WaitOutcome.FAILED:
        print(
            f"[FAIL] {args.name} reached a terminal state '{last_state}' -- it "
            f"will not become Available on its own. Diagnose before recreating.",
            file=sys.stderr,
        )
        return 2
    print(
        f"[TIMEOUT] {args.name} still not Available (last state "
        f"'{last_state or '?'}') after {args.timeout:.0f}s. It may still be "
        f"provisioning -- wait longer rather than declaring it dead.",
        file=sys.stderr,
    )
    return 124


def _cmd_cleanup(args: argparse.Namespace) -> int:
    """Remove stale local state for deleted/rotated codespaces."""
    mode = "Dry run" if args.dry_run else "Cleanup"
    print(f"=== {mode}: pruning stale codespace state ===")

    removed = cleanup_stale(dry_run=args.dry_run)

    ssh_count = len(removed["ssh_configs"])
    socket_count = len(removed["sockets"])
    total = ssh_count + socket_count

    if ssh_count:
        print(f"\nSSH configs ({ssh_count}):")
        for p in removed["ssh_configs"]:
            print(f"  {'[WOULD REMOVE]' if args.dry_run else '[REMOVED]'} {p}")

    if socket_count:
        print(f"\nSockets ({socket_count}):")
        for p in removed["sockets"]:
            print(f"  {'[WOULD REMOVE]' if args.dry_run else '[REMOVED]'} {p}")

    if total == 0:
        print("No stale state found")
    else:
        verb = "would be removed" if args.dry_run else "removed"
        print(f"\n{total} item(s) {verb}")

    return 0


def _cmd_status() -> int:
    """Show service status overview."""
    print("=== agent-codespaces status ===")
    print(f"Runtime dir: {RUNTIME_DIR}")
    print(f"Adopted repos: {ADOPTED_REPOS_FILE}")

    repos = load_adopted_repos()
    print(f"Adopted repo count: {len(repos)}")
    for r in repos:
        exists = r.path.exists()
        status = "[OK]" if exists else "[MISSING]"
        print(f"  {status} {r.path}")

    config = load_merged_config()
    issues = validate_config(config)
    if issues:
        print(f"\nConfig warnings: {len(issues)}")
        for i in issues:
            print(f"  [WARN] {i}")
    else:
        print("\nConfig: [OK]")

    # Check gh CLI
    import shutil
    gh = shutil.which("gh")
    print(f"\ngh CLI: {'[OK] ' + gh if gh else '[MISSING]'}")

    ssh = shutil.which("ssh")
    print(f"ssh: {'[OK] ' + ssh if ssh else '[MISSING]'}")

    return 0


def _cmd_version() -> int:
    """Show version."""
    try:
        from ._build_info import BUILD_INFO
        ver = BUILD_INFO.get("version", "0.0.0")
        commit = BUILD_INFO.get("commit", "unknown")[:8]
        print(f"agent-codespaces {ver} ({commit})")
    except ImportError:
        print("agent-codespaces 0.1.0-dev2")
    return 0


def _cmd_acp_model_flags() -> int:
    """Print the resolved per-session copilot model flags (or nothing).

    The process-to-process seam agent-bridge's CodeSpace dispatch shells out to,
    instead of importing ``agent_codespaces`` in the bridge venv -- keeping the
    two separately-versioned plugin venvs decoupled (avoids the stale-sync class
    of bug). Prints the flag string (e.g. ``--model X --reasoning-effort Y
    --context Z``) to stdout, or an empty line when nothing is configured or
    propagation is opted out.
    """
    from .model_launch import build_model_flags

    print(build_model_flags().strip())
    return 0


def _cmd_provision_command() -> int:
    """Print the CodeSpace relay/auth-helper provision command (#892 Inc 1).

    The process-to-process seam agent-bridge's dispatch path shells out to
    instead of importing ``agent_codespaces.codespace_assets`` in the bridge
    venv -- so a fix to the provision command reaches the dispatch path from
    agent-codespaces' OWN venv with no agent-bridge redeploy (the #733 class).
    Prints the idempotent bash command to stdout.
    """
    from .codespace_assets import build_provision_command

    print(build_provision_command())
    return 0


def _cmd_relay_launch_env(args: argparse.Namespace) -> int:
    """Print JSON ``{"prelude": <str>, "port": <int>}`` for a detached CodeSpace
    launch's relay env (#892 Inc 1).

    The process-to-process seam agent-bridge's dispatch path shells out to
    instead of importing ``agent_codespaces.relay_launch`` in the bridge venv.
    ``--relay-port`` injects the daemon's actually-bound relay port (the daemon
    knows its live port; the standalone path cannot). Mints/reuses the
    per-codespace relay token as a side effect, exactly as the in-process call
    did.
    """
    from .relay_launch import build_relay_launch_env

    prelude, port = build_relay_launch_env(
        args.codespace, relay_port=getattr(args, "relay_port", None)
    )
    print(json.dumps({"prelude": prelude, "port": port}))
    return 0


# --- namespace-* resolver seam (#892 Inc 3) --------------------------------
# The process-boundary form of the `codespace:` NamespaceResolver: agent-bridge
# shells out to these instead of importing `agent_codespaces.resolver`. They emit
# plain JSON (agent_bridge-free) built from the resolver's `*_spec` cores; the
# bridge shim reconstructs SpawnTarget / NamespaceAgentInfo. Exit codes let the
# bridge distinguish not-found (3) / bad-state (4) so it can map them back to the
# resolver's KeyError / ValueError contract.

_NS_NOT_FOUND_EXIT = 3
_NS_BAD_STATE_EXIT = 4


def _cmd_namespace_list() -> int:
    """Print a JSON list of `codespace:` namespace agent specs (#892 Inc 3)."""
    from .resolver import CodespaceResolver

    specs = asyncio.run(CodespaceResolver().list_specs())
    print(json.dumps(specs))
    return 0


def _cmd_namespace_resolve(args: argparse.Namespace) -> int:
    """Print a JSON spawn spec resolving a codespace name (#892 Inc 3).

    ``{"type","spawn_command","user"}`` on success; a not-found maps to exit 3
    (bridge -> ``KeyError``) and a non-connectable state to exit 4 (bridge ->
    ``ValueError``), so the process boundary preserves the resolver's contract.
    """
    from .resolver import CodespaceResolver

    try:
        spec = asyncio.run(CodespaceResolver().resolve_spec(
            args.name,
            extra_plugin_sources=list(getattr(args, "stage_plugin", []) or []),
            repo=getattr(args, "repo", None),
            repo_remote=getattr(args, "repo_remote", None),
        ))
    except KeyError as e:
        print(str(e).strip("'"), file=sys.stderr)
        return _NS_NOT_FOUND_EXIT
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return _NS_BAD_STATE_EXIT
    print(json.dumps(spec))
    return 0


def _cmd_namespace_target_repo(args: argparse.Namespace) -> int:
    """Print the workspace repo a codespace hosts, or empty (#892 Inc 3)."""
    from .resolver import CodespaceResolver

    repo = asyncio.run(CodespaceResolver().target_repo(args.name))
    print(repo or "")
    return 0


def _cmd_namespace_ensure_ready(args: argparse.Namespace) -> int:
    """Exit 0 if the codespace is reachable/startable, else 1 (#892 Inc 3)."""
    from .resolver import CodespaceResolver

    try:
        asyncio.run(CodespaceResolver().ensure_ready(args.name))
    except Exception as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


def _cmd_relay_profile() -> int:
    """Print the declarative credential-relay profile as JSON (#892 Inc 2).

    The process-boundary seam agent-bridge applies (with a file-backed token
    validator) instead of importing ``agent_codespaces.relay_provider`` in the
    bridge venv. Emits the same policy the in-process ``register_relay`` applies.
    """
    from .relay_provider import relay_profile

    print(json.dumps(relay_profile()))
    return 0


def _cmd_config_migrate() -> int:
    """Migrate machine-local config schema (adopted-repos.yaml) in place.

    Idempotent + atomic; machine-local only (never touches repo-committed
    ``codespaces.yaml`` -- that is an adopt concern). Safe no-op when the
    vendored ``config_migrate`` library is absent. Invoked once from the
    installer's install/update flow.
    """
    from . import config_migrations

    if not config_migrations.available():
        print("config-migrate: migration library unavailable; skipping")
        return 0
    results = config_migrations.run_migrations()
    print(config_migrations.summarize(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
