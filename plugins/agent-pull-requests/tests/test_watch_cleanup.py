"""Policy-off cleanup is exact passive retirement, never transaction completion."""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import pytest
from work_coalescing_singleton.client import DaemonUnavailable

from agent_pull_requests.watch_control import WatchControlRefused
from agent_pull_requests.watch_journal import WatchJournal
from test_watch_control import _begin, _client, _receiver, receiver as receiver  # noqa: F401
from test_watch_installation import _mutate, _policy
from test_watch_invocation import installed as installed  # noqa: F401
from test_watch_journal import _manifest, _route, _select
from test_watch_runtime import _complete, _instance

pytestmark = pytest.mark.timeout(90)


def _disable(installation, policy):
    if policy == "missing":
        (installation.os_profile / ".copilot-extensions" / "installation-mode.json").unlink()
    else:
        _policy(installation.os_profile, False)


@pytest.mark.parametrize("policy", ["missing", "disabled"])
@pytest.mark.parametrize("bound", [False, True])
@pytest.mark.timeout(180)
def test_off_after_spawn_recovers_exact_passive_without_completing(installed, policy, bound):
    installation, target, _, _ = installed
    predecessor = _instance(_complete(installation, version="1.0.0"))
    launch_id = str(uuid4())
    with installation.transaction(timeout=1, environment={}) as guard:
        journal = WatchJournal.begin(
            installation, guard, target=target, target_manifest=_manifest(target),
            predecessor=predecessor, environment={},
        )
        journal.reserve_successor(launch_id)
    # The journal exists before spawning. A crash here cannot erase child attribution.
    with _receiver(installed, instance_id=launch_id) as child:
        with installation.transaction(timeout=1, environment={}) as guard:
            journal = WatchJournal.recover(installation, guard, environment={})
            if bound:
                journal.bind_successor(child[1], route=_route(child[1]))
            _disable(installation, policy)
            with pytest.raises(PermissionError, match="governance"):
                journal.advance("draining")
            with pytest.raises(PermissionError, match="governance"):
                WatchJournal.recover(installation, guard, environment={})
        with installation.cleanup_transaction(timeout=1, environment={}) as guard:
            recovered = WatchJournal.recover_cleanup(installation, guard, environment={})
            assert recovered.authorization != journal.authorization
            assert recovered.transaction_id == journal.transaction_id
            client = _client(installation, child, recovered)
            with pytest.raises(PermissionError, match="governance"):
                client.health()
            with pytest.raises(PermissionError, match="governance"):
                client.control("adopt")
            old_capability = _client(installation, child, journal)
            with pytest.raises(WatchControlRefused):
                old_capability.cleanup_health()
            health = client.cleanup_health()
            assert not health["writerHeld"] and not health["effectsEnabled"]
            with client.capture_cleanup_exit() as reference:
                assert not reference.wait(0)
                if not bound:
                    recovered.bind_cleanup_successor(child[1], read_health=client.cleanup_health)
                client.cleanup_stop()
                recovered.record_cleanup_exit(reference, timeout=10)
                assert reference.wait(0)
            child[0].wait(timeout=10)
            assert recovered._document["phase"] == ("passive" if bound else "prepared")
            assert recovered._document["exited"] == ["successor"]
            assert recovered._document["prior"] == journal._document["prior"]
            for action in (recovered.rollback, recovered.commit, recovered.finish):
                with pytest.raises(PermissionError, match="governance"):
                    action()
            with pytest.raises(DaemonUnavailable):
                client.cleanup_health()
            _policy(installation.os_profile)
            with pytest.raises(PermissionError, match="invalid watch owner"):
                recovered.advance("draining")
        assert not installation.state_root.exists() or not any(installation.state_root.iterdir())


@pytest.mark.parametrize("phase", ["draining", "released"])
def test_off_after_drain_intent_stops_only_passive_and_keeps_reconciliation(installed, receiver, phase):
    installation = installed[0]
    with installation.transaction(timeout=1, environment={}) as guard:
        journal = _begin(installed, receiver, guard)
        journal.advance("draining")
        if phase == "released":
            journal.advance("released")
        _disable(installation, "disabled")
        recovered = WatchJournal.recover_cleanup(installation, guard, environment={})
        client = _client(installation, receiver, recovered)
        with client.capture_cleanup_exit() as reference:
            client.cleanup_stop()
            recovered.record_cleanup_exit(reference, timeout=10)
        receiver[0].wait(timeout=10)
        assert recovered._document["phase"] == phase
        with pytest.raises(PermissionError, match="governance"):
            recovered.finish()


