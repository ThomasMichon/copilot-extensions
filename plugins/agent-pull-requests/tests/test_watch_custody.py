"""Crash custody survives controller loss without becoming discovery authority."""

import json
from uuid import uuid4

import pytest

from agent_pull_requests.watch_control import WatchControlClient, WatchControlRefused
from agent_pull_requests.watch_journal import WatchJournal
from agent_pull_requests.watch_private import require_private
from test_watch_control import _receiver
from test_watch_installation import _mutate, _policy
from test_watch_invocation import installed as installed  # noqa: F401 -- pytest fixture
from test_watch_journal import (
    _authorize, _begin, _manifest, _ready, _route, _select,
    cell as cell, passive as passive,  # noqa: F401 -- pytest fixtures
)
from test_watch_runtime import _advance_installation, _complete, _instance

pytestmark = pytest.mark.timeout(90)


def _begin_reserved(installed, guard):
    installation, target, _, _ = installed
    predecessor = _instance(_complete(installation, version="1.0.0"))
    journal = WatchJournal.begin(
        installation, guard, target=target, target_manifest=_manifest(target),
        predecessor=predecessor, environment={},
    )
    journal.reserve_successor(str(uuid4()))
    return journal


def _client(installation, journal, candidate):
    instance, endpoint = candidate
    return WatchControlClient(
        endpoint=endpoint, instance=instance, installation=lambda: installation,
        transaction=lambda: (journal.transaction_id, journal.authorization),
        request_timeout=60,
    )


@pytest.mark.parametrize("outcome", ["complete", "rolled-back"])
def test_terminal_archive_is_explicit_and_next_transaction_retains_history(cell, outcome):
    installation, _, target = cell
    with installation.transaction(timeout=1, environment={}) as guard:
        journal = _begin(cell, guard, live=False)
        if outcome == "complete":
            _select(installation, target)
            journal.commit()
        else:
            journal.rollback()
        journal.finish()
        terminal = dict(journal._document)
        with pytest.raises(FileExistsError, match="archival"):
            _begin(cell, guard, live=False)
        path = journal.archive()
        require_private(path)
        assert json.loads(path.read_text()) == terminal
        assert journal.archive() == path
        recovered = WatchJournal.recover(installation, guard, environment={})
        assert recovered.authorization != journal.authorization
        assert recovered.archive() == path
        next_journal = _begin(cell, guard, live=False)
        assert next_journal.transaction_id != journal.transaction_id
        assert next_journal._document["phase"] == "prepared"
        assert json.loads(path.read_text()) == terminal
        assert WatchJournal.recover(installation, guard, environment={}).transaction_id == next_journal.transaction_id
        with pytest.raises(PermissionError, match="changed"):
            journal.rollback()


def test_pending_or_corrupt_archive_never_permits_overwriting_pending_custody(cell):
    installation, _, _ = cell
    with installation.transaction(timeout=1, environment={}) as guard:
        journal = _begin(cell, guard, live=False)
        prior = (installation.run_root / "watch-owner-transaction.json").read_bytes()
        with pytest.raises(PermissionError, match="pending"):
            journal.archive()
        with pytest.raises(FileExistsError, match="reconciliation"):
            _begin(cell, guard, live=False)
        assert (installation.run_root / "watch-owner-transaction.json").read_bytes() == prior
        journal.rollback()
        journal.finish()
        path = journal.archive()
        _mutate(path, lambda value: value.update(targetRoute="foreign"))
        with pytest.raises(PermissionError, match="different custody"):
            journal.archive()
        terminal = (installation.run_root / "watch-owner-transaction.json").read_bytes()
        with pytest.raises(FileExistsError, match="archival"):
            _begin(cell, guard, live=False)
        assert (installation.run_root / "watch-owner-transaction.json").read_bytes() == terminal


