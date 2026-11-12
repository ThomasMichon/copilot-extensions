"""Fresh callers share retained source, not a mutable bootstrap checkout."""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import stat
import time
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


@pytest.mark.parametrize("winerror", [5, 32, 33])
def test_retention_retries_transient_windows_publication_locks(inputs, monkeypatch, winerror):
    helper, payload, driver = inputs
    rename, attempts, delays = Path.rename, [], []

    def locked(path, destination):
        if path.name == "inputs" and path.parent.name.startswith(".prepare-"):
            attempts.append(path)
            if len(attempts) < 3:
                error = PermissionError("transient publication lock")
                error.winerror = winerror
                raise error
        return rename(path, destination)

    monkeypatch.setattr(Path, "rename", locked)
    monkeypatch.setattr(time, "sleep", delays.append)
    retained, _share = helper.retain(payload, driver)
    assert len(attempts) == 3 and len(delays) == 2
    assert (retained / "src" / "module.py").read_text() == "VALUE = 1\n"


@pytest.mark.parametrize("winerror,expected", [(5, 4), (87, 1)])
def test_retention_publication_retry_is_bounded(inputs, monkeypatch, winerror, expected):
    helper, payload, driver = inputs
    rename, attempts = Path.rename, []

    def denied(path, destination):
        if path.name == "inputs" and path.parent.name.startswith(".prepare-"):
            attempts.append(path)
            error = PermissionError("publication denied")
            error.winerror = winerror
            raise error
        return rename(path, destination)

    monkeypatch.setattr(Path, "rename", denied)
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    with pytest.raises(PermissionError, match="publication denied"):
        helper.retain(payload, driver)
    assert len(attempts) == expected
    assert not list((driver / "bootstrap-inputs").glob("*/worktree-manager"))


@pytest.mark.parametrize("changed", ["source", "candidate"])
def test_retention_rechecks_source_and_candidate_after_a_lock(inputs, monkeypatch, changed):
    helper, payload, driver = inputs
    rename, attempts = Path.rename, []

    def mutate(path, destination):
        if path.name == "inputs" and path.parent.name.startswith(".prepare-"):
            attempts.append(path)
            target = payload if changed == "source" else path / "worktree-manager"
            (target / "src" / "module.py").write_text("CHANGED = True\n", encoding="utf-8")
            error = PermissionError("transient publication lock")
            error.winerror = 32
            raise error
        return rename(path, destination)

    monkeypatch.setattr(Path, "rename", mutate)
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    with pytest.raises(RuntimeError, match="changed|differ"):
        helper.retain(payload, driver)
    assert len(attempts) == 1
    assert not list((driver / "bootstrap-inputs").glob("*/worktree-manager"))


def test_retention_does_not_replace_a_conflicting_snapshot_after_a_lock(inputs, monkeypatch):
    helper, payload, driver = inputs
    rename, winners = Path.rename, []

    def competing(path, destination):
        if path.name == "inputs" and path.parent.name.startswith(".prepare-"):
            destination.mkdir()
            (destination / "foreign").write_text("preserve", encoding="utf-8")
            winners.append(destination)
            error = PermissionError("transient publication lock")
            error.winerror = 32
            raise error
        return rename(path, destination)

    monkeypatch.setattr(Path, "rename", competing)
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    with pytest.raises(FileExistsError):
        helper.retain(payload, driver)
    assert len(winners) == 1
    assert (winners[0] / "foreign").read_text() == "preserve"


@pytest.fixture(params=[1, 2])
def preparation_assets(inputs, request):
    helper, payload, driver = inputs
    tools = payload.parent / "tools"
    tools.mkdir()
    tool = tools / "prepare_runtime_source.py"
    tool.write_text("VALUE = 1\n", encoding="utf-8")
    version = request.param
    (payload / "packaged-assets.json").write_text(json.dumps({
        "schema": "copilot-extensions.packaged-assets", "version": version,
        "canonicalDir": "tools" if version == 1 else ".",
        "payloadDir": "scripts/runtime-source-tools",
        "files": [tool.name if version == 1 else "tools/" + tool.name],
    }), encoding="utf-8")
    return helper, payload, driver, tool


def test_preparation_assets_are_retained_addressed_and_rechecked(preparation_assets):
    helper, payload, driver, tool = preparation_assets
    first, _share = helper.retain(payload, driver)
    declaration = json.loads((payload / "packaged-assets.json").read_text())
    target = Path(declaration["payloadDir"]) / declaration["files"][0]
    first_target = Path(helper.engine._native_retained_path(first / target))
    assert first_target.read_bytes() == tool.read_bytes()
    assert not (payload / target).exists()
    assert helper.retain(payload, driver)[0] == first
    tool.write_text("VALUE = 2\n", encoding="utf-8")
    second, _share = helper.retain(payload, driver)
    assert second != first
    assert first_target.read_text() == "VALUE = 1\n"
    Path(helper.engine._native_retained_path(second / target)).write_text("damaged\n", encoding="utf-8")
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
        {"canonicalDir": ".", "payloadDir": "scripts/runtime-source-tools",
         "files": ["tools/" + tool.name]},
    ]
    (payload / "packaged-assets.json").write_text(json.dumps({
        "schema": "copilot-extensions.packaged-assets", "version": 2, "groups": groups,
    }), encoding="utf-8")
    retained, _share = helper.retain(payload, driver)
    for group in groups:
        for name in group["files"]:
            target = Path(helper.engine._native_retained_path(retained / group["payloadDir"] / name))
            assert target.read_bytes() == (
                payload.parent / group["canonicalDir"] / name
            ).read_bytes()
    assert helper.retain(payload, driver)[0] == retained
    library.write_text("VALUE = 3\n", encoding="utf-8")
    assert helper.retain(payload, driver)[0] != retained


