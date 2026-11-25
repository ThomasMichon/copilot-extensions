"""Explicit generation management cannot certify a stale ready incumbent."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent_vault import launch, service, service_generation as generation


def _health(identity: str) -> dict[str, Any]:
    return {
        "ok": True, "pid": 123, "generation_protocol": 1,
        "runtime_generation": identity, "accepting_work": True,
    }


@pytest.fixture
def selected_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name, identity in (("current", "a" * 32), ("other", "b" * 32)):
        marker = tmp_path / "versions" / name / ".runtime-bootstrap"
        marker.mkdir(parents=True)
        (marker / "runtime-generation").write_text(identity, encoding="utf-8")
    (tmp_path / "current-version").write_text("current", encoding="utf-8")
    monkeypatch.setattr(generation, "home_dir", lambda: tmp_path)
    monkeypatch.setattr(generation, "PROCESS_SLOT", tmp_path / "versions" / "current")
    monkeypatch.setattr(generation, "PROCESS_GENERATION", "a" * 32)
    monkeypatch.setattr(service, "run_dir", lambda: tmp_path / "run")
    monkeypatch.setattr(generation, "read_endpoint", lambda _: None)
    return tmp_path


def test_explicit_ensure_replaces_a_healthy_older_generation(
    selected_root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    status = _health("older")
    launches: list[list[str]] = []

    def spawn(arguments: list[str]) -> None:
        launches.append(arguments)
        status.update(_health("a" * 32))

    monkeypatch.setattr(generation, "endpoint_health", lambda _: status)
    monkeypatch.setattr(launch, "spawn_service", spawn)
    assert generation.ensure_selected()
    assert launches == [["--foreground", "--persistent"]]


def test_ready_selected_generation_is_idempotent(
    selected_root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(generation, "endpoint_health", lambda _: _health("a" * 32))
    monkeypatch.setattr(
        launch, "spawn_service", lambda _: pytest.fail("ready current service must not restart"),
    )
    assert generation.ensure_selected()


@pytest.mark.parametrize("change_during_probe", [False, True])
@pytest.mark.parametrize("peer_current", [False, True])
def test_ready_process_loses_management_authority_after_reselection(
    selected_root: Path, monkeypatch: pytest.MonkeyPatch, change_during_probe: bool,
    peer_current: bool,
) -> None:
    marker = selected_root / "current-version"

    def health(_: object) -> dict[str, Any]:
        marker.write_text("other", encoding="utf-8")
        return _health("a" * 32 if peer_current else "older")

    if not change_during_probe:
        marker.write_text("other", encoding="utf-8")
    monkeypatch.setattr(
        generation, "endpoint_health",
        health if change_during_probe else lambda _: pytest.fail("unselected owner must not probe"),
    )
    monkeypatch.setattr(
        launch, "spawn_service", lambda _: pytest.fail("unselected owner must not launch"),
    )
    assert generation.ensure_selected() is False


def test_reselection_during_incoming_readiness_cannot_report_success(
    selected_root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    probes = 0
    launches: list[list[str]] = []

    def health(_: object) -> dict[str, Any]:
        nonlocal probes
        probes += 1
        if probes > 1:
            (selected_root / "current-version").write_text("other", encoding="utf-8")
        return _health("older" if probes == 1 else "a" * 32)

    monkeypatch.setattr(generation, "endpoint_health", health)
    monkeypatch.setattr(launch, "spawn_service", lambda arguments: launches.append(list(arguments)))
    assert generation.ensure_selected() is False
    assert len(launches) == 1
    assert probes == 2


def test_older_ready_peer_does_not_hide_incoming_readiness_failure(
    selected_root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = iter((0.0, 0.0, 2.0))
    launches: list[list[str]] = []
    sleep: Callable[[float], None] = lambda _: None
    monkeypatch.setattr(
        generation, "time", SimpleNamespace(monotonic=lambda: next(clock), sleep=sleep),
    )
    monkeypatch.setattr(generation, "endpoint_health", lambda _: _health("older"))
    monkeypatch.setattr(launch, "spawn_service", lambda arguments: launches.append(list(arguments)))
    assert generation.ensure_selected(timeout=1.0) is False
    assert launches == [["--foreground", "--persistent"]]
