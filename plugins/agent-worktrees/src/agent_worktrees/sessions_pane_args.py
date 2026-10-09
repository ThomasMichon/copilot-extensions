"""One-shot file transport for Windows mux pane arguments."""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

from . import config, output
from ._installation_context_base import _is_link_or_junction

PANE_ARGS_MAX_AGE = 24 * 60 * 60


def sweep_mux_pane_args() -> None:
    """Expire unconsumed handoffs after a full day of startup grace."""
    root = config.install_dir() / "pane-args"
    if _is_link_or_junction(root):
        output.warn("Refusing pane argument cleanup through a link or junction")
        return
    if not root.exists():
        return
    cutoff = time.time() - PANE_ARGS_MAX_AGE
    try:
        for path in root.glob("aw-pane-*.json"):
            try:
                if not _is_link_or_junction(path) and path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
            except FileNotFoundError:
                pass  # The consumer may have deleted it between inspection and stat.
    except OSError as exc:
        output.warn(f"Could not expire pane argument handoffs: {exc}")


def file_mux_pane_cmd(wrapper: str, wrapper_args: list[str]) -> list[str]:
    launcher = Path(wrapper).with_name("pane-launch.ps1")
    if not launcher.is_file():
        raise RuntimeError("file-based pane launcher is missing; update Worktree Manager")
    root = config.install_dir() / "pane-args"
    if _is_link_or_junction(root):
        raise RuntimeError("pane argument directory must not be a link or junction")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    sweep_mux_pane_args()
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", prefix="aw-pane-", suffix=".json",
        delete=False, dir=root,
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
        and Path(argv[-3][1:-1].replace("''", "'")).name == "pane-launch.ps1"
    ):
        path = Path(argv[-1][1:-1].replace("''", "'"))
        root = config.install_dir() / "pane-args"
        if (
            path.parent.absolute() == root.absolute()
            and path.name.startswith("aw-pane-")
            and path.name.endswith(".json")
            and not _is_link_or_junction(root)
            and not _is_link_or_junction(path)
        ):
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                output.warn(f"Could not remove pane argument handoff; retained for expiry: {exc}")
