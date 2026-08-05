"""Tests for the copilot platform-package preflight (#111)."""

from __future__ import annotations

import pytest

from agent_codespaces import platform_preflight
from agent_codespaces.platform_preflight import (
    ensure_copilot_platform,
    needs_platform_repair,
)


def test_needs_platform_repair_detects_marker():
    assert needs_platform_repair(
        "GitHub Copilot CLI: no platform package found. Reinstall with ..."
    )
    assert needs_platform_repair("NO PLATFORM PACKAGE FOUND")


def test_needs_platform_repair_false_for_healthy():
    assert not needs_platform_repair("GitHub Copilot CLI 1.0.64")
    assert not needs_platform_repair("")


class _Runner:
    """Scripts sequential (rc, output) responses for run_remote calls."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.commands: list[str] = []

    async def __call__(self, cmd: str) -> tuple[int, str]:
        self.commands.append(cmd)
        return self._responses.pop(0)


@pytest.mark.asyncio
async def test_healthy_copilot_no_repair():
    runner = _Runner([(0, "GitHub Copilot CLI 1.0.64")])
    ok, detail = await ensure_copilot_platform(runner)
    assert ok is True
    assert detail == "already-present"
    assert len(runner.commands) == 1  # verify only, no repair


@pytest.mark.asyncio
async def test_missing_platform_repaired_and_verified():
    runner = _Runner([
        (1, "no platform package found"),      # initial verify
        (0, "added 1 package"),                 # repair
        (0, "GitHub Copilot CLI 1.0.64"),      # re-verify
    ])
    ok, detail = await ensure_copilot_platform(runner)
    assert ok is True
    assert detail == "repaired"
    assert len(runner.commands) == 3
    assert runner.commands[1] == platform_preflight.REPAIR_COMMAND


@pytest.mark.asyncio
async def test_repair_install_failure_reports_not_ok():
    runner = _Runner([
        (1, "no platform package found"),
        (1, "npm ERR! 401 Unable to authenticate"),
    ])
    ok, detail = await ensure_copilot_platform(runner)
    assert ok is False
    assert detail == "repair-failed"


@pytest.mark.asyncio
async def test_repair_but_still_missing_reports_not_ok():
    runner = _Runner([
        (1, "no platform package found"),
        (0, "added 1 package"),
        (1, "no platform package found"),      # still broken after repair
    ])
    ok, detail = await ensure_copilot_platform(runner)
    assert ok is False
    assert detail == "repair-verify-failed"


@pytest.mark.asyncio
async def test_non_platform_failure_does_not_repair():
    # copilot missing entirely (not the platform marker) -> no npm repair.
    runner = _Runner([(127, "bash: copilot: command not found")])
    ok, detail = await ensure_copilot_platform(runner)
    assert ok is True
    assert detail == "no-repair-signal"
    assert len(runner.commands) == 1


@pytest.mark.asyncio
async def test_verify_probe_error_is_degrade_safe():
    async def _boom(_cmd: str) -> tuple[int, str]:
        raise RuntimeError("ssh dropped")

    ok, detail = await ensure_copilot_platform(_boom)
    assert ok is True
    assert detail == "verify-skipped"
