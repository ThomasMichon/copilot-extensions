"""The existing daemon samples owner-confirmed evidence, never attachment count."""

from __future__ import annotations

import importlib
from dataclasses import asdict, replace
import json
import subprocess
import sys

import pytest
from agent_procutil import no_window_kwargs

from worktree_manager.mux_mapping_registry import MuxMappingRegistry


NATIVE_ID = "00000000-0000-4000-8000-000000000001"


@pytest.fixture
def observer_module():
    return importlib.import_module("worktree_manager.restart_observer")


@pytest.fixture
def mapping(tmp_path):
    registry = MuxMappingRegistry(tmp_path / "mux-mapping.json")
    registry.register({
        "project": "example", "worktree_id": "worktree-one",
        "worktree_path": "/path/to/worktree-one", "mux_bin": "tmux",
        "mux_session": "wt-worktree-one", "session_incarnation": "mux:1:100",
        "mapping_revision": 1, "live": True, "attached_clients": 0,
    })
    return registry


def owner_result():
    return {
        "schema": "native-hosting-evidence", "version": 1,
        "confirmed": True, "native_session_id": NATIVE_ID,
        "native_incarnation": "native:original", "owner_revision": 1,
        "binding_proof": "inuse-hold-v1",
        "session_incarnation": "mux:1:100",
        "worktree_id": "worktree-one", "worktree_path": "/path/to/worktree-one",
        "mux_session": "wt-worktree-one",
        "platform": "linux", "liveness": "live", "automatic_eligible": False,
    }


def source_bound_owner(*, ended_at=None, end_reason=None, **changes):
    return {
        **owner_result(),
        "activation_ordinal": 1, "source_identity": "native:original",
        "lifecycle_qualified": True, "lifecycle_capability": "source-bound-v1",
        "lifecycle": {
            "schema": "native-activation-evidence", "version": 1,
            "native_session_id": NATIVE_ID, "owner_revision": 1,
            "activations": [{
                "ordinal": 1, "source_identity": "native:original",
                "source_ambiguous": False, "ended_at": ended_at,
                "end_reason": end_reason, "end_source": "hook" if ended_at else None,
            }],
        },
        **changes,
    }


def build_observer(module, tmp_path, **kwargs):
    return module.RestartObserver(
        tmp_path, epoch=module.HostEpoch("linux", "environment-one", "boot-one"),
        **kwargs,
    )


def test_detached_live_session_is_checkpointed(tmp_path, observer_module, mapping):
    observer = build_observer(
        observer_module, tmp_path, owner_probe=lambda *_: owner_result(),
    )
    observer.observe(mapping, now=10.0)
    row = observer.journal.snapshot()[0]
    assert row["last_positive"]["native_session_id"] == NATIVE_ID
    assert row["last_positive"]["session_incarnation"] == "mux:1:100"
    assert row["automatic_eligible"] is False


def test_failed_owner_probe_preserves_last_positive(tmp_path, observer_module, mapping):
    result = owner_result()
    observer = build_observer(observer_module, tmp_path, owner_probe=lambda *_: result)
    observer.observe(mapping, now=10.0)
    original = observer.journal.snapshot()[0]
    result.update(confirmed=False, liveness="unknown")
    observer.observe(mapping, now=100.0)
    assert observer.journal.snapshot()[0] == original
    assert observer.last_result["reason"] == "owner-unconfirmed"


def test_replacement_during_owner_probe_is_not_checkpointed(tmp_path, observer_module, mapping):
    def probe(*_):
        current = mapping.get("example", "worktree-one")
        current.update(session_incarnation="mux:2:200", mapping_revision=2)
        mapping.register(current)
        return owner_result()

    observer = build_observer(observer_module, tmp_path, owner_probe=probe)
    observer.observe(mapping, now=10.0)
    assert observer.journal.snapshot() == []
    assert observer.last_result["reason"] == "mapping-changed"


