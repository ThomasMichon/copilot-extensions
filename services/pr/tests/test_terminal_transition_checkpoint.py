"""The host retains its original cache authority through terminal adoption."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from agent_pull_requests.generation_cache import GenerationCache
from agent_pull_requests.watch_replay_store import ReplayStore
from agent_pull_requests_service import terminal_transition
from agent_pull_requests_service.config import WorkspaceConfig, load_config

if TYPE_CHECKING:
    from services.pr.tests.test_host import Configuration, configuration as configuration
    from services.pr.tests.test_retained_live_cache import metadata_only
else:
    from test_host import Configuration, configuration as configuration
    from test_retained_live_cache import metadata_only


@pytest.mark.parametrize("withdraw", [False, True])
def test_host_does_not_replace_the_original_cache_checkpoint(
    configuration: Configuration, monkeypatch: pytest.MonkeyPatch, withdraw: bool,
) -> None:
    path, _, baseline, values = metadata_only(configuration)
    assert baseline.workspaces is not None
    cache = baseline.workspaces.cache(baseline.store())
    before = baseline.store().path.read_bytes()

    calls = []

    def original_checkpoint() -> None:
        calls.append("original")
        if withdraw:
            raise ValueError("original host cache authority withdrawn")

    def selected(self: WorkspaceConfig, store: ReplayStore) -> GenerationCache:
        return cache

    cache.checkpoint = original_checkpoint
    monkeypatch.setattr(WorkspaceConfig, "cache", selected)
    if withdraw:
        with pytest.raises(ValueError, match="host cache authority withdrawn"):
            terminal_transition.adopt(load_config(path))
        assert baseline.store().path.read_bytes() == before
    else:
        terminal_transition.adopt(load_config(path))
        assert cache.lookup(values[0]["scope"])["found"] is False
    assert calls and cache.checkpoint is original_checkpoint
