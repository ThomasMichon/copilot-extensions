"""Lightweight import/metadata checks for the marker package."""

from __future__ import annotations

import agent_dispatch_service


def test_version_is_a_nonempty_string() -> None:
    assert isinstance(agent_dispatch_service.__version__, str)
    assert agent_dispatch_service.__version__
