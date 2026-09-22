"""CLI dispatch tests for ``agent-codespaces check <name>`` / ``doctor <name>
[--fix]`` -- the connection layer is faked so no real SSH/CodeSpace is
touched; ``venue_check`` itself is exercised directly in
``test_venue_check.py``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from agent_codespaces.__main__ import main


@dataclass
class _FakeResult:
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0


_READY_PROBE_OUTPUT = (
    "COPILOT=/usr/local/bin/copilot\n"
    "TMUX=yes\nNODE=yes\nPYTHON3=yes\nUV=yes\nAPT=yes\nSUDO_NOPASSWD=yes\n"
    "AGENT_WORKTREES_STATE=full\nAGENT_WORKTREES_VERSION=agent-worktrees 1.5.5-dev229\n"
)

_LEAN_PROBE_OUTPUT = (
    "COPILOT=/usr/local/bin/copilot\n"
    "TMUX=no\nNODE=yes\nPYTHON3=yes\nUV=yes\nAPT=yes\nSUDO_NOPASSWD=yes\n"
    "AGENT_WORKTREES_STATE=lean\n"
)


class _FakeManager:
    def __init__(self, probe_outputs: list[str]) -> None:
        self._probe_outputs = list(probe_outputs)
        self.exec_calls: list[str] = []
        self.disconnected: list[str] = []

    async def ensure_connected(self, *_args, **_kwargs):
        return None

    async def exec_command(self, _host: str, command: str) -> _FakeResult:
        self.exec_calls.append(command)
        if self._probe_outputs and "AGENT_WORKTREES_STATE" in _probe_marker(command):
            return _FakeResult(stdout=self._probe_outputs.pop(0))
        return _FakeResult(exit_code=0)

    async def disconnect(self, host: str) -> None:
        self.disconnected.append(host)


def _probe_marker(command: str) -> str:
    # The real probe script always greps for AGENT_WORKTREES_STATE; any other
    # command (tmux install, agent-worktrees --version nudge) is a
    # remediation step, not a probe -- distinguish by the sentinel text the
    # probe script itself contains.
    return "AGENT_WORKTREES_STATE" if "AGENT_WORKTREES_STATE" in command else ""


def _patch(monkeypatch, manager: _FakeManager) -> None:
    monkeypatch.setattr("ssh_manager.ConnectionManager", lambda: manager)
    monkeypatch.setattr(
        "agent_codespaces.lifecycle.account_for_codespace", lambda _name: None,
    )


class TestCmdCheck:
    def test_ready_venue_exits_zero(self, monkeypatch, capsys) -> None:
        manager = _FakeManager([_READY_PROBE_OUTPUT])
        _patch(monkeypatch, manager)

        rc = main(["check", "cs-one"])

        assert rc == 0
        out = capsys.readouterr().out
        assert "READY" in out
        assert manager.disconnected == ["cs-one"]

    def test_not_ready_venue_exits_nonzero(self, monkeypatch, capsys) -> None:
        manager = _FakeManager([_LEAN_PROBE_OUTPUT])
        _patch(monkeypatch, manager)

        rc = main(["check", "cs-one"])

        assert rc == 1
        assert "NOT READY" in capsys.readouterr().out

    def test_json_output(self, monkeypatch, capsys) -> None:
        manager = _FakeManager([_READY_PROBE_OUTPUT])
        _patch(monkeypatch, manager)

        rc = main(["check", "cs-one", "--json"])

        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["ready"] is True
        assert payload["agent_worktrees_state"] == "full"


class TestCmdDoctorVenue:
    def test_no_name_still_uses_host_side_doctor(self, monkeypatch, capsys) -> None:
        # Regression guard: adding the optional <name> positional must not
        # change the existing no-name host-side gh-auth/config.d behavior.
        monkeypatch.setattr(
            "agent_codespaces.__main__._gh_auth_preflight", lambda: [],
        )
        from agent_codespaces import config as config_mod

        class _EmptyReport:
            authority = type("A", (), {"value": "none"})()
            active_configs: list = []
            findings: list = []

        class _Providers:
            active_plugins = _EmptyReport()
            config_d = _EmptyReport()

        monkeypatch.setattr(config_mod, "scan_config_providers", lambda: _Providers())

        rc = main(["doctor"])

        assert rc == 0
        assert "gh is authenticated" in capsys.readouterr().out

    def test_report_only_without_fix(self, monkeypatch, capsys) -> None:
        manager = _FakeManager([_LEAN_PROBE_OUTPUT])
        _patch(monkeypatch, manager)

        rc = main(["doctor", "cs-one"])

        assert rc == 1
        assert manager.exec_calls == [pytest_probe_script()]
        err = capsys.readouterr().err
        assert "Run with --fix" in err

    def test_fix_applies_remediation_and_reprobes(self, monkeypatch, capsys) -> None:
        manager = _FakeManager([_LEAN_PROBE_OUTPUT, _READY_PROBE_OUTPUT])
        _patch(monkeypatch, manager)

        rc = main(["doctor", "cs-one", "--fix"])

        assert rc == 0
        out = capsys.readouterr().out
        assert "READY" in out
        assert "[OK] install tmux" in out
        assert "[OK] provision/refresh agent-worktrees" in out
        # probe, remediate(tmux + agent-worktrees), re-probe == 4 calls
        assert len(manager.exec_calls) == 4

    def test_fix_json_output_includes_remediation(self, monkeypatch, capsys) -> None:
        manager = _FakeManager([_LEAN_PROBE_OUTPUT, _READY_PROBE_OUTPUT])
        _patch(monkeypatch, manager)

        rc = main(["doctor", "cs-one", "--fix", "--json"])

        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["ready"] is True
        assert "remediation" in payload
        assert "install tmux" in payload["remediation"]["succeeded"]


def pytest_probe_script() -> str:
    """The exact probe script text -- imported lazily to avoid a hard
    dependency ordering between this test file and the CLI module."""
    from agent_codespaces.venue_check import _PROBE_SCRIPT

    return _PROBE_SCRIPT
