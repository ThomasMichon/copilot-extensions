"""Actor-free controls of the prepared application's explicit no-service intent."""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "plugins/agent-vault/src/agent_vault"


@pytest.mark.parametrize("qualified", [False, True])
def test_client_only_checks_selection_without_service_or_activation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], qualified: bool,
) -> None:
    name = "promotion_vault_client_install"
    package = ModuleType(name)
    package.__dict__["__path__"] = []
    monkeypatch.setitem(sys.modules, name, package)
    for member in ("config", "configuration"):
        monkeypatch.setitem(sys.modules, name + "." + member, ModuleType(name + "." + member))
    spec = importlib.util.spec_from_file_location(name + ".source_start", SOURCE / "source_start.py")
    assert spec is not None and spec.loader is not None
    owner = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, owner)
    spec.loader.exec_module(owner)
    calls: list[str] = []

    def selected() -> None:
        calls.append("selection")
        if not qualified:
            raise ValueError("private diagnostic must not escape")

    monkeypatch.setattr(owner, "require_selected_slot", selected)
    # Neither activation configuration nor the generation owner exists here.
    assert owner.command(argparse.Namespace(no_service=True)) == (0 if qualified else 1)
    assert calls == ["selection"]
    assert "private diagnostic" not in capsys.readouterr().err


@pytest.mark.parametrize("ready", [False, True])
def test_default_start_still_ensures_the_selected_service(
    monkeypatch: pytest.MonkeyPatch, ready: bool,
) -> None:
    name = "promotion_vault_client_default"
    package = ModuleType(name)
    package.__dict__["__path__"] = []
    monkeypatch.setitem(sys.modules, name, package)
    monkeypatch.setitem(sys.modules, name + ".config", ModuleType(name + ".config"))
    configuration = ModuleType(name + ".configuration")
    configuration.__dict__["runtime_enabled"] = lambda: True
    monkeypatch.setitem(sys.modules, name + ".configuration", configuration)
    service = ModuleType(name + ".service_generation")
    calls: list[str] = []

    def ensure() -> bool:
        calls.append("ensure")
        return ready

    service.__dict__["ensure_selected"] = ensure
    monkeypatch.setitem(sys.modules, name + ".service_generation", service)
    spec = importlib.util.spec_from_file_location(name + ".source_start", SOURCE / "source_start.py")
    assert spec is not None and spec.loader is not None
    owner = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, owner)
    spec.loader.exec_module(owner)
    monkeypatch.setattr(owner, "require_selected_slot", lambda: calls.append("selection"))
    assert owner.command(SimpleNamespace(no_service=False)) == (0 if ready else 1)
    assert calls == (["selection", "ensure", "selection"] if ready else ["selection", "ensure"])
