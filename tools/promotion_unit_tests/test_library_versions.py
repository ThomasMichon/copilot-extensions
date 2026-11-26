"""Changefiles share promotion's shipped-version allocation owner."""

import json
import importlib.util
import shutil
import sys
import time
from pathlib import Path
from typing import Any
from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

import accumulate_bumps as acc
import promote_release as promotion
import promotion_versions as versions
from plugin_test_source_versions import preparation_ready, stage_ready, bind_preparation_owner
from plugin_test_owned_scratch import (
    preparation_scope, UnsettledPreparationError, UnsettledPreparationInputsError,
    run_preparation_install,
)
from plugin_test_containment import TestSandbox as Sandbox, Limits
import plugin_test_owned_scratch as preparation_owner
from source_preparation_process import procutil_module
import plugin_test_preparation_worker as preparation_worker


@pytest.fixture
def library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(acc, "REPO", tmp_path)
    monkeypatch.setattr(acc, "PLUGINS_DIR", tmp_path / "plugins")
    for relative in (
        "libs/agent-procutil",
        "plugins/example/libs/agent-procutil",
        "services/index/libs/agent-procutil",
        "services/index",
    ):
        root = tmp_path / relative
        root.mkdir(parents=True, exist_ok=True)
        (root / "pyproject.toml").write_text(
            '[project]\nname = "agent-procutil"\nversion = "0.1.0-dev7"\n',
            encoding="utf-8",
        )
    return tmp_path


def test_library_changefile_advances_canonical_and_every_physical_copy(library: Path) -> None:
    computed = acc.compute({"libs/agent-procutil": ["dev"]})
    assert computed == {"libs/agent-procutil": ("0.1.0-dev7", "0.1.0-dev8")}
    assert acc.apply(computed) == ["libs/agent-procutil"]
    assert len(acc.library_roots("libs/agent-procutil")) == 3
    for root in acc.library_roots("libs/agent-procutil"):
        assert acc.read_pyproject_project_version(root) == "0.1.0-dev8"


@pytest.mark.parametrize("name", [
    "libs/../services/index", "libs/agent-procutil/..", "libs/missing",
    "agent-procutil",
])
def test_library_identifiers_do_not_alias_other_projects(library: Path, name: str) -> None:
    assert acc.library_roots(name) == []
    assert acc.compute({name: ["dev"]}) == {}


def test_library_seed_preserves_shipped_version_without_reapplying_old_change(
    library: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        versions, "main_version_inputs",
        lambda *args: {"libs/agent-procutil/pyproject.toml": '[project]\nversion="0.1.0-dev8"\n'},
    )
    promotion._seed_versions_from_main(library, "shipped", acc=acc, repo=library)
    for root in acc.library_roots("libs/agent-procutil"):
        assert acc.read_pyproject_project_version(root) == "0.1.0-dev8"
    assert acc.compute({"libs/agent-procutil": ["dev"]}) == {
        "libs/agent-procutil": ("0.1.0-dev8", "0.1.0-dev9"),
    }


def test_source_metadata_only_keeps_source_fallbacks_and_all_copies(library: Path) -> None:
    source = library / "libs/agent-procutil/src/agent_procutil/__init__.py"
    source.parent.mkdir(parents=True)
    source.write_text('__version__ = "0.1.0-dev7"\n', encoding="utf-8")
    computed = acc.compute({"libs/agent-procutil": ["dev"]})
    assert acc.apply(computed, metadata_only=True) == ["libs/agent-procutil"]
    assert source.read_text() == '__version__ = "0.1.0-dev7"\n'
    assert all(
        acc.read_pyproject_project_version(root) == "0.1.0-dev8"
        for root in acc.library_roots("libs/agent-procutil")
    )
    with pytest.raises(acc.VersionError, match="libraries only"):
        acc.apply({"unrelated": ("0.1.0-dev1", "0.1.0-dev2")}, metadata_only=True)


