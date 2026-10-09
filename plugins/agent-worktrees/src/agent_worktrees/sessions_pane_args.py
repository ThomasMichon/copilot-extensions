"""One-shot file transport for Windows mux pane arguments."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path


def file_mux_pane_cmd(wrapper: str, wrapper_args: list[str]) -> list[str]:
    launcher = Path(wrapper).with_name("pane-launch.ps1")
    if not launcher.is_file():
        raise RuntimeError("file-based pane launcher is missing; update Worktree Manager")
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", prefix="aw-pane-", suffix=".json",
        delete=False,
    ) as handoff:
        try:
            json.dump({"version": 1, "wrapper": wrapper, "argv": wrapper_args}, handoff)
        except (OSError, TypeError, ValueError):
            handoff.close()
            Path(handoff.name).unlink(missing_ok=True)
            raise
    return [
        "pwsh.exe", "-NoProfile", "-NoLogo", "-File",
        "'" + str(launcher).replace("'", "''") + "'", "-Manifest",
        "'" + handoff.name.replace("'", "''") + "'",
    ]


def cleanup_mux_pane_args(argv: list[str]) -> None:
    """Remove a rejected launch's unconsumed handoff."""
    if (
        argv[-7:-3] == ["pwsh.exe", "-NoProfile", "-NoLogo", "-File"]
        and argv[-2] == "-Manifest"
        and argv[-3][1:-1].replace("''", "'").endswith("pane-launch.ps1")
    ):
        Path(argv[-1][1:-1].replace("''", "'")).unlink(missing_ok=True)
