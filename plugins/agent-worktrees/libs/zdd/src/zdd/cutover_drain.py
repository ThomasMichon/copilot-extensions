"""Predecessor drain admission and its observable outcome."""

from __future__ import annotations

from typing import TYPE_CHECKING
from collections.abc import Callable

from . import lifecycle
from .routing import Endpoint

if TYPE_CHECKING:
    from .conditional import PreparedCutover
    from .cutover import CutoverOrchestrator, CutoverResult


def _drain_old(
    self: CutoverOrchestrator, old: Endpoint, result: CutoverResult, *,
    drain_timeout: float, force: bool, conditional: PreparedCutover | None = None,
    on_drain: Callable[[], None] | None = None,
) -> None:
    from .cutover import CutoverError

    self._emit(lifecycle.DRAIN, lifecycle.BEGIN, port=old.port)
    if conditional:
        drain_res = conditional.drain_predecessor(drain_timeout)
    else:
        drain = self.make_client(old.base_url).drain
        if on_drain is not None:
            on_drain()
        drain_res = drain(timeout=drain_timeout, poll=1.0, force=force)
    result.drain = drain_res
    result.steps.append(
        f"old drained clean={drain_res.get('clean')} forced={drain_res.get('forced')}"
    )
    if not drain_res.get("drained") or (
        self.drain_before_publish and (
            drain_res.get("drained") is not True
            or drain_res.get("clean") is not True
            or drain_res.get("forced") is not False
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
