"""CodeSpace SSH execution using the CLI-owned preparation hooks."""
from __future__ import annotations
import argparse
import asyncio
import os
import sys
import time

def _cmd_ssh(args: argparse.Namespace) -> int:
    """SSH into a CodeSpace using ssh-manager."""
    from .__main__ import (
        CodespaceSource,
        ConnectStage,
        ConnectTracker,
        _ADO_AUTH_EXIT,
        _BUSY_EXIT,
        _COORDINATION_EXIT,
        _RELAY_REQUIRED_EXIT,
        _SSH_BOOT_TIMEOUT,
        _build_launch_command,
        _build_relay_env,
        _check_cross_harness_fence,
        _clear_status_quietly,
        _emit_remote_cmd_result,
        _interactive_ssh,
        _pipe_stdio,
        _preflight_copilot_platform,
        _provision_dotfiles,
        _provision_harness,
        _provision_relay_helpers,
        _provision_repo_hooks,
        _register_codespace_plugins,
        _relay_listening,
        _settle_codespace_on_disconnect,
        _should_warm_auth_cache,
        _stage_plugins,
        _start_supervised_relay,
        _verify_remote_auth,
        _warm_remote_auth_cache,
        breadcrumb_prelude,
        load_merged_config,
        log,
        relay_launch,
    )
    from ssh_manager import ConnectionManager, TargetBusyError, TargetLock

    from .lifecycle import account_for_codespace
    from .worktrees import ContextRefused

    progress = getattr(args, "native_progress", None)
    try:
        source = CodespaceSource(args.name, account=account_for_codespace(args.name))
        config = load_merged_config()
    except Exception:
        if progress:
            progress("local-config", "failed")
        raise
    if progress:
        progress("local-config", "reached")
        progress("owner-admission", "started")
    from .relay_launch import effective_relay_port
    relay_port = effective_relay_port(config)
    require_relay = bool(getattr(args, "require_relay", False))
    no_plugin_staging = bool(getattr(args, "no_plugin_staging", False))

    # The supervised relay owns this remote listener even when a connection
    # owner provides it. Refuse caller collisions before taking any claims.
    if not args.no_relay and any(
        listen == relay_port for listen, _ in getattr(args, "reverse_forward", [])
    ):
        print("[ERROR] reverse-forward listener conflicts with the credential relay", file=sys.stderr)
        return 2
    if require_relay:
        from .relay_readiness import relay_ping

        if not relay_ping(relay_port):
            print(
                "[FAIL] Required host credential relay is unavailable. Start/check "
                "the credential service (agent-bridge service start; "
                "agent-bridge installer-readiness), then retry.",
                file=sys.stderr,
            )
            return _RELAY_REQUIRED_EXIT

    # Reusing a box (an explicit ssh connect) clears any prune-lifecycle marker
    # -- it is active work again, not a recovered/prunable reclaim candidate.
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
        CoordinationRejected,
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
    from . import execution_claims
    from .lease import _lease_lock

    execution_identity = (
        (args.execution_id, args.generation)
        if getattr(args, "native_transport", False) else None
    )
    try:
        with _lease_lock():
            execution_claims.assert_access(args.name, execution_identity, claim_owner)
    except ClaimConflict as exc:
        print(f"[BUSY] {exc}", file=sys.stderr)
        return _BUSY_EXIT
    except CoordinationRejected as exc:
        print(f"[BLOCKED] {exc}", file=sys.stderr)
        return _COORDINATION_EXIT
    # Resolve the qualified holder ClaimRef once, for BOTH the cross-machine L2
    # claim (below) and the cross-harness in-CodeSpace fence (in _run). It is the
    # marker's holder identity even when L1/L2 claiming is disabled, so hoist it
    # out of the claim block. Degrade-safe: None when not in a worktree.
    from . import coordination
    fence_holder_ref = coordination.owner_ref(
        session_id=getattr(args, "session_id", None),
    )
    if not claim_owner and fence_holder_ref:
        readiness = coordination.preflight(fence_holder_ref)
        if readiness.rejected:
            print(
                "[BLOCKED] CodeSpace operation requires durable coordination: "
                f"{readiness.code}: {readiness.detail}",
                file=sys.stderr,
            )
            return _COORDINATION_EXIT
    if claim_owner:
        holder_ref = fence_holder_ref
        try:
            claim(
                args.name, claim_owner,
                force=getattr(args, "force_claim", False),
                active=active_worktree_ids(),
                holder_ref=holder_ref,
                **({"execution_identity": execution_identity} if execution_identity else {}),
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

    # Reusing a box clears any prune-lifecycle marker only after coordination
    # has allowed the operation to proceed.
    _clear_status_quietly(args.name)

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
        feed_token_env=getattr(config.credentials, "feed_token_env", None),
    )

    manager = ConnectionManager()
    relay_forward = None

    # Connection Owner defer (dotfiles#1345): when the Owner daemon is live and
    # enabled, this one-off ssh becomes a non-owning tenant -- it places a hold so
    # the Owner keeps this CodeSpace's relay up and does NOT stand up its own -R
    # (which would collide, #561). Default-off + fail-safe: if the feature is off,
    # no daemon is live, or the hold fails, we own the relay exactly as before.
    # The decision here is side-effect-free; the hold itself is placed only AFTER
    # the target lock is acquired (below), so a busy-target rejection can't leak a
    # hold that would linger until its TTL.
    from . import connection_owner as _owner
    owner_tenant = f"ssh:{os.getpid()}"
    defer_to_owner = _owner.should_defer_to_owner(config, no_relay=args.no_relay)

    # The remote command is assembled inside _run() -- AFTER the relay is up and
    # any --stage-plugin payloads are staged -- so their on-CodeSpace
    # --plugin-dir paths can be folded into the copilot invocation. See
    # _finalize_remote_cmd below.
    diagnostic_remote_cmd = bool(args.remote_cmd and not args.stdio)
    minimal_provision = bool(getattr(args, "no_provision", False) or diagnostic_remote_cmd)
    overall_timeout = getattr(args, "connect_timeout", None)
    if overall_timeout is None and diagnostic_remote_cmd:
        overall_timeout = args.timeout

    def checkpoint(_event, data):
        phase = {
            3: "ssh-to-target", 4: "target-auth-env", 5: "target-binstub", 6: "worktree",
        }.get(data.get("stage"))
        if progress and phase:
            progress(phase, data["status"])

    tracker = ConnectTracker(
        emit=checkpoint if progress else None,
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

    async def _required_relay_ready() -> bool:
        if not require_relay:
            return True
        from .relay_readiness import remote_relay_ready

        # The dev-tunnel cluster resets ~10% of connections, so a single readiness
        # probe (or the proxy handshake behind it) can transiently fail even when
        # the relay is healthy. Re-check a few times with backoff before failing
        # closed -- the supervised forward self-heals a dropped proxy connection.
        delay = 1.5
        for attempt in range(1, 4):
            serving = (
                _owner.owner_serves_relay(args.name) if defer_to_owner
                else relay_forward is not None and relay_forward.is_alive
            )
            if serving and await remote_relay_ready(manager, args.name, relay_port):
                return True
            if attempt < 3:
                log.info(
                    "Credential relay not yet serving on %s (attempt %d/3); "
                    "re-checking in %.1fs",
                    args.name, attempt, delay,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, 6.0)
        print(
            "[FAIL] Required credential relay is not serving through the "
            "CodeSpace reverse forward; refusing to launch.",
            file=sys.stderr,
        )
        return False

    async def _relay_serving_probe() -> bool:
        from .relay_readiness import remote_relay_ready

        return await remote_relay_ready(manager, args.name, relay_port, fail_open=True)

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
                if not args.no_relay and not defer_to_owner and relay_forward is None:
                    relay_forward = await _start_supervised_relay(
                        args.name,
                        connection.config,
                        relay_port,
                        context="CodeSpace connect",
                        host_port_resolver=lambda: relay_launch.effective_relay_port(
                            config
                        ),
                        serving_probe=_relay_serving_probe,
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

        # Heal a dropped ControlMaster between retryable idempotent execs (plugin
        # staging / registration). ensure_connected is idempotent: it reconnects
        # only if the master died, and no-ops for a healthy or direct-SSH link.
        async def _reconnect() -> None:
            await manager.ensure_connected(
                args.name, source, port_forwards, preserve_existing_forwards=True,
            )

        # Deferring to the Connection Owner: wait briefly for it to bring up this
        # CodeSpace's relay before any git-dependent provisioning runs, so auth
        # over the relay is ready. Best-effort -- a timeout only warns (the Owner
        # keeps reconciling; the token cache cushions the gap).
        if defer_to_owner:
            if await _owner.await_owner_relay(args.name):
                log.info(
                    "Connection Owner relay for %s is live; riding it (no own -R).",
                    args.name,
                )
            else:
                if require_relay:
                    print(
                        "[FAIL] Required credential relay Connection Owner did not "
                        "become ready; refusing to launch.",
                        file=sys.stderr,
                    )
                    return _RELAY_REQUIRED_EXIT
                log.warning(
                    "Connection Owner did not report a live relay for %s within the "
                    "wait window -- git auth may lag until it reconciles.", args.name,
                )

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

        if not await _required_relay_ready():
            return _RELAY_REQUIRED_EXIT

        if not args.no_relay:
            # Stage 4 (target-auth-env): deploy the CodeSpace-side relay helpers
            # so remote auth resolves over the tunnel. Auth verification later
            # in this same stage catches missing local credentials up front.
            tracker.started(ConnectStage.TARGET_AUTH_ENV, "credential relay")
            helpers_ready = await _provision_relay_helpers(manager, args.name)
            if require_relay and not helpers_ready:
                print(
                    "[FAIL] Required credential relay auth-helper setup failed; "
                    "refusing to launch.",
                    file=sys.stderr,
                )
                return _RELAY_REQUIRED_EXIT

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
            if not args.no_relay and not no_plugin_staging:
                cs_plugin_dirs = await _register_codespace_plugins(
                    manager, args.name, getattr(args, "repo", None), config,
                    reconnect=_reconnect,
                )

            # Run repo-declared provision hooks (by-convention extras from the
            # adopted repo's .copilot-extensions/agent-codespaces/config.yaml).
            # Best-effort, idempotent.
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
        # Explicit ``--stage-plugin`` sources are staged whenever the relay is up,
        # even under ``--no-plugin-staging`` or the minimal provisioning path: the
        # caller asked for these specific host payloads (e.g. the native launcher
        # staging the harness plugins it later loads via ``--plugin-dir``). Those
        # flags suppress only the AUTOMATIC ``codespacePlugins`` lane, never an
        # explicit request. The empty-list case stays a no-op, preserving prior
        # behavior for callers that pass no ``--stage-plugin``.
        stage_sources = getattr(args, "stage_plugins", []) or []
        if not args.no_relay and stage_sources:
            plugin_dirs += await _stage_plugins(
                manager,
                args.name,
                stage_sources,
                repo_roots=getattr(config, "source_paths", ()) or (),
                reconnect=_reconnect,
            )
        # NB: the repo-own ``.ai`` lane is intentionally NOT folded here. A
        # CodeSpace ACP dispatch never runs through this front-owns-stdio ``ssh``
        # path -- ``session_manager`` refuses a non-Session-Host codespace target
        # -- so folding ``.ai`` --plugin-dir here would silently no-op for real
        # dispatches. The canonical repo-own lane lives in the Session-Host path
        # in agent-bridge, which resolves the dirs itself
        # (``repo_own_plugins_remote`` over its transport-exec seam,
        # PR2/dotfiles#1422) and folds them into its own ``acp_command``. This
        # path keeps only the related-repo staging above, which serves genuine
        # interactive/diagnostic ``agent-codespaces ssh`` use.
        remote_cmd = _finalize_remote_cmd(plugin_dirs)

        if args.stdio and remote_cmd:
            # #111: a fresh CodeSpace can ship copilot's loader stub without its
            # platform binary, so `copilot --acp` dies at LAUNCH_ACP ("Connection
            # closed"). Verify + self-repair (public-npm reinstall) up front.
            await _preflight_copilot_platform(manager, args.name)

        # Repository/platform preparation can be lengthy. Recheck admission
        # before launch rather than treating an earlier pong as lasting health.
        if not await _required_relay_ready():
            return _RELAY_REQUIRED_EXIT

        if getattr(args, "native_transport", False):
            from .native_transport import serve

            return await serve(args, manager, connection.config, relay_env)

        if args.stdio and remote_cmd:
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
            stdin_options = {}
            if getattr(args, "stdin_bytes", None) is not None:
                stdin_options["input_bytes"] = args.stdin_bytes
            result = await manager.exec_command(
                args.name, remote_cmd, timeout=args.timeout, **stdin_options
            )
            tracker.reached(ConnectStage.LAUNCH_ACP)
            return _emit_remote_cmd_result(result, args.timeout)

        # Keep the managed channel for relay probes and final settlement while
        # the foreground terminal uses its own PTY-bearing SSH child.
        interactive_command = _build_launch_command(
            getattr(args, "interactive_command", None),
            [],
            is_stdio=False,
            relay_env=relay_env,
            breadcrumb=breadcrumb_prelude(args.name),
        )
        from .interactive import ssh_options

        return await _interactive_ssh(
            args.name,
            ssh_options(
                getattr(args, "local_forward", []),
                getattr(args, "reverse_forward", []),
            ),
            relay_port=relay_port if not args.no_relay else None,
            relay_token=relay_token,
            command=interactive_command,
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
    try:
        with _lease_lock():
            execution_claims.assert_access(args.name, execution_identity, claim_owner)
        if execution_identity is not None:
            # Only this target-lock owner may begin infrastructure work. Pure
            # admission failures must not erase an existing cleanup verdict.
            execution_claims.mark(
                args.name, claim_owner, execution_identity, infrastructureStopped=False,
            )
    except (ClaimConflict, CoordinationRejected) as exc:
        target_lock.release()
        print(f"[BLOCKED] {exc}", file=sys.stderr)
        return _BUSY_EXIT if isinstance(exc, ClaimConflict) else _COORDINATION_EXIT
    except BaseException:
        target_lock.release()
        raise
    if progress:
        progress("owner-admission", "reached")

    # Place the Owner hold only AFTER the target lock is held, so a busy-target
    # rejection above can't leak a hold that would linger until its TTL. The
    # matching release lives in _run_with_cleanup's finally, which runs iff we
    # reach it (i.e. iff the lock was acquired and the hold was placed).
    if defer_to_owner:
        try:
            _owner.hold(args.name, owner_tenant)
            log.info(
                "Deferring credential relay for %s to the Connection Owner "
                "(tenant=%s)", args.name, owner_tenant,
            )
        except Exception as exc:
            log.warning(
                "Owner hold for %s failed (%s) -- owning the relay directly "
                "instead", args.name, exc,
            )
            defer_to_owner = False

    async def _run_with_cleanup() -> int:
        heartbeat = None
        input_watch = None
        if getattr(args, "native_transport", False):
            main_task = asyncio.current_task()

            async def watch_input():
                while not args.native_input.closed.is_set():
                    await asyncio.sleep(.1)
                main_task.cancel()

            input_watch = asyncio.create_task(watch_input())
        if claim_owner or defer_to_owner:
            from .ssh_lifetime import maintain_ownership

            heartbeat = asyncio.create_task(maintain_ownership(
                args.name, claim_owner, owner_tenant if defer_to_owner else None,
            ))
        try:
            if overall_timeout is not None and overall_timeout > 0:
                return await asyncio.wait_for(_run(), timeout=overall_timeout)
            return await _run()
        except (TimeoutError, asyncio.TimeoutError):
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
            if input_watch is not None:
                input_watch.cancel()
                await asyncio.gather(input_watch, return_exceptions=True)
            if heartbeat is not None:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)
            # Settle the CodeSpace obligation on the borrowing worktree if its
            # work is at-rest (resource-obligation-settlement Ph3b-wiring/2).
            # Runs while the SSH channel is still up (before disconnect), so the
            # read-only cleanliness probe can execute. Best-effort + degrade-safe:
            # un-probeable / dirty / no holder-ref -> the obligation stays active
            # (never settled blind).
            if fence_holder_ref and (
                not getattr(args, "native_transport", False) or getattr(args, "native_retired", False)
            ):
                await _settle_codespace_on_disconnect(
                    manager, args.name, fence_holder_ref,
                )
            if relay_forward is not None:
                await relay_forward.stop()
            # Release the Connection Owner hold this tenant placed (idempotent;
            # the Owner tears the relay down only when no tenant holds it).
            if defer_to_owner:
                try:
                    _owner.release(args.name, owner_tenant)
                except Exception as exc:
                    log.debug("Owner release for %s failed: %s", args.name, exc)
            await manager.disconnect(args.name)
            if getattr(args, "native_transport", False):
                args.native_cleanup_complete = True

    try:
        return asyncio.run(_run_with_cleanup())
    finally:
        target_lock.release()
