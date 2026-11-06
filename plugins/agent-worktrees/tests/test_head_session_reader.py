"""The bounded head reader preserves loaded overrides without loading the CLI."""

from __future__ import annotations

import argparse
import sys
from types import SimpleNamespace

import pytest

from agent_worktrees import output, session_tracking_cli, tracking
from test_session_lifecycle import _rec


@pytest.mark.parametrize("loaded_override", [False, True])
def test_head_reader_uses_canonical_or_loaded_controller_projection(
    tmp_tracking_dir, monkeypatch_config, monkeypatch, loaded_override,
):
    _rec(tmp_tracking_dir, sessions=[tracking.SessionEntry("current", "fixture")])
    path = tmp_tracking_dir / "wt-1.yaml"
    before = path.read_bytes()
    monkeypatch.setattr(session_tracking_cli, "_all_tracking_dirs", lambda: [tmp_tracking_dir])
    monkeypatch.setattr(session_tracking_cli, "_core", lambda: pytest.fail("read loaded the full CLI"))
    controllers = [{"owner": "loaded-override"}]
    findings = [{"state": "loaded-override"}]
    core = SimpleNamespace(
        _controller_metadata=lambda record: controllers,
        _controller_findings=lambda record: findings,
    ) if loaded_override else None
    monkeypatch.setitem(sys.modules, "agent_worktrees.__main__", core)
    captured = {}
    monkeypatch.setattr(output, "_json_output", lambda value: captured.update(value))
    parser = argparse.ArgumentParser(allow_abbrev=False)
    session_tracking_cli.add_parsers(parser.add_subparsers(dest="command", required=True))
    args = parser.parse_args(["head-session", "--worktree-id", "wt-1", "--json"])

    assert session_tracking_cli.cmd_head_session(args) == 0
    assert captured["tracked"] and captured["head_session"] == "current"
    assert captured["controllers"] == (controllers if loaded_override else [])
    assert captured["controller_findings"] == (findings if loaded_override else [])
    assert sys.modules.get("agent_worktrees.__main__") is core
    assert path.read_bytes() == before