def test_cadence_is_independent_of_publication(tmp_path, observer_module, mapping):
    seen = []
    observer = build_observer(
        observer_module, tmp_path,
        owner_probe=lambda *_: seen.append(True) or owner_result(),
    )
    observer.observe(mapping, now=10.0)
    observer.observe(mapping, now=11.0)
    assert len(seen) == 1
    observer.observe(mapping, now=31.0)
    assert len(seen) == 2
    assert observer.journal.snapshot()[0]["last_positive"]["observation_sequence"] == 2


def test_reasonless_tombstone_cannot_erase_journal(tmp_path, observer_module, mapping):
    observer = build_observer(
        observer_module, tmp_path, owner_probe=lambda *_: owner_result(),
    )
    observer.observe(mapping, now=10.0)
    original = observer.journal.snapshot()[0]["last_positive"]
    mapping.remove("example", "worktree-one")
    observer.observe(mapping, now=100.0)
    assert observer.journal.snapshot()[0]["last_positive"] == original


def test_exact_lifecycle_closure_is_observed_after_mapping_tombstone(
    tmp_path, observer_module, mapping,
):
    result = source_bound_owner()
    calls = []

    def probe(*args):
        calls.append(args)
        return result

    observer = build_observer(observer_module, tmp_path, owner_probe=probe)
    observer.observe(mapping, now=10.0)
    original = observer.journal.snapshot()[0]["last_positive"]
    mapping.remove("example", "worktree-one")
    result = source_bound_owner(
        confirmed=False, ended_at="exit", end_reason="user_exit", liveness="dead",
    )
    observer.observe(mapping, now=100.0)
    row = observer.journal.snapshot()[0]
    assert row["last_positive"] == original
    assert row["disposition"] == "intentional"
    assert row["detail"] == "native-session-end"
    assert calls == [
        ("example", "worktree-one", None),
        ("example", "worktree-one", NATIVE_ID),
    ]
    assert row["automatic_eligible"] is False


@pytest.mark.parametrize("reason", ["complete", "error", "abort", "timeout", None])
def test_other_native_end_reasons_are_not_intentional(
    tmp_path, observer_module, mapping, reason,
):
    result = source_bound_owner()
    observer = build_observer(observer_module, tmp_path, owner_probe=lambda *_: result)
    observer.observe(mapping, now=10.0)
    result = source_bound_owner(confirmed=False, ended_at="end", end_reason=reason)
    observer.observe(mapping, now=100.0)
    assert observer.journal.snapshot()[0]["disposition"] == "unknown"


@pytest.mark.parametrize("count", [256, 257])
def test_retained_lifecycle_budget_accepts_latest_high_ordinal_exit(
    tmp_path, observer_module, mapping, count,
):
    result = source_bound_owner()
    ordinal = 1000
    result["activation_ordinal"] = ordinal
    result["owner_revision"] = ordinal
    result["lifecycle"]["owner_revision"] = ordinal
    result["lifecycle"]["activations"] = [{
        "ordinal": index, "source_identity": f"native:old-{index}",
        "source_ambiguous": False, "ended_at": "old-exit",
        "end_reason": "user_exit", "end_source": "hook",
    } for index in range(ordinal - count + 1, ordinal)]
    latest = {
        "ordinal": ordinal, "source_identity": "native:original",
        "source_ambiguous": False, "ended_at": None,
        "end_reason": None, "end_source": None,
    }
    result["lifecycle"]["activations"].append(latest)
    observer = build_observer(observer_module, tmp_path, owner_probe=lambda *_: result)
    observer.observe(mapping, now=10.0)
    before = observer.journal.snapshot()[0]
    observer.observe(mapping, now=50.0)
    if count == 256:
        assert observer.journal.snapshot()[0]["last_positive"]["observation_sequence"] == 2
    else:
        assert observer.journal.snapshot()[0] == before
        assert observer.last_result["reason"] == "evidence-unavailable"
    latest.update(ended_at="exit", end_reason="user_exit", end_source="hook")
    result["confirmed"] = False
    observer.observe(mapping, now=100.0)
    row = observer.journal.snapshot()[0]
    if count == 256:
        assert row["disposition"] == "intentional"
        assert row["last_positive"]["activation_ordinal"] == ordinal
    else:
        assert row == before
        assert "activation budget" in observer.last_result["error"]


