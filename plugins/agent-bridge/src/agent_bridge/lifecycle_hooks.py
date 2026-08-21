"""Thin, fail-open daemon lifecycle hooks.

Extracted from the app lifespan so the wiring -- the startup dead-port sweep and
the durable ``start``/``stop`` lifecycle records -- is unit-testable without the
full FastAPI app. Each helper is a best-effort wrapper around the shared ``zdd``
primitives and **never raises**, so a lifecycle hook can run inside daemon
startup/shutdown without any risk of perturbing it.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger("agent-bridge.lifecycle")

_SERVICE = "agent-bridge"


def startup_sweep(config_dir: str | os.PathLike[str]) -> None:
    """Dead-port watchdog: retire any advertised-but-dead endpoint before we
    announce ourselves (self-heals a stale port on a plain restart, not only a
    redeploy). Best-effort."""
    try:
        from zdd import routing

        routing.reap_stale_active(config_dir, service=_SERVICE)
    except Exception:
        log.debug("startup dead-port sweep skipped", exc_info=True)


def record_start(
    config_dir: str | os.PathLike[str], version: str | None, port: int | None
) -> bool:
    """Write a durable ``start`` lifecycle record. Returns True iff it was
    actually recorded (so the caller can gate a matching ``stop`` on it)."""
    try:
        from zdd import lifecycle

        rec = lifecycle.record(
            config_dir, lifecycle.START, service=_SERVICE,
            outcome=lifecycle.OK, version=version, port=port,
        )
        return rec is not None
    except Exception:
        log.debug("start lifecycle record skipped", exc_info=True)
        return False


def record_stop(config_dir: str | os.PathLike[str]) -> None:
    """Write a durable ``stop`` lifecycle record (best-effort)."""
    try:
        from zdd import lifecycle

        lifecycle.record(
            config_dir, lifecycle.STOP, service=_SERVICE, outcome=lifecycle.OK,
        )
    except Exception:
        log.debug("stop lifecycle record skipped", exc_info=True)
