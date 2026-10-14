"""Bound text stays exact until a consumer interprets it as a selector."""
from __future__ import annotations

import pytest
import yaml

from agent_worktrees import tracking


WID = "sample-host-win-20260101-120000-abcd"


@pytest.mark.guard
@pytest.mark.parametrize("value", [
    " leading and trailing \n",
    "\tworker\t",
    "first\n\nsecond\n\n",
    "charter: not metadata\n  quoted: 'value'\n",
    " caf\u00e9 \u96ea\n",
    "false",
    "0",
])
def test_creation_load_and_json_preserve_nonblank_bound_text(tmp_path, value):
    from agent_worktrees import __main__ as main

    record = tracking.create_new_record(
        WID, f"worktree/{WID}", str(tmp_path), "sample", "sample-host", "windows",
        tmp_path, bound_agent=value,
    )
    path = tmp_path / f"{WID}.yaml"
    original = path.read_bytes()
    assert record.bound_agent == value
    assert yaml.safe_load(original)["bound_agent"] == value
    tracking.record_cache.clear()
    loaded = tracking.load_record(path)
    assert loaded.bound_agent == value
    assert main._worktree_to_dict(loaded)["bound_agent"] == value
    assert path.read_bytes() == original


@pytest.mark.guard
@pytest.mark.parametrize("value", [None, "", " ", "\t\r\n"])
def test_blank_bound_text_remains_unbound(tmp_path, value):
    record = tracking.create_new_record(
        WID, f"worktree/{WID}", str(tmp_path), "sample", "sample-host", "windows",
        tmp_path, bound_agent=value,
    )
    assert record.bound_agent is None
    tracking.record_cache.clear()
    assert tracking.load_record(tmp_path / f"{WID}.yaml").bound_agent is None


@pytest.mark.guard
@pytest.mark.parametrize("value", [" ", "\t\r\n"])
def test_loading_existing_blank_binding_does_not_rewrite_it(tmp_path, value):
    record = tracking.create_new_record(
        WID, f"worktree/{WID}", str(tmp_path), "sample", "sample-host", "windows",
        tmp_path,
    )
    path = tmp_path / f"{WID}.yaml"
    data = yaml.safe_load(path.read_bytes())
    data["bound_agent"] = value
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    original = path.read_bytes()
    tracking.record_cache.clear()
    assert tracking.load_record(path).bound_agent is None
    assert record.bound_agent is None
    assert path.read_bytes() == original