def _manager_asset_groups():
    consumer = Path(__file__).resolve().parents[1]
    groups = []
    for name in ("packaged-assets.json", "launch-wrapper-assets.json"):
        declaration = json.loads((consumer / name).read_text(encoding="utf-8"))
        groups.extend((name, group) for group in declaration.get("groups", [declaration]))
    return groups


@pytest.fixture(scope="module")
def materialized_manager_assets(tmp_path_factory):
    root = Path(__file__).resolve().parents[2]
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.syspath_prepend(str(root / "tools"))
        assets = importlib.import_module("launch_wrapper_assets_ref")
        materializer = importlib.import_module("materialize_main")
        consumer = root / "worktree-manager"
        expected = []
        for manifest_name, group in _manager_asset_groups():
            # A running pre-upgrade consumer has the original relative-root grammar.
            assets._manifest_group(consumer / manifest_name, group, manifest_name=manifest_name)
            expected.append({
                Path(group["payloadDir"]) / name: root / group["canonicalDir"] / name
                for name in group["files"]
            })
        destination = tmp_path_factory.mktemp("manager-assets") / "worktree-manager"
        destination.mkdir()
        report = materializer.materialize_launch_wrapper_assets_into(
            source_consumer_dir=consumer, dest_consumer_dir=destination, canonical_root=root,
        )
        assert report and all(line.startswith("OK ") for line in report), report
        actual = {
            path.relative_to(destination)
            for path in destination.rglob("*") if path.is_file()
        }
        assert actual == {path for group in expected for path in group}
        yield destination, expected


@pytest.mark.parametrize("group_index", range(len(_manager_asset_groups())))
def test_manager_assets_materialize_for_pre_upgrade_group_reader(
    materialized_manager_assets, group_index,
):
    destination, groups = materialized_manager_assets
    for path, source in groups[group_index].items():
        assert (destination / path).read_bytes() == source.read_bytes(), path