def test_shared_preparation_keeps_consumed_boundary_and_selected_scope(
    library: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(versions, "main_version_inputs", lambda *args: {
        "libs/agent-procutil/pyproject.toml": '[project]\nversion="0.1.57-dev1"\n',
    })
    monkeypatch.setattr(
        versions, "changefile_existed_at", lambda boundary, name, **options: name == "consumed.json",
    )
    computed, applied, pending = versions.prepare_versions(
        library, "pinned-main", acc=acc, repo=library, last_dev_head="pinned-boundary",
        consumers=frozenset({"libs/agent-procutil"}),
        entries=[
            (library / "consumed.json", {"changes": [
                {"plugin": "libs/agent-procutil", "type": "patch"},
            ]}),
            (library / "pending.json", {"changes": [
                {"plugin": "libs/agent-procutil", "type": "dev"},
                {"plugin": "unrelated", "type": "major"},
            ]}),
        ],
    )
    assert computed == {"libs/agent-procutil": ("0.1.57-dev1", "0.1.57-dev2")}
    assert applied == ["libs/agent-procutil"]
    assert [path.name for path, _ in pending] == ["pending.json"]


@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt])
def test_failed_preparation_invalidates_ready_but_preserves_physical_inputs(
    tmp_path: Path, failure: type[BaseException],
) -> None:
    retained = tmp_path / "retained"
    (retained / ".source-inputs").mkdir(parents=True)
    stamp = retained / ".dep-fingerprint"
    stamp.write_text("ready")
    with pytest.raises(failure, match="cleanup"):
        with preparation_ready(retained):
            raise failure("cleanup not confirmed")
    assert not stamp.exists()
    assert (retained / ".source-inputs").is_dir()


def test_owned_worker_forwards_original_absolute_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    runner = SimpleNamespace(_prepare_venv=lambda *args, **kwargs: calls.append((args, kwargs)))
    loader = SimpleNamespace(exec_module=lambda module: None)
    monkeypatch.setattr(importlib.util, "spec_from_file_location", lambda *args: SimpleNamespace(loader=loader))
    monkeypatch.setattr(importlib.util, "module_from_spec", lambda spec: runner)
    monkeypatch.setattr(time, "monotonic", lambda: 1000.0)
    deadline = 1010.0
    monkeypatch.setattr(sys, "argv", ["worker", "example", "uv", str(tmp_path), "reuse", str(deadline)])
    preparation_worker.main()
    assert runner.VENV_ROOT == tmp_path
    assert calls[0][0] == ("example", "uv")
    assert calls[0][1]["deadline"] == deadline
    assert not calls[0][1]["reinstall"]


def test_source_ready_record_is_committed_only_after_scope_cleanup(tmp_path: Path) -> None:
    retained = tmp_path / "retained"
    (retained / ".source-inputs").mkdir(parents=True)
    stamp = retained / ".dep-fingerprint"
    pending = retained / ".dep-fingerprint.pending"
    with preparation_scope(None, tmp_path, Sandbox, retained, deadline=None, dir=tmp_path):
        stage_ready(retained, "complete candidate record")
        assert not stamp.exists()
    assert stamp.read_text() == "complete candidate record"
    assert not pending.exists()
    with pytest.raises(RuntimeError, match="cleanup"):
        with preparation_ready(retained):
            bind_preparation_owner(retained, str(tmp_path / "diagnostic-private"))
            stage_ready(retained, "not committed")
            raise RuntimeError("cleanup not confirmed")
    assert not stamp.exists()
    assert pending.exists()
    with pytest.raises(ValueError, match="existing owner"):
        with preparation_ready(retained):
            pytest.fail("unsettled physical custody cannot be silently retried")


