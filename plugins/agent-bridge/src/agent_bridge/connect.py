"""Checkpointed connection-establishment pipeline.

Reaching a remote agent goes through a sequence of distinct stages, each with
its own failure mode and patience profile. When something goes wrong we must
know *which* stage failed and *why* -- "agent died, trying a new session" is
unacceptable.

This module defines:

- :class:`ConnectStage` -- the ordered stages of bringing up an agent.
- :data:`STAGE_POLICIES` -- per-stage policy (patient vs fail-fast, whether an
  outer layer may retry, and a default timeout).
- :class:`ConnectError` -- a failure tagged with the stage it occurred in and
  whether it is retryable.
- :class:`ConnectTracker` -- records ``connect_checkpoint`` events
  (started/reached/failed, with elapsed time) into a session's event log *and*
  the service log, so progress is visible both in `read` and in the daemon log.

The stages (and their intended behavior, per design):

1. CONNECT_BRIDGE   -- CLI -> agent-bridge service. May be transient (server
                       restart); patient grace + retryable.
2. BRIDGE_TO_SSHMGR -- in-process hand-off to ssh-manager. Reliable; fail fast.
3. SSH_TO_TARGET    -- ssh-manager -> ssh on the target. May need to wait
                       (codespace boot, wake-on-LAN, ProxyJump); patient +
                       retryable, bounded by a deadline.
4. TARGET_AUTH_ENV  -- on target: auth relay + env vars confirmed. If this
                       fails we are dead -- instant fail, not retryable.
5. TARGET_BINSTUB   -- target binstub present / target folder verified. Instant
                       fail if missing.
6. WORKTREE         -- create/resume the target worktree. Fairly fast; failures
                       propagate, no retries.
7. LAUNCH_ACP       -- launch Copilot in ACP mode. Should be fast; propagate.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import IntEnum
from typing import Any

log = logging.getLogger("agent-bridge")


class ConnectStage(IntEnum):
    """Ordered stages of establishing a connection to a remote agent."""

    CONNECT_BRIDGE = 1
    BRIDGE_TO_SSHMGR = 2
    SSH_TO_TARGET = 3
    TARGET_AUTH_ENV = 4
    TARGET_BINSTUB = 5
    WORKTREE = 6
    LAUNCH_ACP = 7


@dataclass(frozen=True)
class StagePolicy:
    """How a stage behaves on the way to a connection.

    - ``patient``: the stage may legitimately take a while (callers should not
      treat slowness as failure).
    - ``retryable``: an outer layer may retry this stage (vs propagate
      immediately).
    - ``default_timeout``: seconds to bound the stage (0 = no explicit cap).
    """

    stage: ConnectStage
    label: str
    patient: bool
    retryable: bool
    default_timeout: float
    note: str


STAGE_POLICIES: dict[ConnectStage, StagePolicy] = {
    ConnectStage.CONNECT_BRIDGE: StagePolicy(
        ConnectStage.CONNECT_BRIDGE, "connect-bridge",
        patient=True, retryable=True, default_timeout=10.0,
        note="CLI -> service; transient on restart, short grace + retry",
    ),
    ConnectStage.BRIDGE_TO_SSHMGR: StagePolicy(
        ConnectStage.BRIDGE_TO_SSHMGR, "bridge-to-sshmgr",
        patient=False, retryable=False, default_timeout=5.0,
        note="in-process hand-off; reliable, fail fast",
    ),
    ConnectStage.SSH_TO_TARGET: StagePolicy(
        ConnectStage.SSH_TO_TARGET, "ssh-to-target",
        patient=True, retryable=True, default_timeout=120.0,
        note="boot/WoL/ProxyJump may be slow; patient + retry to deadline",
    ),
    ConnectStage.TARGET_AUTH_ENV: StagePolicy(
        ConnectStage.TARGET_AUTH_ENV, "target-auth-env",
        patient=False, retryable=False, default_timeout=20.0,
        note="auth relay + env; dead if it fails, instant fail",
    ),
    ConnectStage.TARGET_BINSTUB: StagePolicy(
        ConnectStage.TARGET_BINSTUB, "target-binstub",
        patient=False, retryable=False, default_timeout=20.0,
        note="binstub/folder must exist; instant fail",
    ),
    ConnectStage.WORKTREE: StagePolicy(
        ConnectStage.WORKTREE, "worktree",
        patient=False, retryable=False, default_timeout=120.0,
        note="create/resume worktree; propagate, no retries",
    ),
    ConnectStage.LAUNCH_ACP: StagePolicy(
        ConnectStage.LAUNCH_ACP, "launch-acp",
        patient=False, retryable=False, default_timeout=60.0,
        note="launch Copilot ACP; should be fast, propagate",
    ),
}


def stage_policy(stage: ConnectStage) -> StagePolicy:
    """Return the policy for a stage."""
    return STAGE_POLICIES[stage]


class ConnectError(RuntimeError):
    """A connection failure tagged with the stage where it happened.

    Subclasses :class:`RuntimeError` so existing ``except RuntimeError`` paths
    (and the public ``start_session`` contract) keep working unchanged while
    callers that care can inspect ``.stage`` / ``.retryable``.

    ``retryable`` defaults to the stage's policy but can be overridden when an
    inner layer knows better (e.g. an auth rejection at SSH_TO_TARGET is not
    retryable even though the stage usually is).
    """

    def __init__(
        self,
        stage: ConnectStage,
        message: str,
        *,
        retryable: bool | None = None,
        cause: BaseException | None = None,
    ) -> None:
        self.stage = stage
        self.detail = message
        self.retryable = (
            STAGE_POLICIES[stage].retryable if retryable is None else retryable
        )
        self.cause = cause
        super().__init__(f"[stage {int(stage)}/{stage.name}] {message}")


class ConnectTracker:
    """Emit connection checkpoints to a session event log and the service log.

    ``emit`` is an optional callable ``(event_type, data) -> None`` -- typically
    ``session.event_log.append`` -- so checkpoints show up in the host's feed
    (visible via ``read``). Logging always happens regardless of ``emit``.
    """

    EVENT = "connect_checkpoint"

    def __init__(
        self,
        emit: Callable[[str, dict[str, Any]], Any] | None = None,
        *,
        session_id: str = "",
    ) -> None:
        self._emit = emit
        self.session_id = session_id
        self._started_at: dict[ConnectStage, float] = {}

    def started(self, stage: ConnectStage, detail: str = "") -> None:
        self._started_at[stage] = time.monotonic()
        self._checkpoint(stage, "started", detail)

    def reached(self, stage: ConnectStage, detail: str = "") -> None:
        self._checkpoint(stage, "reached", detail, self._elapsed_ms(stage))

    def failed(
        self,
        stage: ConnectStage,
        detail: str = "",
        *,
        retryable: bool | None = None,
    ) -> None:
        self._checkpoint(
            stage, "failed", detail, self._elapsed_ms(stage),
            retryable=(
                STAGE_POLICIES[stage].retryable if retryable is None else retryable
            ),
        )

    def _elapsed_ms(self, stage: ConnectStage) -> int | None:
        t0 = self._started_at.get(stage)
        if t0 is None:
            return None
        return int((time.monotonic() - t0) * 1000)

    def _checkpoint(
        self,
        stage: ConnectStage,
        status: str,
        detail: str,
        elapsed_ms: int | None = None,
        *,
        retryable: bool | None = None,
    ) -> None:
        policy = STAGE_POLICIES[stage]
        data: dict[str, Any] = {
            "stage": int(stage),
            "stage_name": stage.name,
            "label": policy.label,
            "status": status,
            "patient": policy.patient,
        }
        if detail:
            data["detail"] = detail
        if elapsed_ms is not None:
            data["elapsed_ms"] = elapsed_ms
        if retryable is not None:
            data["retryable"] = retryable

        level = logging.ERROR if status == "failed" else logging.INFO
        suffix = f": {detail}" if detail else ""
        elapsed = f" ({elapsed_ms}ms)" if elapsed_ms is not None else ""
        log.log(
            level,
            "connect[%s] stage %d/%s %s%s%s",
            self.session_id or "-", int(stage), stage.name, status, elapsed, suffix,
        )
        if self._emit is not None:
            try:
                self._emit(self.EVENT, data)
            except Exception:
                # A checkpoint emit must never break connection establishment.
                log.debug("connect checkpoint emit failed", exc_info=True)

    @contextmanager
    def stage(self, stage: ConnectStage, detail: str = "") -> Iterator[None]:
        """Wrap a stage: emit started, then reached or failed.

        A :class:`ConnectError` raised inside is re-raised unchanged (its stage
        is already known). Any other exception is recorded as a failure for this
        stage and wrapped in a :class:`ConnectError` so the stage is never lost.
        """
        self.started(stage, detail)
        try:
            yield
        except ConnectError as exc:
            # Already staged (possibly a deeper stage) -- record + propagate.
            self.failed(exc.stage, exc.detail, retryable=exc.retryable)
            raise
        except Exception as exc:
            self.failed(stage, str(exc))
            raise ConnectError(stage, str(exc), cause=exc) from exc
        else:
            self.reached(stage)
