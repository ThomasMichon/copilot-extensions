"""Strict positive ownership uses an actual held file, not PID-shaped residue."""

from __future__ import annotations

import importlib
import os
import sys
from contextlib import contextmanager
from types import SimpleNamespace

import pytest


pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="Native held-file proof requires Linux procfs",
)
NATIVE_ID = "00000000-0000-4000-8000-000000000001"
OTHER_ID = "00000000-0000-4000-8000-000000000002"


@pytest.fixture
def owned(tmp_path, monkeypatch):
    probe = importlib.import_module("agent_worktrees.session_liveness")
    root = tmp_path / "session-state"
    for session in (NATIVE_ID, OTHER_ID):
        (root / session).mkdir(parents=True)
    monkeypatch.setattr(probe.sessions, "_session_state_dir", lambda: root)
    pid = os.getpid()
    born = probe.locks.process_start_time(pid)
    assert born
    return probe, root, pid, born


def test_matching_open_native_hold_proves_exact_uuid(owned):
    probe, root, pid, born = owned
    with (root / NATIVE_ID / f"inuse.{pid}.hold").open("w"):
        assert probe.session_process_owns_hold(NATIVE_ID, pid, born) is True
        assert probe.session_process_owns_hold(OTHER_ID, pid, born) is False


def test_stale_path_without_open_hold_is_unconfirmed(owned):
    probe, root, pid, born = owned
    (root / NATIVE_ID / f"inuse.{pid}.hold").touch()
    assert probe.session_process_owns_hold(NATIVE_ID, pid, born) is False


def test_wrong_recorded_birth_is_unconfirmed_even_with_open_hold(owned):
    probe, root, pid, _born = owned
    with (root / NATIVE_ID / f"inuse.{pid}.hold").open("w"):
        assert probe.session_process_owns_hold(NATIVE_ID, pid, "older-birth") is False


def test_birth_change_during_descriptor_probe_is_unconfirmed(owned, monkeypatch):
    probe, root, pid, born = owned
    tokens = iter([born, "replacement-birth"])
    monkeypatch.setattr(probe.locks, "process_start_time", lambda _: next(tokens))
    with (root / NATIVE_ID / f"inuse.{pid}.hold").open("w"):
        assert probe.session_process_owns_hold(NATIVE_ID, pid, born) is False


@pytest.mark.parametrize("link_kind", ["file", "directory"])
def test_aliased_uuid_hold_does_not_prove_identity(owned, link_kind):
    probe, root, pid, born = owned
    other = root / OTHER_ID / f"inuse.{pid}.hold"
    if link_kind == "file":
        (root / NATIVE_ID / f"inuse.{pid}.hold").symlink_to(other)
    else:
        (root / NATIVE_ID).rmdir()
        (root / NATIVE_ID).symlink_to(root / OTHER_ID, target_is_directory=True)
    with other.open("w"):
        assert probe.session_process_owns_hold(NATIVE_ID, pid, born) is False


def test_unreadable_descriptor_state_is_explicitly_unconfirmed(owned, monkeypatch, caplog):
    probe, root, pid, born = owned

    def denied(_path):
        raise PermissionError("descriptor access denied")

    with (root / NATIVE_ID / f"inuse.{pid}.hold").open("w"):
        monkeypatch.setattr(probe.os, "scandir", denied)
        assert probe.session_process_owns_hold(NATIVE_ID, pid, born) is False
    assert "could not be corroborated" in caplog.text


def test_descriptor_budget_stops_before_an_unobserved_match(owned, monkeypatch, caplog):
    probe, root, pid, born = owned
    seen = []
    with (root / NATIVE_ID / f"inuse.{pid}.hold").open("w") as hold:
        descriptor = f"/proc/{pid}/fd/{hold.fileno()}"
        entries = [
            SimpleNamespace(
                path="unrelated",
                stat=lambda: seen.append("unrelated") or SimpleNamespace(st_dev=-1, st_ino=-1),
            ),
            SimpleNamespace(
                path=descriptor,
                stat=lambda: seen.append("matching") or os.stat(descriptor),
            ),
        ]

        @contextmanager
        def limited(_path):
            yield iter(entries)

        monkeypatch.setattr(probe.os, "scandir", limited)
        monkeypatch.setattr(probe, "_MAX_BINDING_DESCRIPTORS", 1)
        assert probe.session_process_owns_hold(NATIVE_ID, pid, born) is False
    assert seen == ["unrelated"]
    assert "descriptor budget" in caplog.text


def test_reused_descriptor_is_rechecked_without_direntry_cache(owned, monkeypatch):
    probe, root, pid, born = owned
    with (
        (root / NATIVE_ID / f"inuse.{pid}.hold").open("w") as first,
        (root / OTHER_ID / f"inuse.{pid}.hold").open("w") as replacement,
    ):
        stale = SimpleNamespace(
            path=f"/proc/{pid}/fd/{replacement.fileno()}",
            stat=lambda: os.fstat(first.fileno()),
        )

        @contextmanager
        def changed(_path):
            yield iter([stale])

        monkeypatch.setattr(probe.os, "scandir", changed)
        assert probe.session_process_owns_hold(NATIVE_ID, pid, born) is False


@pytest.mark.parametrize("changes", [
    {"session_id": "../unsafe"}, {"pid": True}, {"pid": 0}, {"start_time": ""},
])
def test_invalid_native_identity_is_rejected_before_probing(owned, changes):
    probe, _root, pid, born = owned
    values = {"session_id": NATIVE_ID, "pid": pid, "start_time": born, **changes}
    with pytest.raises(ValueError):
        probe.session_process_owns_hold(**values)