def test_actual_writer_adoption_is_not_passive_even_before_publish(installed, receiver):
    installation = installed[0]
    with installation.transaction(timeout=1, environment={}) as guard:
        journal = _begin(installed, receiver, guard)
        journal.advance("draining")
        journal.advance("released")
        client = _client(installation, receiver, journal)
        client.control("adopt")
        assert client.health()["writerHeld"] is True
        _disable(installation, "disabled")
        recovered = WatchJournal.recover_cleanup(installation, guard, environment={})
        client = _client(installation, receiver, recovered)
        with pytest.raises(WatchControlRefused):
            client.cleanup_health()
        with pytest.raises(WatchControlRefused):
            client.cleanup_stop()
        assert receiver[0].poll() is None
        assert recovered._document["exited"] == []


@pytest.mark.parametrize("committed", [False, True])
def test_publication_or_commit_never_becomes_off_cleanup_authority(installed, receiver, committed):
    installation, target, _, _ = installed
    with installation.transaction(timeout=1, environment={}) as guard:
        journal = _begin(installed, receiver, guard)
        client = _client(installation, receiver, journal)
        journal.advance("draining")
        journal.advance("released")
        if committed:
            client.control("adopt")
        journal.advance("read-ready")
        journal.advance("publishing")
        _select(installation, target, route=_route(receiver[1]))
        if committed:
            journal.commit(read_health=client.health)
        _disable(installation, "disabled")
        with pytest.raises(PermissionError, match="precommit custody"):
            WatchJournal.recover_cleanup(installation, guard, environment={})
        with pytest.raises(WatchControlRefused):
            client.cleanup_stop()
        assert receiver[0].poll() is None
        assert journal._document["phase"] == ("committed" if committed else "publishing")


@pytest.mark.parametrize("changed", ["capability", "instance", "birth", "selection", "receipt"])
def test_passive_cleanup_refuses_changed_authority(installed, receiver, changed):
    installation, target, _, _ = installed
    with installation.transaction(timeout=1, environment={}) as guard:
        journal = _begin(installed, receiver, guard)
        client = _client(installation, receiver, journal)
        _disable(installation, "disabled")
        if changed == "capability":
            client._transaction = lambda: (journal.transaction_id, "0" * 64)
        elif changed == "instance":
            client.instance = replace(receiver[1], instance_id=str(uuid4()))
        elif changed == "birth":
            client.instance = replace(receiver[1], start_time="0")
        elif changed == "selection":
            _select(installation, target)
        else:
            _mutate(installation.receipt, lambda value: value["roots"].update(run="foreign"))
        with pytest.raises((WatchControlRefused, PermissionError)):
            client.cleanup_stop()
        assert receiver[0].poll() is None
        assert journal._document["exited"] == []


def test_cleanup_requires_live_native_controller_guard(installed, receiver):
    installation = installed[0]
    with installation.transaction(timeout=1, environment={}) as guard:
        journal = _begin(installed, receiver, guard)
        client = _client(installation, receiver, journal)
        _disable(installation, "disabled")
        assert client.cleanup_health()["writerHeld"] is False
    with pytest.raises(WatchControlRefused):
        client.cleanup_stop()
    assert receiver[0].poll() is None


def test_unreserved_spawn_cannot_be_claimed_for_cleanup(installed, receiver):
    installation = installed[0]
    with installation.transaction(timeout=1, environment={}) as guard:
        _begin(installed, receiver, guard)
        _mutate(installation.run_root / "watch-owner-transaction.json", lambda value: value.update(
            successor=None, targetRoute=None, phase="prepared",
        ))
        recovered = WatchJournal.recover(installation, guard, environment={})
        _disable(installation, "disabled")
        client = _client(installation, receiver, recovered)
        with pytest.raises(WatchControlRefused):
            client.cleanup_health()
        with pytest.raises(PermissionError, match="reserved launch"):
            recovered.bind_cleanup_successor(
                receiver[1], read_health=lambda: pytest.fail("must not contact unknown child"),
            )
        assert receiver[0].poll() is None
