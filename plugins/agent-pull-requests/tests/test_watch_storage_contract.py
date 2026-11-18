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


def partitioned_state(**changes: Any) -> str:
    return json.dumps({
        "schema": "agent-pull-requests.watch-subscriptions",
        "version": 2,
        "activeStateVersion": 1,
        "engineGeneration": 1,
        "stateByVersion": {
            "1": {"subscriptions": [], "retained": "selected-extension"},
            "2": {"opaque": {"pending": ["event-1"], "attempts": 3}},
        },
        **changes,
    })


def test_versioned_legacy_partition_preserves_other_versions_and_never_downgrades(
    tmp_path: Path,
) -> None:
    path = watch_storage.subscriptions_path(tmp_path)
    original = json.loads(partitioned_state())
    path.write_text(json.dumps(original), encoding="utf-8")
    assert watch_storage.read_subscriptions_state(tmp_path) == []
    entries = [{"subscriber_id": "legacy", "notify": {"argv": ["callback"]}}]
    watch_storage.write_subscriptions_state(entries, tmp_path)
    assert watch_storage.read_subscriptions_state(tmp_path) == entries
    expected = json.loads(partitioned_state())
    expected["stateByVersion"]["1"]["subscriptions"] = entries
    assert json.loads(path.read_text(encoding="utf-8")) == expected
    watch_storage.write_subscriptions_state([], tmp_path)
    assert json.loads(path.read_text(encoding="utf-8")) == original


@pytest.mark.parametrize("changes", [
    {"engineGeneration": 2}, {"engineGeneration": True}, {"engineGeneration": "1"},
    {"activeStateVersion": 2}, {"activeStateVersion": True},
    {"stateByVersion": {"1": {"subscriptions": {}}}},
    {"stateByVersion": {"2": {"subscriptions": []}}},
])
def test_versioned_state_refuses_incompatible_executor_or_active_partition(
    tmp_path: Path, changes: dict[str, Any],
) -> None:
    path = watch_storage.subscriptions_path(tmp_path)
    original = partitioned_state(**changes).encode()
    path.write_bytes(original)
    with pytest.raises(ValueError, match="persisted watch subscriptions"):
        watch_storage.read_subscriptions_state(tmp_path)
    with pytest.raises(OSError, match="persisted watch subscriptions"):
        watch_storage.write_subscriptions_state([], tmp_path)
    assert path.read_bytes() == original


def test_final_write_never_replaces_a_concurrently_changed_partition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = watch_storage.subscriptions_path(tmp_path)
    path.write_text(partitioned_state(), encoding="utf-8")
    newer = json.loads(partitioned_state())
    newer["stateByVersion"]["2"]["opaque"]["pending"].append("event-2")
    original = json.dumps(newer).encode()
    fsync = os.fsync

    def replace_before_commit(fd: int) -> None:
        fsync(fd)
        path.write_bytes(original)

    monkeypatch.setattr(os, "fsync", replace_before_commit)
    with pytest.raises(OSError, match="persisted watch subscriptions"):
        watch_storage.write_subscriptions_state([], tmp_path)
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]


def test_delayed_activation_rechecks_executor_generation_before_effects(tmp_path: Path) -> None:
    def no_effect(*_args: Any) -> NoReturn:
        pytest.fail("older executor must refuse before observation or delivery")

    daemon = WatchDaemon(fetch=no_effect, notify=no_effect, state_root=tmp_path, active=False)
    try:
        daemon.reload_state()
        path = watch_storage.subscriptions_path(tmp_path)
        original = partitioned_state(engineGeneration=2).encode()
        path.write_bytes(original)
        with pytest.raises(ValueError, match="persisted watch subscriptions"):
            daemon.resume_effects()
        assert path.read_bytes() == original
        assert daemon.quiescent and not daemon.effects_enabled
        assert not daemon._pollers and daemon._deliveries.scheduler is None
    finally:
        daemon.close()


def test_client_self_boot_refuses_newer_engine_generation_before_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agent_procutil
    from agent_pull_requests import __main__ as cli

    monkeypatch.setattr(cli, "_WATCH_INVOCATION", None)
    monkeypatch.setenv("AGENT_PULL_REQUESTS_HOME", str(tmp_path))
    path = watch_storage.subscriptions_path(tmp_path)
    original = partitioned_state(engineGeneration=2).encode()
    path.write_bytes(original)
    monkeypatch.setattr(
        agent_procutil, "spawn_daemon", lambda *_args, **_kwargs: pytest.fail("must not spawn"),
    )
    with pytest.raises(ValueError, match="persisted watch subscriptions"):
        cli._watch_boot()
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]


