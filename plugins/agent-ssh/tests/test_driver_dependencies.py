from __future__ import annotations

from contextlib import redirect_stdout
import argparse
import importlib.resources
import io
import json
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest
from agent_procutil.owned_process import OwnedCleanupError

from agent_ssh import _worktrees, driver, mesh
from agent_ssh import driver_dependencies as dependencies


def test_only_selected_driver_dependencies_are_resolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    machines = tmp_path / "machines.yaml"
    machines.write_text(
        "machines:\n  first:\n    ssh:\n"
        "      driver: {plugin: example-provider, module: example}\n"
        "  second:\n    ssh:\n"
        "      driver: {plugin: example-provider, module: example}\n"
        "  direct: {hostname: direct}\n",
        encoding="utf-8",
    )
    module = tmp_path / "module.yaml"
    module.write_text(
        "module: example\nruntimeDependencies: [example-provider/runtime.client_v2]\n",
        encoding="utf-8",
    )
    calls: list[driver.Driver] = []

    def find(selected: driver.Driver, cwd: Path) -> Path:
        calls.append(selected)
        return module

    monkeypatch.setattr(driver, "find_module", find)
    assert dependencies.selected_dependencies(tmp_path, machines) == ("example-provider/runtime.client_v2",)
    assert calls == [driver.Driver("example-provider", "example")]


@pytest.mark.parametrize("composed", [False, True])
def test_composite_outbound_selection_is_per_machine_and_opt_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, composed: bool,
) -> None:
    machines = tmp_path / "machines.yaml"
    machines.write_text(
        "machines:\n  first:\n    ssh:\n"
        "      driver: {plugin: example-composer, module: topology}\n"
        "      outbound: []\n"
        "  second:\n    ssh:\n"
        "      driver: {plugin: example-composer, module: topology}\n"
        "      outbound: [{plugin: example-provider, module: example}]\n",
        encoding="utf-8",
    )
    composer = tmp_path / "topology.yaml"
    composer.write_text(
        f"module: topology\ncomposesOutboundDrivers: {str(composed).lower()}\n",
        encoding="utf-8",
    )
    provider = tmp_path / "provider.yaml"
    provider.write_text("module: example\nruntimeDependencies: [example-provider/runtime]\n", encoding="utf-8")
    calls: list[str] = []

    def find(selected: driver.Driver, cwd: Path) -> Path:
        calls.append(selected.label)
        return composer if selected.module == "topology" else provider

    monkeypatch.setattr(driver, "find_module", find)
    assert dependencies.selected_dependencies(tmp_path, machines) == (
        ("example-provider/runtime",) if composed else ()
    )
    assert calls == (
        ["example-composer:topology", "example-provider:example"]
        if composed else ["example-composer:topology"]
    )


def test_composite_cycles_refuse_before_owner_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    machines = tmp_path / "machines.yaml"
    machines.write_text(
        "machines:\n  worker:\n    ssh:\n"
        "      driver: {plugin: example-composer, module: topology}\n"
        "      outbound: [{plugin: example-composer, module: topology}]\n",
        encoding="utf-8",
    )
    module = tmp_path / "module.yaml"
    module.write_text("module: topology\ncomposesOutboundDrivers: true\n", encoding="utf-8")
    monkeypatch.setattr(driver, "find_module", lambda *_: module)
    monkeypatch.setattr(dependencies, "repair_command", lambda: pytest.fail("cyclic selection reached an owner"))
    result = dependencies.maintain(tmp_path, machines=machines, apply=True)
    assert result["state"] == "failed"
    assert "cycle" in result["detail"]


@pytest.mark.parametrize("apply,completion", [(False, "ready"), (True, "ready"), (True, "optional-incomplete")])
def test_selected_dependencies_use_bound_owner_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, apply: bool, completion: str,
) -> None:
    monkeypatch.setattr(dependencies, "selected_dependencies", lambda *_: ("example-provider/runtime",))
    monkeypatch.setattr(dependencies, "repair_command", lambda: (["python", "-m", "agent_machines"], {}))
    calls: list[list[str]] = []

    def request(
        command: list[str], environment: dict[str, str], cwd: Path, arguments: list[str],
    ) -> dict[str, Any]:
        calls.append(arguments)
        if "-c" in arguments:
            return {"schema": "copilot-extensions.installation-repair", "version": 1, "refresh": 1}
        if "--apply" in arguments:
            return {"status": completion, "requiredSatisfied": True}
        return {
            "schema": "copilot-extensions.installation-repair", "version": 1,
            "available": ["example-market::example-provider/runtime"],
            "selected": ["example-market::example-provider/runtime"], "planDigest": "bound-plan",
            "outcomes": [{"restart": "none"}],
        }

    monkeypatch.setattr(dependencies, "_request", request)
    result = dependencies.maintain(tmp_path, apply=apply)
    expected = "failed" if apply and completion != "ready" else "applied" if apply else "planned"
    assert result["state"] == expected
    assert any("--apply" in call for call in calls) is apply
    if apply:
        assert calls[-1][-4:] == ["--apply", "--refresh", "--expected-plan-digest", "bound-plan"]
        assert calls[-1][calls[-1].index("--module") + 1] == "example-market::example-provider/runtime"


def test_no_selected_dependency_never_resolves_or_executes_an_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(dependencies, "selected_dependencies", lambda *_: ())
    monkeypatch.setattr(dependencies, "repair_command", lambda: pytest.fail("unselected owner"))
    assert dependencies.maintain(tmp_path, apply=True)["state"] == "ready"


