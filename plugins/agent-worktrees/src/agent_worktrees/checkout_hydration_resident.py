"""Resident status-monitor cadence for automatic checkout dehydration (Phase 4).

Post-exit only queues a candidate; this runs the bounded pass
(:func:`checkout_hydration_auto.run_pending`) from the existing resident
status-monitor loop, so a retired worktree is dehydrated without waiting for
another exit in its project. The first tick after the monitor starts is the
restart reconciliation. :meth:`DehydrationMaintenance.tick` never blocks the
loop: at most one worker thread runs, and each project's pass is a separate
bounded ``hydration --run-pending`` child, because selecting a project is
process-global configuration state.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from . import checkout_hydration_auto as auto
from . import checkout_hydration_catalog as catalog
from . import config as cfg

INTERVAL = 60.0
PASS_TIMEOUT = 300.0
SHUTDOWN_GRACE = 10.0
SETTLE_LIMIT = 30.0

_CHILDREN: set[subprocess.Popen[bytes]] = set()
_UNCONFIRMED: dict[subprocess.Popen[bytes], float | None] = {}  # child -> hold deadline once it exited
_CHILDREN_LOCK = threading.Lock()
_LOG = logging.getLogger(__name__)


def _registered_projects() -> set[str]:
    from .allocation_link_discovery import registered_projects

    return registered_projects()


def _has_work(project: str) -> bool:
    """Cheap filter: automatic policy and a queued candidate or interrupted entry."""
    if auto.policy_mode(project) != auto.MODE_AUTOMATIC:
        return False
    try:
        if any(cfg.tracking_dir(project).glob(f"*/{auto.CANDIDATE}")):
            return True
        roots = catalog.registered_roots(project)
    except (OSError, ValueError, catalog.CatalogError):
        return False
    for root in roots:
        try:
            entries = list(catalog.iter_entries(Path(root)))
        except OSError:
            continue
        for path in entries:
            try:
                entry = catalog.read_entry(path)
            except catalog.CatalogError:
                continue  # inspection reports it as a hold; nothing automatic to do
            if entry and entry["stage"] in (catalog.STAGE_REMOVING, catalog.STAGE_REHYDRATING):
                return True
    return False


def _tree_kwargs() -> dict[str, Any]:
    """Windowless launch in its own process group, so the pass and its Git children stop together."""
    from agent_procutil import contained_test_mode, no_window_kwargs

    kwargs: dict[str, Any] = dict(no_window_kwargs())
    if sys.platform == "win32":
        # The pass needs its own group so a timeout or shutdown can stop its whole tree.
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NEW_PROCESS_GROUP  # headless-guard: allow group isolation for tree kill; no_window_kwargs keeps it windowless
    elif not contained_test_mode():
        kwargs["start_new_session"] = True
    return kwargs


def _kill_tree(child: subprocess.Popen[bytes]) -> None:
    """Kill the pass and its descendants (taskkill /T or killpg), then the root by handle.

    The PID sweep runs only while the open handle shows this exact child is
    still running, so its PID cannot have been reused. A group shared with
    this process (contained tests) is never signalled. When the sweep fails or
    times out, the child is marked unconfirmed so its pass stays busy; the
    handle-bound root kill always runs.
    """
    from agent_procutil import no_window_kwargs

    confirmed = True
    try:
        if child.poll() is None:
            if sys.platform == "win32":
                try:
                    done = subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(child.pid)],
                        stdin=subprocess.DEVNULL, capture_output=True, check=False, timeout=30,
                        **no_window_kwargs(),
                    )
                    confirmed = done.returncode == 0
                except (OSError, subprocess.SubprocessError):
                    confirmed = False
            else:
                try:
                    group = os.getpgid(child.pid)
                    if group != os.getpgrp():
                        os.killpg(group, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except OSError:
                    confirmed = False
    finally:
        if not confirmed:
            _LOG.warning("checkout dehydration pass %s: process-tree stop unconfirmed", child.pid)
            with _CHILDREN_LOCK:
                _UNCONFIRMED.setdefault(child, None)
        try:
            child.kill()
        except OSError:
            pass


def tracked_busy() -> bool:
    """Whether a tracked pass root still runs, or its unconfirmed tree stop is within ``SETTLE_LIMIT`` of its exit."""
    now = time.monotonic()
    with _CHILDREN_LOCK:
        for child in list(_CHILDREN):
            if child.poll() is None:
                continue
            if child in _UNCONFIRMED and _UNCONFIRMED[child] is None:
                _UNCONFIRMED[child] = now + SETTLE_LIMIT  # descendants may outlive the root briefly
            hold = _UNCONFIRMED.get(child)
            if hold is None or now >= hold:
                _CHILDREN.discard(child)
                _UNCONFIRMED.pop(child, None)
        return bool(_CHILDREN)


def run_project_pass(project: str) -> None:
    """One bounded child pass for ``project``; failures wait for the next tick."""
    argv = [sys.executable, "-I", "-X", "utf8", "-m", "agent_worktrees", "--project", project,
            "hydration", "--run-pending", "--json"]
    try:
        child = subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            cwd=str(Path.home()), **_tree_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return
    with _CHILDREN_LOCK:
        _CHILDREN.add(child)
    try:
        try:
            child.wait(timeout=PASS_TIMEOUT)
        except subprocess.TimeoutExpired:
            _kill_tree(child)
            child.wait(timeout=SHUTDOWN_GRACE)
    except subprocess.TimeoutExpired:
        _LOG.warning("checkout dehydration pass %s still running after kill; kept busy", child.pid)
    finally:
        tracked_busy()  # untracks the child only once it exited and its tree stop is settled


def terminate_passes() -> None:
    """Stop running pass trees; an interrupted operation is recovered by reconcile."""
    with _CHILDREN_LOCK:
        children = list(_CHILDREN)
    for child in children:
        if child.poll() is not None:
            continue
        try:
            _kill_tree(child)
        except (OSError, subprocess.SubprocessError):
            pass


class DehydrationMaintenance:
    """Non-blocking resident tick; the first tick runs immediately."""

    def __init__(
        self, *, interval: float = INTERVAL,
        projects: Callable[[], Iterable[str]] = _registered_projects,
        has_work: Callable[[str], bool] = _has_work,
        run_pass: Callable[[str], None] = run_project_pass,
        now: Callable[[], float] = time.monotonic, admitting: bool = True,
        terminate: Callable[[], None] = terminate_passes,
    ) -> None:
        self._terminate = terminate
        self._interval = interval
        self._projects = projects
        self._has_work = has_work
        self._run_pass = run_pass
        self._now = now
        self._lock = threading.Lock()
        self._admitting = admitting
        self._due = 0.0
        self._pending = False
        self._worker: threading.Thread | None = None

    def tick(self) -> bool:
        """Start one pass when due and idle; returns whether it started one."""
        with self._lock:
            if not self._admitting or self._now() < self._due:
                return False
            if self._worker is not None and self._worker.is_alive():
                return False
            if tracked_busy():
                return False
            self._due = self._now() + self._interval
            self._worker = threading.Thread(
                target=self._run, name="checkout-dehydration", daemon=True,
            )
            self._worker.start()
            return True

    def _run(self) -> None:
        still_pending = False
        try:
            projects = sorted(self._projects())
        except Exception:
            projects = []
        for project in projects:
            with self._lock:
                if not self._admitting:
                    break
            try:
                if not self._has_work(project):
                    continue
                self._run_pass(project)
                # Candidates retained for a transient refusal are retried next
                # interval; each one is dropped after a bounded attempt count.
                still_pending = still_pending or self._has_work(project)
            except Exception:
                continue
        with self._lock:
            self._pending = still_pending

    def busy(self) -> bool:
        """Whether a pass (and its child) is still in flight, whatever admission says."""
        with self._lock:
            running = self._worker is not None and self._worker.is_alive()
        return running or tracked_busy()

    def has_demand(self) -> bool:
        """Queued candidates (while admitting) or in-flight work keep the monitor resident."""
        with self._lock:
            in_flight = self._worker is not None and self._worker.is_alive()
            queued = self._admitting and self._pending
        return in_flight or queued or tracked_busy()

    def close_admission(self) -> None:
        """Stop starting passes; a running child finishes and stays :meth:`busy`."""
        with self._lock:
            self._admitting = False

    def activate(self) -> None:
        """Reopen admission when the monitor (re)activates its request surfaces."""
        with self._lock:
            self._admitting = True

    def shutdown(self, timeout: float = PASS_TIMEOUT + SHUTDOWN_GRACE) -> bool:
        """Close admission and settle an in-flight pass before monitor ownership ends.

        Waits up to ``timeout`` (the child's own bound plus a grace), then
        terminates a still-running child and waits briefly again; an operation
        interrupted that way is recovered by the next pass's reconciliation.
        Returns whether no pass is still running.
        """
        self.close_admission()
        with self._lock:
            worker = self._worker
        if worker is not None:
            worker.join(timeout)
        if not self.busy():
            return True
        self._terminate()
        deadline = time.monotonic() + SETTLE_LIMIT + SHUTDOWN_GRACE
        while self.busy() and time.monotonic() < deadline:
            time.sleep(0.2)  # an unconfirmed tree stop keeps ownership for this bounded wait
        if self.busy():
            _LOG.warning("checkout dehydration pass still running or unsettled at monitor shutdown")
            return False
        return True

    def shutdown_or_raise(self, timeout: float = PASS_TIMEOUT + SHUTDOWN_GRACE) -> None:
        """:meth:`shutdown`, raising while a pass is unsettled so monitor retirement keeps ownership and retries."""
        if not self.shutdown(timeout):
            raise RuntimeError("Checkout dehydration pass settlement remains pending")

    def wait(self, timeout: float | None = None) -> None:
        """Test and shutdown helper: wait for a running pass."""
        worker = self._worker
        if worker is not None:
            worker.join(timeout)
