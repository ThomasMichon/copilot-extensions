"""Manager bootstrap input selection without release, process or install actors."""
from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest


@pytest.fixture
def bootstrap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    manager = ModuleType("worktree_manager.self_install")
    monkeypatch.setattr(manager, "manager_ref", lambda root: "main", raising=False)
    monkeypatch.setattr(manager, "manager_repo", lambda root: "https://github.com/example/extensions.git", raising=False)
    monkeypatch.setattr(manager, "payload_version", lambda payload: "1.0", raising=False)
    sources = ModuleType("versioned_runtime_source")
    monkeypatch.setattr(sources, "SOURCE_FILE", "runtime-source.json", raising=False)
    released = ModuleType("worktree_manager.runtime_source_release")
    package = ModuleType("worktree_manager")
    monkeypatch.setattr(package, "runtime_source_release", released, raising=False)
    for name, module in {
        "worktree_manager": package,
        "worktree_manager.self_install": manager,
        "worktree_manager.runtime_source_release": released,
        "installer_engine": ModuleType("installer_engine"),
        "versioned_runtime_source": sources,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    path = Path(__file__).resolve().parents[2] / "worktree-manager/scripts/source_acquisition.py"
    spec = importlib.util.spec_from_file_location("promotion_manager_bootstrap_inputs", path)
    assert spec is not None and spec.loader is not None
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    monkeypatch.setattr(helper.shutil, "which", lambda _: "git")
    return SimpleNamespace(
        helper=helper, manager=manager, released=released,
        original=tmp_path / "source", selected=tmp_path / "released", root=tmp_path / "root",
        uv=tmp_path / "qualified-uv",
    )


@pytest.mark.parametrize("available", [False, True])
def test_main_bootstrap_prefers_held_wheels_without_application_export(
    bootstrap: SimpleNamespace, available: bool,
) -> None:
    state = bootstrap
    calls = []

    @contextmanager
    def prepared(repo: str, root: Path, **options: Any) -> Iterator[tuple[Path, dict[str, Any]] | None]:
        assert repo == state.manager.manager_repo(root) and root == state.root
        assert options == {"uv": state.uv}
        calls.append("acquired")
        try:
            yield (state.selected, {
                "_wheel_inputs": "wheels", "_local_packages": ("manager",),
            }) if available else None
        finally:
            calls.append("released")

    state.released.prepared = prepared
    with state.helper.manager_payload(state.original, state.root, uv=state.uv) as (payload, options):
        assert payload == (state.selected if available else state.original)
        assert options == ({
            "wheel_inputs": "wheels", "local_packages": ("manager",),
        } if available else {})
        assert calls == (["acquired"] if available else ["acquired", "released"])
        calls.append("consumed")
    assert calls == (
        ["acquired", "consumed", "released"] if available else ["acquired", "released", "consumed"]
    )


@pytest.mark.parametrize("failure", ["acquire", "release"])
def test_released_input_failure_does_not_repeat_bootstrap_with_source(
    bootstrap: SimpleNamespace, failure: str,
) -> None:
    state = bootstrap
    calls = []

    @contextmanager
    def prepared(*args: Any, **options: Any) -> Iterator[tuple[Path, dict[str, Any]]]:
        calls.append("acquired")
        if failure == "acquire":
            raise ValueError("release refused")
        try:
            yield state.selected, {"_wheel_inputs": "wheels", "_local_packages": ("manager",)}
        finally:
            calls.append("released")
            raise ValueError("release refused")

    state.released.prepared = prepared
    with pytest.raises(ValueError, match="release refused"):
        with state.helper.manager_payload(state.original, state.root) as (payload, options):
            assert payload == state.selected
            calls.append("consumed")
    assert calls == (["acquired"] if failure == "acquire" else ["acquired", "consumed", "released"])


@pytest.mark.parametrize("preserved", ["custom-ref", "no-git", "attributed", "repair"])
def test_unsupported_or_explicit_inputs_keep_the_original_bootstrap_owner(
    bootstrap: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, preserved: str,
) -> None:
    state = bootstrap
    state.original.mkdir()
    if preserved == "custom-ref":
        state.manager.manager_ref = lambda root: "canary"
    elif preserved == "no-git":
        monkeypatch.setattr(state.helper.shutil, "which", lambda _: None)
    elif preserved == "attributed":
        (state.original / "runtime-source.json").write_text("{}")
    state.released.prepared = lambda *a, **kw: pytest.fail("explicit input was reselected")
    with state.helper.manager_payload(
        state.original, state.root, prefer_release=preserved != "repair",
    ) as (payload, options):
        assert payload == state.original and options == {}