@pytest.mark.parametrize("change", [
    {"ordinal": 2}, {"source_identity": "native:other"}, {"source_ambiguous": True},
    {"source_ambiguous": None},
])
def test_uncorroborated_lifecycle_cannot_retire_last_positive(
    tmp_path, observer_module, mapping, change,
):
    result = source_bound_owner()
    observer = build_observer(observer_module, tmp_path, owner_probe=lambda *_: result)
    observer.observe(mapping, now=10.0)
    before = observer.journal.snapshot()[0]
    result = source_bound_owner(confirmed=False, ended_at="end", end_reason="user_exit")
    result["lifecycle"]["activations"][0].update(change)
    observer.observe(mapping, now=100.0)
    assert observer.journal.snapshot()[0] == before


@pytest.mark.parametrize("change", [
    {"end_reason": ["user_exit"]}, {"end_source": ["hook"]},
    {"end_source": "hook\nunsafe"}, {"ended_at": []},
])
def test_invalid_end_metadata_is_reported_not_applied(
    tmp_path, observer_module, mapping, change,
):
    result = source_bound_owner()
    observer = build_observer(observer_module, tmp_path, owner_probe=lambda *_: result)
    observer.observe(mapping, now=10.0)
    before = observer.journal.snapshot()[0]
    result = source_bound_owner(confirmed=False, ended_at="end", end_reason="user_exit")
    result["lifecycle"]["activations"][0].update(change)
    observer.observe(mapping, now=100.0)
    assert observer.journal.snapshot()[0] == before
    assert observer.last_result["reason"] == "evidence-unavailable"
    assert "Invalid native lifecycle" in observer.last_result["error"]


def test_delayed_old_end_cannot_retire_same_uuid_replacement(
    tmp_path, observer_module, mapping,
):
    result = source_bound_owner()
    observer = build_observer(observer_module, tmp_path, owner_probe=lambda *_: result)
    observer.observe(mapping, now=10.0)
    result = source_bound_owner(
        native_incarnation="native:replacement", owner_revision=2,
        activation_ordinal=2, source_identity="native:replacement",
    )
    result["lifecycle"]["owner_revision"] = 2
    result["lifecycle"]["activations"].append({
        "ordinal": 2, "source_identity": "native:replacement",
        "source_ambiguous": False, "ended_at": None, "end_reason": None, "end_source": None,
    })
    observer.observe(mapping, now=100.0)
    result["lifecycle"]["activations"][0].update(
        ended_at="old-exit", end_reason="user_exit", end_source="hook",
    )
    observer.observe(mapping, now=200.0)
    row = observer.journal.snapshot()[0]
    assert row["disposition"] == "live"
    assert row["last_positive"]["activation_ordinal"] == 2
    assert row["last_positive"]["source_identity"] == "native:replacement"


def test_shutdown_notification_racing_native_end_wins_inside_journal_transaction(
    tmp_path, observer_module, mapping,
):
    result = source_bound_owner()
    observer = build_observer(observer_module, tmp_path, owner_probe=lambda *_: result)
    observer.observe(mapping, now=10.0)

    def probe(*_):
        observer.journal.shutdown_pending(
            "linux", "environment-one", "boot-one", notification_id="shutdown-one",
        )
        return source_bound_owner(
            confirmed=False, ended_at="exit", end_reason="user_exit",
        )

    observer.owner_probe = probe
    observer.observe(mapping, now=100.0)
    row = observer.journal.snapshot()[0]
    assert row["disposition"] == "shutdown-interrupted"
    assert row["notification_id"] == "shutdown-one"