def test_early_native_cleanup_failure_retains_original_scope_and_blocks_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    native: Any = procutil_module("owned_process")
    callbacks: list[str] = []
    job = object()
    error = native.OwnedCleanupError(
        None, job, cleanup_resources=(("fixture-resource", object()),),
        cleanup_retry=lambda deadline: callbacks.append("native-settled"),
        on_settled=lambda: callbacks.append("callback-delivered"),
    )
    retained = tmp_path / "retained"
    private = ""
    input_failure = False
    @contextmanager
    def inputs(*args: Any, **kwargs: Any) -> Iterator[SimpleNamespace]:
        directory = Path(private) / "uv-inputs"
        directory.mkdir()
        (directory / "source.py").write_text("RETAIN = True\n")
        try:
            yield SimpleNamespace(argv=("fixture-uv",), environment={}, cwd=tmp_path)
        finally:
            if input_failure:
                raise installer_dependency_policy.RetainedInputCleanupError("fixture retained inputs")
            shutil.rmtree(directory)
            callbacks.append("inputs-retired")
    def execute(*args: Any, **kwargs: Any) -> None:
        raise error
    import installer_dependency_policy
    monkeypatch.setattr(installer_dependency_policy, "retain_uv_install", inputs)
    monkeypatch.setattr(preparation_owner, "run_preparation_command", execute)
    with pytest.raises(UnsettledPreparationError) as caught:
        with preparation_scope(None, tmp_path, Sandbox, retained, deadline=None, dir=tmp_path) as private:
            assert json.loads((retained / ".dep-fingerprint.pending").read_text())["owner"]["private_state"] == private
            run_preparation_install(
                "fixture-uv", [], deadline=time.monotonic() + 10,
                limits=Limits(), sandbox=Path(private),
            )
    assert Path(private).is_dir()
    assert (Path(private) / "uv-inputs/source.py").is_file()
    assert callbacks == []
    assert not (retained / ".source-inputs").exists()
    assert caught.value.native_cleanup is error
    assert caught.value.job is job
    assert caught.value.native_cleanup.cleanup_resources is error.cleanup_resources
    with pytest.raises(ValueError, match="existing owner"):
        with preparation_scope(None, tmp_path, Sandbox, retained, deadline=None, dir=tmp_path):
            pytest.fail("the old physical owner must be reconciled first")
    monkeypatch.setattr(time, "monotonic", lambda: 1000.0)
    caught.value.retry_cleanup(deadline=1001.0)
    assert callbacks == ["native-settled", "callback-delivered", "inputs-retired"]
    assert not (Path(private) / "uv-inputs").exists()
    shutil.rmtree(private)
    input_failure = True
    import subprocess
    monkeypatch.setattr(
        preparation_owner, "run_preparation_command",
        lambda *args, **kwargs: subprocess.CompletedProcess([], 0, b"", b""),
    )
    second = tmp_path / "retained-input-error"
    with pytest.raises(UnsettledPreparationInputsError) as input_error:
        with preparation_scope(None, tmp_path, Sandbox, second, deadline=None, dir=tmp_path) as private:
            run_preparation_install(
                "fixture-uv", [], deadline=time.monotonic() + 10,
                limits=Limits(), sandbox=Path(private),
            )
    assert isinstance(
        input_error.value.retention_error, installer_dependency_policy.RetainedInputCleanupError,
    )
    assert (Path(private) / "uv-inputs/source.py").is_file()
    assert (second / ".dep-fingerprint.pending").is_file()
    assert not (second / ".dep-fingerprint").exists()
    shutil.rmtree(private)


def _packaged_projects(root: Path) -> None:
    for name, version in (("producer", "0.1.0-dev1"), ("receiver", "0.4.0-dev1")):
        directory = root / "plugins" / name
        directory.mkdir()
        (directory / "plugin.json").write_text(json.dumps({
            "name": name, "version": version,
        }), encoding="utf-8")
        (directory / "pyproject.toml").write_text(
            f'[project]\nname = "{name}"\nversion = "{version}"\n', encoding="utf-8",
        )


