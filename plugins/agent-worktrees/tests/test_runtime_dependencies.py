"""Keep the CLI independent of the Manager's UI dependency stack."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from agent_procutil import no_window_kwargs
from packaging.requirements import Requirement

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib


PLUGIN_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.guard
def test_runtime_dependencies_do_not_install_ui_stack() -> None:
    project = tomllib.loads((PLUGIN_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    names = {Requirement(value).name.lower() for value in project["project"]["dependencies"]}
    assert names.isdisjoint({"textual", "rich", "pygments", "markdown-it-py"})
    assert "packaging" in names


@pytest.mark.guard
def test_cli_version_guard_and_manager_handoff_work_without_ui_imports() -> None:
    code = """
import importlib.abc
import sys

class NoUiImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in {"textual", "rich", "pygments", "markdown_it"}:
            raise ImportError("UI dependency is unavailable: " + fullname)

sys.meta_path.insert(0, NoUiImports())
sys.path.insert(0, sys.argv[1])
from agent_worktrees import reconcile
assert reconcile._version_lt("0.4.0-dev260", "0.4.0.dev262")
assert not reconcile._version_lt("0.4.0-dev262", "0.4.0.dev260")
assert reconcile._versions_equal("0.4.0-dev262", "0.4.0.dev262")
from agent_worktrees.__main__ import main
try:
    result = main(["--help"])
except SystemExit as exc:
    result = exc.code
assert result == 0, result
from agent_worktrees import manager_launch_cli
manager_code = (
    "import sys; "
    "assert sys.argv[1:] == ['--project', 'example']; "
    "print('manager process handoff'); "
    "sys.exit(7)"
)
raise SystemExit(manager_launch_cli._exec_worktree_manager(
    [sys.executable, "-I", "-c", manager_code], "example"
))
"""
    result = subprocess.run(
        [sys.executable, "-I", "-X", "utf8", "-c", code, str(PLUGIN_ROOT / "src")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
        check=False,
        **no_window_kwargs(),
    )
    assert result.returncode == 7, result.stdout + result.stderr
    assert "usage:" in result.stdout
    assert "manager process handoff" in result.stdout
