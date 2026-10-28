"""Prepare immutable pane assets in the existing launch attempt's state."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat
from uuid import uuid4

from . import launch_attempt, tracking

NATIVE_CONSOLE_MARKER = "# worktree-manager:native-console-owner-v1"


@dataclass(frozen=True)
class SessionShell:
    directory: Path
    wrapper: Path

    def as_dict(self) -> dict[str, str]:
        return {"directory": str(self.directory), "wrapper": str(self.wrapper)}


def _plain(path: Path, *, directory: bool = False) -> None:
    for candidate in (path, *path.parents):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if (
            stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise ValueError("Session shell paths must not traverse links or junctions")
    info = path.stat()
    if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
        raise ValueError("Invalid session shell asset")
    if not directory and info.st_nlink != 1:
        raise ValueError("Session shell assets must not be hard-linked")


def prepare(
    record_path: Path,
    wrapper: Path,
    *,
    installation_roots: tuple[Path, ...],
    attempt_id: str | None = None,
) -> SessionShell:
    """Copy shells, not runtimes, outside every caller-declared removal root.

    This creates assets only. Admission, process custody and completion remain
    with the existing launch and provider owners; file creation proves none of
    those outcomes.
    """
    if not record_path.is_absolute() or not wrapper.is_absolute() or not installation_roots:
        raise ValueError("Session shell preparation requires explicit absolute paths")
    _plain(record_path)
    record = tracking.load_record(record_path)
    if record.yaml_path != record_path:
        raise ValueError("Session shell record identity changed")
    names = {
        "pane-wrapper.ps1": ("pane-wrapper.ps1", "pane-launch.ps1"),
        "pane-wrapper.sh": ("pane-wrapper.sh",),
        "default-setup.ps1": (
            "default-setup.ps1", "launch-command.ps1",
            "native-seed-boundary.ps1", "reconcile-machine-settings.ps1",
        ),
        "default-setup.sh": (
            "default-setup.sh", "launch-command.sh",
            "native-seed-reply.sh", "native-seed-boundary.sh", "reconcile-machine-settings.sh",
        ),
    }.get(wrapper.name)
    if names is None:
        raise ValueError("Unsupported session shell asset")
    sources = [wrapper.with_name(name) for name in names]
    for source in sources:
        _plain(source)
    if wrapper.name == "pane-wrapper.ps1" and NATIVE_CONSOLE_MARKER in wrapper.read_text(
        encoding="utf-8-sig",
    ).splitlines():
        from . import installer

        helper = installer.install_dir() / "scripts" / "native-seed-boundary.ps1"
        if not helper.is_file():
            helper = Path(__file__).resolve().parents[2] / "scripts" / helper.name
        _plain(helper)
        sources.append(helper)
    directory = launch_attempt._path(
        record_path.parent, attempt_id or uuid4().hex, uuid4().hex,
    ).with_suffix(".shell")
    for root in installation_roots:
        if not root.is_absolute():
            raise ValueError("Session shell removal roots must be absolute")
        if directory.resolve().is_relative_to(root.resolve()):
            raise ValueError("Session shell storage belongs to a removable installation")
    launch_attempt._mkdir(directory.parent)
    directory.mkdir(mode=0o700)
    copied = []
    try:
        for source in sources:
            target = directory / source.name
            with target.open("xb") as stream:
                copied.append(target)
                os.chmod(target, 0o600)
                stream.write(source.read_bytes())
                stream.flush()
                os.fsync(stream.fileno())
        launch_attempt._sync_directory(directory)
    except BaseException:
        for target in copied:
            target.unlink(missing_ok=True)
        directory.rmdir()
        raise
    return SessionShell(directory, directory / wrapper.name)


def prepare_for_mux(
    worktree_id: str, wrapper: str, *, is_tmux: bool,
    mux_bin: str | None, session_name: str | None,
) -> tuple[str, list[str]]:
    from . import config as cfg

    try:
        record_path = cfg.tracking_dir() / f"{worktree_id}.yaml"
    except RuntimeError:
        return wrapper, []
    if not record_path.is_file():
        return wrapper, []
    try:
        if "# worktree-manager:pane-boundary-v1" not in Path(wrapper).read_text(
            encoding="utf-8-sig",
        ).splitlines():
            return wrapper, []
        from .manager_launch_cli import _worktree_manager_root
        from .sessions import mux_session_name
        from .sessions_pane_retire import _mux_bin

        manager_root = _worktree_manager_root().absolute()
        prepared = prepare(
            record_path.absolute(), Path(wrapper).absolute(),
            installation_roots=(cfg.install_dir(), manager_root),
        )
        selected_mux = mux_bin or _mux_bin(None)
        selected_session = session_name or mux_session_name(worktree_id)
        if is_tmux:
            controls = [
                "--aw-manager-root", str(manager_root), "--aw-project", cfg.project_name(),
                "--aw-mux-session", selected_session, "--aw-mux-bin", selected_mux,
            ]
        else:
            controls = [
                "-AwManagerRoot", str(manager_root), "-AwProject", cfg.project_name(),
                "-AwMuxSession", selected_session, "-AwPsmuxBin", selected_mux,
            ]
        return str(prepared.wrapper), controls
    except (OSError, ValueError) as error:
        raise RuntimeError("Could not prepare launch-owned pane assets") from error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", type=Path, required=True)
    parser.add_argument("--wrapper", type=Path, required=True)
    parser.add_argument("--installation-root", type=Path, action="append", required=True)
    parser.add_argument("--attempt-id")
    args = parser.parse_args()
    try:
        prepared = prepare(
            args.record, args.wrapper, installation_roots=tuple(args.installation_root),
            attempt_id=args.attempt_id,
        )
    except (OSError, ValueError) as error:
        parser.exit(3, f"Could not prepare session shell: {error}\n")
    print(json.dumps(prepared.as_dict()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
