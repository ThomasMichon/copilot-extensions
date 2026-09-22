"""Session Host connection, relay, and reap helpers."""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import is_dataclass
from typing import Any

from .acp_client import AcpClient
from .connect import ConnectError, ConnectStage
from .models import SessionStatus
from .session_manager import _AcpLaunchTiming, _default_cwd, Session, log
from .transport import SpawnTarget


def _core() -> Any:
    from . import session_manager as core

    return core


class _SessionHostConnectionMixin:
    """Session Host connection, relay, and reap helpers."""

    async def _connect_via_session_host(
        self,
        target: SpawnTarget,
        *,
        tracker: Any,
        session_id: str,
        on_acp_event: Any,
        permission_callback: Any | None,
        mcp_servers: list[dict[str, Any]] | None = None,
        spawner: Any | None = None,
        remote_child_argv: list[str] | None = None,
        remote_cwd: str | None = None,
        load_session_id: str | None = None,
        model: str | None = None,
        effort: str | None = None,
        parity_fault_result: dict[str, Any] | None = None,
    ) -> tuple[AcpClient, str]:
        """Spawn a child inside a survivable Session Host and drive ACP over the
        reattachable loopback endpoint (Session-Host mode).

        ``spawner`` selects the boundary seam (default :class:`LocalSpawner`; a
        :class:`CodeSpaceSpawner` bootstraps the Host inside a CodeSpace and
        stands up the ``-L`` forward). Registers the durable host index -- with
        the remote-boundary ``endpoint`` descriptor -- so a restarted frontend
        can re-forward and reattach. Teardown DETACHES (host-mode
        ``AcpClient.shutdown``), never reaping the child inadvertently -- goal 1.
        """
        from . import __version__
        from .session_host.acp_adapter import open_acp_streams
        from .session_host.client import SessionHostClient
        from .session_host.host_index import HostRecord
        from .session_host.spawner import LocalSpawner
        from .transport import resolve_local_launch

        if spawner is None:
            spawner = LocalSpawner(
                unexpected_reap_seconds=self._session_host_unexpected_reap_seconds,
                active_reap_seconds=self._session_host_active_reap_seconds,
                ready_timeout=self._timeouts.session_host_ready,
            )

        timing = _AcpLaunchTiming(
            session_id=session_id,
            mode="session-host",
            boundary=getattr(spawner, "boundary", "unknown"),
        )

        if remote_child_argv is not None:
            # Remote boundary (CodeSpace/mesh): the child runs on the FAR side,
            # so there is no local worktree to resolve -- the Spawner is handed
            # the remote copilot argv + remote cwd directly, and the far-side
            # Session Host owns copilot's stdio as a clean local pipe there.
            args, work_dir, env = remote_child_argv, remote_cwd, {}
        else:
            args, work_dir, env = await resolve_local_launch(
                target, tracker=tracker, session_id=session_id,
            )
            if work_dir and not target.cwd:
                target.cwd = work_dir

        with tracker.stage(ConnectStage.LAUNCH_ACP):
            # Tag the child's environment with its own bridge session id so a
            # command the agent runs (e.g. an in-session `test-chamber services
            # agent-bridge update`) can tell the drain to spare THIS session --
            # cancelling the turn running the update would abort the update
            # (#1790). Any descendant process inherits it.
            child_env = dict(env or {})
            child_env["AGENT_BRIDGE_SESSION_ID"] = session_id
            # Bootstrap the Session Host through the boundary Spawner seam (P2a).
            # The seam is boundary-agnostic: LocalSpawner binds a loopback port
            # directly; CodeSpaceSpawner ships+launches the Host on the CS and
            # stands up an -L forward so the frontend below still dials
            # 127.0.0.1:<local_port>. spawn() blocks briefly on host readiness,
            # so it is already off-loop.
            step_started = time.monotonic()
            spawned = await spawner.spawn(
                args, cwd=work_dir, env=child_env, session_id=session_id,
            )
            timing.add("host_spawn", time.monotonic() - step_started)
            # Retain a remote-boundary forward so reattach can refresh it and
            # teardown can cancel it.
            if getattr(spawned, "forward", None) is not None:
                self._forwards[session_id] = spawned.forward
            relays = getattr(spawned, "relay", None)
            if relays is not None:
                if not isinstance(relays, (list, tuple, set)):
                    relays = [relays]
                relays = list(relays)
                if relays:
                    self._relays[session_id] = relays
            step_started = time.monotonic()
            sock = await SessionHostClient.connect(port=spawned.local_port)
            timing.add("host_connect", time.monotonic() - step_started)
            step_started = time.monotonic()
            await sock.attach(0, nonce=spawned.nonce.encode())
            timing.add("host_attach", time.monotonic() - step_started)
            step_started = time.monotonic()
            streams = await open_acp_streams(sock)
            timing.add("host_streams", time.monotonic() - step_started)

            async def _closer() -> None:
                await streams.aclose()
                await sock.close()

            core = _core()
            client = core.AcpClient(
                on_event=on_acp_event,
                on_permission=permission_callback,
                model_override=model,
                effort_override=effort,
            )
            # Surface a mid-session transport drop (loopback socket down, host +
            # child alive) as ``disconnected`` so the reattach driver fires (P1).
            streams.on_transport_lost = client.mark_transport_lost
            streams.on_child_exit = client.mark_host_child_exited
            # Retain the host control channel so the manager can push STATUS
            # (reapable) / DETACH (graceful) for host self-reap (#51).
            client.session_host_client = sock
            if permission_callback:
                client.auto_approve = False
            try:
                step_started = time.monotonic()
                await asyncio.wait_for(
                    client.start_streams(
                        streams.reader, streams.writer,
                        child_pid=spawned.child_pid, closer=_closer,
                    ),
                    timeout=self._timeouts.session_start,
                )
                timing.add("acp_initialize", time.monotonic() - step_started)
                if streams.child_exit_code is not None:
                    client.mark_host_child_exited(streams.child_exit_code)
                    raise ConnectionError(
                        "Session Host child exited during ACP startup "
                        f"(code={streams.child_exit_code})"
                    )
                session_cwd = remote_cwd or target.cwd or _default_cwd(target)
                if load_session_id:
                    step_started = time.monotonic()
                    await asyncio.wait_for(
                        client.load_session(
                            cwd=session_cwd,
                            session_id=load_session_id,
                            mcp_servers=mcp_servers,
                            timing_callback=timing.add,
                        ),
                        timeout=self._timeouts.session_new,
                    )
                    timing.add("session_load_total", time.monotonic() - step_started)
                    acp_sid = load_session_id
                else:
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
                # Leave a queryable marker so a stalled session-host resume/launch
                # is not a silent [starting]->[stopped] (#1468). The child's
                # stderr lives in the Session Host here (not this frontend), so
                # the tail is empty -- the always-logged child stderr carries it.
                with contextlib.suppress(Exception):
                    on_acp_event("acp_launch_timeout", {
                        "stage": "LAUNCH_ACP",
                        "mode": "session-host",
                        "handshake_timeout_s": self._timeouts.session_start,
                        "session_new_timeout_s": self._timeouts.session_new,
                        "timing_ms": timing.step_map_ms(),
                    })
                log.warning("ACP launch timing: result=timeout %s", timing.summary())
                cleanup_confirmed = await self._rollback_failed_host_launch(
                    spawner,
                    spawned,
                    sock,
                    streams,
                    session_id,
                    parity_fault_result,
                )
                if not cleanup_confirmed:
                    from .session_host.spawner import (
                        RemoteSpawnCleanupPendingError,
                    )

                    raise RemoteSpawnCleanupPendingError(
                        "remote Session Host ACP initialization failed and "
                        f"cleanup is inconclusive for {session_id}; retaining "
                        "target ownership"
                    ) from exc
                raise ConnectError(
                    ConnectStage.LAUNCH_ACP,
                    f"Copilot ACP launch (session host) timed out "
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
                cleanup_confirmed = await self._rollback_failed_host_launch(
                    spawner,
                    spawned,
                    sock,
                    streams,
                    session_id,
                    parity_fault_result,
                )
                if not cleanup_confirmed:
                    from .session_host.spawner import (
                        RemoteSpawnCleanupPendingError,
                    )

                    raise RemoteSpawnCleanupPendingError(
                        "remote Session Host ACP initialization failed and "
                        f"cleanup is inconclusive for {session_id}; retaining "
                        "target ownership"
                    ) from exc
                raise

        if self._host_index is not None:
            self._host_index.register(HostRecord(
                session_id=session_id,
                port=spawned.local_port,
                host_pid=spawned.host_pid,
                child_pid=spawned.child_pid,
                host_version=__version__,
                protocol_version=spawned.protocol_version,
                state_file=spawned.state_file,
                created_at=time.time(),
                nonce=spawned.nonce,
                boundary=spawned.boundary,
                endpoint=getattr(spawned, "endpoint", {}) or {},
                extra={
                    "remote_authority_v2": spawned.boundary != "local",
                },
            ))
        # #4272 bridge-lock: mark this bridge-owned session's liveness as a
        # lattice file the picker reads cheaply, so a bare/bridge Copilot
        # (cwd=home) still shows ACTIVE (#1416). LOCAL boundary only -- a remote
        # child_pid is far-side, so its liveness isn't provable locally. Best-
        # effort: never breaks a launch.
        if getattr(spawned, "boundary", "local") == "local":
            from . import bridge_lock
            with contextlib.suppress(Exception):
                await bridge_lock.write(
                    session_id, target.worktree_id, spawned.child_pid)
        return client, acp_sid

    async def _rollback_failed_host_launch(
        self,
        spawner: Any,
        spawned: Any,
        sock: Any,
        streams: Any,
        session_id: str,
        result: dict[str, Any] | None,
    ) -> bool:
        """Reap a failed Host launch and remove every local holder."""
        with contextlib.suppress(Exception):
            await sock.terminate()
        with contextlib.suppress(Exception):
            await streams.aclose()
        with contextlib.suppress(Exception):
            await sock.close()

        remote = getattr(spawned, "boundary", "local") != "local"
        confirmed = not remote
        if remote:
            abort = getattr(spawner, "abort_spawned", None)
            if callable(abort):
                try:
                    confirmed = bool(await abort(spawned, session_id))
                except Exception:
                    confirmed = False
        with contextlib.suppress(Exception):
            await spawned.aclose()
        self._forwards.pop(session_id, None)
        self._relays.pop(session_id, None)
        if result is not None:
            result.update({
                "host_process_removed": confirmed,
                "child_process_removed": confirmed,
                "remote_authority_removed": confirmed,
                "forward_removed": session_id not in self._forwards,
                "relay_removed": session_id not in self._relays,
            })
        return confirmed

    async def _ensure_forward(
        self,
        rec: Any,
        *,
        refresh_relays: bool = False,
        require_relay_ready: bool = False,
    ) -> None:
        """Ensure a remote-boundary Host's frontend ``-L`` forward is up.

        No-op for a local Host (direct loopback, no forward). For a CodeSpace /
        mesh Host, (re-)establishes the forward so ``rec.port`` resolves before we
        dial it -- the ``refresh_endpoint()`` step of the reattach driver, driven
        from the durable ``rec.endpoint`` descriptor so it works even after a
        frontend restart with no live Spawner. Refreshes an existing ``-L``
        forward (cancel + re-establish) or rebuilds one from the endpoint.

        Credential relay ``-R`` specs in the same endpoint are supervised by
        dedicated relay handles owned separately from the frontend ``-L``. A
        normal front detach/reattach leaves an existing relay alone; a daemon
        restart (no in-memory relay) re-supervises it from the descriptor.
        """
        boundary = getattr(rec, "boundary", "local")
        endpoint = getattr(rec, "endpoint", None) or {}
        if boundary == "local" or not endpoint:
            return
        from .session_host.endpoints import forward_from_endpoint

        existing = self._forwards.get(rec.session_id)
        try:
            if existing is not None:
                local_port = await existing.refresh()
            else:
                fwd = forward_from_endpoint(endpoint)
                try:
                    local_port = await fwd.establish()
                except asyncio.CancelledError:
                    with contextlib.suppress(Exception):
                        await fwd.cancel()
                    raise
                except Exception:
                    with contextlib.suppress(Exception):
                        await fwd.cancel()
                    raise
                self._forwards[rec.session_id] = fwd
            if (
                getattr(rec, "port", None) != local_port
                or endpoint.get("local_port") != local_port
            ):
                rec.port = local_port
                endpoint["local_port"] = local_port
                rec.endpoint = endpoint
                if self._host_index is not None and is_dataclass(rec):
                    self._host_index.register(rec)
            if refresh_relays:
                await self._replace_relays_from_endpoint(
                    rec.session_id,
                    endpoint,
                    require_ready=require_relay_ready,
                )
            else:
                await self._ensure_relays_from_endpoint(
                    rec.session_id,
                    endpoint,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning(
                "Failed to (re-)establish forward for session %s (boundary=%s)",
                rec.session_id, boundary, exc_info=True,
            )
            raise

    async def _ensure_relays_from_endpoint(self, session_id: str, endpoint: dict) -> None:
        """Start endpoint-declared relay supervisors if this daemon owns none.

        The relay is independent of frontend reattach. When a live daemon already
        owns a relay for the Session Host, leave it alone; after a daemon restart
        the in-memory owner set is empty, so this method reconstructs the relay
        from the durable endpoint. If a prior handle is present but no longer
        alive, stop/replace it to avoid double ``-R`` binds.
        """
        if not (endpoint.get("reverse_forwards") or []):
            return
        existing = self._relays.get(session_id) or []
        if existing and all(getattr(relay, "is_alive", True) for relay in existing):
            return
        await self._replace_relays_from_endpoint(session_id, endpoint)

    async def _replace_relays_from_endpoint(
        self,
        session_id: str,
        endpoint: dict,
        *,
        require_ready: bool = False,
    ) -> None:
        """Stop any prior relay owner and start supervisors from ``endpoint``."""
        from .relay_state import get_live_relay_port
        from .session_host.endpoints import (
            CredentialRelayReadinessError,
            endpoint_serving_probe_factory,
            relay_forwards_from_endpoint,
            relay_ports_from_reverse_forwards,
            wait_for_relay_serving,
        )

        await self._stop_relays(session_id)
        reverse_forwards = list(endpoint.get("reverse_forwards") or [])
        relays = relay_forwards_from_endpoint(
            endpoint,
            host_port_resolver=get_live_relay_port,
            serving_probe_for_port=endpoint_serving_probe_factory(endpoint),
        )
        relay_ports = relay_ports_from_reverse_forwards(reverse_forwards)
        if require_ready and not relay_ports:
            raise CredentialRelayReadinessError(
                "credential relay is enabled but the stopped session has no "
                "recoverable reverse-forward declaration"
            )
        strict_probe_for_port = endpoint_serving_probe_factory(
            endpoint,
            fail_open=False,
        )
        started = []
        for relay, relay_port in zip(relays, relay_ports, strict=True):
            try:
                await relay.start()
                if require_ready:
                    try:
                        await wait_for_relay_serving(
                            strict_probe_for_port(relay_port),
                            timeout=5.0,
                        )
                    except Exception as exc:
                        raise CredentialRelayReadinessError(
                            "credential relay readiness probe failed for "
                            f"remote loopback port {relay_port}"
                        ) from exc
            except asyncio.CancelledError:
                with contextlib.suppress(Exception):
                    await relay.stop()
                for prior in started:
                    with contextlib.suppress(Exception):
                        await prior.stop()
                raise
            except Exception as exc:
                with contextlib.suppress(Exception):
                    await relay.stop()
                if require_ready:
                    for prior in started:
                        with contextlib.suppress(Exception):
                            await prior.stop()
                    if isinstance(exc, CredentialRelayReadinessError):
                        raise
                    raise CredentialRelayReadinessError(
                        "credential relay reverse-forward failed before stopped "
                        f"session {session_id} could resume"
                    ) from exc
                log.warning(
                    "Failed to start credential relay supervisor for session %s; "
                    "continuing without relay",
                    session_id, exc_info=True,
                )
                continue
            started.append(relay)
        if started:
            self._relays[session_id] = started

    async def _stop_relays(self, session_id: str) -> None:
        """Stop and forget a session's credential-relay supervisors."""
        relays = self._relays.pop(session_id, [])
        for relay in relays:
            with contextlib.suppress(Exception):
                await relay.stop()

    async def _drop_forward(self, session_id: str) -> None:
        """Cancel and forget a session's remote-boundary forwards (if any)."""
        await self._stop_relays(session_id)
        fwd = self._forwards.pop(session_id, None)
        if fwd is not None:
            with contextlib.suppress(Exception):
                await fwd.cancel()
        self._release_container_lock(session_id)
        self._release_codespace_lock(session_id)

    async def _reattach_one(
        self,
        rec: Any,
        session: Session,
        *,
        new_status: SessionStatus,
        send_resume: bool = False,
        prune_on_fail: bool = False,
        refresh_relays: bool = False,
        require_relay_ready: bool = False,
    ) -> bool:
        """(Re)connect to a live Session Host and adopt its session -- the shared
        core of both startup reattach and in-session liveness-driven recovery.

        Dials the host's endpoint, resumes by the host-retained seq cursor
        (buffered frames past the durable ack replay with no gap and no
        re-stream), re-initializes ACP over the fresh stream pair, and adopts the
        existing ACP session id -- no child respawn. Wires transport-loss
        detection so a *subsequent* drop re-arms the driver. Sets
        ``session.client`` and ``session.status = new_status``; returns True on
        success.

        ``send_resume`` nudges a graceful-cancelled turn back to work with a
        single "Resume". ``prune_on_fail`` drops the index record on failure
        (startup path); the in-session driver leaves it for a later retry.
        """
        from .session_host.acp_adapter import open_acp_streams
        from .session_host.client import SessionHostClient
        from .session_host.endpoints import CredentialRelayReadinessError

        def _on_acp_event(event_type: str, data: dict[str, Any]) -> None:
            if session.event_log:
                session.event_log.append(event_type, data)
            self._capture_progress(session, event_type, data)
            if event_type == "usage_update":
                self._handle_usage_update(session, data)

        # Release any stale (dead-transport) client first so its socketpair +
        # relay tasks are freed; host-mode shutdown DETACHES (child survives).
        old = session.client
        if old is not None:
            with contextlib.suppress(Exception):
                await old.shutdown()

        sock = None
        streams = None
        client = None

        async def _close_partial_reattach() -> None:
            if client is not None:
                with contextlib.suppress(Exception):
                    await client.shutdown()
            if streams is not None:
                with contextlib.suppress(Exception):
                    await streams.aclose()
            if sock is not None:
                with contextlib.suppress(Exception):
                    await sock.close()

        def _settle_dead_child(exit_code: int) -> None:
            client_obj = session.client
            if client_obj is not None:
                client_obj.mark_host_child_exited(exit_code)
            self._reap_host_record(
                rec, f"Session Host child exited (code={exit_code})"
            )
            session.client = None
            session.status = SessionStatus.STOPPED
            self._db.update_session_status(
                rec.session_id, SessionStatus.STOPPED.value, time.time()
            )
            if session.event_log:
                session.event_log.append("session_state_changed", {
                    "status": SessionStatus.STOPPED.value,
                    "host_child_exited": True,
                    "exit_code": exit_code,
                })

        try:
            await self._ensure_forward(
                rec,
                refresh_relays=refresh_relays,
                require_relay_ready=require_relay_ready,
            )
            sock = await SessionHostClient.connect(port=rec.port)
            await sock.attach(0, nonce=getattr(rec, "nonce", "").encode())
            streams = await open_acp_streams(sock)

            async def _closer(_streams: Any = streams, _sock: Any = sock) -> None:
                await _streams.aclose()
                await _sock.close()

            core = _core()
            client = core.AcpClient(
                on_event=_on_acp_event,
                model_override=session.model_override,
                effort_override=session.effort_override,
            )
            streams.on_transport_lost = client.mark_transport_lost
            streams.on_child_exit = client.mark_host_child_exited
            # Retain the host control channel so the manager can push STATUS
            # (reapable) / DETACH (graceful) for host self-reap (#51).
            client.session_host_client = sock
            await asyncio.wait_for(
                client.start_streams(
                    streams.reader, streams.writer,
                    child_pid=rec.child_pid, closer=_closer,
                ),
                timeout=self._timeouts.session_start,
            )
            if streams.child_exit_code is not None:
                client.mark_host_child_exited(streams.child_exit_code)
                await _close_partial_reattach()
                _settle_dead_child(streams.child_exit_code)
                return False
            client.adopt_session(session.acp_session_id)
            session.client = client
            session.status = new_status
            self._db.update_session_status(
                rec.session_id, new_status.value, time.time(), pid=session.pid,
            )
            log.info(
                "Reattached session %s to live Session Host (pid=%s, port=%s)",
                rec.session_id, rec.host_pid, rec.port,
            )
        except asyncio.CancelledError:
            await _close_partial_reattach()
            raise
        except Exception as exc:
            child_exit_code = (
                streams.child_exit_code if streams is not None else None
            )
            await _close_partial_reattach()
            if isinstance(exc, CredentialRelayReadinessError):
                raise
            if child_exit_code is not None:
                session.client = client
                _settle_dead_child(child_exit_code)
                return False
            log.warning(
                "Failed to reattach session %s to host pid=%s%s",
                rec.session_id, rec.host_pid,
                "; pruning" if prune_on_fail else "",
                exc_info=True,
            )
            if prune_on_fail and self._host_index is not None:
                with contextlib.suppress(Exception):
                    await self._drop_forward(rec.session_id)
                with contextlib.suppress(Exception):
                    self._host_index.remove(rec.session_id)
            return False

        # If this session's in-flight turn was graceful-cancelled for a redeploy,
        # nudge it back to work with a single "Resume" now that the frontend is
        # reattached (a bare "Resume" re-orients a Copilot session well).
        if send_resume and self._host_index is not None:
            self._host_index.set_resume_flag(rec.session_id, False)
            try:
                await self.submit_prompt(rec.session_id, "Resume")
                log.info(
                    "Sent 'Resume' to reattached session %s "
                    "(turn was graceful-cancelled for redeploy)",
                    rec.session_id,
                )
            except Exception:
                log.warning(
                    "Failed to send 'Resume' to reattached session %s",
                    rec.session_id, exc_info=True,
                )
        return True

    def _reap_host_record(self, rec: Any, reason: str) -> None:
        """Reap a Session Host + its child and drop the durable index record.

        Kills the **child first** -- a POSIX SIGTERM to the host does not run its
        cleanup, so the child could otherwise orphan -- then the host, then
        removes the record. Cross-platform via ``osutil.kill_pid`` (on Windows
        ``taskkill /T`` collects the process tree, and the host's kill-on-close
        job also takes the child). Used for both the explicit-terminate reap
        (#1786) and the version-mux stranded/forced reap.
        """
        from .session_host.osutil import kill_pid, reap_zombie

        log.info(
            "Reaping Session Host for session %s (host pid=%s, child pid=%s): %s",
            rec.session_id, rec.host_pid, rec.child_pid, reason,
        )
        boundary = getattr(rec, "boundary", "local")
        if boundary == "local":
            kill_pid(rec.child_pid, force=True)
            kill_pid(rec.host_pid, force=True)
            # Clear the zombie a host we parented leaves behind (no-op for a
            # reattached host that init reaps, or on Windows).
            reap_zombie(rec.child_pid)
            reap_zombie(rec.host_pid)
        else:
            # Remote (CodeSpace / mesh) boundary: host_pid/child_pid live on the
            # FAR side -- killing those pid numbers locally would hit unrelated
            # local processes. Tear down the local forward and best-effort kill
            # the remote host (its PR_SET_PDEATHSIG takes the child with it).
            self._kill_forward_sync(
                rec.session_id,
                release_container_lock=False,
            )
            self._schedule_remote_reap(rec, reason)
        with contextlib.suppress(Exception):
            self._host_index.remove(rec.session_id)
        # #4272 bridge-lock: best-effort clear the lattice lock at teardown.
        # Fire-and-forget (this reap path is sync); a lingering lock is already
        # ignored by the picker's reader once the child pid dies.
        with contextlib.suppress(Exception):
            from . import bridge_lock
            bridge_lock.remove_sync(rec.session_id)
        self._disconnected_reattach_failures.pop(rec.session_id, None)

    def _kill_forward_sync(
        self,
        session_id: str,
        *,
        release_container_lock: bool = True,
    ) -> None:
        """Best-effort synchronous teardown of session forward processes."""
        self._kill_relays_sync(session_id)
        fwd = self._forwards.pop(session_id, None)
        if fwd is not None:
            proc = getattr(fwd, "_proc", None)
            if proc is not None and getattr(proc, "returncode", 0) is None:
                with contextlib.suppress(Exception):
                    proc.kill()
        if release_container_lock:
            self._release_container_lock(session_id)
            self._release_codespace_lock(session_id)

    def _kill_relays_sync(self, session_id: str) -> None:
        """Best-effort synchronous teardown of relay supervisors."""
        for relay in self._relays.pop(session_id, []):
            task = getattr(relay, "_monitor_task", None)
            if task is not None and not getattr(task, "done", lambda: True)():
                with contextlib.suppress(Exception):
                    task.cancel()
            proc = getattr(relay, "_proc", None)
            if proc is not None and getattr(proc, "returncode", 0) is None:
                with contextlib.suppress(Exception):
                    proc.kill()

    def _schedule_remote_reap(self, rec: Any, reason: str) -> None:
        """Fire-and-forget a remote ``kill`` of a detached far-side Host.

        Uses the durable endpoint's SSH config (no live Spawner needed) to run a
        one-shot ``kill`` over the tunnel. Best-effort: if there is no running
        loop or the exec fails, the detached Host lingers until the CodeSpace
        stops -- never fatal, and never touches a local process.
        """
        endpoint = getattr(rec, "endpoint", None) or {}
        if not endpoint:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self._remote_reap(rec, endpoint))
        self._remote_reap_tasks.add(task)
        task.add_done_callback(self._remote_reap_tasks.discard)

    async def _remote_reap(self, rec: Any, endpoint: dict) -> bool:
        from ssh_manager import ConnectionManager

        from .session_host.endpoints import ssh_config_from_endpoint

        class _StaticSource:
            def __init__(self, cfg):
                self._cfg = cfg

            def get_ssh_config(self):
                return self._cfg

            def refresh(self):
                return self._cfg

        cfg = ssh_config_from_endpoint(endpoint)
        host = cfg.host_alias
        mgr = None
        confirmed_dead = False
        try:
            mgr = ConnectionManager()
            await mgr.ensure_connected(host, _StaticSource(cfg), [])
            # Kill the host's whole PROCESS GROUP, not just the host pid. The
            # host was launched via ``setsid`` (so it leads its own group,
            # pgid == host_pid) and the ``bash -lc`` wrapper + the copilot
            # grandchild inherit that group -- so ``kill -- -<pgid>`` takes the
            # host AND copilot in one shot, with nothing orphaned (killing only
            # host_pid would leave copilot reparented to init). Fall back to the
            # bare pid if the group send is rejected. SIGTERM first (lets copilot
            # flush), then SIGKILL as a backstop.
            pid = int(rec.host_pid)
            child_pid = int(rec.child_pid)
            result = await mgr.exec_command(
                host,
                f"kill -TERM -{pid} 2>/dev/null || kill -TERM {pid} 2>/dev/null; "
                f"sleep 1; kill -KILL -{pid} 2>/dev/null || kill -KILL {pid} "
                "2>/dev/null || true; "
                "alive() { test -r /proc/$1/stat && "
                "test \"$(awk '{print $3}' /proc/$1/stat 2>/dev/null)\" != Z; }; "
                "i=0; while { alive "
                f"{pid} || alive {child_pid}; "
                "} && test $i -lt 20; do sleep 0.1; i=$((i+1)); done; "
                f"if alive {pid} || alive {child_pid}; then exit 42; fi; "
                "printf __REAPED__",
                timeout=20.0,
            )
            confirmed_dead = (
                result.exit_code == 0
                and "__REAPED__" in (result.stdout or "")
            )
            if not confirmed_dead:
                raise RuntimeError(
                    f"remote reap could not verify process death "
                    f"(rc={result.exit_code}, output={result.stdout!r})"
                )
            log.info("Remote-reaped Session Host group for session %s (far pid=%s)",
                     rec.session_id, rec.host_pid)
        except Exception:
            log.warning(
                "Best-effort remote reap failed for session %s (far pid=%s); "
                "the detached Host will exit when the CodeSpace stops",
                rec.session_id, rec.host_pid, exc_info=True,
            )
        finally:
            if mgr is not None:
                with contextlib.suppress(Exception):
                    await mgr.disconnect(host)
            if confirmed_dead:
                self._set_container_launch_pending(rec.session_id, False)
                self._release_container_lock(rec.session_id)
                self._release_codespace_lock(rec.session_id)
        return confirmed_dead