def test_owner_failure_is_not_reported_as_convergence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(dependencies, "selected_dependencies", lambda *_: ("example-provider/runtime",))
    monkeypatch.setattr(dependencies, "repair_command", lambda: (_ for _ in ()).throw(
        RuntimeError("owner-unavailable")
    ))
    result = dependencies.maintain(tmp_path, apply=True)
    assert result["state"] == "failed"
    assert "owner-unavailable" in result["detail"]


@pytest.mark.parametrize("restart", ["session", "machine"])
def test_restart_bearing_closure_refuses_before_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restart: str,
) -> None:
    monkeypatch.setattr(dependencies, "selected_dependencies", lambda *_: ("example-provider/runtime",))
    monkeypatch.setattr(dependencies, "repair_command", lambda: (["python", "-m", "agent_machines"], {}))
    calls: list[list[str]] = []

    def request(
        command: list[str], environment: dict[str, str], cwd: Path, arguments: list[str],
    ) -> dict[str, Any]:
        calls.append(arguments)
        if "-c" in arguments:
            return {"schema": "copilot-extensions.installation-repair", "version": 1, "refresh": 1}
        return {
            "schema": "copilot-extensions.installation-repair", "version": 1,
            "available": ["example-market::example-provider/runtime"],
            "selected": ["example-market::example-provider/runtime"], "planDigest": "bound-plan",
            "outcomes": [{"restart": restart}, {"restart": "none"}],
        }

    monkeypatch.setattr(dependencies, "_request", request)
    result = dependencies.maintain(tmp_path, apply=True)
    assert result["state"] == "failed"
    assert "restart-free closures" in result["detail"]
    assert not any("--apply" in call for call in calls)


def test_legacy_missing_descriptor_reports_unsupported_before_any_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(dependencies, "selected_dependencies", lambda *_: ("example-provider/runtime",))
    monkeypatch.setattr(dependencies, "repair_command", lambda: (["python", "-m", "agent_machines"], {}))
    calls: list[list[str]] = []

    def request(
        command: list[str], environment: dict[str, str], cwd: Path, arguments: list[str],
    ) -> dict[str, Any]:
        calls.append(arguments)
        assert "-c" in arguments
        output = io.StringIO()
        with monkeypatch.context() as patch:
            patch.setattr(importlib.resources, "files", lambda _: tmp_path)
            with redirect_stdout(output):
                exec(arguments[1], {})
        return cast(dict[str, Any], json.loads(output.getvalue()))

    monkeypatch.setattr(dependencies, "_request", request)
    result = dependencies.maintain(tmp_path, apply=True)
    assert result["state"] == "failed"
    assert "update its owning installation" in result["detail"]
    assert len(calls) == 1


@pytest.mark.parametrize("attempted", [False, True])
def test_multiple_repositories_continue_only_after_preapply_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], attempted: bool,
) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    calls: list[Path] = []

    def maintain(
        cwd: Path, *, machines: Path | None = None, apply: bool = False,
    ) -> tuple[dict[str, Any], bool]:
        calls.append(cwd)
        if cwd == first:
            return driver.make_result("failed", "owner unavailable"), attempted
        return driver.make_result("applied", "dependency converged"), True

    monkeypatch.setattr(dependencies, "_maintain", maintain)
    args = argparse.Namespace(project_root=[first, second], machines=None, apply=True, json=True)
    assert dependencies.command(args) == 1
    assert json.loads(capsys.readouterr().out)["state"] == "failed"
    assert calls == ([first] if attempted else [first, second])


def test_unconfirmed_native_cleanup_is_not_downgraded_to_a_preapply_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = OwnedCleanupError(None)

    def select(*_: Any) -> tuple[str, ...]:
        raise failure

    monkeypatch.setattr(dependencies, "selected_dependencies", select)
    with pytest.raises(OwnedCleanupError) as caught:
        dependencies.maintain(tmp_path, apply=True)
    assert caught.value is failure


@pytest.mark.parametrize("status", ["not-required", "unavailable", "unbound", "error"])
def test_missing_local_topology_requires_a_successful_external_scope_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: str,
) -> None:
    calls: list[Path] = []
    monkeypatch.setattr(_worktrees, "validate_context", lambda: None)
    monkeypatch.setattr(mesh, "find_machines_file", lambda _: None)

    def state_root(cwd: Path, own: object) -> tuple[None, str]:
        calls.append(cwd)
        return None, status

    monkeypatch.setattr(_worktrees, "state_root", state_root)
    monkeypatch.setattr(dependencies, "repair_command", lambda: pytest.fail("unknown topology reached installation"))
    result = dependencies.maintain(tmp_path, apply=True)
    assert result["state"] == ("ready" if status == "not-required" else "failed")
    assert calls == [tmp_path]


def test_external_lookup_retains_unconfirmed_native_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = OwnedCleanupError(None)

    def run(*args: object, **kwargs: object) -> object:
        raise failure

    monkeypatch.setattr(_worktrees, "run_caller_owned", run)
    with pytest.raises(OwnedCleanupError) as caught:
        _worktrees._read_state_root(["owner"], {}, tmp_path, None)
    assert caught.value is failure


def test_external_lookup_timeout_through_the_real_reader_is_not_an_empty_fleet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(_worktrees, "validate_context", lambda: None)
    monkeypatch.setattr(mesh, "find_machines_file", lambda _: None)

    def run(argv: list[str], **kwargs: object) -> object:
        calls.append(argv)
        raise subprocess.TimeoutExpired(argv, 5)

    monkeypatch.setattr(_worktrees, "run_caller_owned", run)
    monkeypatch.setattr(dependencies, "repair_command", lambda: pytest.fail("failed lookup reached installation"))
    result = dependencies.maintain(tmp_path, apply=True)
    assert result["state"] == "failed"
    assert "unavailable" in result["detail"]
    assert len(calls) == 1
