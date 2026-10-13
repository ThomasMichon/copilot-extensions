"""The attended CLI publishes its actual create-or-resume result before attach."""

import argparse
import json
from types import SimpleNamespace

import pytest

from agent_worktrees import copilot_cli, handoff_cli, output


@pytest.mark.guard
@pytest.mark.parametrize("created", [True, False])
def test_copilot_cli_publishes_nonsecret_result_before_attach(tmp_path, monkeypatch, created):
    path = tmp_path / "result.json"
    args = argparse.Namespace(
        launch_result_file=str(path), copilot_process_launcher=str(tmp_path / "copilot"), mux=None,
    )
    monkeypatch.setattr(copilot_cli.sys, "stdin", SimpleNamespace(isatty=lambda: True))

    def embody(_args):
        output._json_output({
            "session": "wt-fixture", "created": created, "resumed": not created, "token": "not-copied",
        })
        return 0

    def attach(binary, argv):
        assert json.loads(path.read_text()) == {
            "version": 1, "created": created, "resumed": not created,
        }
        assert argv == ["tmux", "attach-session", "-t", "wt-fixture"]
        raise OSError("synthetic attach stop")

    monkeypatch.setattr(handoff_cli, "cmd_embody", embody)
    monkeypatch.setattr(copilot_cli.sessions, "_mux_bin", lambda _: "tmux")
    monkeypatch.setattr(copilot_cli.os, "execvp", attach)
    assert copilot_cli.cmd_copilot(args) == 1
