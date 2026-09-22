"""Session start and failed-start parity cleanup helpers."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
import uuid
from dataclasses import replace
from typing import Any

from .acp_client import AcpClient
from .connect import ConnectError, ConnectStage, ConnectTracker
from .events import EventLog
from .models import SessionStatus
from .session_manager import (
    _ACTIVE_STATES,
    _AcpLaunchTiming,
    _REQUEST_OVERRIDES_KEY,
    _append_plugin_dirs,
    _container_remote_child_argv,
    _codespace_claim_key,
    _default_cwd,
    _failed_acp_handshake_command,
    _generate_name,
    _venue_workspace_cwd,
    _workspace_key,
    CodespaceClaimConflictError,
    CodespaceCoordinationRejectedError,
    DaemonDrainingError,
    Session,
    SessionConflictError,
    log,
)
from .transport import AgentProcess, SpawnTarget


def _core() -> Any:
    from . import session_manager as core

    return core


class _SessionStartMixin:
    """Session start and failed-start parity cleanup helpers."""

    def _find_active_session(self, ws_key: tuple) -> Session | None:
        """Return an existing session that occupies the given workspace key.

        A session occupies a workspace when its status is in _ACTIVE_STATES.
        Used by the concurrency guard to enforce one session per CodeSpace.
        """
        for s in self._sessions.values():
            if s.status not in _ACTIVE_STATES:
                continue
            if _workspace_key(s.agent_name, s.target, s.caller_id) == ws_key:
                return s
        return None

    async def start_session(
        self,
        target: SpawnTarget,
        agent_name: str | None = None,
        caller_id: str | None = None,
        permission_callback: Any | None = None,
        mcp_servers: list[dict[str, Any]] | None = None,
        copilot_args: list[str] | None = None,
        env_overrides: dict[str, str] | None = None,
        caller_owner_ref: str | None = None,
        model: str | None = None,
        effort: str | None = None,
        parity_fault: str | None = None,
        replace_session_id: str | None = None,
        retain_container_lock_on_failure: bool = False,
    ) -> Session:
        """Create and start a new agent session.

        Spawns a copilot --acp --stdio subprocess, initializes the ACP
        protocol, and creates a new ACP session. The session is ready
        to receive prompts when this returns.

        Args:
            target: Where/how to spawn the agent.
            agent_name: Optional display name for the agent.
            caller_id: Optional caller identity (e.g. worktree ID) for
                session affinity.  Sessions with matching (agent_name,
                caller_id) are reused instead of creating new ones.
            permission_callback: Optional async callback for permission
                requests. Signature: (session_id, options, tool_call) ->
                RequestPermissionResponse. If set, auto_approve is disabled.
            mcp_servers: Optional per-session MCP toolset (ACP server specs)
                mounted into the ACP session at session/new. None preserves
                the historic empty toolset.
            copilot_args: Optional extra ``copilot`` CLI args appended to
                ``target.copilot_args`` for this session only (e.g. a per-run
                ``--additional-mcp-config``). None preserves the agent's args.
            env_overrides: Request-owned environment overrides already merged
                into ``target.env`` by the route.
        """
        if self._draining:
            raise DaemonDrainingError("session")
        from .protocol import FAILED_ACP_HANDSHAKE_FAULT

        if parity_fault not in {None, FAILED_ACP_HANDSHAKE_FAULT}:
            raise ValueError(f"unsupported parity fault: {parity_fault}")
        core = _core()
        # Per-session copilot args: append to the resolved target's args for
        # THIS spawn only (a fresh target copy so a shared/cached AgentConfig
        # target is never mutated). Every spawn path appends target.copilot_args,
        # so this reaches local, SSH, and command launches uniformly.
        existing_venue = target.venue if isinstance(target.venue, dict) else {}
        existing_overrides = existing_venue.get(_REQUEST_OVERRIDES_KEY)
        existing_request_env = (
            existing_overrides.get("env")
            if isinstance(existing_overrides, dict)
            else None
        )
        existing_request_args = (
            existing_overrides.get("copilot_args")
            if isinstance(existing_overrides, dict)
            else None
        )
        request_copilot_args = (
            list(existing_request_args)
            if copilot_args is None and isinstance(existing_request_args, list)
            else list(copilot_args or [])
        )
        request_env = (
            dict(existing_request_env)
            if env_overrides is None and isinstance(existing_request_env, dict)
            else dict(env_overrides or {})
        )
        if copilot_args:
            target = replace(
                target,
                copilot_args=[*target.copilot_args, *copilot_args],
            )
        if self._provider_backed_target(target):
            target = replace(
                target,
                venue={
                    **(target.venue or {}),
                    _REQUEST_OVERRIDES_KEY: {
                        "env": request_env,
                        "copilot_args": request_copilot_args,
                    },
                },
            )
        # #2178: bind the caller worktree onto the target so the worktree-resolve
        # step records it on the spawned (bridge) worktree, enabling the Picker's
        # "Jump to caller". caller_id is the caller's WORKTREE_ID (agent-bridge
        # convention); a non-worktree caller simply won't resolve in the Picker.
        if caller_id and not target.caller_worktree:
            target = replace(target, caller_worktree=caller_id)
        # resource-obligation-settlement Ph3c: carry the caller's qualified
        # ClaimRef onto the target so the worktree-resolve stamps it as the
        # bridge worktree's owner_ref (via `resolve --new --owner-ref`). The
        # carve then journals the reciprocal `worktree` claim on the caller, so
        # the caller's finalize gate sees the bridge session as an obligation and
        # the bridge worktree's finalize settles it (Phase 3a). Best-effort:
        # None (a non-worktree caller / stale runtime) simply skips it.
        if caller_owner_ref and not target.caller_owner_ref:
            target = replace(target, caller_owner_ref=caller_owner_ref)
        session_id = str(uuid.uuid4())[:12]
        name = _generate_name()
        now = time.time()

        # Concurrency guard: command-type (CodeSpace) agents allow only one
        # active session at a time, since they share a single checkout. This
        # check and the self._sessions registration below run synchronously
        # (no await in between), so concurrent start_session calls cannot
        # race past the guard.
        ws_key = _workspace_key(agent_name, target, caller_id)
        if ws_key is not None:
            existing = self._find_active_session(ws_key)
            if (
                existing is not None
                and existing.status == SessionStatus.STOPPED
                and not existing.acp_session_id
            ):
                # A zero-turn/failed-start incumbent has no ACP identity to
                # preserve and may carry stale provider metadata. Remove it
                # before the fresh target is registered, so the caller's new
                # provider resolution (workspace, Session Host transport,
                # launch policy) is not discarded by the workspace guard.
                log.info(
                    "Replacing stopped session %s with no ACP identity for %s",
                    existing.session_id,
                    agent_name,
                )
                await self.end_session(existing.session_id, force=True)
                existing = self._find_active_session(ws_key)
            if (
                existing is not None
                and existing.session_id != replace_session_id
            ):
                raise SessionConflictError(
                    agent_name=agent_name or "",
                    existing_session_id=existing.session_id,
                )

        session = Session(session_id, name, target, agent_name, caller_id=caller_id)
        # Per-session model / reasoning-effort override (agent-bridge create
        # --model/--effort). Retained on the Session so within-daemon resume /
        # reattach re-applies it (copilot ignores --model under --acp; the model
        # is set per-session via session/set_config_option -- see
        # AcpClient._apply_model_config).
        session.model_override = model
        session.effort_override = effort
        # An explicit caller-supplied mcp_servers always wins; otherwise fall
        # back to the resolved target's own declared toolset
        # (AgentConfig.mcp_servers -> SpawnTarget.mcp_servers, see
        # agent_registry.py). This is the explicit-injection workaround for
        # Copilot CLI not reliably loading a custom agent's own
        # ``mcp-servers:`` frontmatter under headless/ACP sessions
        # (github/copilot-cli#2630). Reassigning the local `mcp_servers` (not
        # just `session.mcp_servers`) so every downstream spawn call below
        # -- which reads this same local, not the Session attribute -- also
        # picks up the resolved value on this, the very first spawn.
        mcp_servers = (
            list(target.mcp_servers or [])
            if mcp_servers is None
            else list(mcp_servers)
        )
        session.mcp_servers = [dict(server) for server in mcp_servers]
        session.event_log = EventLog(
            db=self._db,
            session_id=session_id,
            worktree_id=target.worktree_id,
        )

        # Wire ACP events into the session's event log
        def on_acp_event(event_type: str, data: dict[str, Any]) -> None:
            if session.event_log:
                session.event_log.append(event_type, data)
            self._capture_progress(session, event_type, data)
            if event_type == "usage_update":
                self._handle_usage_update(session, data)

        # Persist to DB. `config_json` carries `mcp_servers` -- otherwise a
        # session's declared per-session MCP toolset (e.g. a reviewer's
        # dedicated, credential-bound tools) lives ONLY in this in-memory
        # Session object and is silently lost on any daemon restart, not just
        # a resume within the same process (aperture-labs #7239).
        config_json = (
            json.dumps({"mcp_servers": session.mcp_servers})
            if session.mcp_servers else None
        )
        self._db.create_session(
            session_id=session_id,
            name=name,
            agent_name=agent_name,
            caller_id=caller_id,
            target_dir=target.cwd,
            target_type=target.type,
            status=SessionStatus.STARTING.value,
            now=now,
            target_json=target.to_json(),
            config_json=config_json,
        )

        session.status = SessionStatus.STARTING
        self._sessions[session_id] = session

        tracker = ConnectTracker(session.event_log.append, session_id=session_id)
        # Stage 3 (SSH connect) is patient for codespace boot, else the
        # general ssh_connect budget.
        connect_timeout = (
            self._timeouts.codespace_boot
            if target.type == "command" or target.spawn_command
            else self._timeouts.ssh_connect
        )
        client: AcpClient | None = None
        agent_proc: AgentProcess | None = None
        acp_sid: str | None = None

        async def _cleanup_failed_process_launch() -> None:
            """Reap a process-owned launch before recording terminal failure."""
            if agent_proc is None:
                return
            pid = agent_proc.pid
            if client is not None:
                with contextlib.suppress(Exception):
                    await client.shutdown()
            # AcpClient.shutdown owns this same process, but retain the
            # AgentProcess whole-tree kill as a fallback if shutdown failed or
            # returned before a wrapper/SSH descendant exited.
            if agent_proc.alive:
                with contextlib.suppress(Exception):
                    await agent_proc.kill()
            session.client = None
            session.event_log.append("failed_launch_cleanup", {
                "pid": pid,
                "reaped": not agent_proc.alive,
            })
            log.info(
                "Reaped failed process-owned launch for session %s (pid=%s, alive=%s)",
                session_id,
                pid,
                agent_proc.alive,
            )

        try:
            cs_target = None
            # Prefer the structured provider metadata (#177); fall back to
            # shape-detecting the spawn_command for agents registered before
            # the metadata seam existed (back-compat).
            if isinstance(target.codespace, dict) and target.codespace.get("name"):
                cs_target = target.codespace
            elif target.spawn_command:
                from .session_host.codespace_transport import parse_codespace_target
                cs_target = parse_codespace_target(target.spawn_command)
            container_target = (
                target.container
                if isinstance(target.container, dict)
                and target.container.get("name")
                else None
            )
            if parity_fault:
                session.parity_fault_result = {
                    "fault": parity_fault,
                    "provider_cleanup": container_target is None,
                }

            if target.type == "local":
                # Session-Host mode: the child lives in a survivable host that
                # outlives this frontend (goal 1/3). resolve->launch host->
                # reattach over loopback->drive ACP.
                client, acp_sid = await self._connect_via_session_host(
                    target,
                    tracker=tracker,
                    session_id=session_id,
                    on_acp_event=on_acp_event,
                    permission_callback=permission_callback,
                    mcp_servers=mcp_servers,
                    model=model,
                    effort=effort,
                )
            elif container_target is not None:
                # Trusted containers use the same durable far-side Session Host
                # model as CodeSpaces.  agent-containers prepares only the SSH
                # endpoint and one launch's secret-backed env command; all Host,
                # forwarding, authority, and recovery ownership stays here.
                from .relay_state import get_live_relay_port
                from .session_host.container_transport import (
                    build_container_spawner,
                    cleanup_container_session_host,
                    ensure_container_ready,
                    prepare_container_session_host,
                )
                from .session_host.spawner import RemoteSpawnCleanupPendingError

                if replace_session_id:
                    self._transfer_container_lock(
                        replace_session_id,
                        session_id,
                        container_target["name"],
                    )
                else:
                    self._acquire_container_lock(
                        session_id, container_target["name"],
                    )
                prepared = None
                try:
                    await ensure_container_ready(container_target)
                    prepared = await prepare_container_session_host(
                        container_target,
                        get_live_relay_port(),
                    )
                    container_target["ssh"] = prepared["ssh"]
                    container_target["state_command"] = prepared["state_command"]
                    self._db.update_session_target(
                        session_id,
                        target.to_json(),
                        target.cwd,
                    )
                    container_spawner = build_container_spawner(
                        container_target,
                        prepared=prepared,
                        ready_timeout=self._timeouts.session_host_ready,
                        unexpected_reap_seconds=(
                            self._session_host_unexpected_reap_seconds
                        ),
                        active_reap_seconds=self._session_host_active_reap_seconds,
                        # Symmetric with the resume path (`_resume_via_new_
                        # remote_host`): when this container declares a
                        # credential-relay reverse-forward, the INITIAL launch
                        # must also refuse to admit a prompt until that relay
                        # actually answers -- not just resume. Without this, a
                        # freshly launched credential-requiring container
                        # session could silently start with no working relay
                        # and only surface the failure later, mid-turn, when a
                        # tool call needs it.
                        require_relay_ready=(
                            container_target.get("relay_remote_port")
                            is not None
                        ),
                    )
                    remote_cwd = (
                        prepared.get("workspace_folder")
                        or container_target.get("workspace_folder")
                        or None
                    )
                    plugin_dirs = (
                        []
                        if parity_fault
                        else await core._resolve_remote_ai_plugin_dirs(
                            container_spawner.transport,
                            f"container:{container_target['name']}",
                            remote_cwd,
                        )
                    )
                    client, acp_sid = await self._connect_via_session_host(
                        target,
                        tracker=tracker,
                        session_id=session_id,
                        on_acp_event=on_acp_event,
                        permission_callback=permission_callback,
                        mcp_servers=mcp_servers,
                        spawner=container_spawner,
                        remote_child_argv=_container_remote_child_argv(
                            container_target,
                            prepared,
                            plugin_dirs,
                            acp_command_override=(
                                _failed_acp_handshake_command()
                                if parity_fault
                                else None
                            ),
                        ),
                        remote_cwd=remote_cwd,
                        model=model,
                        effort=effort,
                        parity_fault_result=session.parity_fault_result,
                    )
                except Exception as exc:
                    if isinstance(exc, RemoteSpawnCleanupPendingError):
                        self._set_container_launch_pending(session_id, True)
                        with contextlib.suppress(Exception):
                            await self._recover_remote_host_records(
                                allow_wake=True,
                                session_ids={session_id},
                            )
                            provisional = self._host_index.get(session_id)
                            if provisional is not None:
                                self._reap_host_record(
                                    provisional,
                                    "incomplete container Session Host launch",
                                )
                    elif (
                        not parity_fault
                        and not retain_container_lock_on_failure
                    ):
                        self._release_container_lock(session_id)
                    raise
                finally:
                    if prepared is not None:
                        try:
                            cleaned = await cleanup_container_session_host(
                                container_target,
                                prepared,
                            )
                        except Exception:
                            cleaned = False
                            log.warning(
                                "Container launch-env cleanup failed for %s",
                                container_target["name"],
                                exc_info=True,
                            )
                        if session.parity_fault_result is not None:
                            session.parity_fault_result[
                                "provider_cleanup"
                            ] = cleaned
                            if (
                                cleaned
                                and session.parity_fault_result.get(
                                    "remote_authority_removed"
                                ) is True
                            ):
                                self._release_container_lock(session_id)
            elif cs_target is not None:
                # CodeSpace Session-Host mode (#177): bootstrap the Host inside
                # the CodeSpace, forward its loopback endpoint, and drive ACP over
                # it -- so a host sleep/tunnel flap disconnects the front while
                # copilot keeps running on the CS and the front reattaches by
                # cursor. The relay port rides the persistent forward's -R for
                # ADO/git during a build.
                from .session_host.codespace_transport import build_codespace_spawner

                # #897 Increment B step 2: acquire the exclusive, worktree-keyed
                # claim BEFORE establishing the Session-Host transport. A
                # CodeSpace is fronted by exactly one bridge, and Session-Host
                # dispatch never runs ``agent-codespaces ssh`` (the direct-path
                # enforcement point), so this is where a second worktree
                # dispatching to an already-claimed CodeSpace is bounced instead
                # of clobbering the incumbent. Degrade-safe: no owner / disabled
                # / binstub absent -> proceed unclaimed, today's behavior.
                claim_owner = getattr(target, "caller_worktree", None) or caller_id
                core = _core()
                _claim_status, _claim_detail = core._claim_codespace(
                    cs_target["name"],
                    claim_owner or "",
                    holder_ref=getattr(target, "caller_owner_ref", None),
                )
                if _claim_status == "conflict":
                    raise CodespaceClaimConflictError(
                        cs_target["name"], claim_owner or "", _claim_detail
                    )
                if _claim_status == "coordination-rejected":
                    raise CodespaceCoordinationRejectedError(
                        cs_target["name"], claim_owner or "", _claim_detail
                    )

                # Same-machine mirror of the container lock acquired below (see
                # ``_codespace_locks``'s docstring in ``session_core``): guards
                # the shared credential-relay reverse-forward this Session-Host
                # transport is about to open against a concurrent local
                # ``agent-codespaces ssh``/``copilot`` invocation (or a second
                # daemon dispatch) against the SAME CodeSpace on this machine.
                if replace_session_id:
                    self._transfer_codespace_lock(
                        replace_session_id, session_id, cs_target["name"],
                    )
                else:
                    self._acquire_codespace_lock(session_id, cs_target["name"])


                # path injects, so a detached copilot on the CS has working
                # ADO/git auth over the credential relay (the daemon owns the
                # relay; the per-codespace token is minted by agent-codespaces).
                # Guarded: if agent-codespaces isn't importable, the Host runs
                # auth-light (fine for ACP + non-ADO turns).
                #
                # Source the port from the daemon's *actually-bound* relay
                # (get_live_relay_port) rather than agent-codespaces' static
                # config port, so the CS env + the persistent forward's ``-R``
                # follow the live relay -- mirroring the mesh path (commit
                # 8a8bd8f8) and fixing CodeSpace ADO auth when the relay isn't on
                # the declared 9857 (dotfiles #489/#540 pt3). None -> the callee
                # falls back to the config port.
                relay_prelude = ""
                relay_port = None
                from .relay_state import get_live_relay_port
                core = _core()
                relay_prelude, relay_port = core._resolve_relay_launch_env(
                    cs_target["name"], get_live_relay_port()
                )
                cs_spawner = build_codespace_spawner(
                    cs_target["name"], cs_target["repo"], relay_port=relay_port,
                    unexpected_reap_seconds=self._session_host_unexpected_reap_seconds,
                    active_reap_seconds=self._session_host_active_reap_seconds,
                )
                # The acp_command is a far-side SHELL string (e.g.
                # ``cd /workspaces/repo && copilot --acp --stdio``), not an argv,
                # so the Session Host execs it through a login shell (with the
                # relay prelude prepended); copilot inherits the host's stdio pipe
                # as fd 0/1 and its exit ends the shell (child-liveness tracks it).
                #
                # Model/effort are NOT passed as ``--model`` launch flags here:
                # copilot ignores them in ``--acp`` mode. The dispatched agent's
                # model is set by the ACP client after the session exists, via
                # ``session/set_config_option`` (see AcpClient._apply_model_config,
                # dotfiles#790) -- the single, uniform mechanism for every
                # dispatch path.
                acp_command = cs_target["acp_command"]
                # Fold the CodeSpace repo's OWN enabled ``.ai`` plugin dirs into
                # the launch as ``--plugin-dir`` so the dispatched ACP agent
                # loads the product repo's own in-repo skills/MCP. ``copilot
                # --acp`` ignores ``enabledPlugins`` -- only ``--plugin-dir``
                # surfaces plugin skills -- and this Session-Host path (never the
                # front-owns-stdio ``agent-codespaces ssh`` path) is where a real
                # CodeSpace dispatch launches, so the fold MUST happen here
                # (dotfiles#1274 WS1-skills). Resolved by shelling agent-codespaces'
                # own venv (process boundary), mirroring the relay-launch-env
                # seam. Best-effort: [] on any failure -> unchanged launch. The
                # payloads already live in the checkout, so ``--plugin-dir`` at
                # ``/workspaces/<repo>/.ai/<name>`` needs no install/egress. The
                # flags append to the tail of ``cd <repo> && copilot --acp …``, so
                # they land on the ``copilot`` invocation.
                ai_plugin_dirs = (
                    []
                    if parity_fault
                    else await core._resolve_remote_ai_plugin_dirs(
                        cs_spawner.transport,
                        f"codespace:{cs_target['name']}",
                        cs_target.get("workspace_folder") or None,
                    )
                )
                acp_command = (
                    _failed_acp_handshake_command()
                    if parity_fault
                    else _append_plugin_dirs(acp_command, ai_plugin_dirs)
                )
                if ai_plugin_dirs:
                    log.info(
                        "Folded %d repo-own .ai --plugin-dir(s) into %s launch: %s",
                        len(ai_plugin_dirs), cs_target["name"], ai_plugin_dirs,
                    )
                remote_argv = [
                    "bash", "-lc", relay_prelude + acp_command,
                ]
                # Copilot runs its tools from the ACP session cwd, so it must be
                # the CodeSpace workspace checkout (e.g. /workspaces/example-web) --
                # NOT the _default_cwd() /home/<user> fallback, or the agent works
                # blind with no repo in view. Prefer the structured provider
                # workspace_folder; else the cd-target parsed from acp_command.
                remote_cwd = cs_target.get("workspace_folder") or None
                from .session_host.spawner import RemoteSpawnCleanupPendingError

                try:
                    client, acp_sid = await self._connect_via_session_host(
                        target,
                        tracker=tracker,
                        session_id=session_id,
                        on_acp_event=on_acp_event,
                        permission_callback=permission_callback,
                        mcp_servers=mcp_servers,
                        spawner=cs_spawner,
                        remote_child_argv=remote_argv,
                        remote_cwd=remote_cwd,
                        model=model,
                        effort=effort,
                        parity_fault_result=session.parity_fault_result,
                    )
                except RemoteSpawnCleanupPendingError:
                    raise
                except Exception:
                    if not parity_fault:
                        claim_key = _codespace_claim_key(session.target)
                        if claim_key is not None:
                            core._release_codespace_claim(*claim_key)
                        self._release_codespace_lock(session_id)
                    raise
            elif self._is_codespace_target(target):
                # A CodeSpace target MUST run under a Session Host: only then does
                # copilot's stdio belong to a survivable host on the far side, so
                # a transport drop (tunnel flap, daemon restart, credential-relay
                # TTL) merely detaches the front instead of closing the child's
                # stdio and self-cancelling its in-flight turn ("Operation
                # cancelled by user"). Session Hosts are always on (dotfiles#1478),
                # so reaching this branch means the codespace target could not be
                # resolved to a spawner -- fail loud rather than silently degrade
                # to a non-survivable process-owned session that loses in-flight
                # work on any hiccup.
                raise RuntimeError(
                    f"CodeSpace target {getattr(target, 'agent_name', None)!r} "
                    f"was detected but could not be resolved to a CodeSpace "
                    f"spawner/transport (cs_target=unresolved). Session Hosts are "
                    f"always on, so this is a resolution/configuration failure, "
                    f"not a disabled mode. Refusing to fall back to the "
                    f"process-owned (non-survivable) path."
                )
            else:
                # Process-owned (front-owns-stdio) transport for ssh/command
                # targets (mesh, elevated, spawn_command providers). Local +
                # CodeSpace always run under a survivable Session Host above;
                # ssh/command have no host-boundary spawner yet (SshSpawner /
                # ElevatedSpawner are the remaining gap -- see
                # ThomasMichon/copilot-extensions#566), so this is their only
                # path. It is NOT reachable for a local target. Emits per-stage
                # checkpoints (auth-env, ssh-connect, worktree) into the event log.
                core = _core()
                agent_proc = await core.spawn(
                    target,
                    tracker=tracker,
                    connect_timeout=connect_timeout,
                    session_id=session_id,
                )

                # Stage 7: launch + initialize Copilot in ACP mode. Should be
                # fast; bound it so a hung launch fails fast.
                with tracker.stage(ConnectStage.LAUNCH_ACP):
                    timing = _AcpLaunchTiming(
                        session_id=session_id,
                        mode="process",
                        boundary=getattr(target, "type", "unknown"),
                    )
                    client = core.AcpClient(
                        on_event=on_acp_event,
                        on_permission=permission_callback,
                        model_override=model,
                        effort_override=effort,
                    )
                    if permission_callback:
                        client.auto_approve = False
                    try:
                        step_started = time.monotonic()
                        await asyncio.wait_for(
                            client.start(agent_proc.proc),
                            timeout=self._timeouts.session_start,
                        )
                        timing.add("acp_initialize", time.monotonic() - step_started)
                        # Create ACP session -- binstub agents resolve CWD
                        # remotely, so target.cwd may be None.  The ACP spec
                        # requires an absolute path.  Prefer the venue's concrete
                        # workspace folder (container fleets surface it) so the
                        # agent runs from the repo checkout, not the home default;
                        # else derive a home-dir default.
                        session_cwd = (
                            _venue_workspace_cwd(target)
                            or target.cwd
                            or _default_cwd(target)
                        )
                        step_started = time.monotonic()
                        acp_sid = await asyncio.wait_for(
                            client.new_session(
                                cwd=session_cwd,
                                mcp_servers=mcp_servers,
                                timing_callback=timing.add,
                            ),
                            timeout=self._timeouts.session_new,
                        )
                        timing.add("session_new_total", time.monotonic() - step_started)
                        log.info("ACP launch timing: result=ok %s", timing.summary())
                    except (TimeoutError, asyncio.TimeoutError) as exc:
                        # Leave a queryable marker so a stalled launch is not a
                        # silent [starting]->[stopped] (#1468). The process-owned
                        # client captured the child's startup stderr -- include
                        # the tail.
                        with contextlib.suppress(Exception):
                            on_acp_event("acp_launch_timeout", {
                                "stage": "LAUNCH_ACP",
                                "mode": "process",
                                "handshake_timeout_s": self._timeouts.session_start,
                                "session_new_timeout_s": self._timeouts.session_new,
                                "stderr_tail": client.stderr_tail(),
                                "timing_ms": timing.step_map_ms(),
                            })
                        log.warning("ACP launch timing: result=timeout %s", timing.summary())
                        raise ConnectError(
                            ConnectStage.LAUNCH_ACP,
                            f"Copilot ACP launch timed out "
                            f"(handshake {self._timeouts.session_start}s / "
                            f"session/new {self._timeouts.session_new}s). A cold "
                            f"session/new on a large workspace may need a larger "
                            f"budget -- raise timeouts.session_new in "
                            f"~/.agent-bridge/config.yaml and restart the daemon.",
                            retryable=False,
                            cause=exc,
                        ) from exc
                    except Exception as exc:
                        log.warning(
                            "ACP launch timing: result=error error=%s %s",
                            type(exc).__name__,
                            timing.summary(),
                        )
                        raise

            session.client = client
            session.acp_session_id = acp_sid
            session.status = SessionStatus.IDLE
            if session.event_log:
                session.event_log.set_telemetry_identity(
                    acp_session_id=acp_sid,
                    worktree_id=target.worktree_id,
                )
            self._db.update_session_acp_id(session_id, acp_sid)
            # Persist target with resolved values (worktree_id, cwd from plan)
            self._db.update_session_target(
                session_id, target.to_json(), target.cwd
            )
            self._db.update_session_status(
                session_id, SessionStatus.IDLE.value, time.time(), pid=session.pid
            )
            session.event_log.append("session_state_changed", {
                "status": SessionStatus.IDLE.value,
                "acp_session_id": acp_sid,
            })
            log.info(
                "Session %s (%s) started, pid=%s, acp=%s",
                session_id, name, session.pid, acp_sid,
            )
            # Phase 4a (worktree-self-knowledge): register this ACP session into
            # the agent-worktrees ground layer so the derived head is correct and
            # the worktree is not left "looking unowned" (the vision's *explicit
            # session binding* names "a spawned successor, a headless launch").
            # LOCAL worktrees only -- a remote worktree's ground layer lives on
            # its own machine. Best-effort / fail-open: never breaks a launch.
            if target.type == "local" and target.worktree_id and acp_sid:
                from . import worktree_lineage
                with contextlib.suppress(Exception):
                    worktree_lineage.register_session(
                        target.worktree_id, acp_sid, pid=session.pid,
                        worktree_dir=target.cwd,
                    )
        except ConnectError as exc:
            # Structured failure: we know exactly which stage failed and
            # whether a retry could help -- never an opaque "agent died".
            await _cleanup_failed_process_launch()
            self._mark_session_failed(session, trigger="connect_failed")
            session.event_log.append("connect_failed", {
                "stage": int(exc.stage),
                "stage_name": exc.stage.name,
                "retryable": exc.retryable,
                "message": exc.detail,
            })
            session.event_log.append("error", {"message": str(exc)})
            log.error(
                "Session %s failed at stage %d/%s: %s",
                session_id, int(exc.stage), exc.stage.name, exc.detail,
                exc_info=True,
            )
        except CodespaceClaimConflictError as exc:
            # #897: the CodeSpace is exclusively held by another live worktree.
            # This is a deliberate BOUNCE, not a transport failure -- mark the
            # session FAILED with an actionable, distinguishable event so a
            # caller (or the Picker) can tell "someone else owns this box" apart
            # from an infra failure, and re-dispatch elsewhere or take over with
            # --force-claim. No claim was acquired, so there is nothing to
            # release here.
            await _cleanup_failed_process_launch()
            self._mark_session_failed(
                session, trigger="codespace_claim_conflict"
            )
            session.event_log.append("codespace_claim_conflict", {
                "codespace": exc.codespace,
                "owner": exc.owner,
                "message": exc.detail or str(exc),
            })
            session.event_log.append("error", {"message": str(exc)})
            log.warning(
                "Session %s bounced: CodeSpace '%s' is claimed by another "
                "worktree (dispatcher '%s')",
                session_id, exc.codespace, exc.owner,
            )
        except CodespaceCoordinationRejectedError as exc:
            await _cleanup_failed_process_launch()
            self._mark_session_failed(
                session, trigger="codespace_coordination_rejected"
            )
            session.event_log.append("codespace_coordination_rejected", {
                "codespace": exc.codespace,
                "owner": exc.owner,
                "message": exc.detail or str(exc),
            })
            session.event_log.append("error", {"message": str(exc)})
            log.warning(
                "Session %s blocked: CodeSpace '%s' lacks durable coordination "
                "for dispatcher '%s'",
                session_id, exc.codespace, exc.owner,
            )
        except Exception as exc:
            await _cleanup_failed_process_launch()
            self._mark_session_failed(session, trigger="start_exception")
            session.event_log.append("error", {"message": str(exc)})
            log.error("Failed to start session %s: %s", session_id, exc, exc_info=True)

        session.touch()
        return session

    async def finalize_parity_fault_start(
        self,
        session: Session,
        fault: str,
    ) -> dict[str, Any]:
        """Remove a confirmed failed-start transaction and report booleans."""
        result = dict(session.parity_fault_result or {})
        result["fault"] = fault
        if session.status != SessionStatus.FAILED:
            with contextlib.suppress(Exception):
                await self.end_session(session.session_id, force=True)
            raise RuntimeError(
                "failed ACP handshake parity injection unexpectedly started "
                "a usable session"
            )
        cleanup_confirmed = all(
            result.get(name) is True
            for name in (
                "host_process_removed",
                "child_process_removed",
                "remote_authority_removed",
                "forward_removed",
                "relay_removed",
                "provider_cleanup",
            )
        )
        if not cleanup_confirmed:
            claim_key = _codespace_claim_key(session.target)
            ownership_retained = (
                session.session_id in self._container_lock_sessions
                or session.session_id in self._codespace_lock_sessions
                or claim_key is not None
            )
            result.update({
                "cleanup_confirmed": False,
                "ownership_retained": ownership_retained,
                "durable_session_retained": (
                    self._db.get_session(session.session_id) is not None
                ),
            })
            return result

        session_id = session.session_id
        claim_key = _codespace_claim_key(session.target)
        core = _core()
        claim_removed = (
            True
            if claim_key is None
            else core._release_codespace_claim(*claim_key)
        )
        result["codespace_claim_removed"] = claim_removed
        if not claim_removed:
            result.update({
                "cleanup_confirmed": False,
                "ownership_retained": True,
                "durable_session_retained": (
                    self._db.get_session(session_id) is not None
                ),
            })
            return result
        self._release_container_lock(session_id)
        self._release_codespace_lock(session_id)
        await self.end_session(session_id, force=True)
        host_index_removed = (
            self._host_index is None
            or self._host_index.get(session_id) is None
        )
        target_lock_removed = (
            session_id not in self._container_lock_sessions
            and session_id not in self._codespace_lock_sessions
        )
        result.update({
            "session_row_removed": self._db.get_session(session_id) is None,
            "session_memory_removed": session_id not in self._sessions,
            "host_index_removed": host_index_removed,
            "forward_removed": session_id not in self._forwards,
            "relay_removed": session_id not in self._relays,
            "target_lock_removed": target_lock_removed,
            "ownership_retained": False,
        })
        result["cleanup_confirmed"] = all(
            result.get(name) is True
            for name in (
                "host_process_removed",
                "child_process_removed",
                "remote_authority_removed",
                "provider_cleanup",
                "session_row_removed",
                "session_memory_removed",
                "host_index_removed",
                "forward_removed",
                "relay_removed",
                "target_lock_removed",
                "codespace_claim_removed",
            )
        )
        return result

