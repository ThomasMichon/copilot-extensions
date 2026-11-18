"""Preserve checkout-local history and unclassified ignored data during disposal."""

from __future__ import annotations

from pathlib import Path
import stat
import subprocess

from agent_procutil.owned_process import run_caller_owned
from plugin_resolve.git import resolve_git

from .git_repository_environment import repository_identity_env


def preservation_reason(checkout: str | Path) -> str | None:
    """Presence or uncertain inspection requires owner-led rescue, not deletion."""
    root = Path(checkout)
    try:
        info = root.lstat()
    except FileNotFoundError:
        return None
    except OSError:
        return "cannot inspect checkout data; retain the checkout until its owner reconciles preservation"
    if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_reparse_tag", False):
        return "checkout data is linked or non-directory; retain it for its owner"
    try:
        (root / ".effort-agenda").lstat()
    except FileNotFoundError:
        pass
    except OSError:
        return (
            "cannot inspect checkout-local .effort-agenda; retain the checkout "
            "until its agenda owner reconciles preservation"
        )
    else:
        return (
            "checkout-local .effort-agenda requires preservation; retain the checkout "
            "until its agenda owner verifies rescue and explicitly retires the source"
        )
    try:
        if next(root.iterdir(), None) is None:
            return None
    except OSError:
        return "cannot inspect checkout contents; retain them for their producer"
    try:
        metadata = (root / ".git").lstat()
    except OSError:
        return "nonempty checkout has no inspectable Git root; retain its data"
    if not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)) or getattr(metadata, "st_reparse_tag", False):
        return "checkout Git root is linked or non-ordinary; retain its data"
    try:
        ignored = run_caller_owned(
            [resolve_git(), "ls-files", "--others", "--ignored", "--exclude-standard",
             "--directory", "--no-empty-directory", "-z"],
            cwd=root, env=repository_identity_env(), timeout=5,
            max_stdout_bytes=65536, max_stderr_bytes=8192,
        )
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        return "cannot classify ignored checkout data; retain it for its producer"
    if ignored.returncode != 0 or not isinstance(ignored.stdout, str) or len(ignored.stdout) > 65536:
        return "ignored checkout data inspection is incomplete; retain it for its producer"
    if ignored.stdout:
        return (
            "checkout contains ignored data without completed producer disposition; "
            "retain it until its artifact owner preserves or explicitly reclaims that data"
        )
    return None