def test_cold_serve_refuses_newer_generation_before_resident_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import argparse
    from agent_pull_requests import __main__ as cli

    monkeypatch.setattr(cli, "_WATCH_INVOCATION", None)
    monkeypatch.setenv("AGENT_PULL_REQUESTS_HOME", str(tmp_path))
    path = watch_storage.subscriptions_path(tmp_path)
    original = partitioned_state(engineGeneration=2).encode()
    path.write_bytes(original)
    monkeypatch.setattr(
        cli, "_serve_legacy", lambda *_args: pytest.fail("must not admit a resident"),
    )
    with pytest.raises(ValueError, match="persisted watch subscriptions"):
        cli._cmd_serve(argparse.Namespace(passive_instance=None, port=0))
    assert path.read_bytes() == original


def test_governed_ensure_refuses_before_transaction_or_owner_discovery(tmp_path: Path) -> None:
    from types import SimpleNamespace
    from agent_pull_requests import watch_entry

    def forbidden(*_args: Any, **_kwargs: Any) -> NoReturn:
        pytest.fail("incompatible executor must not begin a transaction")

    installation = SimpleNamespace(state_root=tmp_path, transaction=forbidden)
    invocation: Any = SimpleNamespace(
        require_current=lambda: None, fresh_installation=lambda: installation,
    )
    path = watch_storage.subscriptions_path(tmp_path)
    original = partitioned_state(engineGeneration=2).encode()
    path.write_bytes(original)
    with pytest.raises(ValueError, match="persisted watch subscriptions"):
        watch_entry.ensure(invocation)
    assert path.read_bytes() == original


def test_restart_refuses_newer_generation_before_lease_or_shutdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import argparse
    import single_instance_lease
    from agent_pull_requests import __main__ as cli

    monkeypatch.setattr(cli, "_WATCH_INVOCATION", None)
    monkeypatch.setenv("AGENT_PULL_REQUESTS_HOME", str(tmp_path))
    path = watch_storage.subscriptions_path(tmp_path)
    original = partitioned_state(engineGeneration=2).encode()
    path.write_bytes(original)
    monkeypatch.setattr(
        single_instance_lease, "SingleInstance",
        lambda *_args, **_kwargs: pytest.fail("must not acquire custody or stop a newer owner"),
    )
    with pytest.raises(ValueError, match="persisted watch subscriptions"):
        cli._cmd_serve_restart(argparse.Namespace(json=True))
    assert path.read_bytes() == original


def test_restart_epoch_change_at_lease_probe_cannot_stop_incumbent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import argparse
    import single_instance_lease
    from agent_pull_requests import __main__ as cli

    monkeypatch.setattr(cli, "_WATCH_INVOCATION", None)
    monkeypatch.setenv("AGENT_PULL_REQUESTS_HOME", str(tmp_path))
    path = watch_storage.subscriptions_path(tmp_path)
    path.write_text("[]", encoding="utf-8")
    newer = partitioned_state(engineGeneration=2).encode()

    class Lease:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def acquire(self) -> NoReturn:
            path.write_bytes(newer)
            raise single_instance_lease.AlreadyRunningError(tmp_path / "lease", 123)

    daemon = WatchDaemon(fetch=lambda *_args: pytest.fail("must not fetch"), state_root=tmp_path)
    requests: list[dict[str, Any]] = []

    def request(kind: str, payload: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        assert kind == "shutdown"
        requests.append(payload)
        return daemon.compute(kind, payload)

    monkeypatch.setattr(single_instance_lease, "SingleInstance", Lease)
    monkeypatch.setattr(cli, "_watch_request", request)
    monkeypatch.setattr(cli, "_watch_boot", lambda: pytest.fail("must not boot"))
    try:
        assert cli._cmd_serve_restart(argparse.Namespace(json=True)) == 1
        assert requests == [{
            "repo": "", "number": 0, "restart_engine_generation": 1,
        }]
        assert not daemon._shutdown_event.is_set()
        assert not daemon._lifecycle_shutdown_event.is_set()
        assert path.read_bytes() == newer
    finally:
        daemon.close()


@pytest.mark.parametrize("requester", [None, True, 0, 1])
def test_newer_incumbent_refuses_invalid_or_older_restart_requester(
    monkeypatch: pytest.MonkeyPatch, requester: object,
) -> None:
    monkeypatch.setattr(watch_storage, "WATCH_ENGINE_GENERATION", 2)
    daemon = WatchDaemon(fetch=lambda *_args: pytest.fail("must not fetch"), persist=False)
    try:
        result = daemon.compute("shutdown", {"restart_engine_generation": requester})
        assert "error" in result and not result.get("shutting_down")
        assert not daemon._shutdown_event.is_set()
        assert not daemon._lifecycle_shutdown_event.is_set()
    finally:
        daemon.close()


def test_newer_requester_can_stop_an_older_incumbent_without_changing_legacy_stop() -> None:
    for payload in ({}, {"restart_engine_generation": 2}):
        daemon = WatchDaemon(fetch=lambda *_args: pytest.fail("must not fetch"), persist=False)
        try:
            assert daemon.compute("shutdown", payload) == {"shutting_down": True}
            assert daemon._shutdown_event.is_set()
        finally:
            daemon.close()


@pytest.mark.parametrize("command", ["serve", "restart"])
def test_actual_cli_process_refuses_newer_generation_without_state_or_route_writes(
    tmp_path: Path, command: str,
) -> None:
    import subprocess
    import sys
    from agent_procutil import no_window_kwargs

    path = watch_storage.subscriptions_path(tmp_path)
    original = partitioned_state(engineGeneration=2).encode()
    path.write_bytes(original)
    result = subprocess.run(
        [sys.executable, "-m", "agent_pull_requests", command],
        env={**os.environ, "AGENT_PULL_REQUESTS_HOME": str(tmp_path)},
        capture_output=True, text=True, timeout=10, **no_window_kwargs(),
    )
    assert result.returncode != 0
    assert "persisted watch subscriptions require a newer engine generation" in result.stderr
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]


