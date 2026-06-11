"""Checkpointed connection stages for the CodeSpace provider.

Mirrors ``agent_bridge.connect`` so CodeSpace connection failures name the stage
that broke instead of surfacing a generic provider error. agent-bridge spawns
this plugin via ``spawn_command`` for CodeSpace targets, so connection stages
3-7 run *inside* this provider -- this module gives them the same observability
(stage logging + an on-device breadcrumb) and fail-fast/patient semantics.

NOTE: this intentionally duplicates the small taxonomy in
``agent_bridge.connect``. The tracked follow-up (dotfiles #27) is to hoist a
single ``ConnectStage`` + breadcrumb helper into the shared ``ssh-manager``
library so both plugins share one source of truth.
"""

from __future__ import annotations

import logging
import shlex
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import IntEnum
from typing import Any

log = logging.getLogger("agent-codespaces")

# Default path for the on-device breadcrumb log (shared with agent-bridge so a
# human only has one file to check on the target).
CONNECT_LOG_ENV = "AGENT_BRIDGE_CONNECT_LOG"
DEFAULT_CONNECT_LOG = "$HOME/.agent-bridge/connect.log"


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
    stage: ConnectStage
    label: str
    patient: bool
    retryable: bool


STAGE_POLICIES: dict[ConnectStage, StagePolicy] = {
    ConnectStage.SSH_TO_TARGET: StagePolicy(
        ConnectStage.SSH_TO_TARGET, "ssh-to-target", patient=True, retryable=True,
    ),
    ConnectStage.TARGET_AUTH_ENV: StagePolicy(
        ConnectStage.TARGET_AUTH_ENV, "target-auth-env", patient=False,
        retryable=False,
    ),
    ConnectStage.TARGET_BINSTUB: StagePolicy(
        ConnectStage.TARGET_BINSTUB, "target-binstub", patient=False,
        retryable=False,
    ),
    ConnectStage.WORKTREE: StagePolicy(
        ConnectStage.WORKTREE, "worktree", patient=False, retryable=False,
    ),
    ConnectStage.LAUNCH_ACP: StagePolicy(
        ConnectStage.LAUNCH_ACP, "launch-acp", patient=False, retryable=False,
    ),
}


class ConnectError(RuntimeError):
    """A connection failure tagged with the stage where it happened."""

    def __init__(
        self,
        stage: ConnectStage,
        message: str,
        *,
        retryable: bool = False,
        cause: BaseException | None = None,
    ) -> None:
        self.stage = stage
        self.detail = message
        self.retryable = retryable
        self.cause = cause
        super().__init__(f"[stage {int(stage)}/{stage.name}] {message}")


class ConnectTracker:
    """Log connection checkpoints (and optionally emit them via a callback).

    In stdio (ACP) mode the provider's stdout is the protocol channel, so
    checkpoints go to the logger only -- never stdout.
    """

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
        self, stage: ConnectStage, detail: str = "", *, retryable: bool = False
    ) -> None:
        self._checkpoint(
            stage, "failed", detail, self._elapsed_ms(stage), retryable=retryable
        )

    def _elapsed_ms(self, stage: ConnectStage) -> int | None:
        t0 = self._started_at.get(stage)
        return None if t0 is None else int((time.monotonic() - t0) * 1000)

    def _checkpoint(
        self,
        stage: ConnectStage,
        status: str,
        detail: str,
        elapsed_ms: int | None = None,
        *,
        retryable: bool | None = None,
    ) -> None:
        data: dict[str, Any] = {
            "stage": int(stage),
            "stage_name": stage.name,
            "status": status,
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
            level, "connect[%s] stage %d/%s %s%s%s",
            self.session_id or "-", int(stage), stage.name, status, elapsed, suffix,
        )
        if self._emit is not None:
            try:
                self._emit("connect_checkpoint", data)
            except Exception:
                log.debug("connect checkpoint emit failed", exc_info=True)

    @contextmanager
    def stage(self, stage: ConnectStage, detail: str = "") -> Iterator[None]:
        self.started(stage, detail)
        try:
            yield
        except ConnectError as exc:
            self.failed(exc.stage, exc.detail, retryable=exc.retryable)
            raise
        except Exception as exc:
            self.failed(stage, str(exc))
            raise ConnectError(stage, str(exc), cause=exc) from exc
        else:
            self.reached(stage)


def breadcrumb_prelude(session_id: str) -> str:
    """A POSIX snippet recording arrival on the CodeSpace.

    Appended (best-effort) to ``$AGENT_BRIDGE_CONNECT_LOG`` (default
    ``$HOME/.agent-bridge/connect.log``) the moment the remote shell runs --
    *before* the binstub/worktree/Copilot steps. If a later step hangs, a human
    can SSH in and confirm the connection reached the CodeSpace (and when),
    distinguishing an unreachable CodeSpace from an on-device failure. Creates
    the log dir if needed and never aborts the command (``( ... ) || true``).
    """
    sid = shlex.quote(session_id or "-")
    log_expr = f'"${{{CONNECT_LOG_ENV}:-{DEFAULT_CONNECT_LOG}}}"'
    ts = '"$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo unknown)"'
    host = '"$(hostname 2>/dev/null || echo \\?)"'
    return (
        f'( mkdir -p "$(dirname {log_expr})" 2>/dev/null; '
        f"printf '%s agent-codespaces reached-device session=%s pid=%s host=%s\\n' "
        f"{ts} {sid} \"$$\" {host} >> {log_expr} 2>/dev/null ) || true"
    )
