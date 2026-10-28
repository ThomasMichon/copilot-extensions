"""Native prompt controls and one-shot Windows mux argument transport."""

from __future__ import annotations

import base64
import json
import re
import tempfile
import time
from pathlib import Path

from . import config, output
from ._installation_context_base import _is_link_or_junction

PANE_ARGS_MAX_AGE = 24 * 60 * 60


def prompt_controls(initial_prompt: str | None, prompt_receipt: str | None) -> list[str]:
    if initial_prompt is not None and not prompt_receipt:
        raise RuntimeError("initial prompt transport requires a receipt token")
    controls = []
    if prompt_receipt:
        receipt_encoded = base64.b64encode(prompt_receipt.encode("utf-8")).decode("ascii")
        controls += ["--aw-prompt-receipt-b64", receipt_encoded]
    if initial_prompt is not None:
        encoded = base64.b64encode(initial_prompt.encode("utf-8")).decode("ascii")
        controls = ["--aw-prompt-b64", encoded, *controls]
    return controls


def _indirect(path: Path) -> bool:
    return any(_is_link_or_junction(item) for item in (path, *path.parents))


def _launch_args_root(root: Path, tracking_root: Path | None = None) -> bool:
    try:
        parts = root.absolute().relative_to(
            (tracking_root if tracking_root is not None else config.tracking_dir()).absolute()
            / "launch-attempts",
        ).parts
    except (RuntimeError, ValueError):
        return False
    return (
        len(parts) == 3 and parts[2] == "pane-args"
        and re.fullmatch(r"[0-9a-f]{32}", parts[0]) is not None
        and re.fullmatch(r"[0-9a-f]{32}\.shell", parts[1]) is not None
    )


def _launch_args_roots(tracking_root: Path):
    attempts = tracking_root / "launch-attempts"
    if _indirect(attempts) or not attempts.is_dir():
        return
    for attempt in attempts.iterdir():
        if (
            re.fullmatch(r"[0-9a-f]{32}", attempt.name) is None
            or _indirect(attempt) or not attempt.is_dir()
        ):
            continue
        for shell in attempt.iterdir():
            root = shell / "pane-args"
            if _launch_args_root(root, tracking_root) and not _indirect(root) and root.is_dir():
                yield root


def _sweep_root(root: Path) -> None:
    if _indirect(root):
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


def sweep_mux_pane_args(tracking_roots: list[Path] | None = None) -> None:
    """Expire unconsumed handoffs after a full day of startup grace."""
    _sweep_root(config.install_dir() / "pane-args")
    if tracking_roots is None:
        try:
            tracking_roots = [config.tracking_dir()]
        except RuntimeError:
            tracking_roots = []
    for tracking_root in tracking_roots:
        try:
            for root in _launch_args_roots(tracking_root):
                _sweep_root(root)
        except OSError as exc:
            output.warn(f"Could not enumerate launch-owned pane argument handoffs: {exc}")


def sweep_registered_mux_pane_args() -> None:
    """Use the resident's registered-project inventory, never ambient context."""
    from .allocation_link_discovery import registered_projects

    roots = []
    try:
        projects = registered_projects()
    except (OSError, RuntimeError, ValueError) as exc:
        output.warn(f"Could not enumerate pane argument projects: {exc}")
        projects = set()
    for project in sorted(projects):
        try:
            roots.append(config.tracking_dir(project))
        except (OSError, RuntimeError, ValueError) as exc:
            output.warn(f"Could not resolve pane argument project {project}: {exc}")
    sweep_mux_pane_args(roots)


def file_mux_pane_cmd(wrapper: str, wrapper_args: list[str]) -> list[str]:
    launcher = Path(wrapper).with_name("pane-launch.ps1")
    if not launcher.is_file():
        raise RuntimeError("file-based pane launcher is missing; update Worktree Manager")
    root = Path(wrapper).parent / "pane-args"
    if not _launch_args_root(root):
        root = config.install_dir() / "pane-args"
    if _indirect(root):
        raise RuntimeError("pane argument directory must not be a link or junction")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    _sweep_root(root)
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
        root = path.parent
        launcher = Path(argv[-3][1:-1].replace("''", "'"))
        if (
            (
                root.absolute() == (config.install_dir() / "pane-args").absolute()
                or (_launch_args_root(root) and root.parent.absolute() == launcher.parent.absolute())
            )
            and path.name.startswith("aw-pane-")
            and path.name.endswith(".json")
            and not _indirect(root)
            and not _is_link_or_junction(path)
        ):
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                output.warn(f"Could not remove pane argument handoff; retained for expiry: {exc}")
