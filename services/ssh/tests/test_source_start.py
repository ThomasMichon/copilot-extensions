"""Service-local Source receiver gates; no Gateway or native actor is started."""
import importlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fleet_manager.private_state import create_private_file
from agent_ssh_service.config import Document, ServiceConfig


def private_policy(path: Path, config: ServiceConfig) -> None:
    with open(create_private_file(path), "wb", closefd=True) as stream:
        stream.write(json.dumps(config.to_dict()).encode())


def test_source_receiver_uses_selected_association_before_local_start(
    config: ServiceConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = importlib.import_module("agent_ssh_service.source_start")
    owner = importlib.import_module("agent_ssh_service.local_service")
    policy = tmp_path / "policy.json"
    private_policy(policy, config)
    binding = SimpleNamespace(root=tmp_path / "runtime", slot=tmp_path / "runtime" / "versions" / "1")
    order = []
    def selected_source() -> SimpleNamespace:
        order.append("source")
        return binding
    monkeypatch.setattr(source, "selected_source", selected_source)
    def start(actual: Any, filename: Path, selected: ServiceConfig) -> Document:
        assert actual is binding and filename == policy
        assert selected.to_dict() == config.to_dict()
        order.append("start")
        return {"state": "ready", "reused": True}
    monkeypatch.setattr(owner, "start", start)
    assert source.dispatch(["__source-start", "--config", str(policy)]) == 0
    assert order == ["source", "start"]
    monkeypatch.setenv(source.POLICY_ENV, str(policy))
    assert source.dispatch(["__source-start"]) == 0
    assert order == ["source", "start", "source", "start"]


def test_source_receiver_does_not_convert_uncertain_startup_to_success(
    config: ServiceConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = importlib.import_module("agent_ssh_service.source_start")
    owner = importlib.import_module("agent_ssh_service.local_service")
    policy = tmp_path / "policy.json"
    private_policy(policy, config)
    monkeypatch.setattr(source, "selected_source", lambda: SimpleNamespace(
        root=tmp_path / "runtime", slot=tmp_path / "runtime" / "versions" / "1",
    ))
    def uncertain(*_: Any) -> None:
        raise RuntimeError("local-startup-outcome-unknown")
    monkeypatch.setattr(owner, "start", uncertain)
    with pytest.raises(RuntimeError, match="outcome-unknown"):
        source.dispatch(["__source-start", "--config", str(policy)])


def test_source_receiver_rejects_unknown_arguments_before_source_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = importlib.import_module("agent_ssh_service.source_start")
    monkeypatch.setattr(source, "selected_source", lambda: pytest.fail("Source lookup occurred"))
    with pytest.raises(SystemExit) as failure:
        source.dispatch(["__source-start", "--conf", "policy.json"])
    assert failure.value.code == 2


def test_missing_installed_provider_is_precise_unsupported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = importlib.import_module("agent_ssh_service.source_start")
    root = tmp_path / "runtime"
    prefix = root / "versions" / "1.0" / ".venv"
    prefix.mkdir(parents=True)
    monkeypatch.setattr(source.sys, "prefix", str(prefix))
    monkeypatch.setenv("AGENT_SSH_SERVICE_HOME", str(root))
    monkeypatch.setattr(source, "load_provider", lambda *_: SimpleNamespace())
    with pytest.raises(RuntimeError, match="installed-Source-provider-unsupported"):
        source.selected_source()


@pytest.mark.parametrize("location", ["configuration", "data"])
def test_private_policy_cannot_relocate_state_into_runtime(
    config: ServiceConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, location: str,
) -> None:
    source = importlib.import_module("agent_ssh_service.source_start")
    root = tmp_path / "runtime"
    root.mkdir()
    value = config.to_dict()
    if location == "data":
        value["data_dir"] = str(root / "state")
    policy = (root if location == "configuration" else tmp_path) / "policy.json"
    private_policy(policy, type(config).from_dict(value))
    with pytest.raises(ValueError, match="outside-runtime"):
        source.selected_policy(policy, SimpleNamespace(root=root))


def test_cli_preserves_precise_unsupported_provider_diagnostic(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    from agent_ssh_service import __main__, source_start

    def unsupported() -> None:
        raise RuntimeError("installed-Source-provider-unsupported")

    monkeypatch.setattr(source_start, "selected_source", unsupported)
    assert __main__.main(["__source-start", "--config", "unused.json"]) == 1
    assert capsys.readouterr().err.strip() == (
        "agent-ssh-service: installed-Source-provider-unsupported"
    )


@pytest.mark.skipif(os.name != "nt", reason="Native Windows extended executable spelling")
def test_selected_source_preserves_explicit_root_spelling_for_native_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = importlib.import_module("agent_ssh_service.source_start")
    root = tmp_path / "runtime"
    prefix = root / "versions" / "1.0" / ".venv"
    prefix.mkdir(parents=True)
    native_prefix = Path("\\\\?\\" + str(prefix))
    assert native_prefix.samefile(prefix)
    monkeypatch.setattr(source.sys, "prefix", str(native_prefix))
    monkeypatch.setenv(source.ROOT_ENV, str(root))

    def qualify(slot: Path, **_: Any) -> SimpleNamespace:
        if slot != prefix.parent:
            raise RuntimeError("application construction inputs changed before publication")
        assert slot.samefile(native_prefix.parent)
        return SimpleNamespace(slot=slot)

    monkeypatch.setattr(source, "load_provider", lambda _: SimpleNamespace(
        qualify_installed_application=qualify,
    ))
    assert source.selected_source().slot == prefix.parent
