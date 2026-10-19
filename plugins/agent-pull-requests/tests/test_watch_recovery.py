"""Pending owner custody recovers without interpreting a miss as absence."""

import json
from types import SimpleNamespace

import pytest
from single_instance_lease import AlreadyRunningError
from zdd import routing

from agent_pull_requests.watch_control import WatchControlClient
from agent_pull_requests.watch_journal import WatchJournal
from agent_pull_requests.watch_recovery import reconcile
from agent_pull_requests import watch_recovery
from test_watch_control import _receiver
from test_watch_driver import _begin
from test_watch_invocation import installed as installed  # noqa: F401 -- pytest fixture
from test_watch_journal import _select

pytestmark = pytest.mark.timeout(180)


@pytest.mark.parametrize("terminal", [False, True])
def test_reconciliation_late_archive_never_reports_success(installed, monkeypatch, terminal):
    installation = installed[0]
    with installation.transaction(timeout=1, environment={}) as guard:
        journal = _begin(installed, guard)
        if terminal:
            reconcile(journal, timeout=120)
        clock = [0.0]
        monkeypatch.setattr(watch_recovery, "time", SimpleNamespace(monotonic=lambda: clock[0]))
        archive = journal.archive

        def late_archive():
            result = archive()
            clock[0] = 121.0
            return result

        monkeypatch.setattr(journal, "archive", late_archive)
        with pytest.raises(TimeoutError, match="reconciliation deadline expired"):
            reconcile(journal, timeout=120)
        assert journal._document["phase"] == "complete"


def test_cold_metadata_recovery_never_launches_or_adopts_watch_state(installed):
    installation = installed[0]
    with installation.transaction(timeout=1, environment={}) as guard:
        original = _begin(installed, guard)
        recovered = WatchJournal.recover(installation, guard, environment={})
        reconcile(recovered, timeout=120)
        assert recovered.authorization != original.authorization
        assert recovered._document["phase"] == "complete"
        assert recovered._document["successor"] is None
        assert not installation.state_root.exists()
        assert not (installation.run_root / "active.json").exists()


def test_reserved_launch_without_native_receipt_stays_pending(installed):
    from uuid import uuid4

    installation = installed[0]
    with installation.transaction(timeout=1, environment={}) as guard:
        journal = _begin(installed, guard)
        journal.reserve_successor(str(uuid4()))
        with pytest.raises(PermissionError, match="launch remains unresolved"):
            journal.commit()
        recovered = WatchJournal.recover(installation, guard, environment={})
        with pytest.raises(FileNotFoundError, match="launch custody"):
            reconcile(recovered, timeout=120)
        assert recovered._document["phase"] == "prepared"
        assert recovered._document["exited"] == []
        assert not installation.state_root.exists()


def test_cold_transaction_refuses_actual_writer_even_without_a_route_or_receipt(installed):
    installation = installed[0]
    with _receiver(installed, active=True):
        with installation.transaction(timeout=1, environment={}) as guard:
            with pytest.raises(AlreadyRunningError):
                _begin(installed, guard)
            assert not (installation.run_root / "watch-owner-transaction.json").exists()
            assert not (installation.run_root / "active.json").exists()