def test_inferred_end_after_cancel_cannot_make_delayed_user_exit_intentional(
    tmp_path, observer_module, mapping,
):
    result = source_bound_owner()
    observer = build_observer(observer_module, tmp_path, owner_probe=lambda *_: result)
    observer.observe(mapping, now=10.0)
    observer.journal.shutdown_pending(
        "linux", "environment-one", "boot-one", notification_id="one",
    )
    observer.journal.shutdown_cancelled(
        "linux", "environment-one", "boot-one", notification_id="one",
    )
    result = source_bound_owner(confirmed=False, ended_at="inferred")
    result["lifecycle"]["activations"][0]["end_source"] = "inferred:next-start"
    observer.observe(mapping, now=50.0)
    result = source_bound_owner(confirmed=False, ended_at="exit", end_reason="user_exit")
    observer.observe(mapping, now=100.0)
    assert observer.journal.snapshot()[0]["disposition"] == "unknown"
    assert observer.journal.snapshot()[0]["detail"] == "native-end-after-cancel-unconfirmed"


def test_closed_unmapped_history_does_not_dilute_live_polling(
    tmp_path, observer_module, mapping,
):
    calls = []
    observer = build_observer(
        observer_module, tmp_path, owner_probe=lambda *args: calls.append(args) or owner_result(),
    )
    observer.observe(mapping, now=10.0)
    original = observer.journal.snapshot()[0]
    evidence = observer_module.PositiveEvidence(**original["last_positive"])
    for index in range(32):
        closed = replace(
            evidence, worktree_id=f"closed-{index:03d}",
            worktree_path=f"/path/to/closed-{index:03d}",
        )
        observer.journal.observe(closed)
        observer.journal.record_exit(closed, reason="intentional", detail="operator-stop")
    for now in (30.0, 50.0, 70.0):
        observer.observe(mapping, now=now)
    assert [args[1] for args in calls] == ["worktree-one"] * 4
    rows = observer.journal.snapshot()
    assert len(rows) == 33
    assert sum(row["disposition"] == "intentional" for row in rows) == 32
    assert rows[-1]["last_positive"]["observation_sequence"] == 4


def test_closed_subject_is_polled_when_a_live_mapping_returns(
    tmp_path, observer_module, mapping,
):
    result = owner_result()
    calls = []
    observer = build_observer(
        observer_module, tmp_path, owner_probe=lambda *args: calls.append(args) or result,
    )
    observer.observe(mapping, now=10.0)
    evidence = observer_module.PositiveEvidence(**observer.journal.snapshot()[0]["last_positive"])
    observer.journal.record_exit(evidence, reason="intentional", detail="operator-stop")
    entry = mapping.get("example", "worktree-one")
    mapping.remove("example", "worktree-one")
    observer.observe(mapping, now=30.0)
    assert len(calls) == 1
    entry["mapping_revision"] = mapping.get("example", "worktree-one")["mapping_revision"] + 1
    assert mapping.register(entry)["applied"]
    result = {**owner_result(), "native_incarnation": "native:replacement", "owner_revision": 2}
    observer.observe(mapping, now=50.0)
    assert len(calls) == 2
    row = observer.journal.snapshot()[0]
    assert row["disposition"] == "live"
    assert row["last_positive"]["native_incarnation"] == "native:replacement"


@pytest.mark.parametrize("reason", ["complete", "error", "abort", "timeout"])
def test_consumed_unknown_hook_end_history_does_not_dilute_live_polling(
    tmp_path, observer_module, mapping, reason,
):
    calls = []
    observer = build_observer(
        observer_module, tmp_path,
        owner_probe=lambda *args: calls.append(args) or source_bound_owner(),
    )
    observer.observe(mapping, now=10.0)
    evidence = observer_module.PositiveEvidence(**observer.journal.snapshot()[0]["last_positive"])
    for index in range(32):
        ended = replace(
            evidence, worktree_id=f"ended-{index:03d}",
            worktree_path=f"/path/to/ended-{index:03d}",
        )
        observer.journal.observe(ended)
        row = {"last_positive": asdict(ended)}
        assert observer._lifecycle_exit(source_bound_owner(
            confirmed=False, ended_at="end", end_reason=reason,
        ), row)["applied"]
    for now in (30.0, 50.0, 70.0):
        observer.observe(mapping, now=now)
    assert [args[1] for args in calls] == ["worktree-one"] * 4
    rows = observer.journal.snapshot()
    assert len(rows) == 33
    assert sum(row["disposition"] == "unknown" for row in rows) == 32
    assert sum(row["end_consumed"] for row in rows) == 32


