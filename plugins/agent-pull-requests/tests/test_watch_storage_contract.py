"""Contract refusal through the real atomic storage and daemon mutation seams."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, NoReturn

import pytest

from agent_pull_requests import watch_storage
from agent_pull_requests.watch_contract import MERGED
from agent_pull_requests.watch_daemon import WatchDaemon

pytestmark = pytest.mark.contract("agent_pull_requests.watch.state_contract")


def future_state(**changes: Any) -> str:
    return json.dumps({
        "schema": "agent-pull-requests.watch-subscriptions",
        "version": 2,
        "subscriptions": {"retained": {"pending": {"event_id": "unchanged"}}},
        **changes,
    })


@pytest.mark.parametrize("raw", [
    future_state(), future_state(version=999), future_state(version=True),
    future_state(schema="other-schema"), future_state(version=1),
    "{}", "null", "{invalid",
])
def test_unknown_state_is_neither_imported_nor_overwritten(tmp_path: Path, raw: str) -> None:
    path = watch_storage.subscriptions_path(tmp_path)
    original = raw.encode()
    path.write_bytes(original)
    with pytest.raises(ValueError, match="persisted watch subscriptions"):
        watch_storage.read_subscriptions_state(tmp_path)
    with pytest.raises(OSError, match="persisted watch subscriptions"):
        watch_storage.write_subscriptions_state([], tmp_path)
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]


def test_write_rechecks_after_temporary_snapshot_is_synced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = watch_storage.subscriptions_path(tmp_path)
    watch_storage.write_subscriptions_state([], tmp_path)
    original = future_state().encode()
    fsync = os.fsync

    def replace_before_commit(fd: int) -> None:
        fsync(fd)
        path.write_bytes(original)

    monkeypatch.setattr(os, "fsync", replace_before_commit)
    with pytest.raises(OSError, match="persisted watch subscriptions"):
        watch_storage.write_subscriptions_state([{"subscriber_id": "late"}], tmp_path)
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("operation", ["register", "replace", "unregister"])
def test_running_owner_refuses_newer_disk_state_without_mutation_or_delivery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str,
) -> None:
    def no_effect(*_args: Any) -> NoReturn:
        pytest.fail("refused state must not start observation or delivery")

    monkeypatch.setattr(WatchDaemon, "_ensure_poller", lambda *_: None)
    daemon = WatchDaemon(fetch=no_effect, notify=no_effect, state_root=tmp_path)
    request: dict[str, Any] = {
        "repo": "example/project", "number": 1, "subscriber_id": "retained",
        "until": [MERGED], "notify": {},
    }
    try:
        assert daemon.compute("register", request) == {"registered": True}
        before = daemon._registry.snapshot_state()
        original = future_state().encode()
        path = watch_storage.subscriptions_path(tmp_path)
        path.write_bytes(original)
        if operation == "register":
            request = {**request, "subscriber_id": "new"}
        elif operation == "replace":
            request = {**request, "timeout": 1}
        with pytest.raises(OSError, match="persisted watch subscriptions"):
            daemon.compute("unregister" if operation == "unregister" else "register", request)
        assert daemon._registry.snapshot_state() == before
        assert path.read_bytes() == original
        assert daemon.status()["persistence_error"] == "state_write_failed"
        assert daemon.status()["legacy_callback_history"]["outcomes"] == []
        assert not daemon._pollers and daemon._deliveries.scheduler is None
    finally:
        daemon.close()


@pytest.mark.parametrize("active", [True, False])
def test_incompatible_state_refuses_cold_start_and_passive_adoption_before_effects(
    tmp_path: Path, active: bool,
) -> None:
    path = watch_storage.subscriptions_path(tmp_path)
    original = future_state().encode()
    path.write_bytes(original)

    def no_effect(*_args: Any) -> NoReturn:
        pytest.fail("incompatible state must refuse before effects")

    if active:
        with pytest.raises(ValueError, match="persisted watch subscriptions"):
            WatchDaemon(fetch=no_effect, notify=no_effect, state_root=tmp_path)
    else:
        daemon = WatchDaemon(
            fetch=no_effect, notify=no_effect, state_root=tmp_path, active=False,
        )
        try:
            with pytest.raises(ValueError, match="persisted watch subscriptions"):
                daemon.reload_state()
            with pytest.raises(RuntimeError, match="not been adopted"):
                daemon.resume_effects()
            assert daemon.quiescent and not daemon.effects_enabled
            assert not daemon._pollers and daemon._deliveries.scheduler is None
        finally:
            daemon.close()
    assert path.read_bytes() == original


def test_legacy_lists_stay_lists_and_lock_metadata_remains_independent(tmp_path: Path) -> None:
    assert watch_storage.read_subscriptions_state(tmp_path) == []
    entries = [{"subscriber_id": "legacy", "notify": {"argv": ["callback"]}}]
    watch_storage.write_subscriptions_state(entries, tmp_path)
    assert watch_storage.read_subscriptions_state(tmp_path) == entries
    assert json.loads(watch_storage.subscriptions_path(tmp_path).read_text()) == entries
    watch_storage.write_subscriptions_state([], tmp_path)
    assert watch_storage.read_subscriptions_state(tmp_path) == []
    watch_storage.subscriptions_path().write_text(future_state(), encoding="utf-8")
    watch_storage.write_lock_data({"watch_generation": "fixture-generation"})
    assert watch_storage.read_lock_data() == {"watch_generation": "fixture-generation"}
