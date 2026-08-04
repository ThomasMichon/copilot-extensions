"""Codespace resolver diagnostics for persisted account bindings."""

from __future__ import annotations

import pytest

from agent_codespaces.resolver import CodespaceResolver


@pytest.mark.asyncio
async def test_ensure_ready_not_found_mentions_bound_account(monkeypatch):
    monkeypatch.setattr("agent_codespaces.resolver.list_codespaces", lambda: [])
    monkeypatch.setattr("agent_codespaces.account_binding.bound_account", lambda name: "acct-a")

    with pytest.raises(RuntimeError, match="bound to gh account 'acct-a'"):
        await CodespaceResolver().ensure_ready("cs-one")
