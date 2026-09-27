"""Run the transplanted production Picker and return its launch decision."""

from __future__ import annotations

import os
import threading
from collections.abc import Mapping

from .. import engine_client
from . import context
from . import engine_group_b
from . import housekeeping
from .picker_tui import run_tui_picker

def _prepare(project: str, *, heal: bool = True) -> bool:
    """Activate ``project`` and return the engine-owned default live mode."""
    context.set_project(project)
    try:
        bootstrap = context.bind_project_bootstrap(
            engine_group_b.picker_bootstrap(project)
        )
    except engine_client.EngineFeatureUnavailable as error:
        raise RuntimeError(
            "the installed agent-worktrees engine predates "
            "`picker-bootstrap --json`; update agent-worktrees before "
            "using the production Picker"
        ) from error
    if bootstrap.should_switch_cwd and bootstrap.cwd is not None:
        os.chdir(bootstrap.cwd)
    if heal:
        _start_anchor_heal_check(bootstrap.project)
    return bootstrap.default_live


def _start_anchor_heal_check(project: str) -> None:
    """Best-effort stale-anchor self-heal, entirely off the render path.

    ``repair-stale-anchor --json`` is already a best-effort, background-safe
    repair seam and its common case (this machine's self-entry is already
    present) is a cheap no-op. Run it fire-and-forget off the render path so
    first paint never waits on control-plane discovery or machine-roster
    inspection; nothing downstream reads its result."""

    def _worker() -> None:
        try:
            engine_group_b.repair_stale_anchor(project)
        except Exception:
            pass

    threading.Thread(
        target=_worker,
        name="production-picker-anchor-heal",
        daemon=True,
    ).start()


def _start_housekeeping() -> None:
    """Run the production pre-Picker sweeps without delaying first paint."""

    def reap_background() -> None:
        try:
            housekeeping.reap_orphan_mux_sessions(only_owned=True)
        except Exception:
            pass
        housekeeping.sweep_managed_on_exit()
        housekeeping.sweep_launcher_shells_on_exit()
        housekeeping.sweep_finished_sessions_on_cadence()

    threading.Thread(
        target=reap_background,
        name="production-picker-reap-orphans",
        daemon=True,
    ).start()


def run(
    project: str,
    *,
    mock_mode: bool | None = None,
    local: bool = False,
) -> Mapping[str, object] | None:
    """Run the production Picker against an adopted project.

    This compatibility boundary preserves the established Picker UX while its
    data/action imports are replaced with Manager-owned process adapters.
    """
    from .picker_tui.engine import _resolve_mock_mode

    resolved_mock = _resolve_mock_mode(mock_mode)
    default_live = _prepare(project, heal=not resolved_mock)
    if not resolved_mock:
        _start_housekeeping()
    picker_root = None if resolved_mock else housekeeping.start_picker_monitor_root()
    live = False if local else default_live
    try:
        if not resolved_mock and mock_mode is None:
            return run_tui_picker(live=live)
        return run_tui_picker(live=live, mock_mode=resolved_mock)
    finally:
        if picker_root is not None:
            picker_root.close()


def capture(
    project: str,
    *,
    live: bool = False,
    pivot: str | None = None,
    wait_pivot: float = 0.0,
) -> dict[str, str]:
    """Capture the Manager-owned production Picker headlessly."""
    _prepare(project, heal=False)
    if live:
        from .picker_tui import data_ssh as source
    else:
        from .picker_tui import data_local as source
    from .picker_tui import capture as picker_capture

    return picker_capture.capture(
        source,
        live=live,
        pivot=pivot,
        wait_pivot=wait_pivot,
    )
