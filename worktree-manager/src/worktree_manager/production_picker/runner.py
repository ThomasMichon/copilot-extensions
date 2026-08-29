"""Run the transplanted production Picker and return its launch decision."""

from __future__ import annotations

import os
import threading
from collections.abc import Mapping
from typing import Any

from ._engine_runtime import engine_module
from .picker_tui import run_tui_picker


def _prepare(project: str, *, heal: bool = True) -> tuple[Any, bool]:
    """Activate ``project`` and return the engine CLI plus default live mode."""
    config_module = engine_module("config")
    cli = engine_module("__main__")
    resolved, assumed = cli._resolve_active_project(project)
    if not resolved:
        raise RuntimeError(f"unknown project {project!r}")
    config_module.set_active_project(resolved)
    if assumed is not None and not cli._cwd_is_inside_project(assumed):
        os.chdir(assumed)
    config = config_module.load_config()
    if heal:
        cli._heal_stale_anchor_if_self_missing(config)
    return cli, not cli._in_ssh_session()


def _start_housekeeping(cli: Any) -> None:
    """Run the production pre-Picker sweeps without delaying first paint."""

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
    cli, default_live = _prepare(project, heal=not resolved_mock)
    if not resolved_mock:
        _start_housekeeping(cli)
    picker_root = None if resolved_mock else cli._start_picker_monitor_root()
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


def compatibility_remote_plan(
    project: str,
    *,
    machine: str,
    environment: str | None,
    worktree_id: str | None,
    mode: str,
    no_mux: bool,
) -> dict[str, object]:
    """Build a remote handoff for engines predating the JSON remote seam."""
    config_module = engine_module("config")
    cli = engine_module("__main__")
    config = config_module.load_config()
    entries = config_module.load_machines_yaml(config.default_repo.anchor)
    key = cli._machine_key_for_display(config, machine)
    entry = entries.get(key)
    if entry is None:
        normalized = machine.casefold()
        entry = next(
            (
                candidate
                for candidate in entries.values()
                if normalized
                in {
                    candidate.key.casefold(),
                    candidate.display_name.casefold(),
                    (candidate.alias or "").casefold(),
                }
            ),
            None,
        )
    if entry is None or not entry.ssh_environments:
        raise RuntimeError(f"unknown or unreachable remote machine: {machine}")

    labels = {"win": "windows", "wsl": "wsl", "linux": "linux"}
    requested = (environment or "").strip().casefold()
    environment_name = labels.get(requested)
    if requested and environment_name is None:
        raise RuntimeError(f"unknown remote environment: {environment}")
    if environment_name:
        ssh_alias = next(
            (
                candidate.alias
                for candidate in entry.ssh_environments
                if candidate.name == environment_name and candidate.alias
            ),
            "",
        )
        if not ssh_alias:
            raise RuntimeError(
                f"remote environment is unavailable: {machine} {environment}"
            )
    else:
        ssh_alias = cli._resolve_ssh_alias(entry)

    remote_args: list[str]
    if mode == "base":
        remote_args = ["--base"]
    elif mode == "new":
        remote_args = ["--new"]
    else:
        if not worktree_id:
            raise RuntimeError("remote resume requires a worktree id")
        remote_args = ["--worktree-id", worktree_id]
        if mode == "bare-resume":
            remote_args.append("--bare-resume")
    if no_mux:
        remote_args.append("--no-mux")
    remote_command = " ".join([project, *remote_args])
    return {
        "action": "remote",
        "ssh_alias": ssh_alias,
        "remote_command": remote_command,
        "machine": entry.key,
        "display_name": f"{entry.display_name} {environment or ''}".strip(),
    }
