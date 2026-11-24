"""Actor-free execution of the selected-generation management owner."""

from __future__ import annotations

import ast
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from typing import Any, cast

import pytest


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = "promotion_vault_generation"


def _health(identity: str) -> dict[str, Any]:
    return {
        "ok": True, "pid": 7, "generation_protocol": 1,
        "runtime_generation": identity, "accepting_work": True,
    }


@pytest.fixture
def owner(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    state = SimpleNamespace(
        selected=True, status=_health("older"), launches=[], probes=0, transition=True,
        on_probe=lambda: None,
    )
    package = ModuleType(PACKAGE)
    package.__path__ = []
    service = ModuleType(f"{PACKAGE}.service")
    service.__dict__["run_dir"] = lambda: "fixture"
    launch = ModuleType(f"{PACKAGE}.launch")

    def spawn(arguments: list[str]) -> None:
        state.launches.append(list(arguments))
        if state.transition:
            state.status = _health("current")

    def health(endpoint: object) -> dict[str, Any]:
        state.probes += 1
        state.on_probe()
        return cast(dict[str, Any], state.status)

    launch.__dict__["spawn_service"] = spawn
    for module in (package, service, launch):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    source = ROOT / "plugins/agent-vault/src/agent_vault/service_generation.py"
    names = {"valid_generation_health", "same_generation", "ensure_selected"}
    tree = ast.parse(source.read_bytes(), filename=str(source))
    statements: list[ast.stmt] = [
        node for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module == "__future__"
        or isinstance(node, ast.FunctionDef) and node.name in names
    ]
    assert len(statements) == len(names) + 1
    clock = iter((0.0, 1.0, 2.0, 3.0))
    namespace: dict[str, Any] = {
        "__name__": f"{PACKAGE}.owner", "__package__": PACKAGE,
        "Any": Any, "PROTOCOL": 1, "PROCESS_GENERATION": "current",
        "selected": lambda: state.selected,
        "read_endpoint": lambda root: "fixture-endpoint",
        "endpoint_health": health,
        "time": SimpleNamespace(monotonic=lambda: next(clock), sleep=lambda _: None),
    }
    exec(compile(ast.Module(body=statements, type_ignores=[]), str(source), "exec"), namespace)
    state.ensure = namespace["ensure_selected"]
    return state


def test_healthy_prior_generation_cannot_complete_current_management(owner: SimpleNamespace) -> None:
    assert owner.ensure()
    assert owner.probes == 2
    assert owner.launches == [["--foreground", "--persistent"]]


def test_ready_current_management_does_not_restart(owner: SimpleNamespace) -> None:
    owner.status = _health("current")
    assert owner.ensure()
    assert owner.probes == 1
    assert owner.launches == []


@pytest.mark.parametrize("during_probe", [False, True])
def test_lost_selection_never_certifies_an_old_ready_process(
    owner: SimpleNamespace, during_probe: bool,
) -> None:
    owner.status = _health("current")
    if during_probe:
        owner.on_probe = lambda: setattr(owner, "selected", False)
    else:
        owner.selected = False
    assert owner.ensure() is False
    assert owner.launches == []
    assert owner.probes == int(during_probe)


def test_current_selection_is_rechecked_after_incoming_health(owner: SimpleNamespace) -> None:
    def reselect() -> None:
        if owner.probes > 1:
            owner.selected = False

    owner.on_probe = reselect
    assert owner.ensure() is False
    assert len(owner.launches) == 1


def test_reselection_during_stale_health_never_launches(owner: SimpleNamespace) -> None:
    owner.on_probe = lambda: setattr(owner, "selected", False)
    assert owner.ensure() is False
    assert owner.launches == []
    assert owner.probes == 1


def test_ready_prior_peer_does_not_mask_failed_incoming_readiness(owner: SimpleNamespace) -> None:
    owner.transition = False
    assert owner.ensure(timeout=0) is False
    assert owner.launches == [["--foreground", "--persistent"]]
