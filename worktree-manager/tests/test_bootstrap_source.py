"""Standalone Dev qualification preserves bootstrap ownership and policy gates."""
from __future__ import annotations

import importlib
from contextlib import contextmanager, nullcontext
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

from worktree_manager import runtime_source_self_update, self_install, source_config
from test_update_bootstrap_activation import (
    CommandDispatched,
    constructed_update as _constructed_update,
)

bootstrap_case = _constructed_update


def _helper() -> ModuleType:
    self_install._runtime_installer()
    return importlib.import_module("source_acquisition")


@pytest.mark.parametrize("ref", ["main", "canary"])
def test_other_manager_refs_do_not_acquire(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ref: str,
) -> None:
    helper = _helper()
    source_config.set_source(ref=ref, root=tmp_path)
    monkeypatch.setattr(helper.engine, "_package_roots", lambda *_: pytest.fail("no source loader"))
    monkeypatch.setattr(runtime_source_self_update, "prepared", lambda *_: pytest.fail("no Dev fetch"))
    with helper.manager_payload(tmp_path / "original", tmp_path) as (payload, options):
        assert payload == tmp_path / "original" and options == {}


@pytest.mark.parametrize("valid", [False, True, None])
def test_explicit_prepared_payload_is_validated_without_reselecting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, valid: bool | None,
) -> None:
    helper = _helper()
    source_config.set_source(ref="dev", root=tmp_path)
    payload = tmp_path / "prepared"
    payload.mkdir()
    (payload / "runtime-source.json").write_text("{}")
    (payload / "pyproject.toml").write_text('[project]\nversion = "1.0.0"\n')
    module = payload / "src/worktree_manager"
    module.mkdir(parents=True)
    (module / "__init__.py").write_text('__version__ = "1.0.0"\n')

    def validate(path: Path, version: str, *, plugin: str) -> object:
        assert (path, version, plugin) == (payload, "1.0.0", "worktree-manager")
        if valid is None:
            return None
        if not valid:
            raise ValueError("invalid prepared bytes")
        return object()

    monkeypatch.setattr(helper.runtime_source, "read_source_identity", validate)
    monkeypatch.setattr(helper.shutil, "which", lambda _: pytest.fail("prepared input needs no Git"))
    with pytest.raises(
        ValueError, match="complete --install-source|invalid prepared bytes|receipt disappeared",
    ):
        with helper.manager_payload(payload, tmp_path):
            pytest.fail("raw attributed source entered Manager construction")


@pytest.mark.parametrize("fault", ["", "missing-helper", "foreign-helper", "missing-git"])
def test_dev_uses_only_own_declared_helper_before_shared_acquisition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str,
) -> None:
    helper = _helper()
    source_config.set_source(repo=str(tmp_path / "upstream"), ref="dev", root=tmp_path)
    package = tmp_path / "process-helper"
    package.mkdir()
    (package / "pyproject.toml").write_text('[project]\nname = "agent-procutil"\n')
    monkeypatch.setattr(helper.shutil, "which", lambda _: None if fault == "missing-git" else "git")
    monkeypatch.setattr(helper.engine, "_package_roots", lambda _: (
        () if fault == "missing-helper" else (package,)
    ))
    origin = (tmp_path / "foreign.py" if fault == "foreign-helper"
              else package / "src/agent_procutil/__init__.py")
    monkeypatch.setattr(helper.importlib, "import_module", lambda _: SimpleNamespace(__file__=str(origin)))
    calls = []
    @contextmanager
    def prepared(repo, root):
        calls.append((repo, root))
        try:
            yield tmp_path / "qualified", {
                "_application": tmp_path / "application", "_wheel_inputs": "retained-wheels",
                "_local_packages": ("copilot-extensions-worktree-manager",),
            }
        finally:
            calls.append("released")

    monkeypatch.setattr(runtime_source_self_update, "prepared", prepared)
    before = list(helper.sys.path)
    if fault:
        with pytest.raises((ValueError, RuntimeError), match={
            "missing-helper": "declared local", "foreign-helper": "different process",
            "missing-git": "requires git",
        }[fault]):
            with helper.manager_payload(tmp_path / "original", tmp_path):
                pytest.fail("invalid source entered construction")
        assert calls == []
    else:
        monkeypatch.chdir(tmp_path)
        source_config.set_source(repo="./upstream", ref="dev", root=tmp_path)
        with helper.manager_payload(tmp_path / "original", Path(".")) as (payload, options):
            assert payload == tmp_path / "qualified"
            assert options == {
                "application": tmp_path / "application", "wheel_inputs": "retained-wheels",
                "local_packages": ("copilot-extensions-worktree-manager",),
            }
            assert calls == [(str(tmp_path / "upstream"), tmp_path)]
        assert calls == [(str(tmp_path / "upstream"), tmp_path), "released"]
    assert helper.sys.path == before


