"""Account-home isolation for copied native installer fixtures."""

from __future__ import annotations

import os
import shlex
import shutil
import stat
import subprocess
from pathlib import Path


def run_native_installer(argv: list[str], *, env: dict[str, str], timeout: float):
    from agent_procutil.owned_process import run_caller_owned

    try:
        result = run_caller_owned(
            argv, env=env, timeout=timeout, binary=True,
            max_stdout_bytes=8 << 20, max_stderr_bytes=8 << 20,
        )
    except subprocess.TimeoutExpired as error:
        for field in ("output", "stderr"):
            value = getattr(error, field)
            if isinstance(value, bytes):
                setattr(error, field, value.decode("utf-8", errors="backslashreplace"))
        raise
    return subprocess.CompletedProcess(
        result.args, result.returncode,
        (result.stdout or b"").decode("utf-8", errors="backslashreplace"),
        (result.stderr or b"").decode("utf-8", errors="backslashreplace"),
    )


def remove_fixture_worktree(owner: Path) -> None:
    def clear_git_readonly(function, path, error):
        if (
            os.name != "nt"
            or not isinstance(error[1], PermissionError)
            or not Path(path).is_relative_to(owner / ".git")
        ):
            raise error[1]
        os.chmod(path, stat.S_IWRITE)
        function(path)

    shutil.rmtree(owner, onerror=clear_git_readonly)


def isolate_account_profile(
    plugin: Path, home: Path, *, runtime_package: str | None = "agent_worktrees",
) -> None:
    if os.name == "nt":
        return
    # HOME does not redirect passwd. Change only copied OS lookups before
    # construction freezes either the bootstrap or installed API graph.
    directories = [plugin / "scripts" / "installation-context"]
    if runtime_package is not None:
        directories.append(plugin / "src" / runtime_package)
    _isolate_profile_directories(
        directories, plugin / "scripts" / "installation-context" / "installation-context.sh", home,
    )


def isolate_source_account_profile(repository: Path, home: Path) -> None:
    """Isolate canonical inputs before promotion materializes their consumers."""
    if os.name == "nt":
        return
    canonical = repository / "libs" / "installation-context"
    _isolate_profile_directories([canonical], canonical / "installation-context.sh", home)
    for bootstrap in sorted(repository.glob("plugins/*/scripts/installation-context")):
        plugin = bootstrap.parents[1]
        isolate_account_profile(
            plugin, home,
            runtime_package="agent_worktrees" if plugin.name == "agent-worktrees" else None,
        )


def _isolate_profile_directories(
    directories: list[Path], shell_context: Path, home: Path,
) -> None:
    for directory in directories:
        resolution = directory / "_installation_context_resolution.py"
        source = resolution.read_text(encoding="utf-8")
        lookup = "profile_value = Path(get_account(get_uid()).pw_dir)"
        assert source.count(lookup) == 1
        resolution.write_text(source.replace(
            lookup, f"profile_value = Path({str(home)!r})",
        ), encoding="utf-8")
    source = shell_context.read_text(encoding="utf-8")
    lookup = 'canonical_path_into CURRENT_PROFILE_HOME "$home_path" true || return'
    assert source.count(lookup) == 1
    shell_context.write_text(source.replace(
        lookup,
        f'canonical_path_into CURRENT_PROFILE_HOME {shlex.quote(str(home))} true || return',
    ), encoding="utf-8")
