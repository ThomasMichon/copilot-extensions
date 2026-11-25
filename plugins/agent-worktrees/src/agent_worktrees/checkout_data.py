"""Preserve checkout-local history and submodule data during disposal.

Ordinary Git-ignored content (build output, caches, virtual environments) is
disposable with its checkout and never holds removal on its own. A checkout
whose Git root is already gone may still be removed when only recognized test
venvs and caches remain (see :mod:`venv_reclaim`); explicitly
preserved homes such as ``.effort-agenda`` are protected whether or not Git
ignores them.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath
import stat
import subprocess
import time

from agent_procutil.owned_process import run_caller_owned
from plugin_resolve.git import resolve_git

from .git_repository_environment import repository_identity_env


def preservation_hint(checkout: str | Path) -> str | None:
    """Cheap known-history hint for advisory list/status projections."""
    try:
        (Path(checkout) / ".effort-agenda").lstat()
    except FileNotFoundError:
        return None
    except OSError:
        return (
            "cannot inspect checkout-local .effort-agenda; retain the checkout "
            "until its agenda owner reconciles preservation"
        )
    return (
        "checkout-local .effort-agenda requires preservation; retain the checkout "
        "until its agenda owner verifies rescue and explicitly retires the source"
    )


def _git_observation(
    root: Path, git: str, arguments: list[str], environment: dict[str, str],
    deadline: float, maximum: int,
) -> str:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("checkout data inspection exhausted its budget")
    result = run_caller_owned(
        [git, "--literal-pathspecs", *arguments], cwd=root, env=environment,
        timeout=remaining, max_stdout_bytes=maximum, max_stderr_bytes=8192,
    )
    text = result.stdout
    if result.returncode != 0 or not isinstance(text, str) or len(text) > maximum:
        raise ValueError("incomplete checkout data observation")
    return text


def _submodule_reason(root: Path, name: str) -> str | None:
    relative = PurePosixPath(name)
    if (
        not relative.parts or relative.is_absolute() or PureWindowsPath(name).drive
        or "\\" in name or any(ord(char) < 32 for char in name)
        or any(part in {"..", ".git"} for part in relative.parts)
    ):
        return "tracked submodule path cannot be safely inspected; retain the checkout"
    current = root
    try:
        for part in relative.parts:
            current /= part
            info = current.lstat()
            if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_reparse_tag", False):
                return "tracked submodule data is linked or non-directory; retain the checkout"
        if next(current.iterdir(), None) is not None:
            return (
                "initialized or nonempty submodule requires owner-led data preservation "
                "and deinitialization before checkout removal"
            )
    except FileNotFoundError:
        return None
    except OSError:
        return "cannot inspect tracked submodule data; retain the checkout"
    return None


def preservation_reason(checkout: str | Path) -> str | None:
    """Fresh, bounded disposal guard; never invoke it from presentation."""
    root = Path(checkout)
    if reason := preservation_hint(root):
        return reason
    try:
        info = root.lstat()
    except FileNotFoundError:
        return None
    except OSError:
        return "cannot inspect checkout data; retain the checkout until its owner reconciles preservation"
    if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_reparse_tag", False):
        return "checkout data is linked or non-directory; retain it for its owner"
    try:
        if next(root.iterdir(), None) is None:
            return None
    except OSError:
        return "cannot inspect checkout contents; retain them for their producer"
    try:
        metadata = (root / ".git").lstat()
    except OSError:
        from .venv_reclaim import only_disposable_content

        # A partial removal can leave only rebuildable test venvs and caches.
        if only_disposable_content(root):
            return None
        return "nonempty checkout has no inspectable Git root; retain its data"
    if not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)) or getattr(metadata, "st_reparse_tag", False):
        return "checkout Git root is linked or non-ordinary; retain its data"
    try:
        git = resolve_git()
        environment = repository_identity_env()
        environment["GIT_OPTIONAL_LOCKS"] = "0"
        environment["GIT_NO_LAZY_FETCH"] = "1"
        deadline = time.monotonic() + 5
        index = _git_observation(
            root, git, ["ls-files", "--sparse", "--format=%(objectmode)%x09%(path)", "-z"],
            environment, deadline, 32 * 1024 * 1024,
        )
        if index and not index.endswith("\0"):
            raise ValueError("incomplete checkout index observation")
        submodules = 0
        for entry in index.split("\0"):
            if not entry:
                continue
            if time.monotonic() >= deadline:
                raise TimeoutError("checkout index inspection exhausted its budget")
            mode, separator, name = entry.partition("\t")
            if not separator or mode not in {"100644", "100755", "120000", "160000", "040000"}:
                raise ValueError("unrecognized checkout index entry")
            if mode == "040000":
                return "sparse index contains uninspected data; retain the checkout"
            if mode == "160000":
                submodules += 1
                if submodules > 256:
                    return "tracked submodule inspection exceeds its bound; retain the checkout"
                if reason := _submodule_reason(root, name):
                    return reason
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        return "cannot inspect the checkout index; retain the checkout until its owner reconciles preservation"
    return None