@pytest.mark.parametrize("state", ["archived-terminal", "unarchived-terminal", "pending"])
def test_receipt_update_accepts_only_already_archived_known_cell_terminal(cell, tmp_path, state):
    installation, _, target = cell
    with installation.transaction(timeout=1, environment={}) as guard:
        journal = _begin(cell, guard, live=False)
        if state != "pending":
            _select(installation, target)
            journal.commit()
            journal.finish()
        archive = journal.archive() if state == "archived-terminal" else None
        original = (installation.run_root / "watch-owner-transaction.json").read_bytes()
        fresh = _advance_installation(installation, tmp_path)
        with pytest.raises(PermissionError, match="binding"):
            WatchJournal.recover(fresh, guard, environment={})
        if archive is not None:
            saved = archive.read_bytes()
            next_journal = WatchJournal.begin(
                fresh, guard, target=target, target_manifest=_manifest(target), environment={},
            )
            assert next_journal.transaction_id != journal.transaction_id
            assert next_journal._document["installation"] == fresh.evidence()
            assert archive.read_bytes() == saved
            assert not fresh.state_root.exists()
            assert not (fresh.run_root / "active.json").exists()
        else:
            with pytest.raises(FileExistsError, match="reconciliation"):
                WatchJournal.begin(
                    fresh, guard, target=target, target_manifest=_manifest(target), environment={},
                )
            assert (installation.run_root / "watch-owner-transaction.json").read_bytes() == original


@pytest.mark.parametrize("field", ["runRoot", "stateRoot", "namespaceGeneration"])
def test_archived_terminal_never_rebinds_foreign_roots_or_future_receipts(cell, field):
    installation, _, _ = cell
    with installation.transaction(timeout=1, environment={}) as guard:
        journal = _begin(cell, guard, live=False)
        journal.rollback()
        journal.finish()
        archive = journal.archive()
        value = 999 if field == "namespaceGeneration" else "foreign"
        for path in (installation.run_root / "watch-owner-transaction.json", archive):
            _mutate(path, lambda document: document["installation"].update({field: value}))
        with pytest.raises(PermissionError, match="another installation identity"):
            _begin(cell, guard, live=False)


def test_reserved_unbound_child_blocks_rollback_completion_and_archival(cell):
    installation, predecessor, _ = cell
    with installation.transaction(timeout=1, environment={}) as guard:
        journal = _begin(cell, guard)
        journal.reserve_successor(str(uuid4()))
        journal.rollback()
        with pytest.raises(PermissionError, match="not authorized"):
            _authorize(installation, predecessor, journal, "resume")
        with pytest.raises(PermissionError, match="launch remains unresolved"):
            journal.finish(read_health=lambda: _ready(predecessor, effects=True))
        with pytest.raises(PermissionError, match="pending"):
            journal.archive()
        assert journal._document["phase"] == "rolling-back"


def test_next_transaction_retains_exact_terminal_owner_and_private_endpoint(cell):
    installation, predecessor, _ = cell
    endpoint = ("127.0.0.1", 12344, "test_owned_token_" + "a" * 32)
    with installation.transaction(timeout=1, environment={}) as guard:
        journal = _begin(cell, guard)
        journal.bind_endpoint(
            "predecessor", endpoint, read_health=lambda: {"instance": predecessor.evidence()},
        )
        journal.rollback()
        journal.finish(read_health=lambda: _ready(predecessor, effects=True))
        journal.archive()
        with pytest.raises(PermissionError, match="exact terminal owner"):
            WatchJournal.begin(
                installation, guard, target=cell[2], target_manifest=_manifest(cell[2]),
                predecessor=_instance(predecessor.runtime), environment={},
            )
        next_journal = _begin(cell, guard)
        assert next_journal.endpoint("predecessor") == (predecessor, endpoint)


def test_uncertain_archive_requires_durability_reconciliation_before_next_transaction(cell, monkeypatch):
    from agent_pull_requests import watch_journal
    from agent_pull_requests.watch_storage import StateCommitUncertain

    installation, _, _ = cell
    with installation.transaction(timeout=1, environment={}) as guard:
        journal = _begin(cell, guard, live=False)
        journal.rollback()
        journal.finish()
        write = watch_journal.write_private

        def uncertain(path, document, **kwargs):
            write(path, document, **kwargs)
            raise StateCommitUncertain("test-owned lost archive acknowledgement")

        monkeypatch.setattr(watch_journal, "write_private", uncertain)
        with pytest.raises(StateCommitUncertain):
            journal.archive()
        monkeypatch.setattr(watch_journal, "write_private", write)
        confirm = watch_journal.confirm_private
        monkeypatch.setattr(watch_journal, "confirm_private", lambda _: (
            (_ for _ in ()).throw(StateCommitUncertain("test-owned sync outage"))
        ))
        terminal = (installation.run_root / "watch-owner-transaction.json").read_bytes()
        with pytest.raises(StateCommitUncertain):
            _begin(cell, guard, live=False)
        assert (installation.run_root / "watch-owner-transaction.json").read_bytes() == terminal
        monkeypatch.setattr(watch_journal, "confirm_private", confirm)
        next_journal = _begin(cell, guard, live=False)
        assert next_journal.transaction_id != journal.transaction_id


