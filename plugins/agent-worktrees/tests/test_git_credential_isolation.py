"""Real nested credential tests stay inside canonical disposable state."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

from agent_procutil import no_window_kwargs
from agent_worktrees.git_repository_environment import repository_identity_env
from plugin_resolve.git import resolve_git

pytestmark = [
    pytest.mark.portfolio_tier("T3"),
    pytest.mark.effect("filesystem"),
    pytest.mark.effect("process"),
]


@pytest.mark.timeout(90)
def test_nested_credential_consumers_preserve_external_sentinel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(source))
    from tools.plugin_test_containment import Limits, run_contained
    from tools.plugin_test_environment import isolated_environment

    root = tmp_path / "nested-isolation"
    root.mkdir()
    sentinel_repo = root / "external-sentinel"
    git = resolve_git()
    parent_env = repository_identity_env()
    subprocess.run(
        [git, "init", "-q", str(sentinel_repo)], env=parent_env, check=True,
        capture_output=True, timeout=10, **no_window_kwargs(),
    )
    subprocess.run(
        [git, "-C", str(sentinel_repo), "config", "--local",
         "credential.https://github.com.username", "sentinel-user"],
        env=parent_env, check=True, capture_output=True, timeout=10, **no_window_kwargs(),
    )
    sentinel = sentinel_repo / ".git" / "config"
    before = sentinel.read_bytes()
    base = repository_identity_env(dict(os.environ))
    base.update({
        "AGENT_WORKTREES_PROJECTS_YAML": str(sentinel),
        "AGENT_WORKTREES_REPOS_YAML": str(sentinel),
        "GIT_CONFIG_GLOBAL": str(root / "home" / ".gitconfig"),
        "GIT_CONFIG_SYSTEM": str(root / "git-system.config"),
        "GH_CONFIG_DIR": str(root / "gh-config"),
    })
    env = isolated_environment(base, root, allow_explicit_tiers=True)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env.pop("PYTEST_ADDOPTS", None)
    env.pop("PYTEST_PLUGINS", None)
    env["PYTHONPATH"] = os.pathsep.join([str(source / "tools"), *filter(None, sys.path)])
    target = Path(__file__).with_name("test_git_ops.py")
    nested = sentinel_repo / "nested-pytest"
    report = root / "results.xml"
    nodes = [
        f"{target}::TestPinGitCredential::test_noop_when_not_a_git_working_tree",
        f"{target}::TestPinGitCredential::test_pins_local_config_on_a_real_repo",
    ]
    code = run_contained(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "-p", "pytest_portfolio_guard", "--basetemp", str(nested),
         "--junitxml", str(report), "-o", "junit_family=xunit1", *nodes],
        cwd=root, env=env, sandbox=root,
        limits=Limits(wall_seconds=60, max_processes=24, max_memory_mb=2048, max_temp_mb=256),
    )
    assert sentinel.read_bytes() == before
    assert code == 0
    cases = ET.parse(report).getroot().findall(".//testcase")
    assert len(cases) == 2
    assert all(case.find("failure") is None and case.find("error") is None for case in cases)
    observed = {
        prop.attrib["name"]: prop.attrib["value"]
        for case in cases for prop in case.findall("./properties/property")
    }
    for name in (
        "HOME", "USERPROFILE", "AGENT_HOME", "COPILOT_HOME",
        "AGENT_WORKTREES_PROJECTS_YAML", "AGENT_WORKTREES_REPOS_YAML",
        "GIT_CONFIG_SYSTEM", "GH_CONFIG_DIR",
    ):
        assert observed[name]
        assert Path(observed[name]).resolve().is_relative_to(root.resolve())
    assert observed["GIT_CONFIG_GLOBAL"] == os.devnull or (
        Path(observed["GIT_CONFIG_GLOBAL"]).resolve().is_relative_to(root.resolve())
    )
    configs = list(nested.glob("test_pins_local_config_on_a*/repo/.git/config"))
    assert len(configs) == 1
    username = subprocess.run(
        [git, "config", "--file", str(configs[0]), "--get",
         "credential.https://github.com.username"],
        env=parent_env, check=True, capture_output=True, text=True, timeout=10,
        **no_window_kwargs(),
    )
    assert username.stdout.strip() == "operator_enterprise"
