"""Run the transplanted production Picker and return its launch decision."""

from __future__ import annotations

import os
import threading
from collections.abc import Mapping

from .. import engine_client, harness_state
from . import context
from . import engine_group_b
from . import housekeeping
from .picker_tui import run_tui_picker


class ProjectSelectionError(RuntimeError):
    """Invalid implicit selection, retaining the CLI's usage-error exit code."""


def _prepare(project: str, *, heal: bool = True) -> bool:
    """Activate ``project`` and return the engine-owned default live mode."""
    from . import project_config

    context.set_project(project)
    try:
        payload = engine_group_b.picker_bootstrap(project)
        bootstrap = context.bind_project_bootstrap(payload)
    except engine_client.EngineFeatureUnavailable as error:
        raise RuntimeError(
            "the installed agent-worktrees engine predates "
            "`picker-bootstrap --json`; update agent-worktrees before "
            "using the production Picker"
        ) from error
    if bootstrap.should_switch_cwd and bootstrap.cwd is not None:
        os.chdir(bootstrap.cwd)
    project_config.bind_bootstrap(bootstrap.project, payload.get("project_config"))
    if heal:
        _start_anchor_heal_check(bootstrap.project)
    return bootstrap.default_live


def _select_project(project: str) -> str:
    if not engine_client.engine_available():
        raise RuntimeError(
            "the agent-worktrees engine is not installed; "
            "run `worktree-manager setup --apply`"
        )
    if project:
        return project
    projects = harness_state.build_projects()
    launchable = [p for p in projects if not (p.repo and p.repo.klass == "knowledge")]
    if len(launchable) == 1:
        return launchable[0].name
    if not launchable:
        raise ProjectSelectionError("no project to open. Adopt one, or pass a project name.")
    raise ProjectSelectionError(
        "multiple projects are registered and none was specified. Pass a project name: "
        + ", ".join(p.name for p in launchable)
    )


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
    from .picker_tui.engine_loading import PickerBinding
    from . import project_config

    resolved_mock = _resolve_mock_mode(mock_mode)
    picker_root = None
    finished = threading.Event()
    root_lock = threading.Lock()

    def prepare():
        selected = project if resolved_mock and project else _select_project(project)
        try:
            payload = engine_group_b.picker_bootstrap(selected)
        except engine_client.EngineFeatureUnavailable as error:
            raise RuntimeError(
                "the installed agent-worktrees engine predates "
                "`picker-bootstrap --json`; update agent-worktrees"
            ) from error
        bootstrap = context.ProjectBootstrap.from_payload(payload)
        live = False if local else bootstrap.default_live
        if live:
            from .picker_tui import data_ssh as source
        else:
            from .picker_tui import data_local as source

        def activate():
            # Commit only on the surviving pending screen. The worker resolves
            # authority but never changes cwd/context after Escape.
            if bootstrap.should_switch_cwd and bootstrap.cwd is not None:
                os.chdir(bootstrap.cwd)
            context.bind_project_bootstrap(payload)
            project_config.bind_bootstrap(bootstrap.project, payload.get("project_config"))

        return PickerBinding(source, live, activate)

    def after_binding():
        nonlocal picker_root
        if resolved_mock or finished.is_set():
            return
        _start_anchor_heal_check(context.project())
        _start_housekeeping()
        root = housekeeping.start_picker_monitor_root()
        with root_lock:
            if finished.is_set():
                if root is not None:
                    root.close()
            else:
                picker_root = root

    try:
        return run_tui_picker(
            prepare=prepare,
            project_label=project or "selecting project",
            mock_mode=resolved_mock,
            after_first_refresh=after_binding,
        )
    finally:
        finished.set()
        with root_lock:
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
        setup_timeout=engine_client._DEFAULT_TIMEOUT,
        pivot=pivot,
        wait_pivot=wait_pivot,
    )
