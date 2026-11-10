"""Unsupported composition and invalid admission never create a native scope."""

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent_procutil import async_foreground as api


@pytest.mark.parametrize("argv", [[], ["bad\0argument"], "not-an-argv"])
def test_invalid_foreground_admission_refuses_before_native_support(monkeypatch, argv):
    support = Mock(side_effect=AssertionError("must not acquire native custody"))
    monkeypatch.setattr(api, "require_foreground_custody", support)
    with pytest.raises(ValueError, match="literal argv"):
        asyncio.run(api.AsyncForegroundProcess.start(argv))
    support.assert_not_called()


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_unsupported_foreground_platform_refuses_before_native_creation(monkeypatch, platform):
    launch = Mock(side_effect=AssertionError("must not create a guardian"))
    monkeypatch.setattr(api, "sys", SimpleNamespace(platform=platform))
    monkeypatch.setattr(api.subprocess, "Popen", launch)
    with pytest.raises(RuntimeError, match="Linux pidfd"):
        api.require_foreground_custody()
    launch.assert_not_called()