def test_consumed_unknown_subject_returns_to_rotation_with_live_mapping(
    tmp_path, observer_module, mapping,
):
    result = source_bound_owner()
    calls = []
    observer = build_observer(
        observer_module, tmp_path, owner_probe=lambda *args: calls.append(args) or result,
    )
    observer.observe(mapping, now=10.0)
    observer._lifecycle_exit(
        source_bound_owner(confirmed=False, ended_at="end", end_reason="error"),
        observer.journal.snapshot()[0],
    )
    entry = mapping.get("example", "worktree-one")
    mapping.remove("example", "worktree-one")
    observer.observe(mapping, now=30.0)
    assert len(calls) == 1
    entry["mapping_revision"] = mapping.get("example", "worktree-one")["mapping_revision"] + 1
    assert mapping.register(entry)["applied"]
    result = source_bound_owner()
    result["native_incarnation"] = "native:replacement"
    result["source_identity"] = "native:replacement"
    result["activation_ordinal"] = 2
    result["owner_revision"] = 2
    result["lifecycle"]["activations"] = []
    result["lifecycle"]["owner_revision"] = 2
    observer.observe(mapping, now=50.0)
    assert len(calls) == 2
    row = observer.journal.snapshot()[0]
    assert row["disposition"] == "live"
    assert row["end_consumed"] is False


def test_inferred_end_still_gets_later_hook_corroboration(
    tmp_path, observer_module, mapping,
):
    result = source_bound_owner()
    calls = []
    observer = build_observer(
        observer_module, tmp_path, owner_probe=lambda *args: calls.append(args) or result,
    )
    observer.observe(mapping, now=10.0)
    mapping.remove("example", "worktree-one")
    result = source_bound_owner(confirmed=False, ended_at="inferred")
    result["lifecycle"]["activations"][0]["end_source"] = "inferred:next-start"
    observer.observe(mapping, now=30.0)
    assert observer.journal.snapshot()[0]["disposition"] == "unknown"
    assert observer.journal.snapshot()[0]["end_consumed"] is False
    result = source_bound_owner(confirmed=False, ended_at="end", end_reason="user_exit")
    observer.observe(mapping, now=50.0)
    assert len(calls) == 3
    assert observer.journal.snapshot()[0]["disposition"] == "intentional"


def test_owner_live_mux_incarnation_must_match_registry(tmp_path, observer_module, mapping):
    observer = build_observer(
        observer_module, tmp_path,
        owner_probe=lambda *_: {**owner_result(), "session_incarnation": "mux:replacement:200"},
    )
    observer.observe(mapping, now=10.0)
    assert observer.journal.snapshot() == []
    assert observer.last_result["applied"] is False


@pytest.mark.parametrize("change", [
    {"native_session_id": "not-a-uuid"},
    {"platform": "windows"},
    {"worktree_id": "other"},
    {"worktree_path": "/other/path"},
    {"mux_session": "other-mux"},
    {"schema": "other"},
    {"version": 2},
    {"version": True},
    {"native_incarnation": ""},
    {"owner_revision": True},
    {"binding_proof": None},
    {"binding_proof": "pid-name-only"},
    {"liveness": "unknown"},
])
def test_malformed_or_foreign_owner_evidence_is_visible(
    tmp_path, observer_module, mapping, change,
):
    observer = build_observer(
        observer_module, tmp_path, owner_probe=lambda *_: {**owner_result(), **change},
    )
    observer.observe(mapping, now=10.0)
    assert observer.journal.snapshot() == []
    assert observer.last_result["applied"] is False


def test_read_only_startup_keeps_previous_boot_evidence(tmp_path, observer_module, mapping):
    first = build_observer(
        observer_module, tmp_path, owner_probe=lambda *_: owner_result(),
    )
    first.observe(mapping, now=10.0)
    second = observer_module.RestartObserver(
        tmp_path, epoch=observer_module.HostEpoch("linux", "environment-one", "boot-two"),
        owner_probe=lambda *_: pytest.fail("startup inspection must not launch or probe"),
    )
    result = second.startup()
    assert result["candidates"][0]["reason"] == "previous-boot-unconfirmed"
    assert second.journal.snapshot()[0]["last_positive"]["boot_id"] == "boot-one"


