"""Shared pytest fixtures for the agent-dispatch suite."""

from __future__ import annotations

import pytest

# Force `single_instance`'s own module-level `if os.name == "nt": import
# msvcrt` to evaluate now, against the *real* `os.name`, rather than lazily
# whenever some test first imports it (directly or transitively). Several
# tests legitimately monkeypatch `os.name = "nt"` to exercise Windows-only
# code paths (`procutil`'s own `run_agent_worktrees_capture` tests, for
# one) -- since that monkeypatch mutates the real, shared `os` module
# object, any *unrelated* first import of `single_instance` that happens to
# land inside such a test's patched window would otherwise try a genuine
# `import msvcrt` on a non-Windows host and crash with
# `ModuleNotFoundError`. Which subsuite/test-file grouping a given pytest
# run collects determines whether some earlier module import already
# cached `single_instance` safely -- a real, environment-dependent flake,
# not merely a hermeticity nicety. Importing it here, at collection time
# before any test runs, makes every subsuite grouping safe.
import agent_dispatch.single_instance  # noqa: F401


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
        # `consume`'s claimed/started fencing reads this to bind a
        # per-session identity (see task_query_cli.py); an ambient value
        # from the *actual* Copilot session running this test suite would
        # otherwise leak in and silently change `consume`'s behavior/
        # transition list out from under tests that don't expect it.
        "COPILOT_AGENT_SESSION_ID",
    ):
        monkeypatch.delenv(var, raising=False)
