"""Legacy home selection must never turn an empty override into cwd."""

from pathlib import Path

import pytest

from agent_mcp.sockio import default_home_dir


@pytest.mark.parametrize("raw", [None, ""])
def test_missing_or_empty_home_uses_legacy_root(tmp_path, monkeypatch, raw):
    profile = tmp_path / "profile"
    launch = tmp_path / "launch"
    launch.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: profile))
    monkeypatch.chdir(launch)
    if raw is None:
        monkeypatch.delenv("AGENT_MCP_HOME", raising=False)
    else:
        monkeypatch.setenv("AGENT_MCP_HOME", raw)

    assert default_home_dir() == profile / ".agent-mcp"
    assert list(launch.iterdir()) == []
    assert not profile.exists(), "selection must not create directories"


@pytest.mark.parametrize("raw", ["state", "state with spaces", " "])
def test_relative_home_is_absolute_without_changing_its_meaning(
    tmp_path,
    monkeypatch,
    raw,
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AGENT_MCP_HOME", raw)

    assert default_home_dir() == tmp_path / raw
    assert list(tmp_path.iterdir()) == []


def test_absolute_home_is_preserved(tmp_path, monkeypatch):
    home = tmp_path / "selected"
    monkeypatch.setenv("AGENT_MCP_HOME", str(home))

    assert default_home_dir() == home
    assert not home.exists()


@pytest.mark.parametrize("absolute", [False, True], ids=["relative", "absolute"])
def test_home_keeps_parent_components_for_filesystem_resolution(
    tmp_path,
    monkeypatch,
    absolute,
):
    monkeypatch.chdir(tmp_path)
    relative = Path("link") / ".." / "state"
    home = tmp_path / relative
    monkeypatch.setenv("AGENT_MCP_HOME", str(home if absolute else relative))

    assert default_home_dir() == home
    assert list(tmp_path.iterdir()) == []