def test_existing_runtime_owns_observer_not_a_second_worker(tmp_path, observer_module):
    from worktree_manager.mux_daemon import MuxDaemonRuntime

    runtime = MuxDaemonRuntime(tmp_path / "mux-mapping.json", root=tmp_path)
    assert isinstance(runtime.recovery_observer, observer_module.RestartObserver)
    assert runtime.recovery_observer.journal.path.is_relative_to(tmp_path)


def test_startup_inspection_precedes_status_server(tmp_path, observer_module, monkeypatch):
    from worktree_manager import mux_daemon

    events = []
    original = mux_daemon.start_server
    monkeypatch.setattr(observer_module, "native_epoch", lambda: observer_module.HostEpoch(
        "linux", "environment-one", "boot-one",
    ))
    startup = observer_module.RestartObserver.startup

    def inspect(self, **kwargs):
        events.append("inspect")
        return startup(self, **kwargs)

    def start(*args, **kwargs):
        assert events == ["inspect"]
        events.append("server")
        return original(*args, **kwargs)

    monkeypatch.setattr(observer_module.RestartObserver, "startup", inspect)
    monkeypatch.setattr(mux_daemon, "start_server", start)
    monkeypatch.setattr(mux_daemon, "_status_monitor_lock_path", lambda: None)
    assert mux_daemon.run_daemon_foreground(
        tmp_path, max_iterations=1, poll_interval_s=0,
    ) == 0
    assert events == ["inspect", "server"]


def test_unqualified_epoch_preserves_visible_stored_evidence(
    tmp_path, observer_module, mapping, monkeypatch,
):
    observer = build_observer(
        observer_module, tmp_path, owner_probe=lambda *_: owner_result(),
    )
    observer.observe(mapping, now=10.0)

    def unavailable():
        raise observer_module.RecoveryEvidenceError("unsupported epoch")

    monkeypatch.setattr(observer_module, "native_epoch", unavailable)
    fresh = observer_module.RestartObserver(tmp_path)
    report = fresh.startup()
    assert report["blocker"] == "unsupported epoch"
    assert report["candidates"][0]["reason"] == "epoch-unconfirmed"
    assert report["candidates"][0]["last_positive"]["native_session_id"] == NATIVE_ID


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_native_epoch_refuses_unqualified_platforms(observer_module, monkeypatch, platform):
    monkeypatch.setattr(observer_module.sys, "platform", platform)
    with pytest.raises(observer_module.RecoveryEvidenceError, match="not qualified"):
        observer_module.native_epoch()


@pytest.mark.parametrize("platform", ["linux", "wsl"])
def test_native_epoch_uses_native_boot_and_distro_init(
    observer_module, monkeypatch, platform,
):
    boot = "00000000-0000-4000-8000-000000000099"
    files = {
        "/proc/1/cgroup": "",
        "/proc/sys/kernel/random/boot_id": boot + "\n",
        "/etc/machine-id": "a" * 32 + "\n",
        "/proc/1/stat": "1 (init process) " + " ".join(["S", *(["0"] * 18), "321"]),
    }

    class NativePath:
        def __init__(self, value):
            self.value = value

        def exists(self):
            return False

        def read_text(self, **_kwargs):
            return files[self.value]

    monkeypatch.setattr(observer_module.sys, "platform", "linux")
    monkeypatch.setattr(observer_module, "Path", NativePath)
    monkeypatch.setattr(observer_module, "detect_platform", lambda: platform)
    epoch = observer_module.native_epoch()
    assert epoch.environment_id == "a" * 32
    assert epoch.platform == platform
    assert epoch.boot_id == (boot + ":321" if platform == "wsl" else boot)


