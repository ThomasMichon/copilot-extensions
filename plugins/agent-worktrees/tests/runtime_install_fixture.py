"""Account-home isolation for copied native installer fixtures."""

from __future__ import annotations

import os
import shlex
import shutil
import stat
from pathlib import Path


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


def isolate_account_profile(plugin: Path, home: Path) -> None:
    if os.name == "nt":
        return
    # HOME does not redirect passwd. Change only copied OS lookups before
    # construction freezes either the bootstrap or installed API graph.
    for directory in (
        plugin / "scripts" / "installation-context",
        plugin / "src" / "agent_worktrees",
    ):
        resolution = directory / "_installation_context_resolution.py"
        source = resolution.read_text(encoding="utf-8")
        lookup = "profile_value = Path(pwd.getpwuid(os.getuid()).pw_dir)"
        assert source.count(lookup) == 1
        resolution.write_text(source.replace(
            lookup, f"profile_value = Path({str(home)!r})",
        ), encoding="utf-8")
    shell_context = plugin / "scripts" / "installation-context" / "installation-context.sh"
    source = shell_context.read_text(encoding="utf-8")
    lookup = 'CURRENT_PROFILE_HOME="$(canonical_path "$home_path" true)"'
    assert source.count(lookup) == 1
    shell_context.write_text(source.replace(
        lookup,
        f'CURRENT_PROFILE_HOME="$(canonical_path {shlex.quote(str(home))} true)"',
    ), encoding="utf-8")
