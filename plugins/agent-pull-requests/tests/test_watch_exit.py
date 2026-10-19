"""A controller crash cannot replace exact exit or native writer acquisition."""

import pytest
from single_instance_lease import AlreadyRunningError, SingleInstance

from agent_pull_requests import watch_exit
from agent_pull_requests.watch_journal import WatchJournal
from agent_pull_requests.watch_writer import WatchWriter
from test_watch_control import _receiver
from test_watch_custody import _begin_reserved
from test_watch_installation import _policy
from test_watch_invocation import installed as installed  # noqa: F401 -- pytest fixture
from test_watch_journal import (
    _authorize, _begin, _ready, _route,
    cell as cell, passive as passive,  # noqa: F401 -- pytest fixtures
)

pytestmark = pytest.mark.timeout(180)


def _stop(passive):
    child = passive[0]
    child.stdin.write('{"operation":"exit"}\n')
    child.stdin.flush()
    assert child.wait(timeout=5) == 0


def test_original_exit_after_controller_loss_still_requires_real_writer_acquisition(cell, passive):
    installation, predecessor, _ = cell
    writer = WatchWriter(
        root=installation.state_root, authorize=lambda _: None,
        fetch=lambda *_: {}, notify=lambda *_: None,
    )
    blocker = SingleInstance(installation.state_root, service="agent-pull-requests-watch")
    try:
        writer.adopt(timeout=5)
        writer.activate(timeout=5)
        with installation.transaction(timeout=1, environment={}) as guard:
            journal = _begin(cell, guard)
            journal.bind_successor(passive[1], route=_route(passive[1]))
            journal.advance("draining")
            writer.drain_and_release(timeout=5)
            journal.rollback()
            recovered = WatchJournal.recover(installation, guard, environment={})
            with pytest.raises(TimeoutError, match="remains live"):
                recovered.observe_exit("successor")
            assert recovered._document["exited"] == []
            _stop(passive)  # No native exit reference survives or is captured.
            recovered.observe_exit("successor")
            assert recovered._document["exited"] == ["successor"]
            writer.authorize = lambda operation: _authorize(
                installation, predecessor, recovered, operation,
            )
            blocker.acquire()
            with pytest.raises(AlreadyRunningError):
                writer.resume(timeout=15)
            assert not writer.writer_held
            assert not writer.read_ready and not writer.daemon.effects_enabled
            blocker.release()
            writer.resume(timeout=15)
            assert writer.writer_held and writer.read_ready and writer.daemon.effects_enabled
            recovered.finish(read_health=lambda: _ready(predecessor, effects=True))
    finally:
        blocker.release()
        writer.close(timeout=5)


@pytest.mark.parametrize("observation", ["denied", "unknown", "unsupported"])
def test_unknown_original_exit_is_not_durable_exit_evidence(cell, passive, monkeypatch, observation):
    installation, _, _ = cell
    with installation.transaction(timeout=1, environment={}) as guard:
        journal = _begin(cell, guard)
        journal.bind_successor(passive[1], route=_route(passive[1]))
        journal.rollback()
        before = (installation.run_root / "watch-owner-transaction.json").read_bytes()

        def observe(*_):
            if observation == "denied":
                raise PermissionError("test-owned access denial")
            if observation == "unsupported":
                raise NotImplementedError("test-owned unsupported platform")
            return None

        monkeypatch.setattr(watch_exit, "observe_original_exit", observe)
        with pytest.raises((PermissionError, NotImplementedError, TimeoutError)):
            journal.observe_exit("successor")
        assert (installation.run_root / "watch-owner-transaction.json").read_bytes() == before


@pytest.mark.parametrize("bound", [False, True])
def test_policy_off_original_passive_exit_recovers_reserved_launch_without_control_or_resume(installed, bound):
    installation = installed[0]
    with installation.transaction(timeout=1, environment={}) as guard:
        journal = _begin_reserved(installed, guard)
        with _receiver(
            installed, instance_id=journal._document["launchInstanceId"], publish_launch=True,
        ) as child:
            if bound:
                journal.bind_successor(child[1], route=_route(child[1]))
            _policy(installation.os_profile, False)
            recovered = WatchJournal.recover_cleanup(installation, guard, environment={})
            with pytest.raises(TimeoutError, match="remains live"):
                recovered.observe_cleanup_exit()
        assert child[0].poll() is not None
        recovered = WatchJournal.recover_cleanup(installation, guard, environment={})
        recovered.observe_cleanup_exit()
        assert recovered._document["successor"] == child[1].evidence()
        assert recovered._document["exited"] == ["successor"]
        assert recovered._document["phase"] == ("passive" if bound else "prepared")
        with pytest.raises(PermissionError, match="governance"):
            recovered.finish()
        assert not installation.state_root.exists()