@pytest.mark.parametrize("interruption", [
    "committed", "uncaptured-native-history", "foreign-native-history",
    "protected-native-history", "foreign-protected-history",
])
def test_committed_original_writer_recovers_forward_without_spawning(installed, interruption, monkeypatch):
    installation, target, _, _ = installed
    with installation.transaction(timeout=1, environment={}) as guard:
        journal = _begin(installed, guard)
        instance_id = None
        if interruption != "committed":
            from uuid import uuid4

            instance_id = str(uuid4())
            journal.reserve_successor(instance_id)
        with _receiver(installed, instance_id=instance_id) as receiver:
            instance, endpoint = receiver[1:]
            route = routing.Endpoint(
                "127.0.0.1", endpoint[1], instance.pid, target.runtime_version,
                generation=1, process_start_time=instance.start_time,
            )
            journal.bind_successor(instance, route=json.dumps({"active": route.to_dict()}).encode())
            journal.bind_endpoint("successor", endpoint, read_health=lambda: {"instance": instance.evidence()})
            client = WatchControlClient(
                endpoint=endpoint, instance=instance, installation=lambda: installation,
                transaction=lambda: (journal.transaction_id, journal.authorization),
                request_timeout=30,
            )
            journal.advance("draining")
            journal.advance("released")
            client.control("adopt")
            journal.advance("read-ready")
            journal.advance("publishing")
            _select(installation, target)
            def publish():
                return routing.publish_active_with_previous_guarded(
                    installation.run_root, bind=route.bind, port=route.port, pid=route.pid,
                    version=route.version, process_start_time=route.process_start_time,
                    expected_active=None,
                )

            protected = interruption in {"protected-native-history", "foreign-protected-history"}
            if protected:
                write = routing.routing_persistence.write

                def interrupted(path, value):
                    if value.get("active") is not None:
                        raise OSError("fixture crash after protected history")
                    write(path, value)

                with monkeypatch.context() as crash:
                    crash.setattr(routing.routing_persistence, "write", interrupted)
                    with pytest.raises(OSError, match="after protected history"):
                        publish()
            else:
                publish()
            before = routing.read_table(installation.run_root, strict=True)
            if interruption == "committed":
                journal.commit(read_health=client.health)
            else:
                with pytest.raises(PermissionError, match="captured native promotion history"):
                    journal.commit(read_health=lambda: pytest.fail("commit probed before custody"))
            assert not client.health()["effectsEnabled"]
            recovered = WatchJournal.recover(installation, guard, environment={})
            if interruption in {"foreign-native-history", "foreign-protected-history"}:
                from test_watch_installation import _mutate

                _mutate(installation.run_root / "active.json", lambda table: (
                    table["publication_history"]["entries"].append({
                        "bind": "127.0.0.1", "port": 1, "pid": 1,
                        "process_start_time": "1", "generation": 999,
                    })
                ))
                changed = (installation.run_root / "active.json").read_bytes()
                message = "protected promotion history" if protected else "captured promotion history"
                with pytest.raises(PermissionError, match=message):
                    reconcile(recovered, timeout=120)
                assert recovered._document["phase"] == "publishing"
                assert (installation.run_root / "active.json").read_bytes() == changed
                assert not client.health()["effectsEnabled"]
                return
            reconcile(recovered, timeout=120)
            assert recovered._document["phase"] == "complete"
            assert client.health()["effectsEnabled"]
            assert receiver[0].poll() is None
            after = routing.read_table(installation.run_root, strict=True)
            if protected:
                assert before.get("active") is None
                assert after["active"] == route.to_dict()
                assert after["publication_history"] == before["publication_history"]
                assert len(after["publication_history"]["entries"]) == 1
            else:
                assert after == before
            if interruption == "uncaptured-native-history":
                from test_watch_installation import _mutate

                _mutate(installation.run_root / "watch-owner-transaction.json", lambda document: (
                    document.update(targetRoute=json.dumps({"active": route.to_dict()}).encode().hex())
                ))
                with pytest.raises(PermissionError, match="committed successor lacks captured native"):
                    WatchJournal.recover(installation, guard, environment={})


@pytest.mark.parametrize("damage", ["missing", "foreign-instance", "foreign-endpoint"])
def test_bound_passive_endpoint_recovery_refuses_unattested_launch(installed, monkeypatch, damage):
    from agent_pull_requests.watch_custody import launch_path
    from agent_pull_requests.watch_journal import _selection
    from agent_pull_requests.watch_private import write_private

    installation = installed[0]
    with installation.transaction(timeout=1, environment={}) as guard:
        journal = _begin(installed, guard)
        from uuid import uuid4

        journal.reserve_successor(str(uuid4()))
        with (
            _receiver(
                installed, instance_id=journal._document["launchInstanceId"], publish_launch=True,
            ) as receiver,
            _receiver(installed) as peer,
        ):
            instance, endpoint = receiver[1:]
            route = routing.Endpoint(
                "127.0.0.1", endpoint[1], instance.pid, instance.runtime.runtime_version,
                process_start_time=instance.start_time,
            )
            journal.bind_successor(instance, route=json.dumps({"active": route.to_dict()}).encode())
            selected = _selection(installation)
            path = launch_path(installation, journal._document)
            launch = json.loads(path.read_text(encoding="utf-8"))
            if damage == "missing":
                path.unlink()
                expected, message = FileNotFoundError, "launch custody"
            else:
                if damage == "foreign-instance":
                    launch["instance"] = peer[1].evidence()
                    expected, message = PermissionError, "reserved child"
                else:
                    launch["endpoint"] = dict(zip(("host", "port", "token"), peer[2], strict=True))
                    expected, message = PermissionError, "health does not match"
                write_private(path, launch)
            recovered = WatchJournal.recover(installation, guard, environment={})
            monkeypatch.setattr(WatchControlClient, "control", lambda *_a, **_k: (
                pytest.fail("unattested launch must not reach lifecycle control")
            ))
            with pytest.raises(expected, match=message):
                reconcile(recovered, timeout=120)
            assert recovered._document["phase"] == "passive"
            assert recovered._document["endpoints"]["successor"] is None
            assert recovered._document["exited"] == []
            assert _selection(installation) == selected
            assert receiver[0].poll() is None and peer[0].poll() is None
