"""Additive strict parser and actual fresh-home, write-free process contract."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest
from repository_policy import AgentHostingDenied

from agent_worktrees import __main__ as cli
from agent_worktrees import agent_policy, registry_paths, sessions
from agent_worktrees import session_observations_cli


def test_parser_and_dispatch_remain_strict_and_project_independent():
    parser = cli.build_parser()
    args = parser.parse_args(["session-attention", "session-1", "--json"])
    assert args.session_id == "session-1" and args.json
    assert "session-attention" in cli._NO_PROJECT_COMMANDS
    assert cli.COMMAND_MAP["session-attention"] is session_observations_cli.cmd_session_attention
    assert cli._LAZY_DISPATCH_TABLE["session-attention"] == (
        "session_observations_cli", "cmd_session_attention")
    assert "session_observations_cli" in cli._CLUSTER_FREE_MODULES
    assert parser.parse_args(["session-observations", "session-1"]).session_id == "session-1"
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["session-attention", "session-1", "--jsno"])
    assert exc.value.code == 2


def test_read_only_does_not_enable_source_hosting(tmp_path, monkeypatch, capfd):
    policy = tmp_path / ".copilot-extensions" / "agent-worktrees" / "config.yaml"
    policy.parent.mkdir(parents=True)
    policy.write_text("expose_agent: false\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(registry_paths, "installation_context", lambda: None)
    monkeypatch.setattr(sessions, "_session_state_dir", lambda: tmp_path / "missing")
    assert cli.main(["session-attention", "session-1", "--json"]) == 0
    assert json.loads(capfd.readouterr().out)["availability"] == "unknown"
    for operation in ("launch", "session-attention"):
        with pytest.raises(AgentHostingDenied):
            agent_policy.require(tmp_path, operation=operation)


@pytest.mark.parametrize("rest", [None, "busy", "idle", "awaiting-operator"])
def test_real_cli_reads_only_one_pulse_in_fresh_home(tmp_path, rest):
    home = tmp_path / "home"
    entry = home / ".copilot" / "session-state" / "session-1"
    entry.mkdir(parents=True)
    policy = tmp_path / ".copilot-extensions" / "agent-worktrees" / "config.yaml"
    policy.parent.mkdir(parents=True)
    policy.write_text("expose_agent: false\n", encoding="utf-8")
    (entry / "events.jsonl").write_text("private-transcript", encoding="utf-8")
    if rest is not None:
        (entry / "substatus.json").write_text(json.dumps({
            "sessionId": "session-1", "intent": "private-intent",
            "rest": rest, "restAt": "2020-01-02T03:04:05Z",
        }), encoding="utf-8")
    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home),
           "AGENT_HOME": str(home), "PYTHONDONTWRITEBYTECODE": "1"}
    before = {str(path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    result = subprocess.run(
        [sys.executable, "-m", "agent_worktrees", "session-attention", "session-1", "--json"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=20,
        creationflags=0x08000000 if sys.platform == "win32" else 0,
    )
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["contract"] == "agent-worktrees.session-attention"
    assert value["version"] == 1 and value["supported_versions"] == [1]
    assert value["availability"] == ("unknown" if rest is None else "observed")
    assert value["rest"] == rest and value["runtime_continuity"] == "unknown"
    assert "private-" not in result.stdout + result.stderr
    assert {str(path): path.read_bytes() for path in tmp_path.rglob("*")
            if path.is_file()} == before
