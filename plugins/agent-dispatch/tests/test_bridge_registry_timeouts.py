"""Single-agent lookups allow CLI startup without overriding caller deadlines."""

from __future__ import annotations

import json
import subprocess

import pytest

from agent_dispatch import bridge, bridge_agent_registry


@pytest.mark.parametrize("lookup", ["registered_agent", "registered_agent_project"])
def test_registered_agent_lookup_allows_startup_but_preserves_explicit_deadline(
    monkeypatch: pytest.MonkeyPatch, lookup: str,
) -> None:
    def fake_run(
        cmd: list[str], *, timeout: float, **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        if timeout < 12:
            raise subprocess.TimeoutExpired(cmd, timeout)
        return subprocess.CompletedProcess(
            cmd, 0, json.dumps({"name": "reviewer", "project": "review-harness"}), "",
        )

    monkeypatch.setattr(
        bridge_agent_registry, "_agent_bridge_launch_prefix", lambda: ["/usr/bin/agent-bridge"],
    )
    monkeypatch.setattr(subprocess, "run", fake_run)
    if lookup == "registered_agent":
        assert bridge.registered_agent("reviewer") == {
            "name": "reviewer", "project": "review-harness",
        }
        assert bridge.registered_agent("reviewer", timeout=8) is None
    else:
        assert bridge.registered_agent_project("reviewer") == "review-harness"
        assert bridge.registered_agent_project("reviewer", timeout=8) is None
