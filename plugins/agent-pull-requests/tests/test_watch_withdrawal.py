"""Stop and restart retain exact native and durable terminal custody."""

import json
from contextlib import ExitStack
from types import SimpleNamespace
from uuid import uuid4

import pytest
from zdd import publication_history, routing
from zdd.process_exit import observe_original_exit

from agent_pull_requests.watch_journal import WatchJournal
from test_watch_driver import _begin, _driver
from test_watch_invocation import _PROBE, _probe, installed as installed  # noqa: F401
from test_watch_journal import _manifest

pytestmark = pytest.mark.timeout(180)

_ENTRY = _PROBE[:_PROBE.index("print(json.dumps({")] + """
from agent_pull_requests.watch_invocation import WatchInvocation
from agent_pull_requests import watch_entry
invocation = WatchInvocation(installation, runtime, str(installation.receipt))
"""
_STOP = _ENTRY + """
print(json.dumps({"stopped": watch_entry.stop(invocation),
                  "endpoint": watch_entry.dial(invocation)}), flush=True)
"""


def _start(installed, children):
    installation = installed[0]
    with installation.transaction(timeout=1, environment={}) as guard:
        journal = _begin(installed, guard)
        driver = _driver(installed, journal, children)
        assert driver.run().ok
    return journal, driver


def test_stop_proves_original_exit_and_retains_generation_history(installed):
    installation = installed[0]
    with ExitStack() as children:
        journal, driver = _start(installed, children)
        before = routing.read_table(installation.run_root, strict=True)
        result = _probe(installed, script=_STOP)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {"stopped": True, "endpoint": None}
        instance = driver.successor.instance
        assert observe_original_exit(instance.pid, instance.start_time) is True
        after = routing.read_table(installation.run_root, strict=True)
        assert "active" not in after and after["previous"] == before["active"]
        assert publication_history.read(after) == publication_history.read(before)
        custody = json.loads((installation.run_root / "watch-owner-withdrawal.json").read_text())
        assert custody["phase"] == "complete" and custody["originId"] == journal.transaction_id
        before_replay = (installation.run_root / "watch-owner-withdrawal.json").read_bytes()
        replay = _probe(installed, script=_STOP)
        assert replay.returncode == 0, replay.stderr
        assert json.loads(replay.stdout) == {"stopped": False, "endpoint": None}
        assert (installation.run_root / "watch-owner-withdrawal.json").read_bytes() == before_replay


@pytest.mark.parametrize("point", ["before-route", "after-route"])
def test_stop_after_failed_rollover_retires_restored_predecessor(installed, monkeypatch, point):
    from agent_pull_requests.watch_selection import WatchSelection

    installation = installed[0]
    with ExitStack() as children:
        _, first = _start(installed, children)
        with installation.transaction(timeout=1, environment={}) as guard:
            journal = _begin(installed, guard, first.successor.instance)
            journal.bind_endpoint(
                "predecessor", first.successor._endpoint, read_health=first.successor.health,
            )
            driver = _driver(installed, journal, children)

            def fail(*_args, **_kwargs):
                raise OSError("fixture failed rollover before owner commit")

            health = driver._health

            def fail_after_route(host, port):
                table = routing.read_table(installation.run_root, strict=True)
                if (
                    journal._document["phase"] == "publishing"
                    and table["active"]["pid"] != first.successor.instance.pid
                    and port != first.successor._endpoint[1]
                ):
                    return False
                return health(host, port)

            with monkeypatch.context() as failure:
                if point == "before-route":
                    failure.setattr(WatchSelection, "publish", fail)
                else:
                    failure.setattr(driver, "_health", fail_after_route)
                result = driver.run()
            assert result.rolled_back and not result.ok
            assert journal._document["phase"] == "rolled-back"
            before = routing.read_table(installation.run_root, strict=True)
            assert before["active"]["pid"] == first.successor.instance.pid
            if point == "after-route":
                assert journal._document["rollbackRoute"] is not None
                assert before["active"]["generation"] == 3
            assert first.successor.health()["effectsEnabled"] is True
        reference = first.successor.capture_exit()
        try:
            stopped = _probe(installed, script=_STOP)
            assert stopped.returncode == 0, stopped.stderr
            assert json.loads(stopped.stdout) == {"stopped": True, "endpoint": None}
            assert reference.wait(30)
        finally:
            reference.close()
        after = routing.read_table(installation.run_root, strict=True)
        assert "active" not in after and after["previous"] == before["active"]
        assert publication_history.read(after) == publication_history.read(before)


