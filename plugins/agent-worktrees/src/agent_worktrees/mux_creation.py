"""Create mux clients without lending a Windows worktree to idle standbys."""

from pathlib import Path
import platform
import subprocess

from agent_procutil import no_window_kwargs


def pane_work_dir(work_dir: str) -> str:
    """Preserve caller-relative pane locations before moving the mux client."""
    if platform.system() == "Windows":
        if not work_dir:
            return str(Path.cwd())
        if not Path(work_dir).is_absolute():
            return str(Path(work_dir).absolute())
    return work_dir


def run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    """Keep the client launch directory separate from the pane's explicit CWD."""
    return subprocess.run(
        argv, capture_output=True, text=True, timeout=15,
        cwd=str(Path.home()) if platform.system() == "Windows" else None,
        **no_window_kwargs(),
    )
