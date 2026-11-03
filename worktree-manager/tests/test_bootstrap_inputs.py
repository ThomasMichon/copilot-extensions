"""Fresh callers share retained source, not a mutable bootstrap checkout."""

from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from worktree_manager import runtime_source, runtime_source_config


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    helper = importlib.import_module("bootstrap_inputs")
    source = tmp_path / "source"
    for path, name in (
        (source / "worktree-manager", "manager"),
        (source / "plugins" / "agent-worktrees", "engine"),
    ):
        (path / "src").mkdir(parents=True)
        (path / "pyproject.toml").write_text(
            f'[project]\nname="{name}"\nversion="1.0.0"\n', encoding="utf-8",
        )
        (path / "src" / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    (source / ".git").mkdir()
    monkeypatch.setattr(runtime_source, "_git", lambda *a: "a" * 40)
    monkeypatch.setattr(
        runtime_source_config, "configured_source", lambda: SimpleNamespace(channel="marketplace"),
    )
    return helper, source / "worktree-manager", tmp_path / "driver"


def test_equivalent_callers_share_the_complete_unchanged_source(inputs):
    helper, payload, driver = inputs
    first, share = helper.retain(payload, driver)
    second, share_again = helper.retain(payload, driver)
    assert first == second and share is True and share_again is True
    sdk = payload.parent / "plugins" / "agent-worktrees" / "src" / "module.py"
    sdk.write_text("VALUE = 2\n", encoding="utf-8")
    changed, _share = helper.retain(payload, driver)
    assert changed != first
    assert (first.parent / "plugins" / "agent-worktrees" / "src" / "module.py").read_text() == (
        "VALUE = 1\n"
    )


def test_changed_retained_source_is_refused_not_overwritten(inputs):
    helper, payload, driver = inputs
    retained, _share = helper.retain(payload, driver)
    damaged = retained / "src" / "module.py"
    damaged.write_text("VALUE = 2\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="differ from their addressed source"):
        helper.retain(payload, driver)
    assert damaged.read_text() == "VALUE = 2\n"


def test_unresolved_source_selection_does_not_even_coalesce_pending_work(inputs, monkeypatch):
    helper, payload, driver = inputs
    monkeypatch.setattr(
        runtime_source_config, "configured_source", lambda: SimpleNamespace(channel="dev"),
    )
    first, share = helper.retain(payload, driver)
    second, share_again = helper.retain(payload, driver)
    assert first != second and share is False and share_again is False


def test_prepared_source_retains_only_its_current_controller_without_sharing(inputs):
    helper, payload, driver = inputs
    first, share = helper.retain(payload, driver, include_sdk=False)
    second, share_again = helper.retain(payload, driver, include_sdk=False)
    assert first != second and share is False and share_again is False
    assert (first / "src" / "module.py").read_text() == "VALUE = 1\n"
    assert not (first.parent / "plugins").exists()


def test_moving_revision_is_not_published_or_joined(inputs, monkeypatch):
    helper, payload, driver = inputs
    heads = iter(("a" * 40, "b" * 40))
    monkeypatch.setattr(runtime_source, "_git", lambda *a: next(heads))
    with pytest.raises(RuntimeError, match="revision changed during retention"):
        helper.retain(payload, driver)
    assert not list((driver / "bootstrap-inputs").glob("*/worktree-manager"))