@pytest.mark.parametrize("point", ["after-control", "before-route", "after-route", "before-archive"])
def test_interrupted_stop_recovers_without_respawn_or_pid_cleanup(installed, point):
    installation = installed[0]
    script = _ENTRY + """
from agent_pull_requests import watch_withdrawal
from zdd import routing
""" + ({
        "before-route": """
def fail(*args, **kwargs):
    raise OSError("fixture interrupted before native withdrawal")
routing.withdraw_active_guarded = fail
""",
        "after-control": """
from agent_pull_requests.watch_control import WatchControlClient
control = WatchControlClient.control
def fail(self, operation, **kwargs):
    result = control(self, operation, **kwargs)
    if operation == "withdraw":
        raise OSError("fixture lost shutdown acknowledgement")
    return result
WatchControlClient.control = fail
""",
        "after-route": """
withdraw = routing.withdraw_active_guarded
def fail(*args, **kwargs):
    withdraw(*args, **kwargs)
    raise OSError("fixture ambiguous native withdrawal")
routing.withdraw_active_guarded = fail
""",
        "before-archive": """
persist = watch_withdrawal._persist
def fail(path, document, **kwargs):
    if path.parent.name == "watch-owner-withdrawals":
        raise OSError("fixture interrupted before withdrawal archive")
    return persist(path, document, **kwargs)
watch_withdrawal._persist = fail
""",
    }[point]) + """
watch_entry.stop(invocation)
"""
    with ExitStack() as children:
        _, driver = _start(installed, children)
        reference = driver.successor.capture_exit()
        try:
            result = _probe(installed, script=script)
            assert result.returncode != 0 and "fixture" in result.stderr
            assert reference.wait(30)
        finally:
            reference.close()
        instance = driver.successor.instance
        recovered = _probe(installed, script=_STOP + """
def forbidden_spawn(*args, **kwargs):
    raise AssertionError("stop recovery must not spawn")
watch_entry.spawn_daemon = forbidden_spawn
assert watch_entry.dial(invocation) is None
""")
        assert recovered.returncode == 0, recovered.stderr
        assert json.loads(recovered.stdout) == {"stopped": False, "endpoint": None}
        table = routing.read_table(installation.run_root, strict=True)
        assert table.get("active") is None and table["previous"]["pid"] == instance.pid
        assert json.loads((installation.run_root / "watch-owner-withdrawal.json").read_text())["phase"] == "complete"


def test_stop_then_real_restart_advances_generation_and_preserves_selection(installed):
    installation = installed[0]
    with ExitStack() as children:
        _, first = _start(installed, children)
        before = routing.read_table(installation.run_root, strict=True)
        result = _probe(installed, script=_STOP)
        assert result.returncode == 0, result.stderr
        with installation.transaction(timeout=1, environment={}) as guard:
            target = installed[1]
            journal = WatchJournal.begin(
                installation, guard, target=target, target_manifest=_manifest(target),
                environment={},
            )
            driver = _driver(installed, journal, children)
            assert driver.run().ok
        after = routing.read_table(installation.run_root, strict=True)
        assert after["active"]["generation"] > before["active"]["generation"]
        assert after["active"]["pid"] != first.successor.instance.pid
        assert all(item in after[publication_history.KEY]["entries"]
                   for item in before[publication_history.KEY]["entries"])
        assert driver.successor.health()["effectsEnabled"] is True
        final = _probe(installed, script=_STOP)
        assert final.returncode == 0, final.stderr


