"""Original exit, writer exclusion and sanitized native refusal diagnostics."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
from typing import NoReturn, cast

import pytest

from agent_vault import service, service_generation as generation
from agent_vault import service_predecessor as predecessor
from agent_vault.service_connections import VaultConnections


@pytest.mark.parametrize("original_exited", [False, True])
@pytest.mark.parametrize("writer_busy", [False, True])
def test_writer_admission_uses_original_exit_not_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, original_exited: bool,
    writer_busy: bool,
) -> None:
    monkeypatch.setattr(service, "run_dir", lambda: tmp_path / "run")
    monkeypatch.setattr(generation, "home_dir", lambda: tmp_path)
    monkeypatch.setattr(generation, "selected", lambda: True)
    monkeypatch.setattr(generation, "read_endpoint", lambda _: SimpleNamespace(pid=os.getpid()))

    def controller() -> generation.ServiceGeneration:
        return generation.ServiceGeneration(
            cast(service.VaultService, SimpleNamespace()),
            cast(VaultConnections, SimpleNamespace()), [], [],
        )

    incoming = controller()
    incoming.hooks_started = True
    incoming.aliases_after_exit = True
    custody = object()
    original = SimpleNamespace(
        custody=custody, recover=lambda: False, exited=lambda: original_exited,
    )
    incoming.predecessor = cast(predecessor.Predecessor, original)
    incumbent = controller()
    if writer_busy:
        incumbent.writer.acquire()
    try:
        asyncio.run(incoming.tick())
        assert incoming.has_writer is (original_exited and not writer_busy)
        assert original.custody is custody
        assert asyncio.run(incoming._recover_predecessors()) is False
    finally:
        if incoming.has_writer:
            incoming.writer.release()
        if writer_busy:
            incumbent.writer.release()


def test_native_refusal_reports_phase_not_private_error_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    target = predecessor.Predecessor(123, tmp_path, {"birth": "fixture-birth"}, None)
    monkeypatch.setattr(target, "exited", lambda: False)
    monkeypatch.setattr(target, "_owned", lambda _: True)
    monkeypatch.setattr(predecessor, "inspect_instances", lambda _: [])
    error = PermissionError("private credential path must not enter diagnostics")
    setattr(error, "winerror", 5)

    class Custody:
        def __enter__(self) -> None:
            pass

        def pin(self, pid: int, birth: str) -> NoReturn:
            assert (pid, birth) == (123, "fixture-birth")
            raise error

        def close(self) -> None:
            pass

    monkeypatch.setattr(predecessor, "RetirementCustody", lambda _: Custody())
    assert target.retire(lambda: True) == "refused-native-retirement"
    assert "phase=native-pin error_type=PermissionError winerror=5" in caplog.text
    assert "private credential" not in caplog.text


def test_writer_publication_retries_after_lease_acquisition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service, "run_dir", lambda: tmp_path / "run")
    monkeypatch.setattr(generation, "home_dir", lambda: tmp_path)
    monkeypatch.setattr(generation, "selected", lambda: True)
    monkeypatch.setattr(generation, "read_endpoint", lambda _: SimpleNamespace(pid=os.getpid()))
    incoming = generation.ServiceGeneration(
        cast(service.VaultService, SimpleNamespace(_runtime_can_work=False)),
        cast(VaultConnections, SimpleNamespace()), [], [],
    )
    incoming.aliases_after_exit = True
    custody = object()
    original = SimpleNamespace(custody=custody, recover=lambda: False, exited=lambda: True)
    incoming.predecessor = cast(predecessor.Predecessor, original)
    publications: list[bool] = []
    startups: list[object] = []

    def publish() -> bool:
        publications.append(incoming.has_writer)
        return len(publications) > 1

    monkeypatch.setattr(incoming, "_publish_writer_pid", publish)
    monkeypatch.setattr(incoming.api, "get_registry", lambda: SimpleNamespace(
        run_startup=lambda *_: startups.append(object()),
    ))
    monkeypatch.setattr(incoming.api, "resolve_context", lambda: SimpleNamespace(
        kpdb=None, group=None, vault_name=None,
    ))
    try:
        asyncio.run(incoming.tick())
        assert incoming.has_writer and not incoming.hooks_started
        assert not incoming.service._runtime_can_work and not startups
        asyncio.run(incoming.tick())
        assert publications == [True, True] and len(startups) == 1
        assert incoming.hooks_started and incoming.service._runtime_can_work
        assert original.custody is custody
        assert asyncio.run(incoming._recover_predecessors()) is False
    finally:
        if incoming.has_writer:
            incoming.writer.release()
