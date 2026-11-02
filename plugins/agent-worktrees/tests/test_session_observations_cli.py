"""Real parser/process contract and unchanged negative source-hosting policy."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

import pytest
from repository_policy import AgentHostingDenied

from agent_worktrees import __main__ as cli
from agent_worktrees import agent_policy, registry_paths, sessions
from agent_worktrees import session_observations_cli


def test_parser_is_additive_strict_and_project_independent():
    parser = cli.build_parser()
    args = parser.parse_args(["session-observations", "session-1", "--json"])
    assert args.session_id == "session-1" and args.json
    assert "session-observations" in cli._NO_PROJECT_COMMANDS
    assert cli.COMMAND_MAP["session-observations"] is session_observations_cli.cmd_session_observations
    old = parser.parse_args(["session-tail", "session-1", "--limit", "4", "--json"])
    assert old.limit == 4 and old.session_id == "session-1"
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["session-observations", "session-1", "--limti", "4"])
    assert exc.value.code == 2


def test_read_only_command_does_not_enable_source_hosting(tmp_path, monkeypatch, capfd):
    policy = tmp_path / ".copilot-extensions" / "agent-worktrees" / "config.yaml"
    policy.parent.mkdir(parents=True)
    policy.write_text("expose_agent: false\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(registry_paths, "installation_context", lambda: None)
    state = tmp_path / "session-state"
    entry = state / "session-1"
    entry.mkdir(parents=True)
    (entry / "events.jsonl").write_text('{"type":"session.start"}\n', encoding="utf-8")
    monkeypatch.setattr(sessions, "_session_state_dir", lambda: state)
    before = sorted(str(path) for path in tmp_path.rglob("*"))
    assert cli.main(["session-observations", "session-1", "--json"]) == 0
    assert json.loads(capfd.readouterr().out)["availability"] == "available"
    for operation in ("launch", "session-observations"):
        with pytest.raises(AgentHostingDenied):
            agent_policy.require(tmp_path, operation=operation)
    assert sorted(str(path) for path in tmp_path.rglob("*")) == before


@pytest.mark.parametrize("available", [True, False])
def test_actual_cli_json_subprocess_is_metadata_only_and_write_free(tmp_path, available):
    home = tmp_path / "home"
    entry = home / ".copilot" / "session-state" / "session-1"
    entry.mkdir(parents=True)
    policy = tmp_path / ".copilot-extensions" / "agent-worktrees" / "config.yaml"
    policy.parent.mkdir(parents=True)
    policy.write_text("expose_agent: false\n", encoding="utf-8")
    if available:
        events = [
            {"type": "session.start"},
            {"type": "tool.execution_start", "data": {
                "toolName": "read", "arguments": "private-arguments"}},
            {"type": "assistant.message", "data": {"content": "private-message"}},
        ]
        (entry / "events.jsonl").write_text(
            "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8",
        )
    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home),
           "AGENT_HOME": str(home), "PYTHONDONTWRITEBYTECODE": "1"}
    before = sorted(str(path) for path in tmp_path.rglob("*"))
    result = subprocess.run(
        [sys.executable, "-m", "agent_worktrees", "session-observations", "session-1", "--json"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=20,
        creationflags=0x08000000 if sys.platform == "win32" else 0,
    )
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["contract"] == "agent-worktrees.session-observations"
    assert value["version"] == 1 and value["supported_versions"] == [1]
    assert value["availability"] == ("available" if available else "unavailable")
    if available:
        assert value["tools"]["started_records"] == 1
    else:
        assert value["tools"] is None
    assert "private-" not in result.stdout + result.stderr
    assert sorted(str(path) for path in tmp_path.rglob("*")) == before


def test_handler_unavailable_is_structured_not_transcript_fallback(monkeypatch, capfd):
    monkeypatch.setattr(sessions, "read_session_transcript", lambda _: pytest.fail("transcript"))
    monkeypatch.setattr(sessions, "session_message_tail", lambda *a, **k: pytest.fail("tail"))
    assert session_observations_cli.cmd_session_observations(
        argparse.Namespace(session_id="../invalid", json=True),
    ) == 0
    value = json.loads(capfd.readouterr().out)
    assert value["availability"] == "unavailable" and value["tools"] is None
