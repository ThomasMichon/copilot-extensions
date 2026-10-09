"""Manager state is validated, durable, private, and separately leased."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

from zdd.singleton_state import (
    LEASE_ENV, ManagerAlreadyRunning, ManagerLease, ManagerState, ProcessIdentity, StateStore,
)


def _state() -> ManagerState:
    return ManagerState(
        ProcessIdentity(10, "100", "boot"), ProcessIdentity(20, "200", "boot"),
    )


def test_state_roundtrip_and_permissions(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "private")
    assert store.read() is None
    store.write(_state())
    assert store.read() == _state()
    assert not list(store.directory.glob(".manager-*"))
    if sys.platform == "linux":
        assert store.path.stat().st_mode & 0o777 == 0o600
        assert store.directory.stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize(
    "field,value",
    [("schema_version", 2), ("schema_version", True), ("phase", "unknown"),
     ("exit_code", False), ("watched", None), ("deadline", 10)],
)
def test_malformed_state_is_an_error(tmp_path: Path, field: str, value: object) -> None:
    store = StateStore(tmp_path)
    raw = asdict(_state())
    raw[field] = value
    store.path.write_text(json.dumps(raw))
    with pytest.raises(ValueError):
        store.read()


@pytest.mark.parametrize("deadline", [None, float("nan"), float("inf"), True])
def test_discovery_state_requires_real_deadline(deadline: object) -> None:
    raw = asdict(_state())
    raw.update(phase="discovering", deadline=deadline)
    with pytest.raises(ValueError, match="deadline"):
        ManagerState.parse(raw)


def test_atomic_write_failure_keeps_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = StateStore(tmp_path)
    store.write(_state())
    original = store.path.read_bytes()

    def fail_replace(source: object, destination: object) -> None:
        raise OSError("injected replace failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failure"):
        store.write(ManagerState(
            _state().owner, ProcessIdentity(30, "300", "boot"),
        ))
    assert store.path.read_bytes() == original
    assert not list(tmp_path.glob(".manager-*"))


@pytest.mark.skipif(sys.platform != "linux", reason="Linux flock contract")
def test_lease_excludes_second_owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(LEASE_ENV, raising=False)
    first = ManagerLease(tmp_path)
    try:
        assert not os.get_inheritable(first.fd)
        first.prepare_exec()
        assert os.get_inheritable(first.fd)
        with pytest.raises(ManagerAlreadyRunning):
            ManagerLease(tmp_path)
    finally:
        first.close()
    replacement = ManagerLease(tmp_path)
    replacement.close()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux inherited lease contract")
def test_inherited_descriptor_must_match_state_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrong = tmp_path / "wrong"
    wrong.touch()
    tmp_path.joinpath("manager.lock").touch()
    with wrong.open("r") as handle:
        monkeypatch.setenv(LEASE_ENV, str(handle.fileno()))
        with pytest.raises(ValueError, match="does not match"):
            ManagerLease(tmp_path)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux same-process exec lease")
def test_inherited_lease_cannot_bypass_another_manager_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(LEASE_ENV, raising=False)
    first = ManagerLease(tmp_path)
    try:
        StateStore(tmp_path).write(_state())
        monkeypatch.setenv(LEASE_ENV, str(first.fd))
        with pytest.raises(ValueError, match="same-process exec"):
            ManagerLease(tmp_path)
    finally:
        first.close()