@pytest.mark.parametrize("repair", [False, True])
def test_bootstrap_installs_qualified_manager_but_keeps_original_repair_sdk(
    bootstrap_case: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, repair: bool,
) -> None:
    state = bootstrap_case
    prepared = state.payload / "prepared"
    helper = _helper()
    calls = []
    held = []

    @contextmanager
    def qualify(payload, root):
        calls.append(("qualify", payload, root))
        held.append(True)
        try:
            yield prepared, {
                "wheel_inputs": "retained-wheels", "local_packages": ("manager",),
                "application": state.payload / "application",
            }
        finally:
            held.clear()
            calls.append(("release",))

    monkeypatch.setattr(helper, "manager_payload", qualify)
    ownership = []
    monkeypatch.setattr(
        importlib.import_module("update_entry"), "validate_manager_ownership",
        lambda root, payload, **kwargs: ownership.append((root, payload)) or {},
    )

    def install(payload: Path, **kwargs: object) -> SimpleNamespace:
        calls.append(("install", payload, kwargs["root"]))
        assert held == [True]
        assert kwargs["_wheel_inputs"] == "retained-wheels"
        assert kwargs["_local_packages"] == ("manager",)
        assert kwargs["_application"] == state.payload / "application"
        if repair:
            assert kwargs["_transactional_activation"]
            kwargs["_before_publish"]()
        return SimpleNamespace(action="installed", slot=str(state.slot))

    monkeypatch.setattr(self_install, "self_install", install)
    arguments = ["--root", str(state.root), "--payload", str(state.payload)]
    arguments += ["--repair-update", "--", "update"] if repair else ["--", "--version"]
    with pytest.raises(CommandDispatched):
        state.installer.main(arguments)
    assert calls == [
        ("qualify", state.payload, state.root), ("install", prepared, state.root), ("release",),
    ]
    assert ownership == ([(state.root, state.payload)] * 2 if repair else [])
    dispatched = state.dispatch[0][1]
    assert dispatched[9:] == (
        ["update", "--repair-engine", str(state.payload.parent)] if repair else ["--version"]
    )
    assert not held


