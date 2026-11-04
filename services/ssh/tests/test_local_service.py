"""Exact cohort reuse and independent local lifecycle outcomes."""
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent_ssh_service import local_service
from agent_ssh_service.local_control import config_identity
from agent_ssh_service.config import Document, ServiceConfig


@pytest.fixture(autouse=True)
def source_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_ssh_service import source_start

    monkeypatch.setattr(source_start, "ensure_selected", lambda _: None)
    monkeypatch.setattr(source_start, "load_provider", lambda _: SimpleNamespace(
        _resources=lambda _: (None, SimpleNamespace(_native_retained_path=str)),
    ))


def binding(root: Path) -> SimpleNamespace:
    root.mkdir()
    slot = root / "versions" / "fixture-generation"
    slot.mkdir(parents=True)
    bootstrap = slot / ".runtime-bootstrap"
    bootstrap.mkdir()
    (bootstrap / "runtime_launcher.py").write_text("# actor-free fixture", encoding="utf-8")
    return SimpleNamespace(
        root=root, slot=slot, python=slot / ".venv" / "Scripts" / "python.exe",
        identity=SimpleNamespace(document=lambda: {"fixture": "service-source"}),
    )


def owner_record(selected: SimpleNamespace, config: ServiceConfig) -> Document:
    return {
        "root": str(selected.root), "slot": selected.slot.name,
        "source": selected.identity.document(), "config_identity": config_identity(selected, config),
        "peer": {"pid": 123, "start_time": "456", "principal": "fixture"},
    }


def test_exact_healthy_owner_reused_without_spawn_or_stop(
    config: ServiceConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = binding(tmp_path / "runtime")
    record = owner_record(selected, config)
    calls = []
    monkeypatch.setattr(local_service, "records", lambda _: [record])
    monkeypatch.setattr(local_service, "spawn_daemon", lambda *a, **k: pytest.fail("spawned"))
    def health(_config: ServiceConfig, _record: Document, action: str, **_: Any) -> Document:
        calls.append(action)
        return {"ready": True}
    monkeypatch.setattr(local_service, "request", health)
    assert local_service.start(selected, tmp_path / "policy.json", config) == {
        "state": "ready", "reused": True,
    }
    assert calls == ["health"]


def test_unready_owner_is_preserved_and_failed_candidate_stays_attributable(
    config: ServiceConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = binding(tmp_path / "runtime")
    monkeypatch.setattr(local_service, "records", lambda _: [owner_record(selected, config)])
    candidate = SimpleNamespace(pid=456, poll=lambda: 1)
    monkeypatch.setattr(local_service, "spawn_daemon", lambda *a, **k: candidate)
    monkeypatch.setattr(local_service, "request", lambda *a, **_: {"ready": False})
    with pytest.raises(local_service.LocalStartupUncertain) as caught:
        local_service.start(selected, tmp_path / "policy.json", config)
    assert caught.value.process is candidate


def test_different_source_owner_is_preserved_without_control(
    config: ServiceConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = binding(tmp_path / "runtime")
    record = owner_record(selected, config)
    record["slot"] = "other-generation"
    monkeypatch.setattr(local_service, "records", lambda _: [record])
    monkeypatch.setattr(local_service, "request", lambda *a, **k: pytest.fail("controlled predecessor"))
    candidate = SimpleNamespace(pid=456, poll=lambda: 1)
    monkeypatch.setattr(local_service, "spawn_daemon", lambda *a, **k: candidate)
    with pytest.raises(local_service.LocalStartupUncertain):
        local_service.start(selected, tmp_path / "policy.json", config)


def test_explicit_stop_waits_for_exact_native_exit(
    config: ServiceConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = binding(tmp_path / "runtime")
    record = owner_record(selected, config)
    monkeypatch.setattr(local_service, "records", lambda _: [record])
    calls = []
    monkeypatch.setattr(
        local_service, "request",
        lambda actual, target, action, **options: calls.append((actual, target, action, options)),
    )
    local_service.stop(selected, config)
    assert calls == [(config, record, "stop", {"wait_exit": True})]


def test_new_owner_launches_native_source_qualified_interpreter_not_alternate(
    config: ServiceConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = binding(tmp_path / "runtime")
    candidate = SimpleNamespace(pid=456, poll=lambda: None)
    launched: list[list[str]] = []
    owner = owner_record(selected, config)
    owner["peer"]["pid"] = candidate.pid
    monkeypatch.setattr(sys, "executable", str(tmp_path / "unadmitted-python.exe"))
    from agent_ssh_service import source_start

    conversions = []
    native = "\\\\?\\" + str(selected.python)

    def convert(path: Path) -> str:
        assert path == selected.python
        conversions.append(path)
        return native

    monkeypatch.setattr(source_start, "load_provider", lambda slot: SimpleNamespace(
        _resources=lambda actual: (
            None, SimpleNamespace(_native_retained_path=convert),
        ) if actual == slot == selected.slot else pytest.fail("foreign slot"),
    ))
    monkeypatch.setattr(local_service, "records", lambda _: [owner] if launched else [])

    def spawn(command: list[str], **_: Any) -> SimpleNamespace:
        assert command[0] == native, "Source-qualified native spelling was not forwarded"
        launched.append(command)
        return candidate

    monkeypatch.setattr(local_service, "spawn_daemon", spawn)
    monkeypatch.setattr(
        local_service, "request",
        lambda _config, _record, operation, **_: (
            {"prepared": True} if operation == "prepare" else {"ready": True}
        ),
    )
    assert local_service.start(selected, tmp_path / "policy.json", config) == {
        "state": "ready", "reused": False,
    }
    assert conversions == [selected.python]
