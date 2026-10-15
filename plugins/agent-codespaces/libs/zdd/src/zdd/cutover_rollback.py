"""Rollback and writer-admission reconciliation for active/passive cutover."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from . import routing_persistence
from .routing import Endpoint

if TYPE_CHECKING:
    from .cutover import CutoverOrchestrator, CutoverResult, _Handle

log = logging.getLogger("zdd")


def _undrain(self: CutoverOrchestrator, old: Endpoint, result: CutoverResult) -> bool:
    """Best-effort release of the old daemon's drain gate (#1756).

    A cutover that aborts after opening the drain gate must not leave the
    survivor drained. Records the outcome as a step either way so a rollback
    is traceable rather than silent.
    """
    from .cutover import CutoverError

    try:
        response = self.make_client(old.base_url).undrain()
        if self.drain_before_publish and (
            not isinstance(response, dict) or response.get("draining") is not False
        ):
            raise CutoverError("old daemon did not confirm undrain")
        result.steps.append("rollback: old daemon undrained")
        return True
    except Exception as exc:  # noqa: BLE001 -- undrain is best-effort
        result.steps.append(f"rollback: old undrain failed (non-fatal): {exc}")
        log.warning("Rollback could not undrain old daemon: %s", exc)
        return False


def _rollback(
    self: CutoverOrchestrator,
    old: Endpoint | None,
    handle: _Handle,
    new_port: int,
    result: CutoverResult,
    *,
    flipped: bool,
    expected_old: Endpoint | None = None,
    expected_new: Endpoint | None = None,
    drain_started: bool = True,
) -> None:
    from .cutover import CutoverError

    log.warning("Rolling back cutover: %s", result.error)
    successor_stopped = False
    old_live: bool | None = None
    if self.drain_before_publish:
        try:
            old_live = bool(
                drain_started and old and self.health_check(old.client_host, old.port)
            )
        except Exception:
            old_live = False
        if old_live or not flipped:
            try:
                handle.terminate()
                stopped = self._await_exit(handle)
            except Exception as exc:
                result.steps.append(f"rollback: successor termination failed: {exc}")
                stopped = False
            if not stopped:
                result.steps.append(
                    "rollback: successor exit unconfirmed; predecessor state left unchanged"
                )
                return
            result.steps.append("rollback: confirmed successor exit before restoring old")
            successor_stopped = True
        if not flipped and not drain_started:
            result.rolled_back = successor_stopped
            return

    # 1. Try to restore the old endpoint as active (only if it is still alive).
    old_restored = False
    if old is not None and (not self.drain_before_publish or drain_started):
        try:
            predecessor_live = (
                old_live if old_live is not None
                else self.health_check(old.client_host, old.port)
            )
            if predecessor_live:
                if self.drain_before_publish:
                    current_raw = self._raw_active()
                    current = Endpoint.from_dict(current_raw) if current_raw else None
                    if (
                        (not flipped and current != expected_old)
                        or (flipped and (
                            expected_new is None or current != expected_new
                        ))
                    ):
                        raise CutoverError("active owner changed; cannot restore predecessor")
                    self.routing.publish_active_with_previous_guarded(
                        self.config_dir, bind=old.bind, port=old.port,
                        pid=old.pid, version=old.version, demote_existing=True,
                        process_start_time=old.process_start_time,
                        expected_active=current,
                    )
                else:
                    self.routing.publish_active(
                        self.config_dir, bind=old.bind, port=old.port,
                        pid=old.pid, version=old.version, demote_existing=True,
                    )
                old_restored = True
                result.steps.append("rollback: restored old as active")
                # We may have opened the old daemon's drain gate before the
                # failure -- release it so the survivor does not stay closed
                # to new work (#1756). Recorded either way for traceability.
                undrained = self._undrain(old, result)
                if self.drain_before_publish:
                    old_restored = undrained
        except routing_persistence.PublicationCommitUncertain:
            result.publication_uncertain = True
            result.serving_endpoint = None
            result.steps.append("rollback publication uncertain; generations retained")
            log.error("Rollback publication uncertain; generations retained")
            return
        except Exception as exc:  # noqa: BLE001
            log.error("Rollback could not restore old endpoint: %s", exc)
            result.steps.append(f"rollback: predecessor restoration failed: {exc}")

    if (
        self.drain_before_publish and successor_stopped
        and old is not None and not old_restored
    ):
        result.steps.append("rollback: successor stopped; predecessor reconciliation pending")
        return

    # 2. If the route was already flipped to the new daemon and we could NOT
    #    restore the old one, terminating the new daemon would strand every
    #    client (active -> dead new, previous -> dead old, fallback -> nothing
    #    listening). Commit forward to the new daemon instead, as long as it is
    #    still healthy -- it is the only viable home for the route.
    if flipped and not old_restored:
        host = self._client_host()
        if self.health_check(host, new_port):
            # Re-assert the new daemon as active (it already is, but make the
            # intent explicit and bump the generation) and keep it alive.
            try:
                if self.drain_before_publish:
                    if expected_new is None:
                        raise CutoverError("successor publication identity is unconfirmed")
                    self.routing.publish_active_with_previous_guarded(
                        self.config_dir, bind=self.bind, port=new_port,
                        pid=getattr(handle, "pid", None), version=self.version,
                        process_start_time=expected_new.process_start_time,
                        demote_existing=True, expected_active=expected_new,
                    )
                else:
                    self.routing.publish_active(
                        self.config_dir, bind=self.bind, port=new_port,
                        pid=getattr(handle, "pid", None), version=self.version,
                        demote_existing=True,
                    )
            except Exception as exc:  # noqa: BLE001
                log.error("Commit-forward could not re-assert route: %s", exc)
                if self.drain_before_publish:
                    result.steps.append(f"rollback: successor reconciliation pending: {exc}")
                    return
            result.steps.append(
                "rollback: old unreachable -- committed forward to the "
                "healthy new daemon (no rollback)"
            )
            self._adopt_relay(new_port, result)
            result.committed = True
            result.ok = True
            result.rolled_back = False
            return
        # Both old and new are unreachable: a double failure. Leave the route
        # as-is (readers heal to whatever recovers) and do not kill the new
        # daemon -- if it is merely slow it may still come back.
        result.steps.append(
            "rollback: both old and new unhealthy -- left route untouched"
        )
        result.rolled_back = not self.drain_before_publish
        return

    # 3. Safe to terminate the freshly spawned passive daemon: either the old
    #    endpoint is serving again, or the route was never flipped (the new
    #    daemon never served any client).
    if not successor_stopped:
        try:
            handle.terminate()
            result.steps.append("rollback: terminated new daemon")
        except Exception as exc:  # noqa: BLE001
            log.error("Rollback could not terminate new daemon: %s", exc)
    result.rolled_back = True