def test_bootstrap_acquisition_failure_never_constructs_or_dispatches(
    bootstrap_case: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state = bootstrap_case

    def refuse(*_: object) -> Path:
        raise ValueError("controlled source refusal")

    monkeypatch.setattr(_helper(), "manager_payload", refuse)
    monkeypatch.setattr(self_install, "self_install", lambda *a, **kw: pytest.fail("no construction"))
    assert state.installer.main([
        "--root", str(state.root), "--payload", str(state.payload), "--", "--version",
    ]) == 1
    assert not state.dispatch
    assert "Manager source acquisition refused: controlled source refusal" in capsys.readouterr().err


@pytest.mark.parametrize("installation_error", [False, True])
def test_complete_input_exit_failure_does_not_repeat_install_or_dispatch(
    bootstrap_case, monkeypatch, installation_error,
):
    state = bootstrap_case
    calls = []

    @contextmanager
    def qualified(payload, root):
        calls.append("acquired")
        try:
            yield payload, {"application": payload / "application"}
        finally:
            calls.append("revalidated")
            raise ValueError("held source changed on exit")

    def install(payload, **options):
        assert calls == ["acquired"]
        assert options["_application"] == payload / "application"
        calls.append("installed")
        return SimpleNamespace(
            action="error" if installation_error else "installed",
            reason="controlled construction failure", slot=str(state.slot),
        )

    monkeypatch.setattr(_helper(), "manager_payload", qualified)
    monkeypatch.setattr(self_install, "self_install", install)
    with pytest.raises(ValueError, match="held source changed on exit"):
        state.installer.main([
            "--root", str(state.root), "--payload", str(state.payload), "--", "--version",
        ])
    assert calls == ["acquired", "installed", "revalidated"]
    assert not state.dispatch
    for name, content in state.selectors.items():
        assert (state.root / name).read_bytes() == content


def test_original_source_digest_refuses_before_complete_acquisition(bootstrap_case, monkeypatch):
    state = bootstrap_case
    monkeypatch.setattr(state.installer, "_source_digest", lambda payload: "b" * 64)
    monkeypatch.setattr(_helper(), "manager_payload", lambda *args: pytest.fail(
        "changed driver source reached complete preparation",
    ))
    with pytest.raises(RuntimeError, match="source changed after driver admission"):
        state.installer.main([
            "--root", str(state.root), "--payload", str(state.payload),
            "--install-only", "--source-digest", "a" * 64,
        ])
    assert not state.dispatch


def test_standalone_install_capabilities_are_read_only(bootstrap_case, monkeypatch, capsys):
    state = bootstrap_case
    monkeypatch.setattr(
        state.installer, "_execution_driver",
        lambda *args: pytest.fail("capability discovery entered installation"),
    )
    assert state.installer.main(["--describe-install"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "schema": "copilot-extensions.manager-install-entry", "version": 1,
        "provider": "worktree-manager", "capabilities": {"stage": 1, "activate": 1},
    }
    assert not state.dispatch


@pytest.mark.parametrize("extra", [
    ["--install-source", "missing-input"], ["--describe-source-inputs"],
])
def test_install_discovery_rejects_source_options_before_reading_inputs(
    bootstrap_case, monkeypatch, extra,
):
    source = ModuleType("install_source")

    def forbidden(*args):
        pytest.fail("installation discovery read operation inputs")

    monkeypatch.setattr(source, "select", forbidden, raising=False)
    monkeypatch.setitem(sys.modules, "install_source", source)
    with pytest.raises(SystemExit) as rejected:
        bootstrap_case.installer.main(["--describe-install", *extra])
    assert rejected.value.code == 2
    assert not bootstrap_case.dispatch


@pytest.mark.parametrize("action", ["staged", "installed"])
def test_standalone_stage_prepares_without_dispatch_or_activation(bootstrap_case, monkeypatch, capsys, action):
    state = bootstrap_case
    monkeypatch.setattr(_helper(), "manager_payload", lambda payload, root: nullcontext((payload, {})))
    calls = []

    def install(payload, **options):
        assert payload == state.payload and options["root"] == state.root
        assert options["stage_only"] is True and options["dry_run"] is False
        calls.append(options)
        return SimpleNamespace(action=action, slot=str(state.slot))

    monkeypatch.setattr(self_install, "self_install", install)
    code = state.installer.main([
        "--root", str(state.root), "--payload", str(state.payload), "--stage",
    ])
    output = capsys.readouterr()
    if action == "staged":
        assert code == 0
        assert json.loads(output.out) == {
            "schema": "copilot-extensions.manager-stage", "version": 1,
            "root": str(state.root), "source": None, "slot": str(state.slot),
        }
    else:
        assert code == 1 and not output.out
        assert "non-staged result" in output.err
    assert len(calls) == 1 and not state.dispatch
    for name, before in state.selectors.items():
        assert (state.root / name).read_bytes() == before


@pytest.mark.parametrize("extra", [
    ["--", "--version"], ["--repair-update"], ["--recover-for-repair"],
])
def test_standalone_stage_rejects_other_operations_before_installation(
    bootstrap_case, monkeypatch, extra,
):
    state = bootstrap_case
    monkeypatch.setattr(
        state.installer, "_execution_driver",
        lambda *args: pytest.fail("invalid staging arguments reached installation"),
    )
    with pytest.raises(SystemExit) as rejected:
        state.installer.main(["--root", str(state.root), "--stage", *extra])
    assert rejected.value.code == 2
    assert not state.dispatch


@pytest.mark.parametrize("targeted", [False, True])
def test_only_suite_repair_admits_independent_service_policy(
    bootstrap_case: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, targeted: bool,
) -> None:
    from worktree_manager.runtime_source_config import RuntimeSource, set_source

    state = bootstrap_case
    set_source(RuntimeSource("dev", allow_dev=True), root=state.root)
    calls = []
    monkeypatch.setattr(
        importlib.import_module("update_entry"), "validate_manager_ownership",
        lambda *a, **kw: calls.append("ownership") or {},
    )
    monkeypatch.setattr(_helper(), "manager_payload", lambda payload, root: nullcontext((payload, {})))
    monkeypatch.setattr(
        self_install, "self_install",
        lambda *a, **kw: calls.append("install") or SimpleNamespace(
            action="installed", slot=str(state.slot),
        ),
    )
    arguments = [
        "--root", str(state.root), "--payload", str(state.payload),
        "--repair-update", "--", "update",
    ]
    if targeted:
        assert state.installer.main([*arguments, "--target", "agent-worktrees"]) == 1
        assert calls == [] and not state.dispatch
    else:
        with pytest.raises(CommandDispatched):
            state.installer.main(arguments)
        assert calls == ["ownership", "install"]
        assert state.dispatch[0][1][9:] == ["update", "--repair-engine", str(state.payload.parent)]
