"""Fresh callers share retained source, not a mutable bootstrap checkout."""

from __future__ import annotations

import importlib
import json
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


@pytest.fixture
def preparation_assets(inputs):
    helper, payload, driver = inputs
    tools = payload.parent / "tools"
    tools.mkdir()
    tool = tools / "prepare_runtime_source.py"
    tool.write_text("VALUE = 1\n", encoding="utf-8")
    (payload / "packaged-assets.json").write_text(json.dumps({
        "schema": "copilot-extensions.packaged-assets", "version": 1,
        "canonicalDir": "tools", "payloadDir": "scripts/runtime-source-tools",
        "files": [tool.name],
    }), encoding="utf-8")
    return helper, payload, driver, tool


def test_preparation_assets_are_retained_addressed_and_rechecked(preparation_assets):
    helper, payload, driver, tool = preparation_assets
    first, _share = helper.retain(payload, driver)
    target = Path("scripts/runtime-source-tools") / tool.name
    assert (first / target).read_bytes() == tool.read_bytes()
    assert not (payload / target).exists()
    assert helper.retain(payload, driver)[0] == first
    tool.write_text("VALUE = 2\n", encoding="utf-8")
    second, _share = helper.retain(payload, driver)
    assert second != first
    assert (first / target).read_text() == "VALUE = 1\n"
    (second / target).write_text("damaged\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="preparation assets differ"):
        helper.retain(payload, driver)


def test_preparation_asset_change_during_freezing_is_not_published(preparation_assets, monkeypatch):
    helper, payload, driver, tool = preparation_assets
    freeze = helper.engine.freeze_inputs

    def changed(*args, **kwargs):
        frozen = freeze(*args, **kwargs)
        tool.write_text("changed during freezing\n", encoding="utf-8")
        return frozen

    monkeypatch.setattr(helper.engine, "freeze_inputs", changed)
    with pytest.raises(RuntimeError, match="preparation assets changed"):
        helper.retain(payload, driver)
    assert not list((driver / "bootstrap-inputs").glob("*/worktree-manager"))


def test_retention_rejects_source_different_from_the_admitted_payload(preparation_assets):
    helper, payload, driver, _tool = preparation_assets
    with pytest.raises(RuntimeError, match="installation source changed before retention"):
        helper.retain(payload, driver, expected_payload_digest="a" * 64)
    assert not driver.exists()
