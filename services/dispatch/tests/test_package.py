"""Lightweight import/metadata checks for the marker package."""

from __future__ import annotations

import agent_dispatch_service
import pytest


def test_version_is_a_nonempty_string() -> None:
    assert isinstance(agent_dispatch_service.__version__, str)
    assert agent_dispatch_service.__version__


def test_standalone_module_delegates_arguments_and_exit_without_a_plugin_checkout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_dispatch import __main__ as backend
    from agent_dispatch_service.__main__ import main

    calls: list[list[str] | None] = []

    def invoke(argv: list[str] | None = None) -> int:
        calls.append(argv)
        return 17

    monkeypatch.setattr(backend, "main", invoke)
    assert main(["health", "--help"]) == 17
    assert calls == [["health", "--help"]]
