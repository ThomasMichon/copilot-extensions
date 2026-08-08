"""Shared test fixtures for agent-codespaces."""

from __future__ import annotations

import pytest


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
    # The cross-harness fence (git-ref-resource-leases Phase 4) shells
    # ``agent-worktrees get lease-origin`` for the harness identity; neutralize
    # it (None -> no identity -> fence proceeds without shelling out) so the ssh
    # CLI tests never touch host state. Tests covering the fence opt back in.
    monkeypatch.setattr(coordination, "harness_identity", lambda *a, **k: None)
