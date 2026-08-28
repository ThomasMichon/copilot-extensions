"""Run the transplanted production Picker and return its launch decision."""

from __future__ import annotations

import os
import threading
from collections.abc import Mapping

from ._engine_runtime import engine_module
from .picker_tui import run_tui_picker


def run(project: str) -> Mapping[str, object] | None:
    """Run the production Picker against an adopted project.

    This compatibility boundary preserves the established Picker UX while its
    data/action imports are replaced with Manager-owned process adapters.
    """
    config_module = engine_module("config")
    cli = engine_module("__main__")
    resolved, assumed = cli._resolve_active_project(project)
    if not resolved:
        raise RuntimeError(f"unknown project {project!r}")
    config_module.set_active_project(resolved)
    if assumed is not None and not cli._cwd_is_inside_project(assumed):
        os.chdir(assumed)
    config = config_module.load_config()
    config = cli._heal_stale_anchor_if_self_missing(config)

    def reap_background() -> None:
        try:
            cli.reap_orphan_mux_sessions()
        except Exception:
            pass
        cli._sweep_managed_on_exit()
        cli._sweep_launcher_shells_on_exit()
        cli._sweep_finished_sessions_on_cadence()

    threading.Thread(
        target=reap_background,
        name="production-picker-reap-orphans",
        daemon=True,
    ).start()
    picker_root = cli._start_picker_monitor_root()
    live = not cli._in_ssh_session()
    try:
        return run_tui_picker(live=live)
    finally:
        if picker_root is not None:
            picker_root.close()
