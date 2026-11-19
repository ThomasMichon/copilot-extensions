"""Preparation retains the original native writer and never activates migration."""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from agent_pull_requests.watch_contract import PRSnapshot
from agent_pull_requests.watch_storage import read_subscriptions_state, watch_state_paths
from agent_pull_requests.watch_writer import WatchWriter

pytestmark = pytest.mark.contract("agent_pull_requests.watch.writer_handoff")


@pytest.fixture
def writer(tmp_path: Path) -> Iterator[WatchWriter]:
    owner = WatchWriter(
        root=tmp_path, authorize=lambda _: None,
        fetch=lambda *_: PRSnapshot(pr_state="open"),
    )
    yield owner
    owner.close(timeout=3)


def test_preparation_drains_but_retains_original_lease_and_legacy_bytes(
    writer: WatchWriter, tmp_path: Path,
) -> None:
    paths = watch_state_paths(tmp_path)
    paths.legacy_subscriptions.write_text(" [ ] \n", encoding="utf-8")
    writer.adopt(timeout=1)
    writer.activate(timeout=1)
    result = writer.prepare_state_transition(timeout=1)
    assert result["phase"] == "prepared"
    assert writer.writer_held and not writer.read_ready
    assert writer.daemon.quiescent and not writer.daemon.effects_enabled
    assert paths.historical_lock.is_file()
    assert not paths.retained_lock.exists()
    assert not paths.staged_barrier.exists()
    assert not paths.isolated_subscriptions.parent.exists()
    original = paths.legacy_subscriptions.read_bytes()
    with pytest.raises(ValueError, match="guarded recovery"):
        read_subscriptions_state(tmp_path)
    with pytest.raises(RuntimeError, match="read-ready"):
        writer.activate(timeout=1)
    assert paths.legacy_subscriptions.read_bytes() == original


def test_prepared_recovery_reuses_only_original_inode_and_retains_latest_legacy(
    writer: WatchWriter, tmp_path: Path,
) -> None:
    paths = watch_state_paths(tmp_path)
    writer.adopt(timeout=1)
    first = writer.prepare_state_transition(timeout=1)
    writer.close(timeout=1)
    # A historical writer can still publish before sealing. Preparation is not import.
    paths.legacy_subscriptions.write_bytes(b"[]\n")
    recovered = WatchWriter(
        root=tmp_path, authorize=lambda _: None,
        fetch=lambda *_: pytest.fail("preparation must not fetch"),
    )
    try:
        second = recovered.prepare_state_transition(timeout=1)
        assert second == first
        assert recovered.writer_held and not recovered.read_ready
        assert paths.legacy_subscriptions.read_bytes() == b"[]\n"
        assert not paths.isolated_subscriptions.parent.exists()
    finally:
        recovered.close(timeout=1)


@pytest.mark.parametrize("stage", ["authority", "drain", "commit"])
def test_failed_preparation_retains_custody_and_publishes_no_marker(
    writer: WatchWriter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str,
) -> None:
    from agent_pull_requests import watch_transition

    writer.adopt(timeout=1)
    writer.activate(timeout=1)

    def refused(*_args: Any, **_kwargs: Any) -> None:
        raise PermissionError("controlled preparation refusal")

    if stage == "authority":
        writer.authorize = refused
    elif stage == "drain":
        monkeypatch.setattr(writer.daemon, "quiesce", refused)
    else:
        monkeypatch.setattr(watch_transition, "write_private", refused)
    with pytest.raises(PermissionError, match="controlled"):
        writer.prepare_state_transition(timeout=1)
    assert writer.writer_held
    assert not watch_state_paths(tmp_path).journal.exists()