def test_versioned_restore_rejects_complete_invalid_candidate_before_effects(tmp_path: Path) -> None:
    data = json.loads(partitioned_state())
    data["stateByVersion"]["1"]["subscriptions"] = [{
        "repo": "example/project", "number": 1, "subscriber_id": "valid",
        "until": [MERGED], "notify": {},
    }, {"subscriber_id": "invalid"}]
    original = json.dumps(data).encode()
    path = watch_storage.subscriptions_path(tmp_path)
    path.write_bytes(original)

    def no_effect(*_args: Any) -> NoReturn:
        pytest.fail("partial restore must not start observation or delivery")

    with pytest.raises(ValueError, match="persisted|registration"):
        WatchDaemon(fetch=no_effect, notify=no_effect, state_root=tmp_path)
    assert path.read_bytes() == original


@pytest.mark.parametrize("fails", [False, True])
def test_cli_invocation_context_does_not_leak_into_the_next_executor(
    monkeypatch: pytest.MonkeyPatch, fails: bool,
) -> None:
    import argparse
    from agent_pull_requests import __main__ as cli
    from agent_pull_requests.watch_invocation import WatchInvocation

    sentinel = object()
    marker = RuntimeError("handler failed")
    monkeypatch.setattr(cli, "_WATCH_INVOCATION", None)
    monkeypatch.setattr(WatchInvocation, "capture", lambda **_kwargs: sentinel)

    def handler(_args: argparse.Namespace) -> int:
        assert cli._WATCH_INVOCATION is sentinel
        if fails:
            raise marker
        return 0

    monkeypatch.setattr(cli, "_cmd_status", handler)
    argv = ["status", "--repo", "example/project", "--number", "1"]
    if fails:
        with pytest.raises(RuntimeError) as caught:
            cli.main(argv)
        assert caught.value is marker
    else:
        assert cli.main(argv) == 0
    assert cli._WATCH_INVOCATION is None


def test_versioned_partition_adoption_preserves_pending_identity_without_delivery(
    tmp_path: Path,
) -> None:
    from agent_pull_requests.watch_contract import PRSnapshot
    from agent_pull_requests.watch_registry import WatchKey, WatchRegistry
    from agent_pull_requests.watch_writer import WatchWriter

    registry = WatchRegistry()
    key = WatchKey("example/project", 1)
    registry.register(
        key, "retained", until=(MERGED,), acknowledged=True,
        notify={"argv": ["fixture-callback"]},
    )
    registry.apply_snapshot(key, PRSnapshot(merged=True, pr_state="closed"))
    entries = registry.snapshot_state()
    document = json.loads(partitioned_state())
    document["stateByVersion"]["1"]["subscriptions"] = entries
    path = watch_storage.subscriptions_path(tmp_path)
    path.write_text(json.dumps(document), encoding="utf-8")

    def no_effect(*_args: Any) -> NoReturn:
        pytest.fail("passive adoption must not observe or deliver")

    writer = WatchWriter(root=tmp_path, authorize=lambda _operation: None, fetch=no_effect)
    try:
        writer.adopt(timeout=2)
        restored = writer.daemon._registry.snapshot_state()[0]
        assert restored["registration_id"] == entries[0]["registration_id"]
        assert restored["pending"] == entries[0]["pending"]
        writer.daemon._persist()
        written = json.loads(path.read_text(encoding="utf-8"))
        assert written["stateByVersion"]["2"] == document["stateByVersion"]["2"]
        assert written["stateByVersion"]["1"]["subscriptions"][0]["pending"] == (
            entries[0]["pending"]
        )
        assert writer.read_ready and not writer.daemon.effects_enabled
    finally:
        writer.close(timeout=2)


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