@pytest.mark.parametrize("failure", [
    "traversal", "absolute", "duplicate", "overlap", "case-duplicate", "case-overlap",
])
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
    elif failure == "case-duplicate":
        group["files"].append(tool.name.upper())
    elif failure == "case-overlap":
        groups.append({"canonicalDir": "tools", "payloadDir": "SCRIPTS", "files": ["RUNTIME-SOURCE-TOOLS"]})
    else:
        groups.append({"canonicalDir": "tools", "payloadDir": "scripts", "files": ["runtime-source-tools"]})
    (payload / "packaged-assets.json").write_text(json.dumps({
        "schema": "copilot-extensions.packaged-assets", "version": 2, "groups": groups,
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid|Duplicate"):
        helper.retain(payload, driver)
    assert not driver.exists()


def test_invocation_tooling_is_retained_and_rechecked_with_preparer(preparation_assets):
    helper, payload, driver, _tool = preparation_assets
    tooling = payload.parent / "libs/payload-invocation"
    tooling.mkdir(parents=True)
    names = ("generate.py", "runtime_admission.py")
    for name in names:
        (tooling / name).write_text("# pinned " + name + "\n", encoding="utf-8")
    (payload / "launch-wrapper-assets.json").write_text(json.dumps({
        "schema": "copilot-extensions.launch-wrapper-assets", "version": 1,
        "canonicalDir": "libs/payload-invocation", "payloadDir": "scripts/libs/payload-invocation",
        "files": list(names),
    }), encoding="utf-8")
    retained, _share = helper.retain(payload, driver)
    for name in names:
        assert (retained / "scripts/libs/payload-invocation" / name).read_bytes() == (
            tooling / name
        ).read_bytes()
    (retained / "scripts/libs/payload-invocation/generate.py").write_text("damaged\n")
    with pytest.raises(RuntimeError, match="preparation assets differ"):
        helper.retain(payload, driver)


@pytest.mark.parametrize("materialized", [False, True])
def test_preparation_assets_refuse_shared_targets_across_manifests(preparation_assets, materialized):
    helper, payload, driver, tool = preparation_assets
    directory = "scripts/libs/payload-invocation"
    library = payload.parent / "libs/payload-invocation" / tool.name
    library.parent.mkdir(parents=True)
    library.write_text("VALUE = 2\n", encoding="utf-8")
    (payload / "packaged-assets.json").write_text(json.dumps({
        "schema": "copilot-extensions.packaged-assets", "version": 2, "groups": [{
            "canonicalDir": "tools", "payloadDir": directory, "files": [tool.name],
        }],
    }), encoding="utf-8")
    (payload / "launch-wrapper-assets.json").write_text(json.dumps({
        "schema": "copilot-extensions.launch-wrapper-assets", "version": 1,
        "canonicalDir": "libs/payload-invocation", "payloadDir": directory, "files": [tool.name],
    }), encoding="utf-8")
    if materialized:
        target = payload / directory / tool.name
        target.parent.mkdir(parents=True)
        target.write_text("VALUE = 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate"):
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


@pytest.mark.parametrize("changed", ["source", "candidate"])
def test_retention_rechecks_preparation_assets_after_a_lock(preparation_assets, monkeypatch, changed):
    helper, payload, driver, tool = preparation_assets
    rename, attempts = Path.rename, []
    declaration = json.loads((payload / "packaged-assets.json").read_text())
    relative = Path(declaration["payloadDir"]) / declaration["files"][0]

    def mutate(path, destination):
        if path.name == "inputs" and path.parent.name.startswith(".prepare-"):
            attempts.append(path)
            target = tool if changed == "source" else (
                path / "worktree-manager" / relative
            )
            target.write_text("changed after freezing\n", encoding="utf-8")
            error = PermissionError("transient publication lock")
            error.winerror = 32
            raise error
        return rename(path, destination)

    monkeypatch.setattr(Path, "rename", mutate)
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    with pytest.raises(RuntimeError, match="changed|differ"):
        helper.retain(payload, driver)
    assert len(attempts) == 1
    assert not list((driver / "bootstrap-inputs").glob("*/worktree-manager"))


@pytest.mark.skipif(os.name != "nt", reason="Windows extended path handling")
def test_retention_revalidates_long_windows_asset_path(preparation_assets):
    helper, payload, driver, tool = preparation_assets
    declaration = json.loads((payload / "packaged-assets.json").read_text())
    relative = Path(declaration["payloadDir"]) / declaration["files"][0]
    expected = driver / "bootstrap-inputs" / ("a" * 64) / "worktree-manager" / relative
    padding = max(0, 270 - len(str(expected)) - 1)
    if padding:
        driver = driver / ("x" * padding)
    retained, _share = helper.retain(payload, driver)
    selected = retained / relative
    assert len(str(selected)) >= 260
    assert Path(helper.engine._native_retained_path(selected)).read_bytes() == tool.read_bytes()


@pytest.mark.skipif(os.name != "nt", reason="Windows extended path handling")
@pytest.mark.parametrize("fail_copy", [False, True])
def test_retention_freezes_and_cleans_deep_windows_inputs(inputs, monkeypatch, fail_copy):
    helper, payload, driver = inputs
    expected = driver / "bootstrap-inputs" / ("a" * 64) / "worktree-manager" / "src"
    padding = max(1, 320 - len(str(expected / "module.py")) - 1)
    relative = Path("x" * padding) / "module.py"
    original = payload / "src" / relative
    Path(helper.engine._native_retained_path(original.parent)).mkdir(parents=True)
    Path(helper.engine._native_retained_path(original)).write_text("DEEP = True\n", encoding="utf-8")
    if fail_copy:
        copymode = helper.engine.shutil.copymode

        def interrupted(source, target, **kwargs):
            if str(target).endswith(str(relative)):
                raise RuntimeError("interrupted deep copy")
            return copymode(source, target, **kwargs)

        monkeypatch.setattr(helper.engine.shutil, "copymode", interrupted)
        with pytest.raises(RuntimeError, match="interrupted deep copy"):
            helper.retain(payload, driver)
        assert not list((driver / "bootstrap-inputs").glob(".prepare-*"))
    else:
        retained, _share = helper.retain(payload, driver)
        selected = retained / "src" / relative
        assert len(str(selected)) >= 260
        assert Path(helper.engine._native_retained_path(selected)).read_text() == "DEEP = True\n"


def test_retention_rejects_source_different_from_the_admitted_payload(preparation_assets):
    helper, payload, driver, _tool = preparation_assets
    with pytest.raises(RuntimeError, match="installation source changed before retention"):
        helper.retain(payload, driver, expected_payload_digest="a" * 64)
    assert not driver.exists()


@pytest.mark.parametrize("name", ["../outside", "tools/../outside", "tools//outside", "C:/outside", "tools\\outside"])
def test_nested_preparation_assets_refuse_unsafe_members_before_retention(inputs, name):
    helper, payload, driver = inputs
    (payload / "packaged-assets.json").write_text(json.dumps({
        "schema": "copilot-extensions.packaged-assets", "version": 2,
        "canonicalDir": ".", "payloadDir": "scripts/runtime-source-tools", "files": [name],
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="asset name"):
        helper.retain(payload, driver)
    assert not driver.exists()
