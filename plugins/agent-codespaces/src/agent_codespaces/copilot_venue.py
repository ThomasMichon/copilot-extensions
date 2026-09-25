"""``agent-codespaces copilot <name>`` -- the venue counterpart to
``agent-worktrees copilot`` (PR #3126), agent-bridge-cli-mode-sessions Phase 4.

Split out of ``__main__.py`` (module-size guard, ``tools/check-module-size.py``)
rather than left inline -- this is a self-contained feature with its own
argparse wiring and command body, exactly like ``connection_owner``/
``relay_launch`` already live in their own modules.
"""
from __future__ import annotations

import argparse
import os
import shlex
import sys
import threading
from collections.abc import Callable

from ._ssh_retry import exec_with_retry

# Mirrors __main__._BUSY_EXIT / __main__._COORDINATION_EXIT exactly (kept as a
# separate copy, not an import, to preserve this module's existing no-
# circular-import-with-__main__ constraint -- see cmd_copilot's own docstring
# on interactive_ssh being injected for the same reason).
_BUSY_EXIT = 75
_COORDINATION_EXIT = 78


def add_copilot_subparser(sub) -> None:
    """Register the ``copilot`` subcommand on the shared subparsers group."""
    copilot_parser = sub.add_parser(
        "copilot",
        help="Deliver a TTY Copilot session in THIS terminal, remotely, via "
             "this CodeSpace -- the venue counterpart to `agent-worktrees "
             "copilot` (PR #3126): reserves the worktree's CLI-mode slot, "
             "holds+heartbeats a Connection Owner tenant for as long as this "
             "process stays attached, and SSHes `-t` in to run "
             "`agent-worktrees copilot` there.",
    )
    copilot_parser.add_argument("name", help="CodeSpace name")
    copilot_parser.add_argument(
        "--worktree-id", dest="worktree_id", default=None,
        help="The worktree id to reserve/attach on the venue side (forwarded "
             "verbatim to the remote `agent-worktrees copilot --worktree-id`). "
             "Omit for the default: a CodeSpace is conventionally anchor-only "
             "(the devcontainer already clones the repo directly; no worktree "
             "exists unless an operator explicitly created one), so this "
             "delivers a Copilot session directly in the anchor checkout "
             "instead -- matching headless ACP dispatch's own existing "
             "behavior of running straight in the venue's workspace_folder.",
    )
    copilot_parser.add_argument(
        "--driver", default="cli-mode",
        help="Forwarded to the remote `agent-worktrees copilot --driver` "
             "(stamps the 'driven by' banner; default 'cli-mode')",
    )
    copilot_parser.add_argument(
        "--seed", help="Forwarded to the remote `agent-worktrees copilot --seed`",
    )
    copilot_parser.add_argument(
        "--ttl-seconds", type=float, default=300.0,
        help="CLI-mode reservation lifetime before it's reclaimable (default 300)",
    )
    copilot_parser.add_argument(
        "--no-ensure-mux", dest="ensure_mux", action="store_false", default=True,
        help="Do not forward --ensure-mux to the remote `agent-worktrees "
             "copilot` (by default it self-heals a missing tmux on the venue).",
    )
    copilot_parser.add_argument(
        "--no-relay", action="store_true",
        help="Skip the credential-relay reverse forward",
    )
    copilot_parser.add_argument(
        "--effort", default=None,
        help="Explicit claim-owner worktree id (for a dispatched invocation "
             "whose cwd is not the caller's own worktree). Forwarded to the "
             "same exclusive-claim resolution `agent-codespaces ssh` uses.",
    )
    copilot_parser.add_argument(
        "--force-claim", dest="force_claim", action="store_true",
        help="Take over this CodeSpace's exclusive claim even if a different, "
             "still-live worktree holds it -- evicts that owner's claim; its "
             "in-flight work may be disrupted. Without this, a live claim "
             "held by another worktree/machine refuses the connect (matches "
             "`agent-codespaces ssh`'s own claim enforcement).",
    )
    copilot_parser.add_argument(
        "--force", action="store_true",
        help="Take over a same-machine, cross-process SSH target lock held by "
             "another live local process (e.g. a concurrent `ssh`/`copilot` "
             "invocation against the same CodeSpace on THIS machine) -- "
             "terminates that process. Distinct from --force-claim, which "
             "evicts a different WORKTREE's cross-machine claim; this guards "
             "the shared credential-relay connection itself (matches "
             "`agent-codespaces ssh`'s own same-machine serialization).",
    )
    lifecycle = copilot_parser.add_mutually_exclusive_group()
    lifecycle.add_argument(
        "--detach", action="store_true",
        help="Start (or rejoin) the CodeSpace's CLI-mode session in the "
             "background instead of attaching this terminal -- for an "
             "orchestrating agent. Runs the full dispatch-grade venue prep, "
             "seeds the session, waits until it is registered with the host "
             "agent-bridge, and prints a JSON handle (session id plus "
             "status/observe/nudge/attach/stop commands). The Connection Owner "
             "keeps the relay and bridge forwards alive while the session runs.",
    )
    lifecycle.add_argument(
        "--stop", action="store_true",
        help="Stop the CodeSpace's detached session (kills its mux session, "
             "verifies it is gone, then releases its forwards and reservation). "
             "The CodeSpace itself and the conversation state are kept.",
    )
    copilot_parser.add_argument(
        "--seed-file", dest="seed_file", default=None, metavar="PATH",
        help="Read the seed from PATH ('-' = stdin) instead of --seed; use for "
             "long or multi-line prompts.",
    )
    copilot_parser.add_argument(
        "--ref-file", dest="ref_files", action="append", default=[], metavar="PATH",
        help="With --detach: copy a reference file or folder (a HAR trace, a "
             "transcript, logs) into the venue outside the repo and tell the "
             "worker where it is -- in its seed for a new session, as a message "
             "for a running one. Repeatable; the caller never reads the file.",
    )
    copilot_parser.add_argument(
        "--reverse-forward", dest="reverse_forwards", action="append", default=[],
        metavar="VENUE_PORT:HOST_PORT",
        help="With --detach: keep the CodeSpace's 127.0.0.1:VENUE_PORT forwarded "
             "to this host's 127.0.0.1:HOST_PORT for the session's life (the "
             "Connection Owner supervises it with the bridge forward), e.g. a "
             "host browser's DevTools port as the venue's 9222. Repeatable; a "
             "rejoin without it keeps the session's existing forwards.",
    )
    copilot_parser.add_argument(
        "--forward", dest="local_forwards", action="append", default=[],
        metavar="PORT[:VENUE_PORT]",
        help="With --detach: keep this host's 127.0.0.1:PORT forwarded to the "
             "CodeSpace's 127.0.0.1:VENUE_PORT (default: the same port) for the "
             "session's life, e.g. a worker's dev server that a host browser "
             "loads. The forward may exist before the server starts. Repeatable; "
             "a rejoin without it keeps the session's existing forwards.",
    )
    copilot_parser.add_argument(
        "--copilot-arg", dest="copilot_args", action="append", default=[],
        metavar="ARG",
        help="With --detach: extra Copilot argument for the launched session "
             "(repeatable; `--copilot-arg=--no-ask-user`, "
             "`--copilot-arg=--autopilot`, `--copilot-arg=--resume=<id>`).",
    )
    copilot_parser.add_argument(
        "--register-timeout", dest="register_timeout", type=float, default=180.0,
        metavar="SECS",
        help="With --detach: how long to wait for the session to register with "
             "the host bridge after it starts (default 180).",
    )
    copilot_parser.add_argument(
        "--dry-run", dest="dry_run", action="store_true",
        help="With --detach: print the resolved plan (identities, venue) "
             "without touching anything.",
    )


