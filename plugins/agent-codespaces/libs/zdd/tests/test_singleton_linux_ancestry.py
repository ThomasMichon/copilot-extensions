"""Deep/cyclic ancestry proof without creating a large real process tree."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from zdd import singleton_linux
from zdd.singleton_linux import LinuxBackend
from zdd.singleton_state import ProcessIdentity


def test_descendant_deeper_than_128_parents_is_owned(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = LinuxBackend.__new__(LinuxBackend)
    backend.manager_pid = 999
    backend.boot_id = "boot"
    identities = {pid: ProcessIdentity(pid, str(pid), "boot") for pid in range(100, 300)}
    monkeypatch.setattr(backend, "identify", identities.get)
    monkeypatch.setattr(
        singleton_linux, "_process_stat",
        lambda pid: ("S", pid + 1 if pid < 299 else 999),
    )
    reference = SimpleNamespace(identity=identities[100], alive=lambda: True)
    assert backend.owns(reference)


def test_cyclic_ancestry_is_not_owned(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = LinuxBackend.__new__(LinuxBackend)
    backend.manager_pid = 999
    backend.boot_id = "boot"
    identities = {pid: ProcessIdentity(pid, str(pid), "boot") for pid in (100, 101)}
    monkeypatch.setattr(backend, "identify", identities.get)
    monkeypatch.setattr(
        singleton_linux, "_process_stat", lambda pid: ("S", 101 if pid == 100 else 100),
    )
    reference = SimpleNamespace(identity=identities[100], alive=lambda: True)
    assert not backend.owns(reference)
