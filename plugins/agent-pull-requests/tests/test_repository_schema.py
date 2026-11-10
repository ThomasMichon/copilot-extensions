"""Explicit fresh adoption and truthful rollback of the ReplayStore extension."""

import json
import time

import pytest

from agent_pull_requests.watch_replay_store import ReplayStore, ReplayStoreInvalid


def test_fresh_projection_uses_same_owner_and_old_reader_refuses(tmp_path):
    store = ReplayStore(tmp_path, "namespace", repository_cache=True)
    store.provision()
    document = json.loads(store.path.read_bytes())
    assert document["schema"] == "agent-pull-requests.owner-store"
    assert document["repository_cache_version"] == 1
    assert document["repository_cache"]["events"] == []
    assert store.observation_targets(expires_at=int(time.time()) + 60) == ()
    original = store.path.read_bytes()
    with pytest.raises(ReplayStoreInvalid):
        ReplayStore(tmp_path, "namespace").observation_targets(expires_at=int(time.time()) + 60)
    assert store.path.read_bytes() == original
    with pytest.raises(FileExistsError):
        store.provision()


def test_old_layout_is_not_implicitly_upgraded_and_supported_rollback_stays_valid(tmp_path):
    old = ReplayStore(tmp_path, "namespace")
    old.provision()
    original = old.path.read_bytes()
    with pytest.raises(ReplayStoreInvalid):
        ReplayStore(tmp_path, "namespace", repository_cache=True)._review_transaction(
            lambda document: None, expires_at=int(time.time()) + 60,
        )
    assert old.path.read_bytes() == original
    assert old.observation_targets(expires_at=int(time.time()) + 60) == ()


@pytest.mark.parametrize("change", [
    lambda document: document.update(repository_cache_version=True),
    lambda document: document.update(repository_cache_version=2),
    lambda document: document["repository_cache"].update(unrecognized=[]),
    lambda document: document["repository_cache"].update(next_sequence=True),
    lambda document: document["repository_cache"].update(events=[{}]),
])
def test_unknown_or_corrupt_layout_is_never_recreated(tmp_path, change):
    store = ReplayStore(tmp_path, "namespace", repository_cache=True)
    store.provision()
    value = json.loads(store.path.read_bytes())
    change(value)
    store.path.write_text(json.dumps(value))
    original = store.path.read_bytes()
    with pytest.raises((ValueError, KeyError)):
        store.observation_targets(expires_at=int(time.time()) + 60)
    assert store.path.read_bytes() == original