def _resolve_anchor_identity(name: str, config) -> str:  # noqa: ANN001
    """Synthesize the CLI-mode reservation identity for anchor mode:
    ``anchor-<repo_name>``, where ``repo_name`` is the basename of this
    CodeSpace's resolved workspace folder (the same ``/workspaces/<repo>``
    convention headless dispatch already resolves via
    ``config.resolved_workspace_folder_for``).

    Must match exactly what ``agent-worktrees get session-scope-id`` reports
    when the remote ``agent-worktrees copilot --anchor`` self-registers
    (``anchor-<config.repo_name>`` there, where that repo_name is however the
    CodeSpace's own agent-worktrees project happens to be registered) -- by
    convention this is the same basename, since a project is registered under
    its checkout's own directory name. If the two ever disagree in practice,
    the reservation is simply never claimed (a visible, diagnosable no-op,
    never a wrong-session mix-up: the daemon only correlates on an exact
    string match).
    """
    from .lifecycle import list_codespaces

    repository = next(
        (cs.repository for cs in list_codespaces() if cs.name == name), None,
    )
    workspace_folder = config.resolved_workspace_folder_for(repository) or ""
    repo_name = os.path.basename(workspace_folder.rstrip("/")) or name
    return f"anchor-{repo_name}"


def _ensure_agent_bridge_plugin(name: str) -> None:
    """Implicit CLI-mode preflight: install the ``agent-bridge`` Copilot
    plugin on the venue if it's missing, and provision the daemon's own
    registration credentials there, all before ever opening the interactive
    connection.

    Two distinct gaps, closed in the same round trip:

    1. Without the ``agent-bridge`` plugin loaded in the *remote* Copilot
       process, that process never even attempts to self-register (confirmed
       live: a real CodeSpace session ran for 5+ minutes with zero
       registration, purely because this was missing).
    2. Even with the plugin loaded, the interactive extension's own
       ``resolveToken()``/``resolveBaseUrl()`` (``extension.mjs``) read
       ``~/.agent-bridge/auth.yaml``/``active.json`` -- files that exist only
       on the machine actually running the daemon. A remote venue never has
       them, so the extension logs "no local agent-bridge auth token found;
       not registering (ok)" and silently never calls the registration
       endpoint at all -- confirmed live (agent-bridge-cli-mode-sessions
       Phase 4 follow-up): a real CodeSpace session loaded the extension,
       reached a ready prompt, and still never registered, for exactly this
       reason (a distinct bug from #1, and layered underneath it -- fixing
       only #1 still leaves CLI-mode registration silently broken).

    Both are preconditions of the `copilot` verb's own contract, not a
    project policy choice, so both are applied unconditionally here -- no
    `--fix`/opt-in needed. Best-effort throughout: any probe/install/
    provisioning failure is reported but never blocks the connection attempt
    (the operator may still want to attach and diagnose by hand).
    """
    import asyncio

    from . import venue_check
    from .lifecycle import account_for_codespace

    async def _probe_and_fix() -> None:
        from ssh_manager import ConnectionManager

        from .codespace_config import CodespaceSource

        source = CodespaceSource(name, account=account_for_codespace(name))
        manager = ConnectionManager()
        await manager.ensure_connected(name, source, [])
        try:
            readiness = await venue_check.check_remote_venue(
                manager.exec_command, name,
            )
            if not readiness.agent_bridge_plugin:
                print(
                    f"[PREP] agent-bridge plugin missing on '{name}' -- "
                    "installing (CLI-mode sessions can't self-register "
                    "without it) ...",
                    file=sys.stderr,
                )
                remediation = await venue_check.remediate_remote_venue(
                    manager.exec_command, name, readiness,
                )
                if "install agent-bridge plugin" in remediation.succeeded:
                    print("[PREP] agent-bridge plugin installed.", file=sys.stderr)
                else:
                    print(
                        "[PREP] Could not install agent-bridge plugin "
                        f"automatically -- CLI-mode registration may not "
                        f"work. Run `agent-codespaces doctor {name} --fix` "
                        "to retry.",
                        file=sys.stderr,
                    )

            from venue_copilot import (
                registration_credentials_script,
                resolve_daemon_port,
                resolve_local_auth_token,
            )

            daemon_port = resolve_daemon_port()
            token = resolve_local_auth_token()
            if daemon_port is None or not token:
                print(
                    "[PREP] Could not resolve the host agent-bridge daemon's "
                    "live port/token -- skipping remote registration-"
                    "credential provisioning (CLI-mode registration may not "
                    "work).",
                    file=sys.stderr,
                )
                return
            script = registration_credentials_script(token, daemon_port)
            result = await exec_with_retry(manager, name, f"bash -lc {shlex.quote(script)}")
            if getattr(result, "exit_code", 1) != 0:
                print(
                    f"[PREP] Could not provision registration credentials on "
                    f"'{name}' -- CLI-mode registration may not work.",
                    file=sys.stderr,
                )
        finally:
            await manager.disconnect(name)

    try:
        asyncio.run(_probe_and_fix())
    except Exception as exc:  # noqa: BLE001 -- best-effort, never fatal
        print(
            f"[PREP] agent-bridge preflight probe failed ({exc}) -- "
            "continuing anyway.",
            file=sys.stderr,
        )