@pytest.mark.parametrize("receiver_bump", [None, "dev"])
def test_compute_bumps_packaged_manifest_receiver(library: Path, receiver_bump: str | None) -> None:
    _packaged_projects(library)
    (library / "plugins/receiver/packaged-assets.json").write_text(json.dumps({
        "schema": "copilot-extensions.packaged-assets",
        "version": 1,
        "canonicalDir": "plugins",
        "payloadDir": "src/bundle",
        "files": ["producer/plugin.json"],
    }), encoding="utf-8")
    grouped = {"producer": ["patch"]}
    if receiver_bump:
        grouped["receiver"] = [receiver_bump]

    assert acc.compute(grouped) == {
        "producer": ("0.1.0-dev1", "0.1.1-dev1"),
        "receiver": ("0.4.0-dev1", "0.4.0-dev2" if receiver_bump else "0.4.1-dev1"),
    }
    assert grouped == {
        "producer": ["patch"],
        **({"receiver": ["dev"]} if receiver_bump else {}),
    }


def test_packaged_manifest_bumps_close_cycles_and_reach_standalone(library: Path) -> None:
    _packaged_projects(library)
    manager = library / "worktree-manager"
    manager.mkdir()
    (manager / "pyproject.toml").write_text(
        '[project]\nname = "manager"\nversion = "0.7.0-dev1"\n', encoding="utf-8",
    )
    for consumer, files in (
        ("plugins/producer", ["receiver/plugin.json"]),
        ("plugins/receiver", ["producer/pyproject.toml", "receiver/plugin.json"]),
        ("worktree-manager", ["receiver/plugin.json"]),
    ):
        (library / consumer / "packaged-assets.json").write_text(json.dumps({
            "schema": "copilot-extensions.packaged-assets", "version": 2,
            "groups": [{
                "canonicalDir": "plugins", "payloadDir": "src/bundle", "files": files,
            }],
        }), encoding="utf-8")

    assert acc.compute({"producer": ["patch"]}) == {
        "producer": ("0.1.0-dev1", "0.1.1-dev1"),
        "receiver": ("0.4.0-dev1", "0.4.1-dev1"),
        "worktree-manager": ("0.7.0-dev1", "0.7.1-dev1"),
    }
    assert acc.compute({}) == {}


