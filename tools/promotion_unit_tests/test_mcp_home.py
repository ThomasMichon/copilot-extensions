"""No-actor coverage for the shared legacy MCP home selection contract."""

import importlib.util
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "plugins/agent-mcp/src/agent_mcp/home.py"
SPEC = importlib.util.spec_from_file_location("unit_mcp_home", SOURCE)
home = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(home)


@pytest.mark.parametrize(
    "selection",
    [
        "missing",
        "empty",
        "relative",
        "absolute",
        "whitespace",
        "relative-parent",
        "absolute-parent",
    ],
)
def test_legacy_home_selection_is_absolute_and_side_effect_free(
    tmp_path,
    monkeypatch,
    selection,
):
    profile = tmp_path / "profile"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: profile))
    monkeypatch.chdir(tmp_path)
    if selection == "missing":
        monkeypatch.delenv("AGENT_MCP_HOME", raising=False)
    else:
        value = {
            "empty": "",
            "relative": "state",
            "absolute": str(tmp_path / "state"),
            "whitespace": " ",
            "relative-parent": str(Path("link") / ".." / "state"),
            "absolute-parent": str(tmp_path / "link" / ".." / "state"),
        }[selection]
        monkeypatch.setenv("AGENT_MCP_HOME", value)
    expected = (
        profile / ".agent-mcp"
        if selection in ("missing", "empty")
        else tmp_path / " "
        if selection == "whitespace"
        else tmp_path / "state"
    )

    assert home.default_home_dir() == expected
    assert list(tmp_path.iterdir()) == []