def test_authenticated_endpoint_custody_survives_capability_rotation(cell, passive):
    installation, predecessor, _ = cell
    successor = passive[1]
    endpoint = ("127.0.0.1", 12345, "test_owned_token_" + "a" * 32)
    old_endpoint = ("127.0.0.1", 12344, "test_owned_token_" + "b" * 32)
    with installation.transaction(timeout=1, environment={}) as guard:
        journal = _begin(cell, guard)
        journal.bind_endpoint(
            "predecessor", old_endpoint, read_health=lambda: {"instance": predecessor.evidence()},
        )
        journal.bind_successor(successor, route=_route(successor))
        journal.bind_endpoint(
            "successor", endpoint, read_health=lambda: {"instance": successor.evidence()},
        )
        recovered = WatchJournal.recover(installation, guard, environment={})
        assert recovered.endpoint("successor") == (successor, endpoint)
        assert recovered.endpoint("predecessor") == (predecessor, old_endpoint)
        with pytest.raises(PermissionError, match="cannot be replaced"):
            recovered.bind_endpoint(
                "successor", (*endpoint[:2], "changed_token_" + "b" * 32),
                read_health=lambda: {"instance": successor.evidence()},
            )
        with pytest.raises(PermissionError, match="authenticated endpoint"):
            _mutate(installation.run_root / "watch-owner-transaction.json", lambda value: value["endpoints"]["successor"].update(port=12346))
            WatchJournal.recover(installation, guard, environment={})


@pytest.mark.parametrize("phase", ["complete", "rolled-back"])
def test_terminal_phase_without_obsolete_instance_exit_never_becomes_archive(cell, passive, phase):
    installation = cell[0]
    with installation.transaction(timeout=1, environment={}) as guard:
        journal = _begin(cell, guard)
        journal.bind_successor(passive[1], route=_route(passive[1]))
        _mutate(installation.run_root / "watch-owner-transaction.json", lambda value: value.update(phase=phase))
        with pytest.raises(PermissionError, match="obsolete-instance exit"):
            WatchJournal.recover(installation, guard, environment={})
        with pytest.raises(PermissionError, match="obsolete-instance exit"):
            _begin(cell, guard)
        assert not (installation.run_root / "watch-owner-history").exists()


def test_begin_refuses_a_prior_route_that_does_not_belong_to_exact_predecessor(cell):
    from dataclasses import replace

    installation, predecessor, _ = cell
    route = _route(replace(predecessor, pid=predecessor.pid + 1))
    installation.run_root.mkdir(parents=True, exist_ok=True)
    (installation.run_root / "active.json").write_bytes(route)
    with installation.transaction(timeout=1, environment={}) as guard:
        with pytest.raises(PermissionError, match="route does not match"):
            _begin(cell, guard)
    assert not (installation.run_root / "watch-owner-transaction.json").exists()
    assert (installation.run_root / "active.json").read_bytes() == route


def test_predecessor_endpoint_must_match_the_captured_prior_route(cell):
    installation, predecessor, _ = cell
    installation.run_root.mkdir(parents=True, exist_ok=True)
    (installation.run_root / "active.json").write_bytes(_route(predecessor))
    endpoint = ("127.0.0.1", 12346, "test_owned_token_" + "a" * 32)
    with installation.transaction(timeout=1, environment={}) as guard:
        journal = _begin(cell, guard)
        with pytest.raises(PermissionError, match="authenticated endpoint"):
            journal.bind_endpoint(
                "predecessor", endpoint, read_health=lambda: {"instance": predecessor.evidence()},
            )
        assert journal._document["endpoints"]["predecessor"] is None


