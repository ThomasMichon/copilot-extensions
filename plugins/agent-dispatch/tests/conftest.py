"""Shared pytest fixtures for the agent-dispatch suite."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_discovery(monkeypatch, tmp_path):
    """Isolate endpoint discovery from ambient machine state, suite-wide.

    Without this, any test that resolves the local endpoint (``client_url`` /
    ``_resolve_client_target``) reads the real ``~/.agent-dispatch/run/endpoint.json``
    of a *live* coordinator on the test machine and gets its OS-assigned
    (discovered) port instead of the fixed fallback -- a hermeticity bug that only
    surfaces once a discovery-capable coordinator is actually running (Stage C).
    Point the run dir at an empty tmp dir and clear the endpoint / Windows-mount
    overrides so discovery finds nothing and the fixed-fallback path is exercised.

    A test that needs a specific rendezvous file sets ``AGENT_DISPATCH_RUN_DIR``
    itself; this fixture runs first, so the test's ``setenv`` wins.
    """
    monkeypatch.setenv("AGENT_DISPATCH_RUN_DIR", str(tmp_path / "run"))
    for var in (
        "AGENT_DISPATCH_ENDPOINT",
        "AGENT_DISPATCH_WINDOWS_RUN_DIR",
        "AGENT_DISPATCH_WINDOWS_MOUNT",
    ):
        monkeypatch.delenv(var, raising=False)
