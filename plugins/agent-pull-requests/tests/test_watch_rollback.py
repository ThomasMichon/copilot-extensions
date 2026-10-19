"""Owner rollback composes actual native CAS without undoing promotion history."""

from __future__ import annotations

import json
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest
from zdd import publication_history, routing, routing_persistence

from agent_pull_requests import watch_rollback
from agent_pull_requests.watch_journal import WatchJournal
from test_watch_journal import (
    _authorize, _begin, _ready, _select,
    cell as cell, passive as passive,  # noqa: F401 -- pytest fixtures
)

pytestmark = pytest.mark.timeout(180)


def _native_publish(installation, instance, port, *, expected=None):
    return routing.publish_active_with_previous_guarded(
        installation.run_root, bind="127.0.0.1", port=port, pid=instance.pid,
        version=instance.runtime.runtime_version, process_start_time=instance.start_time,
        expected_active=expected, demote_existing=True,
    )[0]


def _prepare(cell, passive, guard, *, flipped):
    installation, predecessor, _ = cell
    old = _native_publish(installation, predecessor, 12345)
    _select(installation, predecessor.runtime)
    journal = _begin(cell, guard)
    successor = passive[1]
    new = routing.Endpoint(
        "127.0.0.1", 23456, successor.pid, successor.runtime.runtime_version,
        old.generation + 1, successor.start_time,
    )
    journal.bind_successor(successor, route=json.dumps({"active": new.to_dict()}).encode())
    journal.advance("draining")
    if flipped:
        for phase in ("released", "read-ready", "publishing"):
            journal.advance(phase)
        assert _native_publish(installation, successor, new.port, expected=old) == new
    journal.rollback()
    return journal, old, new


def _exit(installation, journal, passive):
    child, successor = passive
    with successor.capture_exit(
        installation, read_health=lambda: {"instance": successor.evidence()}, environment={},
    ) as reference:
        child.stdin.write('{"operation":"exit"}\n')
        child.stdin.flush()
        journal.record_exit("successor", reference, timeout=5)
        assert child.wait(timeout=5) == 0


@pytest.mark.parametrize("flipped", [False, True])
def test_native_restoration_preserves_generation_history_and_terminal_custody(cell, passive, flipped):
    installation, predecessor, target = cell
    with installation.transaction(timeout=1, environment={}) as guard:
        journal, old, new = _prepare(cell, passive, guard, flipped=flipped)
        prior = dict(journal._document["prior"])
        before = routing.read_table(installation.run_root, strict=True)
        _exit(installation, journal, passive)
        restored = journal.restore_route(
            expected_active=new if flipped else old, deadline=time.monotonic() + 30,
        )
        after = routing.read_table(installation.run_root, strict=True)
        assert restored == replace(old, generation=(new if flipped else old).generation + 1)
        assert after["active"] == restored.to_dict()
        assert publication_history.read(after)["epoch"] == publication_history.read(before)["epoch"]
        assert all(
            entry in publication_history.read(after)["entries"]
            for entry in publication_history.read(before)["entries"]
        )
        if flipped:
            assert after["previous"] == new.to_dict()
            assert not publication_history.never_published(
                after, publication_history.read(before)["epoch"], pid=new.pid, port=new.port,
            )
        assert journal._document["prior"] == prior
        _authorize(installation, predecessor, journal, "resume")
        journal.finish(read_health=lambda: _ready(predecessor, effects=True))
        archive = journal.archive()
        recovered = WatchJournal.recover(installation, guard, environment={})
        assert recovered.archive() == archive
        following = WatchJournal.begin(
            installation, guard, target=target,
            target_manifest=bytes.fromhex(journal._document["targetManifest"]),
            predecessor=predecessor, environment={},
        )
        assert bytes.fromhex(following._document["prior"]["route"]) == (
            installation.run_root / "active.json"
        ).read_bytes()
        assert archive.exists()


@pytest.mark.parametrize("crash", ["intent", "history", "publication"])
def test_native_restoration_crash_recovers_durable_intent_without_erasing_history(
    cell, passive, monkeypatch, crash,
):
    installation, predecessor, _ = cell
    with installation.transaction(timeout=1, environment={}) as guard:
        journal, old, new = _prepare(cell, passive, guard, flipped=True)
        _exit(installation, journal, passive)
        write = routing_persistence.write
        calls = []

        def interrupted(path, table):
            calls.append(table)
            if crash == "intent":
                raise OSError("test-owned prepublication crash")
            write(path, table)
            if crash == "history" or len(calls) == 2:
                raise routing_persistence.PublicationCommitUncertain("test-owned lost acknowledgement")

        monkeypatch.setattr(routing_persistence, "write", interrupted)
        with pytest.raises(OSError):
            journal.restore_route(expected_active=new, deadline=time.monotonic() + 30)
        monkeypatch.setattr(routing_persistence, "write", write)
        recovered = WatchJournal.recover(installation, guard, environment={})
        if crash != "publication":
            with pytest.raises(PermissionError, match="restored route"):
                _authorize(installation, predecessor, recovered, "resume")
        actual = recovered.restore_route(expected_active=new, deadline=time.monotonic() + 30)
        assert actual == replace(old, generation=3)
        assert routing.read_table(installation.run_root, strict=True)["previous"] == new.to_dict()
        _authorize(installation, predecessor, recovered, "resume")
        recovered.finish(read_health=lambda: _ready(predecessor, effects=True))


