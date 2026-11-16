"""Publish Manager-owned mux presentation through the selected transport."""

from __future__ import annotations

from collections.abc import Callable
import subprocess
from typing import Any

from agent_procutil import no_window_kwargs

from . import mux_discovery


def apply_status_options(
    entry: dict[str, Any], values: dict[str, str], *,
    run: Callable[..., subprocess.CompletedProcess[str]],
) -> bool:
    """Require native PSMux readback; retain the bounded legacy tmux writer."""
    mux_bin = entry["mux_bin"]
    if not mux_discovery.passive_cli_supported(mux_bin):
        from agent_remote_driver.mux_status import apply_psmux_status

        return apply_psmux_status(
            entry["mux_session"], entry.get("session_incarnation", ""),
            mux_discovery.initial_status_values(entry, values),
        )
    session = entry["mux_session"]
    values = mux_discovery.initial_status_values(entry, values)
    all_ok = True
    for option, value in values.items():
        try:
            result = run(
                [mux_bin, "set-option", "-t", session, str(option), str(value)],
                capture_output=True, text=True, timeout=15, **no_window_kwargs(),
            )
            if result.returncode != 0:
                all_ok = False
        except Exception:
            all_ok = False
    return all_ok
