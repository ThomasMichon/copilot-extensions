"""Cutover health gates, predecessor drain and post-publication retirement."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from . import lifecycle
from .routing import Endpoint

if TYPE_CHECKING:
    from .conditional import PreparedCutover
    from .cutover import CutoverOrchestrator, CutoverResult, _Client

log = logging.getLogger("zdd")


def adopt_relay(
    self: CutoverOrchestrator, new_port: int, result: CutoverResult, *,
    make_client: Callable[[str], _Client] | None = None,
    adopt_relay: Callable[[], dict] | None = None,
) -> None:
    from .cutover import CutoverError

    try:
        if adopt_relay is not None:
            adopt = adopt_relay()
        else:
            new_client = (make_client or self.make_client)(self._base_url(new_port))
            adopt = new_client.adopt_relay()
        adopted = adopt.get("adopted")
        if adopted is not None and type(adopted) is not bool:
            raise CutoverError("relay adoption response has an invalid boolean")
        result.steps.append(f"relay adopt: {adopted}")
    except Exception as exc:
        failure = type(exc).__name__
        result.steps.append(f"relay adopt failed (non-fatal): {failure}")
        log.warning("Relay adoption failed after cutover (%s)", failure)


def _drain_old(
    self: CutoverOrchestrator, old: Endpoint, result: CutoverResult, *,
    drain_timeout: float, force: bool, conditional: PreparedCutover | None = None,
    on_drain: Callable[[], None] | None = None,
) -> None:
    try:
        _drain(
            self, old, result, drain_timeout=drain_timeout, force=force,
            conditional=conditional, on_drain=on_drain,
        )
    except Exception as exc:
        if conditional is not None or self.retire_failed_predecessor is None:
            raise
        result.predecessor_cleanup = {
            "exit_confirmed": False, "attempted": False, "reason": type(exc).__name__,
        }
        result.steps.append("predecessor recovery failed; retirement deferred until successor serves")


def _drain(
    self: CutoverOrchestrator, old: Endpoint, result: CutoverResult, *,
    drain_timeout: float, force: bool, conditional: PreparedCutover | None = None,
    on_drain: Callable[[], None] | None = None,
) -> None:
    from .cutover import CutoverError

    graceful_only = self.drain_before_publish or conditional is not None
    if graceful_only and force:
        raise CutoverError("graceful-only predecessor drain cannot force")
    self._emit(lifecycle.DRAIN, lifecycle.BEGIN, port=old.port)
    if conditional:
        drain_res = conditional.drain_predecessor(drain_timeout)
    else:
        client = self.make_client(old.base_url)
        drain = client.drain
        if graceful_only or getattr(self, "expected_old", None) is not None:
            strict_drain = getattr(client, "drain_gracefully", None)
            if strict_drain is not None:
                if not callable(strict_drain):
                    raise CutoverError("graceful-only drain capability is invalid")
                if force:
                    raise CutoverError("graceful-only predecessor drain cannot force")
                graceful_only = True
                drain = strict_drain
        if on_drain is not None:
            on_drain()
        drain_res = drain(timeout=drain_timeout, poll=1.0, force=force)
    result.drain = drain_res
    result.steps.append(
        f"old drained clean={drain_res.get('clean')} forced={drain_res.get('forced')}"
    )
    if not drain_res.get("drained") or (
        graceful_only and (
            drain_res.get("drained") is not True
            or drain_res.get("clean") is not True
            or drain_res.get("forced") is not False
            or drain_res.get("mode") == "incompatible-contract-cold"
        )
    ):
        busy_work = drain_res.get(
            "busy_sessions_count" if conditional else "busy_sessions"
        )
        self._emit(
            lifecycle.DRAIN, lifecycle.FAIL, port=old.port,
            detail={"busy_sessions": busy_work},
        )
        if conditional:
            raise CutoverError(
                f"conditional predecessor drain incomplete (busy: {busy_work}); "
                "retirement refused"
            )
        raise CutoverError(
            f"old daemon did not drain (busy: {busy_work})"
            + ("" if self.drain_before_publish else "; rerun with force to proceed")
        )
    self._emit(
        lifecycle.DRAIN, lifecycle.OK, port=old.port,
        detail={"clean": drain_res.get("clean"), "forced": drain_res.get("forced")},
    )


def _retire_failed_predecessor(self: CutoverOrchestrator, old: Endpoint, result: CutoverResult) -> None:
    """Cleanup failure cannot reverse a healthy incoming generation."""
    result.retirement_requested = True
    try:
        cleanup = self.retire_failed_predecessor(old)
        if not isinstance(cleanup, dict):
            raise ValueError("predecessor cleanup returned no outcome")
    except Exception as exc:
        cleanup = {"exit_confirmed": False, "reason": type(exc).__name__}
    result.predecessor_cleanup = cleanup
    result.retirement_confirmed = cleanup.get("exit_confirmed") is True
    result.steps.append(
        "predecessor recovery failed; best-effort retirement "
        + ("confirmed" if result.retirement_confirmed else "unconfirmed")
    )


def shutdown_predecessor(self: CutoverOrchestrator, old: Endpoint, client, result: CutoverResult) -> None:
    from .cutover import CutoverError

    if not self.drain_predecessor or result.predecessor_cleanup is not None:
        _retire_failed_predecessor(self, old, result)
        return
    try:
        retirement = client.shutdown()
        if self.drain_before_publish and (
            not isinstance(retirement, dict) or retirement.get("retired") is not True
        ):
            raise CutoverError("old daemon retirement was not confirmed by its owner")
        if self.drain_before_publish:
            result.retirement_confirmed = True
            result.steps.append("old daemon exit confirmed")
    except Exception:
        if self.retire_failed_predecessor is None:
            raise
        _retire_failed_predecessor(self, old, result)


def complete_self_retirement(
    self: CutoverOrchestrator, successor: Endpoint, old: Endpoint | None, result: CutoverResult, *,
    health_timeout: float, poll: float, health_check: Callable[[str, int], bool],
) -> CutoverResult:
    from . import breadcrumb
    from .cutover import CutoverError

    activate = self.activate_successor
    assert activate is not None
    activate(successor)
    if not self._await_health(successor.port, health_timeout, poll, health_check):
        raise CutoverError("published successor did not become ready")
    if old is not None and self.retire_failed_predecessor is not None:
        _retire_failed_predecessor(self, old, result)
        self._emit(
            lifecycle.CUTOVER_RETIRE,
            lifecycle.OK if result.retirement_confirmed else lifecycle.FAIL,
            port=old.port, detail={
                "old_endpoint": old.to_dict(), "predecessor_cleanup": result.predecessor_cleanup,
            },
        )
    if not breadcrumb.clear_breadcrumb(self.config_dir):
        raise CutoverError("committed recovery record retained; retry cleanup")
    result.ok = True
    result.steps.append("successor ready; predecessor retirement is asynchronous")
    return result


def await_health(
    self: CutoverOrchestrator, port: int, timeout: float, poll: float,
    check: Callable[[str, int], bool] | None = None,
) -> bool:
    from .cutover import CutoverError

    check = check or self.health_check
    deadline = self.clock() + timeout
    while self.clock() < deadline:
        try:
            if check(self._client_host(), port):
                return True
        except CutoverError:
            raise
        except Exception:
            pass
        self.sleep(poll)
    try:
        return bool(check(self._client_host(), port))
    except CutoverError:
        raise
    except Exception:
        return False
