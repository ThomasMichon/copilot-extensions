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
    # Coordinator self-update is now default-ON in production (mirrors
    # self-retire/the supervisor daemon's own already-default-on loop), but
    # no coordinator test here ever anticipated an extra always-on background
    # loop touching the `current-version` marker and potentially firing a
    # self-triggered `deploy` subprocess during its own lifespan -- none of
    # them set this var, since it never needed setting before. Disable it as
    # part of this suite's own hermetic baseline; a test that specifically
    # exercises the live self-update loop sets it explicitly in its own body,
    # same as `AGENT_DISPATCH_SELF_RETIRE`/`AGENT_DISPATCH_ABANDONED_PASSIVE_REAP`
    # already do above this fixture's defaults (last `setenv` wins).
    monkeypatch.setenv("AGENT_DISPATCH_SELF_UPDATE", "0")
    # Isolate every test from this (or any) machine's real installed
    # service.env (read by `apply_service_env_overlay`, called from both
    # `__main__.py`'s first-use bootstrap and `coordinator_cli.py`'s own
    # cutover). A real machine's installed root can carry a genuine pinned
    # `AGENT_DISPATCH_HOST`/token -- discovered via a cutover test silently
    # getting this machine's actual production `AGENT_DISPATCH_HOST=
    # 127.0.0.1` pin instead of the test's own monkeypatched host, clobbered
    # by the overlay immediately before `_config.load_config()` ran. Point it
    # at an empty tmp dir so the overlay always finds no file and no-ops.
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path / "install"))
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