def test_same_instance_republication_without_durable_intent_never_authorizes_resume(cell, passive):
    installation, predecessor, _ = cell
    with installation.transaction(timeout=1, environment={}) as guard:
        journal, old, new = _prepare(cell, passive, guard, flipped=True)
        _exit(installation, journal, passive)
        _native_publish(installation, predecessor, old.port, expected=new)
        for action in (
            lambda: _authorize(installation, predecessor, journal, "resume"),
            lambda: journal.finish(read_health=lambda: _ready(predecessor, effects=True)),
            lambda: journal.restore_route(
                expected_active=routing.read_active_endpoint(
                    installation.run_root, verify_listener=False,
                ), deadline=time.monotonic() + 30,
            ),
        ):
            with pytest.raises(PermissionError):
                action()
        assert journal._document["rollbackRoute"] is None


@pytest.mark.parametrize("drift", ["generation", "history", "previous", "manifest"])
def test_restored_selection_drift_never_authorizes_resume_or_terminal_completion(
    cell, passive, drift,
):
    installation, predecessor, _ = cell
    with installation.transaction(timeout=1, environment={}) as guard:
        journal, _, new = _prepare(cell, passive, guard, flipped=True)
        _exit(installation, journal, passive)
        journal.restore_route(expected_active=new, deadline=time.monotonic() + 30)
        path = installation.run_root / "active.json"
        table = routing.read_table(installation.run_root, strict=True)
        if drift == "generation":
            table["active"]["generation"] += 1
        elif drift == "history":
            table[publication_history.KEY]["entries"].pop()
        elif drift == "previous":
            table.pop("previous")
        else:
            (installation.plugin_root / "deploy-manifest.json").write_text("{}")
        path.write_text(json.dumps(table), encoding="utf-8")
        for action in (
            lambda: _authorize(installation, predecessor, journal, "resume"),
            lambda: journal.finish(read_health=lambda: _ready(predecessor, effects=True)),
        ):
            with pytest.raises(PermissionError, match="rollback selection"):
                action()


def test_restoration_requires_successor_exit_and_still_current_native_cas(cell, passive, monkeypatch):
    installation, predecessor, _ = cell
    with installation.transaction(timeout=1, environment={}) as guard:
        journal, old, new = _prepare(cell, passive, guard, flipped=True)
        before = (installation.run_root / "active.json").read_bytes()
        with pytest.raises(PermissionError, match="exit"):
            journal.restore_route(expected_active=new, deadline=time.monotonic() + 30)
        assert (installation.run_root / "active.json").read_bytes() == before
        _exit(installation, journal, passive)
        publish = watch_rollback.routing.publish_active_with_previous_guarded

        def concurrent_publication(*args, **kwargs):
            routing.publish_active(
                installation.run_root, bind=old.bind, port=old.port, pid=predecessor.pid,
                version=old.version, process_start_time=old.process_start_time,
            )
            return publish(*args, **kwargs)

        monkeypatch.setattr(watch_rollback.routing, "publish_active_with_previous_guarded", concurrent_publication)
        with pytest.raises(PermissionError, match="changed before"):
            journal.restore_route(expected_active=new, deadline=time.monotonic() + 30)
        assert journal._document["rollbackRoute"] is None


@pytest.mark.parametrize("late", ["preflight", "intent", "publication"])
def test_late_restoration_is_not_success_and_keeps_exact_native_recovery_custody(
    cell, passive, monkeypatch, late,
):
    installation, _, _ = cell
    with installation.transaction(timeout=1, environment={}) as guard:
        journal, old, new = _prepare(cell, passive, guard, flipped=True)
        _exit(installation, journal, passive)
        readings = iter({
            "preflight": [0, 2], "intent": [0, 0, 2], "publication": [0, 0, 0, 2],
        }[late])
        with monkeypatch.context() as patched:
            patched.setattr(watch_rollback, "time", SimpleNamespace(monotonic=lambda: next(readings)))
            with pytest.raises(TimeoutError, match="deadline expired"):
                journal.restore_route(expected_active=new, deadline=1)
        recovered = WatchJournal.recover(installation, guard, environment={})
        assert (recovered._document["rollbackRoute"] is None) is (late == "preflight")
        restored = recovered.restore_route(expected_active=new, deadline=time.monotonic() + 30)
        assert restored == replace(old, generation=3)
