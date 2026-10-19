"""The public orchestrator drives real private controls and native route custody."""

from contextlib import ExitStack
import socket
import time

import pytest
from zdd import breadcrumb, publication_history, routing

from agent_pull_requests.watch_driver import WatchDriver
from agent_pull_requests.watch_control import WatchControlClient
from agent_pull_requests.watch_journal import WatchJournal
from agent_pull_requests.watch_selection import WatchSelection
from test_watch_control import _receiver
from test_watch_invocation import installed as installed  # noqa: F401 -- pytest fixture
from test_watch_journal import _manifest, _select

pytestmark = pytest.mark.timeout(180)


def _port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _driver(installed, journal, children, **kwargs):
    def spawn(port, instance_id):
        children.enter_context(_receiver(
            installed, instance_id=instance_id, publish_launch=True, bind_port=port,
        ))

    return WatchDriver(
        journal, spawn=spawn, pick_free_port=_port, timeout=120, **kwargs,
    )


def _begin(installed, guard, predecessor=None):
    installation, target, _, _ = installed
    return WatchJournal.begin(
        installation, guard, target=target, target_manifest=_manifest(target),
        predecessor=predecessor, environment={},
    )


def _active(installed, children):
    installation = installed[0]
    _, instance, endpoint = children.enter_context(_receiver(installed, active=True))
    candidate = routing.Endpoint(
        "127.0.0.1", endpoint[1], instance.pid, instance.runtime.runtime_version,
        generation=1, process_start_time=instance.start_time,
    )
    client = WatchControlClient(
        endpoint=endpoint, instance=instance, installation=lambda: installation,
        transaction=lambda: ("fixture bootstrap has no controller", ""),
        request_timeout=30,
    )
    _select(installation, installed[1])
    routing.publish_active_with_previous_guarded(
        installation.run_root, **{
            key: value for key, value in candidate.to_dict().items()
            if key != "generation"
        }, expected_active=None,
    )
    return client


def test_shared_cold_start_commits_before_effects(installed):
    installation = installed[0]
    with ExitStack() as children, installation.transaction(timeout=1, environment={}) as guard:
        first = _begin(installed, guard)
        assert not installation.state_root.exists()
        driver = _driver(installed, first, children)
        result = driver.run()
        assert result.ok and result.committed
        assert first._document["phase"] == "complete"
        health = driver.successor.health()
        assert all(health[key] is True for key in ("writerHeld", "readReady", "effectsEnabled"))
        before = routing.read_table(installation.run_root, strict=True)
        assert before["active"]["pid"] == driver.successor.instance.pid
        assert before["active"]["generation"] == 1
        assert publication_history.read(before) is not None
        assert len(before[publication_history.KEY]["entries"]) == 1


def test_archival_past_cutover_deadline_never_reports_success(installed, monkeypatch):
    installation = installed[0]
    with ExitStack() as children, installation.transaction(timeout=1, environment={}) as guard:
        journal = _begin(installed, guard)
        driver = _driver(installed, journal, children)
        archive = journal.archive

        def late_archive():
            archive()
            driver.deadline = time.monotonic() - 1

        monkeypatch.setattr(journal, "archive", late_archive)
        with pytest.raises(TimeoutError, match="cutover deadline expired"):
            driver.run()
        assert journal._document["phase"] == "complete"
        assert driver.successor.health()["effectsEnabled"] is True


def test_shared_rollover_retires_exact_old_before_completing(installed):
    installation = installed[0]
    with ExitStack() as children, installation.transaction(timeout=1, environment={}) as guard:
        initial = _active(installed, children)
        before = routing.read_table(installation.run_root, strict=True)
        second = _begin(installed, guard, initial.instance)
        second.bind_endpoint("predecessor", initial._endpoint, read_health=initial.health)
        successor = _driver(installed, second, children)
        result = successor.run()
        assert result.ok and result.committed
        assert second._document["phase"] == "complete"
        assert second._document["exited"] == ["predecessor"]
        after = routing.read_table(installation.run_root, strict=True)
        assert after["active"]["pid"] == successor.successor.instance.pid
        assert after["active"]["generation"] > before["active"]["generation"]
        assert all(entry in after[publication_history.KEY]["entries"]
                   for entry in before[publication_history.KEY]["entries"])
        assert successor.successor.health()["effectsEnabled"] is True


def test_cold_prepublication_failure_stops_reserved_child_before_restoring_selection(installed, monkeypatch):
    installation = installed[0]
    with ExitStack() as children, installation.transaction(timeout=1, environment={}) as guard:
        journal = _begin(installed, guard)
        original = dict(journal._document["prior"])
        monkeypatch.setattr(WatchSelection, "publish", lambda _: (
            (_ for _ in ()).throw(OSError("fixture selection publication failure"))
        ))
        result = _driver(installed, journal, children).run()
        assert result.rolled_back and not result.ok
        assert journal._document["phase"] == "rolled-back"
        assert journal._document["exited"] == ["successor"]
        assert journal._document["prior"] == original
        assert not (installation.run_root / "active.json").exists()


def test_native_reassert_preserves_history_and_owner_commit_retirement_contract(installed, monkeypatch):
    installation = installed[0]
    with ExitStack() as children, installation.transaction(timeout=1, environment={}) as guard:
        initial = _active(installed, children)
        journal = _begin(installed, guard, initial.instance)
        journal.bind_endpoint("predecessor", initial._endpoint, read_health=initial.health)
        driver = _driver(installed, journal, children)
        health = driver._health
        failed = False

        def interrupted(host, port):
            nonlocal failed
            if journal._document["phase"] == "publishing":
                table = routing.read_table(installation.run_root, strict=True)
                if table and table.get("active", {}).get("pid") != initial.instance.pid:
                    if not failed:
                        failed = True
                        return False
                    if port == initial._endpoint[1]:
                        return False
            return health(host, port)

        monkeypatch.setattr(driver, "_health", interrupted)
        result = driver.run()
        assert result.ok and result.committed and not result.rolled_back
        assert failed
        assert journal._document["phase"] == "complete"
        assert journal._document["exited"] == ["predecessor"]
        table = routing.read_table(installation.run_root, strict=True)
        assert table["active"]["generation"] == 3
        assert len(table[publication_history.KEY]["entries"]) == 3
        assert driver.successor.health()["effectsEnabled"] is True
        assert breadcrumb.read_breadcrumb(installation.run_root) is None