def test_active_daemon_recovers_hot_journal_before_sampling(
    tmp_path, observer_module, mapping, monkeypatch, capsys,
):
    from worktree_manager import mux_daemon, self_install
    from worktree_manager.__main__ import main

    first = build_observer(
        observer_module, tmp_path, owner_probe=lambda *_: owner_result(),
    )
    first.observe(mapping, now=10.0)
    committed = first.journal.snapshot()[0]["last_positive"]
    crash = subprocess.run(
        [
            sys.executable, "-c",
            "import os,sqlite3,sys\n"
            "connection=sqlite3.connect(sys.argv[1])\n"
            "connection.execute('PRAGMA journal_mode=DELETE')\n"
            "connection.execute('PRAGMA synchronous=FULL')\n"
            "connection.execute('PRAGMA cache_size=1')\n"
            "connection.execute('PRAGMA cache_spill=ON')\n"
            "connection.execute('BEGIN IMMEDIATE')\n"
            "connection.execute('UPDATE journal SET payload=?', ('x'*65536,))\n"
            "for number in range(8):\n"
            " connection.execute('INSERT INTO journal(project,worktree_id,payload) "
            "VALUES (?,?,?)', ('uncommitted',str(number),'x'*65536))\n"
            "os._exit(23)\n",
            str(first.journal.path),
        ],
        capture_output=True, text=True, timeout=10, **no_window_kwargs(),
    )
    assert crash.returncode == 23, crash.stderr
    hot = first.journal.path.with_name(first.journal.path.name + "-journal")
    assert hot.is_file()
    readonly = build_observer(observer_module, tmp_path).startup()
    assert readonly.get("blocker")
    assert hot.is_file()
    before_report = (first.journal.path.read_bytes(), hot.read_bytes())
    monkeypatch.setattr(self_install, "default_root", lambda: tmp_path)
    assert main(["mux-daemon", "recovery-evidence"]) == 1
    assert "readonly" in json.loads(capsys.readouterr().out)["blocker"].lower()
    assert (first.journal.path.read_bytes(), hot.read_bytes()) == before_report

    observed = []

    class CapturingObserver(observer_module.RestartObserver):
        def __init__(self, root):
            super().__init__(
                root, epoch=observer_module.HostEpoch("linux", "environment-one", "boot-one"),
                owner_probe=lambda *_: owner_result(),
            )
            observed.append(self)

    monkeypatch.setattr(mux_daemon, "RestartObserver", CapturingObserver)
    monkeypatch.setattr(mux_daemon, "_status_monitor_lock_path", lambda: None)
    monkeypatch.setattr(mux_daemon.AttachedClientObserver, "observe", lambda *_: None)
    assert mux_daemon.run_daemon_foreground(
        tmp_path, passive=True, max_iterations=1, poll_interval_s=0,
    ) == 0
    assert observed[0].startup_result == {"candidates": [], "automatic_eligible": False}
    assert (first.journal.path.read_bytes(), hot.read_bytes()) == before_report
    assert mux_daemon.run_daemon_foreground(
        tmp_path, max_iterations=1, poll_interval_s=0,
    ) == 0
    assert not observed[1].startup_result.get("blocker")
    assert observed[1].startup_result["candidates"][0]["last_positive"] == committed
    latest = observed[1].journal.snapshot()
    assert len(latest) == 1
    assert latest[0]["last_positive"]["native_session_id"] == NATIVE_ID
    assert latest[0]["last_positive"]["observation_sequence"] == 2


def test_authorized_storage_recovery_is_retained_across_startup_retry(
    tmp_path, observer_module, mapping, monkeypatch,
):
    observer = build_observer(
        observer_module, tmp_path, owner_probe=lambda *_: owner_result(),
    )
    snapshot = observer.journal.snapshot
    attempts = []

    def interrupted(*, recover_existing=False):
        attempts.append(recover_existing)
        if len(attempts) == 1:
            raise observer_module.RecoveryEvidenceError("temporarily unavailable")
        return snapshot(recover_existing=recover_existing)

    monkeypatch.setattr(observer.journal, "snapshot", interrupted)
    assert observer.startup(recover_existing=True).get("blocker")
    observer.observe(mapping, now=10.0)
    assert attempts[:2] == [True, True]
    assert observer.last_result["applied"] is True
