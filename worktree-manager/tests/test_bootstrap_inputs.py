"""Fresh callers share retained source, not a mutable bootstrap checkout."""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import stat
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


@pytest.mark.parametrize("channel,has_revision", [("dev", True), ("marketplace", False)])
def test_unresolved_source_selection_reuses_bytes_without_allowing_request_sharing(
    inputs, monkeypatch, channel, has_revision,
):
    helper, payload, driver = inputs
    monkeypatch.setattr(
        runtime_source_config, "configured_source", lambda: SimpleNamespace(channel=channel),
    )
    if not has_revision:
        (payload.parent / ".git").rmdir()
    first, share = helper.retain(payload, driver)
    second, share_again = helper.retain(payload, driver)
    assert first == second and share is False and share_again is False
    assert len(list((driver / "bootstrap-inputs").glob("*/worktree-manager"))) == 1


def test_prepared_source_retains_only_its_current_controller_without_sharing(inputs):
    helper, payload, driver = inputs
    first, share = helper.retain(payload, driver, include_sdk=False)
    second, share_again = helper.retain(payload, driver, include_sdk=False)
    assert first == second and share is False and share_again is False
    assert (first / "src" / "module.py").read_text() == "VALUE = 1\n"
    assert not (first.parent / "plugins").exists()


def test_first_touch_retention_creates_private_queue_directories(inputs):
    helper, payload, driver = inputs
    previous = os.umask(0o022)
    try:
        helper.retain(payload, driver)
    finally:
        os.umask(previous)
    directories = (driver, driver / "bootstrap-inputs")
    if os.name != "nt":
        assert [stat.S_IMODE(path.stat().st_mode) for path in directories] == [0o700, 0o700]
        return
    import ctypes as c
    from ctypes import wintypes as w

    security = c.WinDLL("advapi32", use_last_error=True)
    kernel = c.WinDLL("kernel32", use_last_error=True)
    security.GetNamedSecurityInfoW.argtypes = [
        w.LPWSTR, c.c_int, w.DWORD, w.LPVOID, w.LPVOID, w.LPVOID, w.LPVOID,
        c.POINTER(w.LPVOID),
    ]
    security.GetNamedSecurityInfoW.restype = w.DWORD
    security.GetSecurityDescriptorControl.argtypes = [
        w.LPVOID, c.POINTER(w.USHORT), c.POINTER(w.DWORD),
    ]
    security.GetSecurityDescriptorControl.restype = w.BOOL
    kernel.LocalFree.argtypes = [w.LPVOID]
    kernel.LocalFree.restype = w.LPVOID
    for path in directories:
        descriptor, control, revision = w.LPVOID(), w.USHORT(), w.DWORD()
        assert security.GetNamedSecurityInfoW(
            str(path), 1, 4, None, None, None, None, c.byref(descriptor),
        ) == 0
        try:
            assert security.GetSecurityDescriptorControl(
                descriptor, c.byref(control), c.byref(revision),
            )
            assert control.value & 0x1000
        finally:
            kernel.LocalFree(descriptor)


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


def test_grouped_preparation_assets_retain_each_declared_root(preparation_assets):
    helper, payload, driver, tool = preparation_assets
    library = payload.parent / "libs" / "example" / "helper.py"
    library.parent.mkdir(parents=True)
    library.write_text("VALUE = 2\n", encoding="utf-8")
    groups = [
        {"canonicalDir": "tools", "payloadDir": "scripts/runtime-source-tools", "files": [tool.name]},
        {"canonicalDir": "tools", "payloadDir": "scripts/runtime-input-provider/tools", "files": [tool.name]},
        {"canonicalDir": "libs/example", "payloadDir": "scripts/runtime-input-provider/libs/example",
         "files": [library.name]},
    ]
    (payload / "packaged-assets.json").write_text(json.dumps({
        "schema": "copilot-extensions.packaged-assets", "version": 2, "groups": groups,
    }), encoding="utf-8")
    retained, _share = helper.retain(payload, driver)
    for group in groups:
        for name in group["files"]:
            assert (retained / group["payloadDir"] / name).read_bytes() == (
                payload.parent / group["canonicalDir"] / name
            ).read_bytes()
    assert helper.retain(payload, driver)[0] == retained
    library.write_text("VALUE = 3\n", encoding="utf-8")
    assert helper.retain(payload, driver)[0] != retained


@pytest.mark.parametrize("failure", ["traversal", "absolute", "duplicate", "overlap"])
def test_grouped_preparation_assets_refuse_unsafe_or_overlapping_targets(
    preparation_assets, failure,
):
    helper, payload, driver, tool = preparation_assets
    group = {"canonicalDir": "tools", "payloadDir": "scripts/runtime-source-tools", "files": [tool.name]}
    groups = [group]
    if failure == "traversal":
        group["canonicalDir"] = "../outside"
    elif failure == "absolute":
        group["payloadDir"] = "/outside"
    elif failure == "duplicate":
        groups.append(dict(group))
    else:
        groups.append({"canonicalDir": "tools", "payloadDir": "scripts", "files": ["runtime-source-tools"]})
    (payload / "packaged-assets.json").write_text(json.dumps({
        "schema": "copilot-extensions.packaged-assets", "version": 2, "groups": groups,
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid|Duplicate"):
        helper.retain(payload, driver)
    assert not driver.exists()


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
