"""Tests for agent-bridge-cli-mode-sessions Phase 4's venue preflight surface:
``agent-codespaces check <name>`` (read-only) and ``doctor <name> [--fix]``
(optional remediation).

``venue_check`` is fully transport-agnostic (a fake ``exec_command`` stands
in for ``ConnectionManager.exec_command``) -- no real SSH connection or
CodeSpace is needed to exercise the probe-parsing and remediation-decision
logic.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from agent_codespaces import venue_check


@dataclass
class _FakeResult:
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0


_READY_PROBE_OUTPUT = """COPILOT=/usr/local/share/nvm/current/bin/copilot
TMUX=yes
NODE=yes
PYTHON3=yes
UV=yes
APT=yes
SUDO_NOPASSWD=yes
AGENT_BRIDGE_PLUGIN=yes
AGENT_WORKTREES_STATE=full
AGENT_WORKTREES_VERSION=agent-worktrees 1.5.5-dev229  commit unknown  branch unknown
"""

_LEAN_PROBE_OUTPUT = """COPILOT=/usr/local/share/nvm/current/bin/copilot
TMUX=no
NODE=yes
PYTHON3=yes
UV=yes
APT=yes
SUDO_NOPASSWD=yes
AGENT_BRIDGE_PLUGIN=no
AGENT_WORKTREES_STATE=lean
"""

_ABSENT_PROBE_OUTPUT = """COPILOT=
TMUX=no
NODE=no
PYTHON3=no
UV=no
APT=no
SUDO_NOPASSWD=no
AGENT_BRIDGE_PLUGIN=no
AGENT_WORKTREES_STATE=absent
"""


class TestParseProbeOutput:
    def test_fully_ready_venue(self) -> None:
        readiness = venue_check.parse_probe_output(_READY_PROBE_OUTPUT)
        assert readiness.ready is True
        assert readiness.copilot_present is True
        assert readiness.tmux is True
        assert readiness.agent_worktrees_state == "full"
        assert readiness.agent_worktrees_full is True
        assert "1.5.5-dev229" in (readiness.agent_worktrees_version or "")
        assert readiness.gaps == []

    def test_lean_and_missing_tmux(self) -> None:
        readiness = venue_check.parse_probe_output(_LEAN_PROBE_OUTPUT)
        assert readiness.ready is False
        assert readiness.tmux is False
        assert readiness.agent_worktrees_state == "lean"
        assert readiness.agent_worktrees_full is False
        assert "tmux not installed" in readiness.gaps
        assert (
            "agent-worktrees is only lean-staged (not fully provisioned)"
            in readiness.gaps
        )

    def test_freshly_provisioned_absent_venue(self) -> None:
        readiness = venue_check.parse_probe_output(_ABSENT_PROBE_OUTPUT)
        assert readiness.ready is False
        assert readiness.copilot_present is False
        assert readiness.agent_worktrees_state == "absent"
        assert "copilot CLI not found on PATH" in readiness.gaps
        assert "agent-worktrees not installed" in readiness.gaps

    def test_unparseable_lines_are_ignored(self) -> None:
        readiness = venue_check.parse_probe_output(
            "not a key value line\nTMUX=yes\n"
        )
        assert readiness.tmux is True


class TestCheckRemoteVenue:
    @pytest.mark.asyncio
    async def test_delegates_to_exec_command_and_parses_result(self) -> None:
        seen = {}

        async def fake_exec_command(host: str, command: str) -> _FakeResult:
            seen["host"] = host
            seen["command"] = command
            return _FakeResult(stdout=_READY_PROBE_OUTPUT)

        readiness = await venue_check.check_remote_venue(
            fake_exec_command, "cs-target", timeout=30.0,
        )
        assert seen["host"] == "cs-target"
        assert "agent-worktrees" in seen["command"]
        assert readiness.ready is True


class TestRemediateRemoteVenue:
    @pytest.mark.asyncio
    async def test_installs_tmux_when_missing_and_installable(self) -> None:
        readiness = venue_check.parse_probe_output(_LEAN_PROBE_OUTPUT)
        calls: list[str] = []

        async def fake_exec_command(host: str, command: str) -> _FakeResult:
            calls.append(command)
            return _FakeResult(exit_code=0)

        result = await venue_check.remediate_remote_venue(
            fake_exec_command, "cs-target", readiness,
        )
        assert "install tmux" in result.attempted
        assert "install tmux" in result.succeeded
        assert any("apt-get install -y tmux" in c for c in calls)

    @pytest.mark.asyncio
    async def test_skips_tmux_without_passwordless_sudo(self) -> None:
        readiness = venue_check.parse_probe_output(_LEAN_PROBE_OUTPUT)
        readiness.sudo_nopasswd = False
        readiness.agent_worktrees_state = "full"  # isolate the tmux remediation
        readiness.agent_bridge_plugin = True  # isolate the tmux remediation

        async def fake_exec_command(host: str, command: str) -> _FakeResult:
            raise AssertionError("should not attempt a remote command")

        result = await venue_check.remediate_remote_venue(
            fake_exec_command, "cs-target", readiness,
        )
        assert any("no passwordless sudo" in s for s in result.skipped)
        assert "install tmux" not in result.attempted

    @pytest.mark.asyncio
    async def test_reports_tmux_install_failure(self) -> None:
        readiness = venue_check.parse_probe_output(_LEAN_PROBE_OUTPUT)

        async def fake_exec_command(host: str, command: str) -> _FakeResult:
            return _FakeResult(exit_code=1, stderr="Unable to locate package tmux")

        result = await venue_check.remediate_remote_venue(
            fake_exec_command, "cs-target", readiness,
        )
        assert "install tmux" in result.failed

    @pytest.mark.asyncio
    async def test_provisions_agent_worktrees_when_lean(self) -> None:
        readiness = venue_check.parse_probe_output(_LEAN_PROBE_OUTPUT)
        readiness.tmux = True  # isolate the agent-worktrees remediation
        calls: list[str] = []

        async def fake_exec_command(host: str, command: str) -> _FakeResult:
            calls.append(command)
            return _FakeResult(exit_code=0)

        result = await venue_check.remediate_remote_venue(
            fake_exec_command, "cs-target", readiness,
        )
        assert "provision/refresh agent-worktrees" in result.succeeded
        assert any("agent-worktrees --version" in c for c in calls)

    @pytest.mark.asyncio
    async def test_never_attempts_agent_worktrees_when_absent(self) -> None:
        readiness = venue_check.parse_probe_output(_ABSENT_PROBE_OUTPUT)
        readiness.tmux = True

        async def fake_exec_command(host: str, command: str) -> _FakeResult:
            raise AssertionError(
                "must not run any agent-worktrees command when absent"
            )

        result = await venue_check.remediate_remote_venue(
            fake_exec_command, "cs-target", readiness,
        )
        assert any(
            "install agent-worktrees" in s for s in result.skipped
        )

    @pytest.mark.asyncio
    async def test_installs_agent_bridge_plugin_when_missing(self) -> None:
        readiness = venue_check.parse_probe_output(_READY_PROBE_OUTPUT)
        readiness.agent_bridge_plugin = False  # isolate this one remediation
        calls: list[str] = []

        async def fake_exec_command(host: str, command: str) -> _FakeResult:
            calls.append(command)
            return _FakeResult(exit_code=0)

        result = await venue_check.remediate_remote_venue(
            fake_exec_command, "cs-target", readiness,
        )
        assert "install agent-bridge plugin" in result.succeeded
        assert any("copilot plugin install agent-bridge@" in c for c in calls)

    @pytest.mark.asyncio
    async def test_reports_agent_bridge_plugin_install_failure(self) -> None:
        readiness = venue_check.parse_probe_output(_READY_PROBE_OUTPUT)
        readiness.agent_bridge_plugin = False

        async def fake_exec_command(host: str, command: str) -> _FakeResult:
            return _FakeResult(exit_code=1, stderr="marketplace unreachable")

        result = await venue_check.remediate_remote_venue(
            fake_exec_command, "cs-target", readiness,
        )
        assert "install agent-bridge plugin" in result.failed

    @pytest.mark.asyncio
    async def test_skips_agent_bridge_plugin_install_without_copilot(self) -> None:
        readiness = venue_check.parse_probe_output(_ABSENT_PROBE_OUTPUT)

        async def fake_exec_command(host: str, command: str) -> _FakeResult:
            raise AssertionError("no copilot CLI to install a plugin into")

        result = await venue_check.remediate_remote_venue(
            fake_exec_command, "cs-target", readiness,
        )
        assert any(
            "install agent-bridge plugin" in s for s in result.skipped
        )

    @pytest.mark.asyncio
    async def test_fully_ready_venue_needs_no_remediation(self) -> None:
        readiness = venue_check.parse_probe_output(_READY_PROBE_OUTPUT)

        async def fake_exec_command(host: str, command: str) -> _FakeResult:
            raise AssertionError("a fully-ready venue needs no remote calls")

        result = await venue_check.remediate_remote_venue(
            fake_exec_command, "cs-target", readiness,
        )
        assert result.attempted == []
        assert result.succeeded == []
        assert result.failed == []
        assert result.skipped == []


class TestFormatReport:
    def test_ready_report_has_no_gaps_section(self) -> None:
        readiness = venue_check.parse_probe_output(_READY_PROBE_OUTPUT)
        report = venue_check.format_report(readiness)
        assert "READY" in report
        assert "gaps:" not in report

    def test_not_ready_report_lists_gaps(self) -> None:
        readiness = venue_check.parse_probe_output(_LEAN_PROBE_OUTPUT)
        report = venue_check.format_report(readiness)
        assert "NOT READY" in report
        assert "tmux not installed" in report

    def test_remediation_section_included_when_given(self) -> None:
        readiness = venue_check.parse_probe_output(_LEAN_PROBE_OUTPUT)
        remediation = venue_check.RemediationResult(
            attempted=["install tmux"], succeeded=["install tmux"],
        )
        report = venue_check.format_report(readiness, remediation=remediation)
        assert "[OK] install tmux" in report