def test_preparation_does_not_create_a_missing_historical_root(tmp_path: Path) -> None:
    root = tmp_path / "not-yet-a-legacy-root"
    owner = WatchWriter(root=root, authorize=lambda _: None, fetch=lambda *_: pytest.fail("must not fetch"))
    try:
        with pytest.raises(FileNotFoundError):
            owner.prepare_state_transition(timeout=1)
        assert not owner.writer_held
        assert not root.exists()
    finally:
        owner.close(timeout=1)


def test_durability_uncertainty_retains_marker_and_same_owner_can_recover(
    writer: WatchWriter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_pull_requests import watch_transition
    from agent_pull_requests.watch_storage import StateCommitUncertain

    writer.adopt(timeout=1)
    persist = watch_transition.write_private

    def uncertain(*args: Any, **kwargs: Any) -> None:
        persist(*args, **kwargs)
        raise StateCommitUncertain("controlled acknowledgement loss")

    with monkeypatch.context() as failed:
        failed.setattr(watch_transition, "write_private", uncertain)
        with pytest.raises(StateCommitUncertain, match="acknowledgement loss"):
            writer.prepare_state_transition(timeout=1)
    original = watch_state_paths(tmp_path).journal.read_bytes()
    assert writer.writer_held and not writer.read_ready
    assert writer.prepare_state_transition(timeout=1)["phase"] == "prepared"
    assert watch_state_paths(tmp_path).journal.read_bytes() == original


def test_final_guard_refuses_lost_native_custody_without_reacquiring(
    writer: WatchWriter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_pull_requests import watch_transition

    writer.adopt(timeout=1)
    persist = watch_transition.write_private

    def released(path: Path, document: dict[str, Any], **kwargs: Any) -> None:
        writer._lease.release()
        return persist(path, document, **kwargs)

    monkeypatch.setattr(watch_transition, "write_private", released)
    with pytest.raises(PermissionError, match="not held"):
        writer.prepare_state_transition(timeout=1)
    assert not writer.writer_held
    assert not watch_state_paths(tmp_path).journal.exists()


@pytest.mark.parametrize("change", ["binding", "inode", "phase", "extra"])
def test_prepared_recovery_refuses_conflicting_evidence(
    writer: WatchWriter, tmp_path: Path, change: str,
) -> None:
    paths = watch_state_paths(tmp_path)
    writer.adopt(timeout=1)
    writer.prepare_state_transition(timeout=1)
    writer.close(timeout=1)
    document = json.loads(paths.journal.read_text(encoding="utf-8"))
    if change == "binding":
        document["binding"]["stateRoot"] += "-foreign"
    elif change == "inode":
        document["lease"]["inode"] += 1
    elif change == "phase":
        document["phase"] = "sealed-legacy"
    else:
        document["unexpected"] = True
    paths.journal.write_text(json.dumps(document), encoding="utf-8")
    original = paths.journal.read_bytes()
    recovered = WatchWriter(
        root=tmp_path, authorize=lambda _: None,
        fetch=lambda *_: pytest.fail("must not fetch"),
    )
    try:
        with pytest.raises((ValueError, PermissionError)):
            recovered.prepare_state_transition(timeout=1)
        assert not recovered.writer_held
        assert paths.journal.read_bytes() == original
    finally:
        recovered.close(timeout=1)


def test_preparation_rechecks_authority_before_atomic_publication(
    writer: WatchWriter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_pull_requests import watch_transition

    writer.adopt(timeout=1)
    persist = watch_transition.write_private
    calls: list[str] = []

    def changed(path: Path, document: dict[str, Any], **kwargs: Any) -> None:
        def refused(_action: str) -> None:
            calls.append("refused")
            raise PermissionError("authority changed before publication")

        writer.authorize = refused
        return persist(path, document, **kwargs)

    monkeypatch.setattr(watch_transition, "write_private", changed)
    with pytest.raises(PermissionError, match="changed before publication"):
        writer.prepare_state_transition(timeout=1)
    assert calls == ["refused"]
    assert writer.writer_held
    assert not watch_state_paths(tmp_path).journal.exists()