@pytest.mark.timeout(180)
def test_actual_reserved_child_publishes_private_custody_before_controller_binding(installed):
    installation = installed[0]
    with installation.transaction(timeout=1, environment={}) as guard:
        journal = _begin_reserved(installed, guard)
        instance_id = journal._document["launchInstanceId"]
        with _receiver(installed, instance_id=instance_id, publish_launch=True) as receiver:
            candidate = journal.launched_successor()
            assert candidate == (receiver[1], receiver[2])
            assert journal._document["phase"] == "prepared"
            assert journal._document["successor"] is None
            assert not installation.state_root.exists()
            assert not (installation.run_root / "active.json").exists()
            path = installation.run_root / f"watch-owner-launch-{journal.transaction_id}.json"
            require_private(path)
            original = path.read_bytes()
            recovered = WatchJournal.recover(installation, guard, environment={})
            assert recovered.launched_successor() == candidate
            assert path.read_bytes() == original
            client = _client(installation, recovered, candidate)
            health = client.health()
            assert not health["writerHeld"] and not health["effectsEnabled"]
            route = json.loads(_route(candidate[0]))
            route["active"]["port"] = candidate[1][1]
            recovered.bind_successor(candidate[0], route=json.dumps(route).encode())
            recovered.bind_endpoint("successor", candidate[1], read_health=client.health)
            assert recovered.endpoint("successor") == candidate
            stale = _client(installation, journal, candidate)
            with pytest.raises(WatchControlRefused):
                stale.control("adopt")
            assert path.read_bytes() == original


@pytest.mark.timeout(180)
def test_prebind_policy_off_cleanup_uses_persisted_launch_not_stdout_or_discovery(installed):
    installation = installed[0]
    with installation.transaction(timeout=1, environment={}) as guard:
        journal = _begin_reserved(installed, guard)
        with _receiver(
            installed, instance_id=journal._document["launchInstanceId"], publish_launch=True,
        ) as receiver:
            _policy(installation.os_profile, False)
            recovered = WatchJournal.recover_cleanup(installation, guard, environment={})
            candidate = recovered.cleanup_successor()
            assert candidate[0] == receiver[1]
            client = _client(installation, recovered, candidate)
            recovered.bind_cleanup_successor(
                candidate[0], endpoint=candidate[1], read_health=client.cleanup_health,
            )
            reference = client.capture_cleanup_exit()
            try:
                client.cleanup_stop(timeout=30)
                recovered.record_cleanup_exit(reference, timeout=10)
                assert receiver[0].wait(timeout=10) == 0
            finally:
                reference.close()
            assert recovered._document["phase"] == "prepared"
            assert recovered._document["exited"] == ["successor"]
            assert not installation.state_root.exists()
            assert not (installation.run_root / "active.json").exists()
            assert recovered._document["prior"] == journal._document["prior"]


@pytest.mark.timeout(180)
def test_reused_reserved_uuid_cannot_cleanup_a_different_native_child(installed):
    installation = installed[0]
    with installation.transaction(timeout=1, environment={}) as guard:
        journal = _begin_reserved(installed, guard)
        instance_id = journal._document["launchInstanceId"]
        with _receiver(installed, instance_id=instance_id, publish_launch=True) as first:
            with _receiver(installed, instance_id=instance_id) as second:
                assert first[1].pid != second[1].pid
                wrong = _client(installation, journal, (second[1], second[2]))
                with pytest.raises(WatchControlRefused):
                    wrong.cleanup_stop(timeout=30)
                assert second[0].poll() is None
                correct = _client(installation, journal, journal.launched_successor())
                assert correct.cleanup_health()["instance"] == first[1].evidence()
                assert first[0].poll() is None
                assert journal._document["phase"] == "prepared"


@pytest.mark.parametrize("changed", ["transaction", "instance", "endpoint", "installation"])
@pytest.mark.timeout(180)
def test_known_launch_receipt_refuses_foreign_or_malformed_identity(installed, changed):
    installation = installed[0]
    with installation.transaction(timeout=1, environment={}) as guard:
        journal = _begin_reserved(installed, guard)
        with _receiver(
            installed, instance_id=journal._document["launchInstanceId"], publish_launch=True,
        ):
            path = installation.run_root / f"watch-owner-launch-{journal.transaction_id}.json"
            mutations = {
                "transaction": lambda value: value.update(transactionId=str(uuid4())),
                "instance": lambda value: value["instance"].update(instanceId=str(uuid4())),
                "endpoint": lambda value: value["endpoint"].update(host="192.0.2.1"),
                "installation": lambda value: value["installation"].update(runRoot="foreign"),
            }
            _mutate(path, mutations[changed])
            with pytest.raises((PermissionError, ValueError)):
                journal.launched_successor()
            assert journal._document["phase"] == "prepared"
            assert journal._document["successor"] is None
            assert not (installation.run_root / "active.json").exists()
