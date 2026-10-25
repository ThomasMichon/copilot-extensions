"""Standalone Dev qualification preserves bootstrap ownership and policy gates."""
from __future__ import annotations

import importlib
from pathlib import Path
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
    monkeypatch.setattr(runtime_source_self_update, "fetch", lambda *_: pytest.fail("no Dev fetch"))
    assert helper.manager_payload(tmp_path / "original", tmp_path) == tmp_path / "original"


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
    if valid:
        assert helper.manager_payload(payload, tmp_path) == payload
    else:
        with pytest.raises(ValueError, match="invalid prepared bytes|receipt disappeared"):
            helper.manager_payload(payload, tmp_path)


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
    monkeypatch.setattr(
        runtime_source_self_update, "fetch",
        lambda repo, root: calls.append((repo, root)) or tmp_path / "qualified",
    )
    before = list(helper.sys.path)
    if fault:
        with pytest.raises((ValueError, RuntimeError), match={
            "missing-helper": "declared local", "foreign-helper": "different process",
            "missing-git": "requires git",
        }[fault]):
            helper.manager_payload(tmp_path / "original", tmp_path)
        assert calls == []
    else:
        monkeypatch.chdir(tmp_path)
        source_config.set_source(repo="./upstream", ref="dev", root=tmp_path)
        assert helper.manager_payload(tmp_path / "original", Path(".")) == tmp_path / "qualified"
        assert calls == [(str(tmp_path / "upstream"), tmp_path)]
    assert helper.sys.path == before


@pytest.mark.parametrize("repair", [False, True])
def test_bootstrap_installs_qualified_manager_but_keeps_original_repair_sdk(
    bootstrap_case: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, repair: bool,
) -> None:
    state = bootstrap_case
    prepared = state.payload / "prepared"
    helper = _helper()
    calls = []
    monkeypatch.setattr(
        helper, "manager_payload",
        lambda payload, root: calls.append(("qualify", payload, root)) or prepared,
    )
    ownership = []
    monkeypatch.setattr(
        importlib.import_module("update_entry"), "validate_manager_ownership",
        lambda root, payload, **kwargs: ownership.append((root, payload)) or {},
    )

    def install(payload: Path, **kwargs: object) -> SimpleNamespace:
        calls.append(("install", payload, kwargs["root"]))
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
        ("qualify", state.payload, state.root), ("install", prepared, state.root),
    ]
    assert ownership == ([(state.root, state.payload)] * 2 if repair else [])
    dispatched = state.dispatch[0][1]
    assert dispatched[9:] == (
        ["update", "--repair-engine", str(state.payload.parent)] if repair else ["--version"]
    )


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
    monkeypatch.setattr(_helper(), "manager_payload", lambda payload, root: payload)
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
