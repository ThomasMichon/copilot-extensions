"""Shared test fixtures for agent-codespaces."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _neutralize_remote_mode_inspection(monkeypatch):
    from unittest.mock import AsyncMock
    from agent_codespaces import __main__ as cli

    monkeypatch.setattr(cli, "_check_remote_execution_mode",
                        AsyncMock(return_value=(0, "EXECUTION_MODE_CLEAR")))


@pytest.fixture(autouse=True)
def _disable_codespace_claim(monkeypatch):
    """Disable the #897 exclusive-claim enforcement by default in unit tests.

    The auto-claim in ``agent-codespaces ssh`` shells out to ``agent-worktrees``
    (to resolve the calling worktree + enumerate active worktrees) and writes the
    real host lease file. Unit tests that exercise the ``ssh`` CLI must not do
    real subprocess I/O or touch host state, so claiming is off by default. Tests
    that specifically cover claim enforcement opt back in by deleting the env var
    and mocking the ``lease`` seam.
    """
    monkeypatch.setenv("AGENT_CODESPACES_DISABLE_CLAIM", "1")


@pytest.fixture(autouse=True)
def _neutralize_l2(monkeypatch):
    """Neutralize the cross-machine L2 lease layer (git-ref-resource-leases) in
    unit tests by default, so nothing shells out to ``agent-worktrees lease`` /
    ``get owner-ref`` (real subprocess + host state). ``owner_ref`` resolves to
    None (holder unknown -> L2 skipped) and every lease op reports UNAVAILABLE
    (the degrade-safe path), so claim/release/heartbeat behave exactly as the
    L1-only broker. Tests that specifically cover L2 opt back in by re-patching
    these seams after this fixture runs.
    """
    from agent_codespaces import coordination

    monkeypatch.setattr(coordination, "owner_ref", lambda *a, **k: None)
    monkeypatch.setattr(
        coordination,
        "preflight",
        lambda *a, **k: coordination.PreflightResult("absent"),
    )
    monkeypatch.setattr(
        coordination, "acquire",
        lambda *a, **k: coordination.L2Result("unavailable"),
    )
    monkeypatch.setattr(
        coordination, "renew",
        lambda *a, **k: coordination.L2Result("unavailable"),
    )
    monkeypatch.setattr(
        coordination, "release",
        lambda *a, **k: coordination.L2Result("unavailable"),
    )
    # The pool's cross-machine L2 overlay reads via ``list_leases``; neutralize it
    # too (None -> overlay absent) so ``build_pool`` never shells out in units.
    monkeypatch.setattr(coordination, "list_leases", lambda *a, **k: None)
    # The pool's cleanliness-beacon overlay reads via ``list_cleanliness`` and
    # publishes via ``publish_cleanliness`` (both shell ``agent-worktrees lease``);
    # neutralize them (None -> overlay absent; publish no-op) so ``build_pool`` and
    # the disconnect/finalize paths never shell out in units. Tests covering the
    # beacon opt back in by re-patching these seams after this fixture runs.
    monkeypatch.setattr(coordination, "list_cleanliness", lambda *a, **k: None)
    monkeypatch.setattr(coordination, "publish_cleanliness", lambda *a, **k: False)
    # The cross-harness fence (git-ref-resource-leases Phase 4) shells
    # ``agent-worktrees get lease-origin`` for the harness identity; neutralize
    # it (None -> no identity -> fence proceeds without shelling out) so the ssh
    # CLI tests never touch host state. Tests covering the fence opt back in.
    monkeypatch.setattr(coordination, "harness_identity", lambda *a, **k: None)


@pytest.fixture
def ssh_runtime(monkeypatch):
    """Owned SSH lifecycle fixture shared by terminal and preparation contracts."""
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import ssh_manager
    from agent_codespaces import __main__ as cli
    from agent_codespaces import connection_owner, coordination, lease

    events = []
    config = SimpleNamespace(
        credentials=SimpleNamespace(relay_port=9857, ado_host=None, feed_token_env=None),
    )
    monkeypatch.setattr(cli, "_gh_binary_available", lambda: True)
    monkeypatch.setattr(cli, "load_merged_config", lambda: config)
    monkeypatch.setattr(cli, "CodespaceSource", lambda *a, **k: object())
    monkeypatch.setattr("agent_codespaces.lifecycle.account_for_codespace", lambda _: None)
    monkeypatch.setattr(cli, "_clear_status_quietly", lambda _: events.append("clear-status"))
    monkeypatch.setattr(cli, "_relay_listening", lambda _: True)
    monkeypatch.setattr("agent_codespaces.relay_token.token_for", lambda _: "synthetic-relay")
    monkeypatch.setattr(connection_owner, "should_defer_to_owner", lambda *a, **k: False)
    monkeypatch.delenv("AGENT_CODESPACES_DISABLE_CLAIM", raising=False)
    monkeypatch.setattr(lease, "resolve_owner_worktree", lambda **k: "example-worktree")
    monkeypatch.setattr(lease, "active_worktree_ids", lambda: {"example-worktree"})
    monkeypatch.setattr(lease, "claim", lambda *a, **k: events.append("claim"))
    monkeypatch.setattr(coordination, "owner_ref", lambda **k: None)
    monkeypatch.setattr(cli, "_check_cross_harness_fence", AsyncMock(return_value=True))
    manager = SimpleNamespace(
        ensure_connected=AsyncMock(return_value=SimpleNamespace(config=SimpleNamespace())),
        disconnect=AsyncMock(),
    )
    monkeypatch.setattr(ssh_manager, "ConnectionManager", lambda: manager)

    class Lock:
        def __init__(self, *a, **k):
            pass

        def acquire(self, force=False):
            assert not force
            events.append("lock")

        def release(self):
            events.append("unlock")

    monkeypatch.setattr(ssh_manager, "TargetLock", Lock)
    for name in ("_provision_relay_helpers", "_verify_remote_auth", "_warm_remote_auth_cache"):
        monkeypatch.setattr(cli, name, AsyncMock())
    for name in ("_provision_dotfiles", "_provision_harness", "_register_codespace_plugins",
                 "_provision_repo_hooks", "_stage_plugins"):
        monkeypatch.setattr(cli, name, lambda *a, **k: pytest.fail("unexpected heavy provisioning"))

    async def start_relay(*a, **k):
        events.append("relay-start")

        async def heartbeat():
            while True:
                events.append("heartbeat")
                await asyncio.sleep(0.01)

        task = asyncio.create_task(heartbeat())

        async def stop():
            events.append("relay-stop")
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        return SimpleNamespace(stop=stop, is_alive=True)

    monkeypatch.setattr(cli, "_start_supervised_relay", start_relay)
    return events, manager
