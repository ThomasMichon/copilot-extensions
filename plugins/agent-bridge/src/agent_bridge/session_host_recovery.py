"""Session Host authority recovery and reattach helpers."""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any

from .acp_client import AcpClient
from .connect import ConnectTracker
from .models import SessionStatus
from .session_manager import (
    _append_plugin_dirs,
    _container_remote_child_argv,
    Session,
    RemoteHostRecoveryPendingError,
    log,
)


def _core() -> Any:
    from . import session_manager as core

    return core


class _SessionHostRecoveryMixin:
    """Session Host authority recovery and reattach helpers."""

    async def reattach_session_hosts(
        self,
        *,
        remote_recovery_timeout: float = 60.0,
    ) -> int:
        """Reconnect to every surviving Session Host on startup (goal 3).

        After an agent-bridge restart, ``_rehydrate`` has marked host-backed
        sessions STOPPED. This reads the durable host index and, for each host
        whose process is still alive, re-establishes the ACP connection over the
        reattached loopback endpoint and **adopts** the existing ACP session --
        no child respawn, no lost session. Dead hosts are pruned. Returns the
        count reattached. No-op when no host index exists.

        **Version-mux (Phase 4).** A host advertising a wire-envelope protocol
        this frontend no longer speaks (a rare breaking host-layer change) is
        *not* driven with incompatible client code. Per :func:`plan_host`:
        a compatible host is reattached; an incompatible host whose child is
        still alive is **left running** so it keeps its child until the child's
        own stop (goal 1 -- never reap mid-turn); an incompatible host whose
        child has already stopped is reaped so it stops pinning its old install.
        """
        if self._host_index is None:
            return 0
        from .session_host.version_mux import HostDisposition, plan_host

        loop = asyncio.get_running_loop()
        startup_budget = max(0.0, float(remote_recovery_timeout))
        deadline = loop.time() + startup_budget
        startup_session_ids = {
            session.session_id
            for session in self._sessions.values()
            if session.status not in {SessionStatus.ENDED, SessionStatus.FAILED}
            and session.background_recovery_enabled
        }
        recovery_budget = min(
            startup_budget / 2,
            max(0.0, deadline - loop.time()),
        )
        recovered = await self._recover_remote_host_records(
            session_ids=startup_session_ids,
            timeout_seconds=recovery_budget,
        )
        if recovered:
            log.info(
                "Recovered %d remote Session Host record(s) from far-side "
                "authority files",
                recovered,
            )
        self._prune_dead_hosts()
        reattached = 0
        now = time.time()
        for rec in self._live_host_records():
            session = self._sessions.get(rec.session_id)
            if (
                session is not None
                and session.status in {SessionStatus.ENDED, SessionStatus.FAILED}
            ):
                log.info(
                    "Skipping startup reattach for terminal session %s (%s)",
                    rec.session_id, session.status.value,
                )
                continue
            if session is not None and not session.background_recovery_enabled:
                log.info("Skipping startup reattach for dormant session %s", rec.session_id)
                continue
            if (
                rec.session_id in self._remote_recovery_skipped
                or rec.session_id in self._remote_recovery_inconclusive
            ):
                log.info(
                    "Skipping startup reattach for %s because its remote venue "
                    "could not be authoritatively inspected",
                    rec.session_id,
                )
                continue
            if deadline - loop.time() <= 0:
                self._remote_recovery_inconclusive.add(rec.session_id)
                log.warning(
                    "Startup Session Host reattach budget exhausted before %s",
                    rec.session_id,
                )
                continue
            plan = plan_host(
                protocol_version=rec.protocol_version,
                child_alive=self._rec_child_alive(rec),
                age_seconds=(now - rec.created_at) if rec.created_at else None,
                stale_reap_seconds=self._session_host_stale_reap_seconds,
            )
            if plan.disposition is HostDisposition.STRAND:
                log.info(
                    "Session %s pinned to incompatible Session Host "
                    "(proto=%s, build=%s, pid=%s); %s",
                    rec.session_id, rec.protocol_version, rec.host_version,
                    rec.host_pid, plan.reason,
                )
                continue
            if plan.disposition in (HostDisposition.REAP_STOPPED,
                                    HostDisposition.FORCE_REAP):
                self._reap_host_record(rec, plan.reason)
                continue
            session = self._sessions.get(rec.session_id)
            if session is None or not session.acp_session_id:
                # A live host with no adoptable session -- ended out from under
                # it (its row was deleted) or a pre-#1786 orphan. Reap it rather
                # than leak the host + child forever.
                self._reap_host_record(rec, "no adoptable session on reattach")
                continue

            remaining = deadline - loop.time()
            if remaining <= 0:
                self._remote_recovery_inconclusive.add(rec.session_id)
                log.warning(
                    "Startup Session Host reattach budget exhausted before %s",
                    rec.session_id,
                )
                continue
            # A request-driven resume_session() already holds this session's
            # `_lifecycle_lock` for the exact same reattach-or-respawn decision.
            # Racing it from this background startup pass would let both paths
            # read/recover the far-side authority and establish competing
            # forwards concurrently. Skip here (non-blocking check-then-acquire
            # is safe -- no `await` happens between them) and leave the session
            # for resume_session (or a later reconciliation pass) to finish.
            if session._lifecycle_lock.locked():
                self._remote_recovery_inconclusive.add(rec.session_id)
                log.info(
                    "Skipping startup reattach for %s: an in-flight "
                    "resume/lifecycle operation already owns it",
                    rec.session_id,
                )
                continue
            try:
                async with session._lifecycle_lock:
                    attached = await asyncio.wait_for(
                        self._reattach_one(
                            rec, session, new_status=SessionStatus.IDLE,
                            send_resume=(
                                getattr(rec, "resume_on_reattach", False)
                                or session.restart_status
                                == SessionStatus.STARTING.value
                            ),
                            # A failed remote attach may be a transient SSH/control-plane
                            # outage while the far-side host, child, and auth relay are
                            # still serving tools. Retain its record and relay ownership;
                            # only an authoritative far-side liveness probe may declare it
                            # dead. Local PIDs remain directly authoritative.
                            prune_on_fail=(
                                getattr(rec, "boundary", "local") == "local"
                                or not (getattr(rec, "extra", {}) or {}).get(
                                    "remote_authority_v2"
                                )
                            ),
                        ),
                        timeout=remaining,
                    )
            except asyncio.TimeoutError:
                self._remote_recovery_inconclusive.add(rec.session_id)
                log.warning(
                    "Startup Session Host reattach timed out for %s after %.1fs",
                    rec.session_id, remaining,
                )
                continue
            if attached:
                reattached += 1
        if reattached:
            log.info("Reattached %d session(s) to surviving Session Hosts", reattached)
        return reattached

    async def _recover_remote_host_records(
        self,
        *,
        allow_wake: bool = False,
        session_ids: set[str] | None = None,
        timeout_seconds: float = 120.0,
    ) -> int:
        """Rebuild missing remote HostIndex rows from far-side records.

        The frontend DB identifies adoptable ACP sessions and their remote venue
        targets. The Session Host itself is authoritative for host/child PID,
        port, nonce, version, and relay-forward metadata.
        """
        if self._host_index is None:
            return 0
        from .relay_state import get_live_relay_port
        from .session_host.codespace_transport import (
            build_codespace_spawner,
            parse_codespace_target,
        )
        from .session_host.container_transport import build_container_spawner
        from .session_host.spawner import RemoteHostDeadError

        groups: dict[str, tuple[Any, list[tuple[Session, Any]]]] = {}
        for session in self._sessions.values():
            if session_ids is not None and session.session_id not in session_ids:
                continue
            target = session.target
            has_container_target = (
                isinstance(target.container, dict)
                and bool(target.container.get("name"))
            )
            if not session.acp_session_id and not has_container_target:
                continue
            existing = self._host_index.get(session.session_id)
            if (
                existing is not None
                and not (getattr(existing, "extra", {}) or {}).get(
                    "remote_authority_v2"
                )
            ):
                continue
            cs_target = None
            container_target = (
                target.container
                if isinstance(target.container, dict)
                and target.container.get("name")
                else None
            )
            if isinstance(target.codespace, dict) and target.codespace.get("name"):
                cs_target = target.codespace
            elif target.spawn_command:
                cs_target = parse_codespace_target(target.spawn_command)
            if not cs_target and not container_target:
                continue
            if container_target:
                name = f"container:{container_target['name']}"
                if name not in groups:
                    spawner = build_container_spawner(
                        container_target,
                        ready_timeout=self._timeouts.session_host_ready,
                        unexpected_reap_seconds=(
                            self._session_host_unexpected_reap_seconds
                        ),
                        active_reap_seconds=(
                            self._session_host_active_reap_seconds
                        ),
                    )
                    groups[name] = (spawner, [])
            else:
                codespace_name = cs_target["name"]
                name = f"codespace:{codespace_name}"
                if name not in groups:
                    spawner = build_codespace_spawner(
                        codespace_name,
                        cs_target.get("repo") or "",
                        relay_port=get_live_relay_port(),
                        unexpected_reap_seconds=(
                            self._session_host_unexpected_reap_seconds
                        ),
                        active_reap_seconds=(
                            self._session_host_active_reap_seconds
                        ),
                    )
                    groups[name] = (spawner, [])
            groups[name][1].append((session, existing))

        semaphore = asyncio.Semaphore(3)

        async def _inspect_group(spawner: Any, entries: list[tuple[Session, Any]]):
            async with semaphore:
                inspect_before_connect = (
                    not allow_wake
                    or getattr(spawner, "boundary", "") == "container"
                )
                if inspect_before_connect:
                    try:
                        can_inspect = await spawner.can_inspect_without_wake()
                    except Exception:
                        log.warning(
                            "Could not determine remote venue state without waking; "
                            "skipping startup authority inspection",
                            exc_info=True,
                        )
                        return [
                            (session, existing, "unknown", None)
                            for session, existing in entries
                        ]
                    if not can_inspect:
                        status = (
                            "dead"
                            if getattr(spawner, "boundary", "") == "container"
                            else "skipped"
                        )
                        return [
                            (session, existing, status, None)
                            for session, existing in entries
                        ]
                results = []
                for session, existing in entries:
                    try:
                        record = await spawner.recover_record(session.session_id)
                        status = "live" if record is not None else "missing"
                        results.append((session, existing, status, record))
                    except RemoteHostDeadError:
                        results.append((session, existing, "dead", None))
                    except Exception:
                        log.warning(
                            "Could not inspect remote Session Host authority for %s",
                            session.session_id,
                            exc_info=True,
                        )
                        results.append((session, existing, "unknown", None))
                return results

        task_entries = {
            asyncio.create_task(_inspect_group(spawner, entries)): entries
            for spawner, entries in groups.values()
        }
        tasks = list(task_entries)
        if not tasks:
            return 0
        done, pending = await asyncio.wait(tasks, timeout=timeout_seconds)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        if pending:
            log.warning(
                "Remote Session Host authority recovery timed out with %d "
                "remote venue group(s) still pending after %.1fs",
                len(pending), timeout_seconds,
            )
            for task in pending:
                for session, _existing in task_entries[task]:
                    self._remote_recovery_inconclusive.add(session.session_id)

        recovered = 0
        for task in done:
            try:
                results = task.result()
            except Exception:
                log.warning(
                    "Remote Session Host authority recovery group failed",
                    exc_info=True,
                )
                for session, _existing in task_entries[task]:
                    self._remote_recovery_skipped.add(session.session_id)
                continue
            for session, existing, status, record in results:
                if status == "live" and record is not None:
                    container_target = (
                        session.target.container
                        if isinstance(session.target.container, dict)
                        and session.target.container.get("name")
                        else None
                    )
                    if container_target is not None:
                        try:
                            self._acquire_container_lock(
                                session.session_id,
                                container_target["name"],
                            )
                        except Exception:
                            self._remote_recovery_inconclusive.add(
                                session.session_id
                            )
                            log.warning(
                                "Live container Session Host authority for %s "
                                "could not reclaim target ownership",
                                session.session_id,
                                exc_info=True,
                            )
                            continue
                    self._remote_recovery_inconclusive.discard(session.session_id)
                    self._remote_recovery_skipped.discard(session.session_id)
                    if existing is not None:
                        record.resume_on_reattach = existing.resume_on_reattach
                    self._host_index.register(record)
                    if existing is None:
                        recovered += 1
                elif status == "dead":
                    self._release_container_lock(session.session_id)
                    self._set_container_launch_pending(
                        session.session_id,
                        False,
                    )
                    self._remote_recovery_inconclusive.discard(session.session_id)
                    self._remote_recovery_skipped.discard(session.session_id)
                    if existing is not None:
                        await self._drop_forward(session.session_id)
                        self._host_index.remove(session.session_id)
                        self._disconnected_reattach_failures.pop(
                            session.session_id, None
                        )
                        log.info(
                            "Pruned confirmed-dead remote Session Host for %s",
                            session.session_id,
                        )
                elif status == "unknown":
                    self._remote_recovery_skipped.discard(session.session_id)
                    self._remote_recovery_inconclusive.add(session.session_id)
                elif status == "missing":
                    self._release_container_lock(session.session_id)
                    self._set_container_launch_pending(
                        session.session_id,
                        False,
                    )
                    self._remote_recovery_inconclusive.discard(session.session_id)
                    self._remote_recovery_skipped.discard(session.session_id)
                    if existing is not None:
                        await self._drop_forward(session.session_id)
                        self._host_index.remove(session.session_id)
                        self._disconnected_reattach_failures.pop(
                            session.session_id, None
                        )
                        log.info(
                            "Pruned confirmed-absent remote Session Host "
                            "authority for %s",
                            session.session_id,
                        )
                elif status == "skipped":
                    self._remote_recovery_skipped.add(session.session_id)
        return recovered

    async def _try_reattach_live_host(self, session: Session) -> bool:
        """Adopt a still-alive Session Host for ``session`` instead of respawning.

        For a host-backed session whose frontend transport dropped (laptop sleep,
        tunnel flap, SSH sever) but whose Session Host + copilot child survive on
        the far side, reattach and adopt the running child -- recovering the
        in-flight/just-finished turn -- rather than spawning a *fresh* child +
        ``load_session``, which abandons the running work (the #145 "each send
        does a fresh load_session, losing the mid-turn tool call" symptom).

        Returns True on a successful reattach. No-op (False) unless Session-Host
        mode is on and a live, protocol-compatible host record exists for this
        session. Passes ``send_resume=False``: a caller resuming to submit a new
        prompt drives the turn itself, and it avoids re-entering ``submit_prompt``
        from within a resume.
        """
        if self._host_index is None:
            return False
        if not session.acp_session_id:
            return False
        rec = self._host_index.get(session.session_id)
        authority_v2 = bool(
            rec is not None
            and getattr(rec, "boundary", "local") != "local"
            and (getattr(rec, "extra", {}) or {}).get("remote_authority_v2")
        )
        if rec is None or authority_v2:
            await self._recover_remote_host_records(
                allow_wake=True,
                session_ids={session.session_id},
            )
            rec = self._host_index.get(session.session_id)
            if session.session_id in self._remote_recovery_inconclusive:
                raise RemoteHostRecoveryPendingError(
                    f"Remote Session Host state is inconclusive for "
                    f"{session.session_id}; refusing to spawn a duplicate "
                    "Copilot process"
                )
            if rec is None:
                return False
        container_target = (
            session.target.container
            if isinstance(session.target.container, dict)
            else {}
        )
        if (
            container_target.get("launch_pending_session_id")
            == session.session_id
        ):
            if rec is not None:
                self._reap_host_record(
                    rec,
                    "partially launched container Session Host",
                )
            raise RemoteHostRecoveryPendingError(
                f"Container Session Host cleanup is pending for "
                f"{session.session_id}; refusing to attach or spawn a "
                "duplicate Copilot process"
            )
        if not self._rec_host_alive(rec) or not self._rec_child_alive(rec):
            return False
        from .session_host.version_mux import HostDisposition, plan_host

        plan = plan_host(
            protocol_version=rec.protocol_version,
            child_alive=True,
            age_seconds=(time.time() - rec.created_at) if rec.created_at else None,
            stale_reap_seconds=self._session_host_stale_reap_seconds,
        )
        authority_v2 = (getattr(rec, "extra", {}) or {}).get(
            "remote_authority_v2"
        )
        if plan.disposition is not HostDisposition.REATTACH:
            if (
                getattr(rec, "boundary", "local") != "local"
                and authority_v2
            ):
                raise RemoteHostRecoveryPendingError(
                    f"Remote Session Host for {session.session_id} is still "
                    f"authoritative but cannot be reattached: {plan.reason}"
                )
            return False
        relay_required = (
            getattr(rec, "boundary", "local") == "container"
            and container_target.get("relay_remote_port") is not None
        )
        attached = await self._reattach_one(
            rec,
            session,
            new_status=SessionStatus.IDLE,
            send_resume=False,
            refresh_relays=relay_required,
            require_relay_ready=relay_required,
        )
        if (
            not attached
            and getattr(rec, "boundary", "local") != "local"
            and authority_v2
        ):
            raise RemoteHostRecoveryPendingError(
                f"Could not reattach remote Session Host for "
                f"{session.session_id}; retained its authority record and "
                "credential relay, refusing to spawn a duplicate Copilot process"
            )
        return attached

    async def _resume_via_new_remote_host(
        self,
        session: Session,
        *,
        on_acp_event: Any,
        permission_callback: Any | None,
        load_existing: bool = True,
    ) -> tuple[AcpClient, str] | None:
        """Replace a dead remote Host and load/create its ACP session."""
        target = session.target
        container_target = (
            target.container
            if isinstance(target.container, dict)
            and target.container.get("name")
            else None
        )
        if container_target is not None:
            from .relay_state import get_live_relay_port
            from .session_host.container_transport import (
                build_container_spawner,
                cleanup_container_session_host,
                ensure_container_ready,
                prepare_container_session_host,
            )
            from .session_host.spawner import RemoteSpawnCleanupPendingError

            already_held = (
                session.session_id in self._container_lock_sessions
            )
            self._acquire_container_lock(
                session.session_id, container_target["name"],
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
                    session.session_id,
                    target.to_json(),
                    target.cwd,
                )
                spawner = build_container_spawner(
                    container_target,
                    prepared=prepared,
                    ready_timeout=self._timeouts.session_host_ready,
                    unexpected_reap_seconds=(
                        self._session_host_unexpected_reap_seconds
                    ),
                    active_reap_seconds=self._session_host_active_reap_seconds,
                    require_relay_ready=True,
                )
                remote_cwd = (
                    prepared.get("workspace_folder")
                    or container_target.get("workspace_folder")
                    or None
                )
                core = _core()
                plugin_dirs = await core._resolve_remote_ai_plugin_dirs(
                    spawner.transport,
                    f"container:{container_target['name']}",
                    remote_cwd,
                )
                tracker = ConnectTracker(
                    session.event_log.append,
                    session_id=session.session_id,
                )
                return await self._connect_via_session_host(
                    target,
                    tracker=tracker,
                    session_id=session.session_id,
                    on_acp_event=on_acp_event,
                    permission_callback=permission_callback,
                    mcp_servers=session.mcp_servers,
                    spawner=spawner,
                    remote_child_argv=_container_remote_child_argv(
                        container_target,
                        prepared,
                        plugin_dirs,
                    ),
                    remote_cwd=remote_cwd,
                    load_session_id=(
                        session.acp_session_id if load_existing else None
                    ),
                    model=session.model_override,
                    effort=session.effort_override,
                )
            except Exception as exc:
                if isinstance(exc, RemoteSpawnCleanupPendingError):
                    self._set_container_launch_pending(
                        session.session_id,
                        True,
                    )
                elif not already_held:
                    self._release_container_lock(session.session_id)
                raise
            finally:
                if prepared is not None:
                    with contextlib.suppress(Exception):
                        await cleanup_container_session_host(
                            container_target,
                            prepared,
                        )
        cs_target = (
            target.codespace
            if isinstance(target.codespace, dict)
            and target.codespace.get("name")
            else None
        )
        if cs_target is None and target.spawn_command:
            from .session_host.codespace_transport import parse_codespace_target

            cs_target = parse_codespace_target(target.spawn_command)
        if not cs_target:
            return None
        from .relay_state import get_live_relay_port
        from .session_host.codespace_transport import build_codespace_spawner

        core = _core()
        relay_prelude, relay_port = core._resolve_relay_launch_env(
            cs_target["name"],
            get_live_relay_port(),
        )
        acp_command = cs_target["acp_command"]
        spawner = build_codespace_spawner(
            cs_target["name"],
            cs_target.get("repo") or "",
            relay_port=relay_port,
            unexpected_reap_seconds=self._session_host_unexpected_reap_seconds,
            active_reap_seconds=self._session_host_active_reap_seconds,
        )
        ai_plugin_dirs = await core._resolve_remote_ai_plugin_dirs(
            spawner.transport,
            f"codespace:{cs_target['name']}",
            cs_target.get("workspace_folder") or None,
        )
        acp_command = _append_plugin_dirs(acp_command, ai_plugin_dirs)
        tracker = ConnectTracker(
            session.event_log.append,
            session_id=session.session_id,
        )
        client, acp_sid = await self._connect_via_session_host(
            target,
            tracker=tracker,
            session_id=session.session_id,
            on_acp_event=on_acp_event,
            permission_callback=permission_callback,
            mcp_servers=session.mcp_servers,
            spawner=spawner,
            remote_child_argv=["bash", "-lc", relay_prelude + acp_command],
            remote_cwd=cs_target.get("workspace_folder") or None,
            load_session_id=(session.acp_session_id if load_existing else None),
            model=session.model_override,
            effort=session.effort_override,
        )
        return client, acp_sid

    async def _codespace_host_available(
        self,
        rec: Any,
        session: Session,
        availability: dict[str, bool],
    ) -> bool:
        """Check venue availability without SSH, once per CodeSpace per pass."""
        from .session_host.codespace_transport import (
            CodeSpaceTransport,
            parse_codespace_target,
        )

        name = (getattr(rec, "endpoint", None) or {}).get("codespace")
        if not name:
            target = session.target
            cs_target = target.codespace
            if not isinstance(cs_target, dict) or not cs_target.get("name"):
                cs_target = parse_codespace_target(target.spawn_command or [])
            name = (cs_target or {}).get("name")
        if not isinstance(name, str) or not name:
            self._remote_recovery_skipped.add(rec.session_id)
            log.warning(
                "Skipping background reattach for %s: CodeSpace identity "
                "is unavailable for a no-wake availability check",
                rec.session_id,
            )
            return False
        if name not in availability:
            try:
                availability[name] = await CodeSpaceTransport(name).is_running()
            except Exception:
                availability[name] = False
                log.warning(
                    "Could not check CodeSpace %s without waking; "
                    "skipping background reattach",
                    name, exc_info=True,
                )
        if not availability[name]:
            self._remote_recovery_skipped.add(rec.session_id)
            return False
        self._remote_recovery_skipped.discard(rec.session_id)
        return True

