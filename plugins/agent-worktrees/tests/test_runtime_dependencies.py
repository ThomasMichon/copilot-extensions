"""Keep the CLI independent of the Manager's UI dependency stack."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from agent_procutil import no_window_kwargs
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
UI_PACKAGES = {
    "textual": "textual",
    "rich": "rich",
    "pygments": "pygments",
    "markdown-it-py": "markdown_it",
}


@pytest.mark.guard
def test_runtime_dependencies_do_not_install_ui_stack() -> None:
    project = tomllib.loads((PLUGIN_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    names = {
        canonicalize_name(Requirement(value).name)
        for value in project["project"]["dependencies"]
    }
    assert names.isdisjoint(UI_PACKAGES)
    assert "packaging" in names


@pytest.mark.guard
@pytest.mark.parametrize(
    "ui_requirement",
    ["Textual", "RICH", "Pygments", "markdown_it_py", "Markdown.IT.Py", "markdown--it--py"],
)
def test_dependency_guard_rejects_equivalent_ui_names(tmp_path, monkeypatch, ui_requirement):
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\ndependencies = ["packaging>=23", "{ui_requirement}"]\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(sys.modules[__name__], "PLUGIN_ROOT", tmp_path)
    with pytest.raises(AssertionError):
        test_runtime_dependencies_do_not_install_ui_stack()


@pytest.mark.guard
def test_cli_version_guard_and_manager_handoff_work_without_ui_imports() -> None:
    code = """
import importlib.abc
import json
import sys

ui_roots = set(json.loads(sys.argv[2]))
attempted_ui_imports = []

class NoUiImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in ui_roots:
            attempted_ui_imports.append(fullname)
            print("UI import attempted: " + fullname, file=sys.stderr, flush=True)
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
sys.stdout.flush()
sys.stderr.flush()
from agent_worktrees import manager_launch_cli
assert not attempted_ui_imports, attempted_ui_imports
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
        [
            sys.executable, "-I", "-X", "utf8", "-c", code,
            str(PLUGIN_ROOT / "src"), json.dumps(list(UI_PACKAGES.values())),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=25,
        check=False,
        **no_window_kwargs(),
    )
    assert result.returncode == 7, result.stdout + result.stderr
    assert "UI import attempted:" not in result.stderr
    assert "usage:" in result.stdout
    assert "manager process handoff" in result.stdout