def test_restart_protected_publication_recovers_same_reserved_writer(installed, monkeypatch):
    from agent_pull_requests.watch_control import WatchControlClient
    from agent_pull_requests.watch_recovery import reconcile
    from test_watch_control import _receiver

    installation, target, _, _ = installed
    with ExitStack() as children:
        _start(installed, children)
        stopped = _probe(installed, script=_STOP)
        assert stopped.returncode == 0, stopped.stderr
        prior = routing.read_table(installation.run_root, strict=True)
        with installation.transaction(timeout=1, environment={}) as guard:
            journal = _begin(installed, guard)
            instance_id = str(uuid4())
            journal.reserve_successor(instance_id)
            _, instance, endpoint = children.enter_context(_receiver(installed, instance_id=instance_id))
            route = routing.Endpoint(
                "127.0.0.1", endpoint[1], instance.pid, target.runtime_version,
                generation=prior["previous"]["generation"] + 1,
                process_start_time=instance.start_time,
            )
            journal.bind_successor(instance, route=json.dumps({"active": route.to_dict()}).encode())
            client = WatchControlClient(
                endpoint=endpoint, instance=instance, installation=lambda: installation,
                transaction=lambda: (journal.transaction_id, journal.authorization),
            )
            journal.bind_endpoint("successor", endpoint, read_health=client.health)
            journal.advance("draining")
            journal.advance("released")
            client.control("adopt")
            journal.advance("read-ready")
            journal.advance("publishing")
            write = routing.routing_persistence.write

            def interrupt(path, value):
                if value.get("active") is not None:
                    raise OSError("fixture restart publication interrupted")
                write(path, value)

            with monkeypatch.context() as crash:
                crash.setattr(routing.routing_persistence, "write", interrupt)
                with pytest.raises(OSError, match="publication interrupted"):
                    routing.publish_active_with_previous_guarded(
                        installation.run_root, bind=route.bind, port=route.port, pid=route.pid,
                        version=route.version, process_start_time=route.process_start_time,
                        expected_active=None,
                    )
            protected = routing.read_table(installation.run_root, strict=True)
            recovered = WatchJournal.recover(installation, guard, environment={})
            reconcile(recovered, timeout=120)
            assert recovered._document["phase"] == "complete"
            assert recovered._document["successor"]["instanceId"] == instance_id
            assert client.health()["effectsEnabled"]
            after = routing.read_table(installation.run_root, strict=True)
            assert after["active"] == route.to_dict()
            assert after[publication_history.KEY] == protected[publication_history.KEY]


@pytest.mark.parametrize("unknown", ["held-writer", "native-route"])
def test_unknown_route_and_held_writer_are_not_absent_stop(installed, unknown):
    installation = installed[0]
    from test_watch_control import _receiver

    with ExitStack() as children:
        if unknown == "held-writer":
            children.enter_context(_receiver(installed, active=True))
            message = "AlreadyRunningError"
        else:
            installation.run_root.mkdir(parents=True)
            (installation.run_root / "active.json").write_text("{}")
            message = "no attributable owner journal"
        result = _probe(installed, script=_STOP)
        assert result.returncode != 0 and message in result.stderr
        assert not (installation.run_root / "watch-owner-withdrawal.json").exists()


def test_stop_refuses_guard_from_another_installation_before_any_write(installed, tmp_path):
    from agent_pull_requests.watch_withdrawal import finish
    from zdd.cutover_lock import CutoverLock

    installation = installed[0]
    invocation = SimpleNamespace(
        require_current=lambda: None, fresh_installation=lambda: installation,
    )
    with CutoverLock(tmp_path / "foreign-run") as guard:
        with pytest.raises(PermissionError, match="another controller"):
            finish(invocation, guard, {}, timeout=5)
    assert not (installation.run_root / "watch-owner-withdrawal.json").exists()


def test_withdraw_control_without_stop_custody_cannot_close_live_owner(installed):
    from agent_pull_requests.watch_control import WatchControlRefused

    with ExitStack() as children:
        _, driver = _start(installed, children)
        with pytest.raises(WatchControlRefused):
            driver.successor.control("withdraw")
        health = driver.successor.health()
        assert all(health[key] is True for key in ("writerHeld", "readReady", "effectsEnabled"))


def test_governance_revocation_after_quiescence_retains_native_writer(installed, monkeypatch):
    from agent_pull_requests.watch_writer import WatchWriter

    writer = WatchWriter(
        root=installed[0].state_root, authorize=lambda _: None, fetch=lambda *_: {},
    )
    writer.adopt(timeout=5)
    writer.activate(timeout=5)
    closed = writer.daemon.close

    def revoke(*, timeout):
        closed(timeout=timeout)
        writer.authorize = lambda _: (_ for _ in ()).throw(PermissionError("fixture revoked"))

    monkeypatch.setattr(writer.daemon, "close", revoke)
    try:
        with pytest.raises(PermissionError, match="revoked"):
            writer.withdraw(timeout=5)
        assert writer.writer_held
    finally:
        monkeypatch.setattr(writer.daemon, "close", closed)
        writer.close(timeout=5)