def claim_or_exit_code(args: argparse.Namespace) -> int | None:
    """Enforce the exclusive, worktree-keyed CodeSpace claim for a connect.

    Returns ``None`` to proceed, or the exit code to return. Shared by the
    attached and detached ``copilot`` paths (the same ``claim_for_connect``
    choke point ``agent-codespaces ssh`` uses) so a venue is never touched
    while a different, still-live worktree holds it.
    """
    from .lease import ClaimConflict, CoordinationRejected, claim_for_connect
    from .worktrees import ContextRefused

    try:
        claim_for_connect(
            args.name,
            force=getattr(args, "force_claim", False),
            effort=getattr(args, "effort", None),
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
    except (CoordinationRejected, ContextRefused) as exc:
        print(
            f"[BLOCKED] CodeSpace claim requires durable coordination: {exc}",
            file=sys.stderr,
        )
        return _COORDINATION_EXIT
    except RuntimeError as exc:
        # Never let a claim-bookkeeping error block a connect.
        print(f"[WARN] CodeSpace claim skipped: {exc}", file=sys.stderr)
    return None


def cmd_copilot(
    args: argparse.Namespace,
    *,
    interactive_ssh: Callable[..., int],
    ssh_session: Callable[..., int] | None = None,
) -> int:
    """Deliver a TTY Copilot session to this terminal, remotely, via a CodeSpace.

    The venue counterpart to ``agent-worktrees copilot`` (PR #3126) --
    ``agent-bridge-cli-mode-sessions`` Phase 4: reserves the worktree's
    CLI-mode Session Host slot on the host ``agent-bridge`` daemon, places a
    Connection Owner tenant hold (spinning the Owner up on-demand) so the
    daemon-port + credential-relay reverse forwards ride one durable
    connection, re-heartbeats that hold for as long as this process stays
    attached (self-hosted tenancy -- no agent-bridge daemon involvement), and
    SSHes ``-t`` into the CodeSpace to run ``agent-worktrees copilot`` there --
    identical contract to the local verb, just dispatched remotely.

    Defaults to **anchor mode** (no ``--worktree-id``): a CodeSpace is
    conventionally anchor-only (the devcontainer already clones the repo
    directly; no worktree exists unless an operator explicitly created one
    with ``agent-worktrees create`` inside it), so forcing ``--worktree-id``
    on every caller would be needless ceremony for the common case. This
    mirrors headless ACP dispatch's own existing behavior of running
    straight in the venue's ``workspace_folder``, never a worktree.

    ``interactive_ssh`` is injected (``__main__._interactive_ssh``) rather than
    imported, to avoid a circular import between this module and ``__main__``.

    Before connecting, implicitly ensures the ``agent-bridge`` Copilot
    plugin is installed on the venue (see ``_ensure_agent_bridge_plugin``) --
    a single-shot prep step so an operator/caller never has to run
    ``agent-codespaces doctor --fix`` by hand before their first CLI-mode
    session on a given CodeSpace.

    Also enforces the SAME exclusive, worktree-keyed CodeSpace claim
    ``agent-codespaces ssh`` already does (via the shared
    ``lease.claim_for_connect`` choke point) before ever attempting to
    connect. Previously this verb never claimed at all: a live claim held by
    one worktree/machine was silently bypassable simply by using `copilot`
    instead of `ssh` to drive the same CodeSpace -- confirmed live
    (agent-bridge-cli-mode-sessions Phase 4 follow-up): a CLI-mode session
    was driven successfully on a CodeSpace a different, still-live worktree
    on another machine had legitimately claimed, disrupting its in-flight
    work.

    Also acquires the SAME same-machine, cross-process ``ssh-manager``
    ``TargetLock`` ``agent-codespaces ssh`` already does, guarding the
    shared credential-relay reverse-forward this verb rides just like
    ``ssh`` does -- previously only ``ssh`` serialized concurrent local
    processes against this collision (a second local `copilot`/`ssh`
    invocation against the same CodeSpace could collide on the relay port
    and collapse the first one's connection); `copilot` never took this
    lock at all, even though `agent-containers`' own `copilot` verb already
    does (this fix brings the two venues' `copilot` verbs into consistent
    parity on this same-machine axis too).

    ``--detach`` / ``--stop`` hand off to :mod:`agent_codespaces.copilot_detach`
    (the agent-facing, no-TTY lifecycle); ``ssh_session`` is injected for it
    for the same no-circular-import reason as ``interactive_ssh``.
    """
    if getattr(args, "ref_files", None) and not getattr(args, "detach", False):
        print("[FAIL] --ref-file requires --detach", file=sys.stderr)
        return 2
    for flag, dest in (("--reverse-forward", "reverse_forwards"), ("--forward", "local_forwards")):
        if getattr(args, dest, None) and not getattr(args, "detach", False):
            print(f"[FAIL] {flag} requires --detach", file=sys.stderr)
            return 2
    if getattr(args, "stop", False) or getattr(args, "detach", False):
        from . import copilot_detach

        if ssh_session is None:
            raise RuntimeError("detached copilot requires the ssh_session seam")
        if getattr(args, "stop", False):
            return copilot_detach.cmd_stop(args, ssh_session=ssh_session)
        return copilot_detach.cmd_detach(args, ssh_session=ssh_session)

    claim_rc = claim_or_exit_code(args)
    if claim_rc is not None:
        return claim_rc

    from ssh_manager import TargetBusyError, TargetLock

    target_lock = TargetLock(args.name, op="copilot")
    try:
        target_lock.acquire(force=getattr(args, "force", False))
    except TargetBusyError as busy:
        print(busy.user_message(), file=sys.stderr)
        return _BUSY_EXIT

    try:
        return _cmd_copilot_connect(args, interactive_ssh=interactive_ssh)
    finally:
        target_lock.release()


def _cmd_copilot_connect(
    args: argparse.Namespace,
    *,
    interactive_ssh: Callable[..., int],
) -> int:
    """The actual reserve/connect/release body of ``cmd_copilot``, run only
    once both the worktree claim and the same-machine target lock are held.
    Split out purely so ``cmd_copilot`` itself can wrap it in a
    ``try/finally`` releasing ``target_lock`` regardless of outcome.
    """
    from .copilot_detach import read_seed

    try:
        seed = read_seed(args)
    except (OSError, ValueError) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 2

    _ensure_agent_bridge_plugin(args.name)

    from venue_copilot import VenueCopilotError, resolve_daemon_port, run_venue_copilot

    from . import connection_owner as _owner
    from .config import load_merged_config
    from .relay_launch import effective_relay_port, scoped_relay_token

    config = load_merged_config()
    anchor_mode = not args.worktree_id
    identity = args.worktree_id or _resolve_anchor_identity(args.name, config)
    tenant = f"copilot:{os.getpid()}"

    if not _owner.ensure_owner_running(config):
        print(
            "[WARN] Connection Owner could not be started; the daemon-port "
            "forward is still attempted for this connection only (it will "
            "not survive past this process's own exit).",
            file=sys.stderr,
        )
    _owner.hold(args.name, tenant)

    stop_heartbeat = threading.Event()

    def _heartbeat_loop() -> None:
        # Well inside DEFAULT_TTL (1h) so a scheduling hiccup never reclaims a
        # live tenant; this is the "who renews the hold" answer from the
        # effort's Phase 4 design: the running `copilot` process itself.
        while not stop_heartbeat.wait(60.0):
            _owner.heartbeat(args.name, tenant)

    heartbeat_thread = threading.Thread(target=_heartbeat_loop, daemon=True)
    heartbeat_thread.start()

    reverse_forwards: list[str] = []
    relay_port: int | None = None
    relay_token: str | None = None
    if not args.no_relay:
        relay_port = effective_relay_port(config)
        reverse_forwards.append(f"{relay_port}:127.0.0.1:{relay_port}")
        relay_token = scoped_relay_token(args.name, config)
    daemon_port = resolve_daemon_port()
    if daemon_port:
        reverse_forwards.append(f"{daemon_port}:127.0.0.1:{daemon_port}")
    else:
        print(
            "[WARN] Could not resolve the host agent-bridge daemon's live "
            "port; the remote session will not be able to register back to "
            "it (it will still attach locally inside the CodeSpace).",
            file=sys.stderr,
        )

    def connect(remote_command: str) -> int:
        return interactive_ssh(
            args.name, reverse_forwards,
            relay_port=relay_port, relay_token=relay_token,
            remote_command=remote_command,
        )

    try:
        return run_venue_copilot(
            identity,
            connect=connect,
            anchor=anchor_mode,
            ttl_seconds=args.ttl_seconds,
            driver=args.driver,
            seed=seed,
            ensure_mux=args.ensure_mux,
        )
    except VenueCopilotError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1
    finally:
        stop_heartbeat.set()
        _owner.release(args.name, tenant)
