"""Cold recovery derives its tool environment from the fresh source owners."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

REPOSITORY = Path(__file__).resolve().parents[2]
pytestmark = [
    pytest.mark.portfolio_tier("T1"),
    pytest.mark.effect("filesystem"),
    pytest.mark.contract("manager.source_recovery_tool_dependencies"),
]


@pytest.fixture
def recovery(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(REPOSITORY / "worktree-manager" / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "recovery_dependency_subject", REPOSITORY / "tools" / "recover_source.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_tool_requirements_keep_external_constraints_and_markers(
    recovery: ModuleType, tmp_path: Path,
) -> None:
    for relative, name, requirements, source in (
        ("worktree-manager", "fixture-manager",
         ["helper_pkg>=1", "packaging>=25", "tomli>=2 ; python_version < '3.11'"],
         "../libs/helper"),
        ("plugins/agent-worktrees", "agent-worktrees",
         ["helper_pkg", "pyyaml>=6"], "../../libs/helper"),
        ("libs/helper", "helper-pkg", ["packaging>=24", "pyyaml>=6.0.3"], None),
    ):
        project = tmp_path / relative
        project.mkdir(parents=True)
        (project / "pyproject.toml").write_text(
            "[project]\nname=" + json.dumps(name) + "\nversion='1.0.0'\ndependencies="
            + json.dumps(requirements) + "\n"
            + ("[tool.uv.sources]\nhelper_pkg={path=" + json.dumps(source) + "}\n" if source else ""),
            encoding="utf-8",
        )
    assert set(recovery.tool_requirements(tmp_path)) == {
        "packaging>=25", "packaging>=24", "pyyaml>=6", "pyyaml>=6.0.3",
        "tomli>=2 ; python_version < '3.11'",
    }


def test_host_uv_allows_home_tools_but_not_current_or_toolkit_directories(
    recovery: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    toolkit = home / "toolkit"
    tools = home / ".local" / "bin"
    for directory in (home, toolkit, tools):
        directory.mkdir(parents=True, exist_ok=True)
    executable = "uv.exe" if recovery.os.name == "nt" else "uv"
    for directory in (home, toolkit, tools):
        candidate = directory / executable
        candidate.write_bytes(b"fixture")
        candidate.chmod(0o700)
    monkeypatch.chdir(home)
    monkeypatch.setenv("PATH", recovery.os.pathsep.join(map(str, (home, toolkit, tools))))
    assert recovery.host_uv(toolkit, "fixture") == str(tools / executable)