@pytest.mark.parametrize("kind", ["root", "version", "assets"])
def test_indirect_packaged_version_receiver_refuses(
    library: Path, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    _packaged_projects(library)
    receiver = library / "plugins/receiver"
    assets = receiver / "packaged-assets.json"
    assets.write_text(json.dumps({
        "schema": "copilot-extensions.packaged-assets", "version": 1,
        "canonicalDir": "plugins", "payloadDir": "src/bundle",
        "files": ["producer/plugin.json"],
    }), encoding="utf-8")
    linked = {"root": receiver, "version": receiver / "plugin.json", "assets": assets}[kind]
    original = type(linked).is_symlink
    monkeypatch.setattr(type(linked), "is_symlink", lambda path: path == linked or original(path))

    with pytest.raises(acc.VersionError, match="unsafe library version"):
        acc.compute({"producer": ["patch"]})


def test_library_apply_failure_is_not_accepted(library: Path) -> None:
    copy = library / "plugins/example/libs/agent-procutil/pyproject.toml"
    copy.write_text("[project]\nname='agent-procutil'\n", encoding="utf-8")
    assert acc.apply({"libs/agent-procutil": ("0.1.0-dev7", "0.1.0-dev8")}) == []


def test_shipped_version_seed_failure_refuses(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(versions, "main_version_inputs", lambda *args: {})
    fake = SimpleNamespace(
        iter_standalone_consumer_names=lambda: [],
        iter_library_consumer_names=lambda: ["libs/agent-procutil"],
        _project_version_at=lambda *args: "0.1.0-dev8",
        read_consumer_version=lambda *args: "0.1.0-dev7",
        apply=lambda *args: [],
    )
    with pytest.raises(promotion.PromotionError, match="cannot seed shipped versions"):
        promotion._seed_versions_from_main(tmp_path, "shipped", acc=fake, repo=tmp_path)


@pytest.mark.parametrize("target", [
    "plugin.json", "pyproject.toml", "src", "src/package",
    "src/package/__init__.py", "src/package/_build_info.py",
    "instruction-projections.json", "template.md", "marketplace.json",
])
def test_all_version_write_targets_are_admitted_before_any_change(
    library: Path, monkeypatch: pytest.MonkeyPatch, target: str,
) -> None:
    _packaged_projects(library)
    marketplace = library / ".github/plugin/marketplace.json"
    marketplace.parent.mkdir(parents=True)
    marketplace.write_text(json.dumps({"plugins": [
        {"name": name, "version": version}
        for name, version in (("producer", "0.1.0-dev1"), ("receiver", "0.4.0-dev1"))
    ]}), encoding="utf-8")
    monkeypatch.setattr(acc, "MARKETPLACE", marketplace)
    root = library / "plugins/receiver"
    (root / "src/package").mkdir(parents=True)
    (root / "src/package/__init__.py").write_text(
        '__version__ = "0.4.0-dev1"\n', encoding="utf-8",
    )
    (root / "instruction-projections.json").write_text(json.dumps({
        "projections": [{"template": "template.md"}],
    }), encoding="utf-8")
    (root / "template.md").write_text("[owner: receiver@0.4.0-dev1]\n", encoding="utf-8")
    linked = marketplace if target == "marketplace.json" else root / target
    before = {path.relative_to(library): path.read_bytes()
              for path in library.rglob("*") if path.is_file()}
    original = type(linked).is_symlink
    monkeypatch.setattr(type(linked), "is_symlink", lambda path: path == linked or original(path))

    with pytest.raises(acc.VersionError, match="unsafe library version"):
        acc.apply({
            "producer": ("0.1.0-dev1", "0.1.1-dev1"),
            "receiver": ("0.4.0-dev1", "0.4.1-dev1"),
        })

    assert {path.relative_to(library): path.read_bytes()
            for path in library.rglob("*") if path.is_file()} == before


@pytest.mark.parametrize("kind", ["manifest", "ancestor", "source-directory", "fallback"])
def test_linked_library_targets_refuse_before_any_version_write(library: Path, kind: str) -> None:
    external = library.parent / (library.name + "-external")
    external.mkdir()
    manifest = external / "pyproject.toml"
    manifest.write_text('[project]\nversion="0.1.0-dev7"\n', encoding="utf-8")
    fallback = external / "__init__.py"
    fallback.write_text('__version__ = "0.1.0-dev7"\n', encoding="utf-8")
    canonical = library / "libs/agent-procutil/pyproject.toml"
    before = (canonical.read_bytes(), manifest.read_bytes(), fallback.read_bytes())
    copy = library / "plugins/example/libs/agent-procutil"
    source = copy / "src"
    source.mkdir()
    if kind == "manifest":
        link, target = copy / "pyproject.toml", manifest
        link.unlink()
    elif kind == "ancestor":
        (copy / "pyproject.toml").unlink()
        source.rmdir()
        copy.rmdir()
        link, target = copy, external
    elif kind == "source-directory":
        link, target = source / "package", external
    else:
        (source / "package").mkdir()
        link, target = source / "package/__init__.py", fallback
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
    except OSError as error:
        if getattr(error, "winerror", None) == 1314:
            pytest.skip("native symlink creation requires Windows privilege")
        raise
    with pytest.raises(acc.VersionError, match="unsafe library version"):
        acc.apply({"libs/agent-procutil": ("0.1.0-dev7", "0.1.0-dev8")})
    assert (canonical.read_bytes(), manifest.read_bytes(), fallback.read_bytes()) == before
