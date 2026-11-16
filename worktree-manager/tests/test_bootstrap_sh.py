"""Dependency-free bootstrap progress does not decorate the launched command."""
from __future__ import annotations

import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess

import pytest

from test_bootstrap_ps1 import _fake_tools, _retained_sparse, _source_repo

BOOTSTRAP = Path(__file__).resolve().parents[1] / "bootstrap.sh"
BASH = shutil.which("bash") or ""
pytestmark = pytest.mark.skipif(
    os.name == "nt" or not BASH or shutil.which("git") is None,
    reason="native POSIX bash and git required",
)


@pytest.mark.parametrize(
    ("python_exit", "incomplete_checkout", "retained_sparse"),
    [(0, False, False), (17, False, False), (0, True, False), (0, False, True)],
)
def test_bootstrap_progress_keeps_command_exit_and_stdout(
    tmp_path: Path, python_exit: int, incomplete_checkout: bool, retained_sparse: bool,
) -> None:
    repo = _source_repo(tmp_path)
    root = tmp_path / "manager-root"
    root.mkdir()
    if retained_sparse:
        _retained_sparse(repo, root)
    (root / "config.toml").write_text(
        f'[source]\nrepo = "{repo.as_posix()}"\nref = "main"\n', encoding="utf-8",
    )
    tools = tmp_path / "tools"
    _fake_tools(tools, uv_exit=0, python_exit=python_exit)
    if incomplete_checkout:
        real_git = shutil.which("git")
        assert real_git is not None
        missing = root / "staging" / "worktree-manager" / "README.md"
        adapter = tools / "git"
        adapter.write_text(
            f"#!/bin/sh\n{shlex.quote(real_git)} \"$@\"\ncode=$?\n"
            'if [ "$code" -eq 0 ]; then\ncase " $* " in\n'
            f'  *" checkout "*) rm -- {shlex.quote(str(missing))} ;;\n'
            'esac\nfi\nexit "$code"\n',
            encoding="utf-8",
        )
        adapter.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    environment = {
        **os.environ, "HOME": str(home), "USERPROFILE": str(home),
        "WORKTREE_MANAGER_ROOT": str(root),
        "PATH": str(tools) + os.pathsep + os.environ.get("PATH", ""),
    }
    result = subprocess.run(
        [BASH, str(BOOTSTRAP), "--version"],
        env=environment, capture_output=True, text=True, timeout=20,
    )
    if incomplete_checkout:
        assert result.returncode != 0
        assert "source checkout is incomplete or changed" in result.stderr
        assert "refusing Manager construction" in result.stderr
        assert not (root / "current-version").exists()
        return
    assert result.returncode == python_exit, result.stdout + result.stderr
    if retained_sparse:
        from worktree_manager.core_install import install_script

        assert install_script(root / "staging") == (
            root / "staging" / "plugins" / "agent-worktrees" / "scripts" / "install.sh"
        )
    assert result.stdout == ""
    progress = [
        line for line in result.stderr.splitlines()
        if "update/bootstrap " in line
    ]
    assert len(progress) == 2, result.stderr
    assert " running Worktree Manager bootstrap " in progress[0]
    assert " warning add " in progress[1]
    for line in progress:
        assert re.fullmatch(
            r"\[\d{2}:\d{2}:\d{2}Z\] update/bootstrap "
            r"(?:running|warning) .+ \[elapsed unavailable\]", line,
        ), line


def test_bootstrap_invalid_repair_is_an_explicit_failed_diagnostic(tmp_path: Path) -> None:
    result = subprocess.run(
        [BASH, str(BOOTSTRAP), "--repair-update"],
        env={**os.environ, "WORKTREE_MANAGER_ROOT": str(tmp_path / "manager-root")},
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert re.fullmatch(
        r"\[\d{2}:\d{2}:\d{2}Z\] update/bootstrap failed "
        r"incomplete update bootstrap source\. \[elapsed unavailable\]\n",
        result.stderr,
    ), result.stderr


@pytest.mark.parametrize(("missing", "relative_uv", "uv_version"), [
    (False, True, "0.13.7"), (True, False, "0.13.7"), (True, False, "0.7.0"),
])
def test_bootstrap_uses_managed_python_without_publishing_ambient_commands(
    tmp_path: Path, missing: bool, relative_uv: bool, uv_version: str,
) -> None:
    repo = _source_repo(tmp_path)
    root = tmp_path / "manager-root"
    root.mkdir()
    (root / "config.toml").write_text(
        f'[source]\nrepo = "{repo.as_posix()}"\nref = "main"\n', encoding="utf-8",
    )
    tools = tmp_path / "tools"
    _fake_tools(tools, uv_exit=0, python_exit=0, uv_version=uv_version)
    home = tmp_path / "profile"
    if uv_version == "0.7.0":
        _fake_tools(home / ".local" / "bin", uv_exit=0, python_exit=0)
        curl = tools / "curl"
        curl.write_text(
            "#!/bin/sh\n"
            '[ "$2" = https://astral.sh/uv/install.sh ] || exit 1\n'
            f"printf '%s\\n' {shlex.quote('touch ' + shlex.quote(str(tmp_path / 'uv-prepared')))}\n",
            encoding="utf-8",
        )
        curl.chmod(0o755)
    trace = tmp_path / "uv-trace.txt"
    result = subprocess.run(
        [BASH, str(BOOTSTRAP), "--version"],
        env={
            **os.environ, "WORKTREE_MANAGER_ROOT": str(root),
            "HOME": str(home), "USERPROFILE": str(home),
            "PATH": ("tools" if relative_uv else str(tools)) + os.pathsep + os.environ.get("PATH", ""),
            "VIRTUAL_ENV": str(tmp_path / "active-venv"),
            "UV_PYTHON": str(tmp_path / "ambient-python"),
            "BOOTSTRAP_UV_TRACE": str(trace),
            "BOOTSTRAP_UV_MISSING": "1" if missing else "0",
        },
        cwd=tmp_path, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    lookup = "python find --no-project --system --managed-python --no-python-downloads 3.11"
    expected = [lookup, "python install --no-bin --no-registry 3.11", lookup] if missing else [lookup]
    assert trace.read_text(encoding="utf-8").splitlines() == expected
    assert result.stdout == ""
    if uv_version == "0.7.0":
        assert (tmp_path / "uv-prepared").exists()


def test_system_python_mode_is_explicitly_unsupported_before_posix_effects(tmp_path: Path) -> None:
    root = tmp_path / "manager-root"
    result = subprocess.run(
        [BASH, str(BOOTSTRAP), "--system-python", "--version"],
        env={**os.environ, "WORKTREE_MANAGER_ROOT": str(root)},
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 2
    assert "Windows WinGet bootstrap mode; unsupported on POSIX" in result.stderr
    assert not root.exists()


def test_bootstrap_progress_frames_each_physical_message_line() -> None:
    header = BOOTSTRAP.read_text(encoding="utf-8").split("\nROOT=", 1)[0]
    result = subprocess.run(
        [BASH, "-c", header + '\n_bootstrap_progress "$1" warning', "--", "first\nsecond"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert re.fullmatch(
        r"\[\d{2}:\d{2}:\d{2}Z\] update/bootstrap warning first \[elapsed unavailable\]\n"
        r"\[\d{2}:\d{2}:\d{2}Z\] update/bootstrap warning second \[elapsed unavailable\]\n",
        result.stderr,
    ), result.stderr
