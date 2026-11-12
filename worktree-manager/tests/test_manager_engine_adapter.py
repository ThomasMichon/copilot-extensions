"""Small adapter contracts; real construction is in the native acceptance lane."""

from __future__ import annotations

import json
import hashlib
import os
import shutil
import subprocess
import sys
import time
import tomllib
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from worktree_manager import self_install as si


def _source(tmp_path, version="1.2.3-dev1"):
    payload = tmp_path / "source"
    package = payload / "src" / "worktree_manager"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(f'__version__ = "{version}"\n', encoding="utf-8")
    (package / "__main__.py").write_text("", encoding="utf-8")
    (payload / "pyproject.toml").write_text(
        f'[project]\nname = "copilot-extensions-worktree-manager"\nversion = "{version}"\n',
        encoding="utf-8",
    )
    uv = tmp_path / "uv"
    uv.touch()
    return payload, tmp_path / "root", uv


def _capture(adapter, monkeypatch):
    calls = []
    @contextmanager
    def candidate(root, version, **kwargs):
        calls.append((root, version, kwargs))
        yield SimpleNamespace(slot=root / "versions" / version, reused=False)
    monkeypatch.setattr(adapter.engine, "numbered_candidate", candidate)
    return calls


def test_adapter_delegates_construction_health_and_retention_without_selecting(tmp_path, monkeypatch):
    adapter = si._runtime_installer()
    payload, root, uv = _source(tmp_path)
    calls = _capture(adapter, monkeypatch)
    with adapter.candidate(payload, root, assemble=si._copy_payload, uv=uv) as ready:
        assert ready.slot.name == "1.2.3-dev1"
        assert not (root / "current-version").exists()
        _, _, options = calls[0]
        assert options["process_retention"] is True
        assert options["health_module"] == "worktree_manager.runtime_candidate"
        assert options["health_callable"] == "verify_runtime_candidate"
        assert options["construction_callable"] == "worktree_manager.runtime_candidate:prepare_runtime_candidate"
        assert options["base_python"].is_file()
        assert options["expected_source_identity"] == {}


@pytest.mark.skipif(os.name != "nt", reason="Windows extended-length construction inputs")
def test_adapter_retains_long_windows_members_through_source_observation(tmp_path, monkeypatch):
    adapter = si._runtime_installer()
    payload, root, uv = _source(tmp_path)
    native = adapter.engine._native_retained_path
    member = Path("resources") / ("nested-" * 12) / ("retained-" * 12) / "data.bin"
    source = Path(native(payload / member))
    source.parent.mkdir(parents=True)
    source.write_bytes(b"retained native construction resource")
    calls = _capture(adapter, monkeypatch)

    with adapter.candidate(Path(native(payload)), root, assemble=si._copy_payload, uv=uv):
        prepared = calls[0][2]["plugin"]
        assert len(str(prepared / member)) > 260
        assert (prepared / member).read_bytes() == source.read_bytes()
        assert adapter.runtime.fingerprint_source(
            adapter.engine._source_files(adapter.engine._package_roots(prepared)),
        )
        assert not (root / "current-version").exists()


@pytest.mark.parametrize("qualified", [False, True])
def test_prepare_runtime_candidate_reports_real_copy_and_inventory_progress(
    tmp_path, monkeypatch, capsys, qualified,
):
    import worktree_manager
    from worktree_manager import runtime_candidate

    adapter = si._runtime_installer()
    payload, root, _uv = _source(tmp_path)
    manager = Path(__file__).resolve().parents[1]
    (payload / "references").mkdir()
    for name in ("uninstall-contract.json", "control-plane-provider.json"):
        shutil.copyfile(manager / "references" / name, payload / "references" / name)
    data = {"frozen_plugin": str(payload)}
    if qualified:
        nested = payload / "libs" / "example" / "pyproject.toml"
        nested.parent.mkdir(parents=True)
        nested.write_text(
            '[project]\nname="example"\nversion="1.0"\n'
            '[tool.uv.sources]\nhelper={path="../helper",editable=true}\n',
            encoding="utf-8",
        )
        _attribute_source(adapter, payload)
        retained = tmp_path / "retained"
        shutil.copytree(payload, retained)
        from installer_dependency_config import freeze_source_editables
        from installer_dependency_policy import _write_toml

        manifest = retained / nested.relative_to(payload)
        project = tomllib.loads(manifest.read_text(encoding="utf-8"))
        freeze_source_editables(project)
        _write_toml(manifest, project)
        assert adapter.runtime_source.assembly_hash(retained) != adapter.runtime_source.assembly_hash(payload)
        data = {"frozen_plugin": str(retained), "source_payload": str(payload)}
    slot = root / "versions" / "1.2.3-dev1"
    slot.mkdir(parents=True)
    monkeypatch.setattr(runtime_candidate.sys, "prefix", str(slot))
    monkeypatch.setattr(
        worktree_manager, "__file__", str(slot / "Lib" / "site-packages" / "worktree_manager" / "__init__.py"),
    )
    monkeypatch.setattr(si, "_runtime_installer", lambda: adapter)
    runtime_candidate.prepare_runtime_candidate(data)
    output = capsys.readouterr()
    assert not output.out
    assert "phase=payload-copy completed-units=1" in output.err
    assert "phase=inventory completed-units=1" in output.err
    assert (slot / "payload" / ".install-complete").is_file() is not qualified
    if qualified:
        assert adapter.runtime_source.read_source_identity(
            slot / "payload", "1.2.3-dev1", plugin="worktree-manager",
        ) == adapter.runtime_source.read_source_identity(payload, "1.2.3-dev1", plugin="worktree-manager")
    assert (slot / ".manager-artifacts.json").is_file()
    assert not (root / "current-version").exists()
    assert not (slot / adapter.runtime.COMPLETE_MARKER).exists()
    from types import SimpleNamespace

    assert adapter._finish(
        SimpleNamespace(install_only=True, root=root, source_digest="fixture"),
        slot, [],
    ) == 0
    receipt = capsys.readouterr()
    assert not receipt.err
    record = json.loads((output.err + receipt.out).splitlines()[-1])
    assert record["schema"] == "copilot-extensions.manager-bootstrap"
    assert record["slot"] == str(slot)


def test_explicit_interpreter_is_forwarded_instead_of_reselected(tmp_path, monkeypatch):
    adapter = si._runtime_installer()
    payload, root, uv = _source(tmp_path)
    selected = tmp_path / "selected-python"
    selected.touch()
    calls = _capture(adapter, monkeypatch)
    monkeypatch.setattr(adapter, "base_python", lambda: pytest.fail("explicit interpreter was reselected"))
    with adapter.candidate(payload, root, assemble=si._copy_payload, uv=uv, python=selected):
        assert calls[0][2]["base_python"] == selected


@pytest.mark.skipif(os.name != "nt", reason="native Windows command discovery")
def test_default_uv_discovery_uses_the_sdk_before_traversing_the_selected_file(tmp_path, monkeypatch):
    adapter = si._runtime_installer()
    payload, root, uv = _source(tmp_path)
    calls = _capture(adapter, monkeypatch)
    selected = []
    original_which = shutil.which

    def which(command, *args, **kwargs):
        if command == "uv":
            pytest.fail("Manager performed a traversing UV availability check")
        return original_which(command, *args, **kwargs)

    monkeypatch.setattr(adapter.shutil, "which", which)
    monkeypatch.setattr(
        adapter.engine, "_select_uv_executable",
        lambda command: selected.append(command) or str(uv),
    )
    with adapter.candidate(payload, root, assemble=si._copy_payload):
        assert calls[0][2]["uv"] == uv
    assert selected == ["uv"]


@pytest.mark.skipif(os.name != "nt", reason="native Windows command discovery")
@pytest.mark.parametrize("name", ["uv", "uv.exe"])
def test_explicit_relative_uv_is_bound_to_its_path_before_sdk_selection(tmp_path, monkeypatch, name):
    adapter = si._runtime_installer()
    payload, root, original = _source(tmp_path)
    selected_file = tmp_path / name
    if selected_file != original:
        original.rename(selected_file)
    monkeypatch.chdir(tmp_path)
    calls = _capture(adapter, monkeypatch)
    selected = []

    def select(command):
        assert Path(command) == selected_file
        assert Path(command).is_absolute()
        selected.append(command)
        return command

    monkeypatch.setattr(adapter.engine, "_select_uv_executable", select)
    with adapter.candidate(payload, root, assemble=si._copy_payload, uv=Path(name)):
        assert calls[0][2]["uv"] == selected_file
    assert selected == [str(selected_file)]


@pytest.mark.parametrize("name", ["uv", "uv.exe"])
@pytest.mark.parametrize("boundary", ["retention", "construction"])
def test_python_engine_path_arguments_are_not_reinterpreted_as_command_names(
    tmp_path, monkeypatch, name, boundary,
):
    engine = si._runtime_installer().engine
    payload, root, original = _source(tmp_path)
    selected_file = tmp_path / name
    if selected_file != original:
        original.rename(selected_file)
    monkeypatch.chdir(tmp_path)

    class SelectedPath(Exception):
        pass

    def select(command):
        assert Path(command) == selected_file
        assert Path(command).is_absolute()
        raise SelectedPath

    monkeypatch.setattr(engine, "_select_uv_executable", select)
    with pytest.raises(SelectedPath):
        if boundary == "retention":
            inputs = engine.FrozenInputs(payload, (payload,), "")
            with engine._retained_inputs(inputs, Path(name), Path(sys.executable), refresh=False):
                pytest.fail("selection witness was not reached")
        else:
            engine._construct(None, root, Path(name), Path(sys.executable), -1, 1)


@pytest.mark.parametrize("boundary", [
    "retention", "construction",
    pytest.param("manager", marks=pytest.mark.skipif(os.name != "nt", reason="Windows SDK selection")),
])
def test_explicit_uv_parent_components_survive_binding_until_resolution(tmp_path, monkeypatch, boundary):
    adapter = si._runtime_installer()
    engine = adapter.engine
    payload, root, _uv = _source(tmp_path)
    monkeypatch.chdir(tmp_path)
    relative = Path("linked") / ".." / "uv.exe"
    expected = tmp_path / relative

    class ObservedPath(Exception):
        pass

    def select(command):
        assert Path(command) == expected
        assert ".." in Path(command).parts
        raise ObservedPath

    monkeypatch.setattr(engine, "_select_uv_executable", select)
    with pytest.raises(ObservedPath):
        if boundary == "manager":
            with adapter.candidate(payload, root, assemble=si._copy_payload, uv=relative):
                pytest.fail("selection witness was not reached")
        elif boundary == "retention":
            with engine._retained_inputs(
                engine.FrozenInputs(payload, (payload,), ""), relative, Path(sys.executable), refresh=False,
            ):
                pytest.fail("selection witness was not reached")
        else:
            engine._construct(None, root, relative, Path(sys.executable), -1, 1)


def _bundled_wheel_source(tmp_path):
    payload, root, _ = _source(tmp_path)
    manager = Path(__file__).resolve().parents[1]
    data = tomllib.loads((manager / "pyproject.toml").read_text(encoding="utf-8"))
    declaration = data["tool"]["uv"]["sources"]["dbus-next"]
    wheel = Path(declaration["path"])
    (payload / wheel).parent.mkdir(parents=True)
    shutil.copyfile(manager / wheel, payload / wheel)
    with (payload / "pyproject.toml").open("a", encoding="utf-8") as stream:
        stream.write(
            'dependencies = ["dbus-next==0.2.3 ; sys_platform == \'linux\'"]\n'
            '[tool.uv.sources]\ndbus-next = {path = ' + json.dumps(wheel.as_posix()) + '}\n'
        )
    return payload, root, wheel


@pytest.mark.parametrize("platform,uses_wheel", [("linux", True), ("windows", False)])
def test_bundled_wheel_reaches_real_manager_installation_inputs(
    tmp_path, monkeypatch, platform, uses_wheel,
):
    adapter = si._runtime_installer()
    payload, root, wheel = _bundled_wheel_source(tmp_path)
    contents = (payload / wheel).read_bytes()
    frozen = adapter.engine.freeze_inputs(payload, tmp_path / "frozen")
    assert (frozen.plugin / wheel).read_bytes() == contents
    files = adapter.engine._source_files(frozen.packages)
    assert frozen.plugin / wheel in files
    assert frozen.payload_hash != adapter.runtime.fingerprint_source(
        tuple(path for path in files if path != frozen.plugin / wheel),
    )
    uv = shutil.which("uv")
    assert uv is not None, "Manager construction requires UV"
    reached = []

    class InstallationReached(Exception):
        pass

    def inspect_installation(inputs, *args, **kwargs):
        assert inputs.transaction is not None
        assert all(package.is_dir() for package in inputs.packages)
        assert (inputs.plugin / wheel).read_bytes() == contents
        original = next(
            source for source, retained in inputs.transaction.sources.items()
            if retained == inputs.plugin
        )
        assert inputs.transaction.source_hashes[original / wheel] == hashlib.sha256(contents).hexdigest()
        command = list(inputs.transaction.argv)
        assert "--no-sources" not in command
        assert command[command.index("-r") + 1] == str(inputs.plugin / "pyproject.toml")
        # Exercise the retained dependency resolver offline without building the
        # fixture project or installing anything into the host interpreter.
        command = [argument for argument in command if argument not in map(str, inputs.packages)]
        command[command.index("--python") + 1] = sys.executable
        command.extend(["--dry-run", "--offline", "--no-cache", "--python-platform", platform])
        result = subprocess.run(
            command, env=inputs.transaction.environment, cwd=inputs.transaction.cwd,
            capture_output=True, text=True, timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        diagnostic = result.stdout + result.stderr
        assert ("dbus-next" in diagnostic) is uses_wheel
        assert (wheel.name in diagnostic) is uses_wheel
        reached.append(inputs.plugin)
        raise InstallationReached

    # Keep assembly, numbered discovery and retained dependency admission real;
    # stop only at the boundary that would execute venv/UV installation.
    monkeypatch.setattr(adapter.engine, "_construct", inspect_installation)
    with pytest.raises(InstallationReached):
        with adapter.candidate(frozen.plugin, root, assemble=si._copy_payload, uv=Path(uv)):
            pytest.fail("installation boundary did not stop construction")
    assert len(reached) == 1
    assert not (tmp_path / "root" / "current-version").exists()


def test_real_manager_discovery_rejects_modified_bundled_wheel(tmp_path, monkeypatch):
    adapter = si._runtime_installer()
    payload, root, relative_wheel = _bundled_wheel_source(tmp_path)
    wheel = payload / relative_wheel
    wheel.write_bytes(wheel.read_bytes() + b"modified")
    uv = tmp_path / "uv"
    uv.touch()

    def unexpected_installation(*args, **kwargs):
        pytest.fail("modified wheel reached installation")

    monkeypatch.setattr(adapter.engine, "_construct", unexpected_installation)
    with pytest.raises(adapter.engine.DependencyPolicyError, match="local UV artifacts"):
        with adapter.candidate(payload, root, assemble=si._copy_payload, uv=uv):
            pytest.fail("modified wheel was admitted")
    assert not (tmp_path / "root" / "current-version").exists()


@pytest.mark.parametrize("name,accepted", [
    ("Copilot_Extensions_Worktree_Manager", True), ("foreign-manager", False),
])
def test_project_identity_is_normalized_but_foreign_projects_refuse(tmp_path, monkeypatch, name, accepted):
    adapter = si._runtime_installer()
    payload, root, uv = _source(tmp_path)
    project = payload / "pyproject.toml"
    project.write_text(project.read_text(encoding="utf-8").replace(
        "copilot-extensions-worktree-manager", name,
    ), encoding="utf-8")
    calls = _capture(adapter, monkeypatch)
    if accepted:
        with adapter.candidate(payload, root, assemble=si._copy_payload, uv=uv):
            assert len(calls) == 1
    else:
        with pytest.raises(ValueError, match="attributed project name"):
            with adapter.candidate(payload, root, assemble=si._copy_payload, uv=uv):
                pytest.fail("foreign project was admitted")
        assert calls == []
    assert not (root / "current-version").exists()


def test_selected_repair_is_reused_instead_of_allocating_another(tmp_path, monkeypatch):
    adapter = si._runtime_installer()
    payload, root, uv = _source(tmp_path)
    legacy = root / "versions" / "1.2.3-dev1"
    legacy.mkdir(parents=True)
    (legacy / ".install-complete").touch()
    repair = "1.2.3-dev1+repair.fixture"
    adapter.runtime.mark_complete(root, repair, payload_hash="a" * 64)
    si._write_marker(root, repair)
    calls = _capture(adapter, monkeypatch)
    with adapter.candidate(payload, root, assemble=si._copy_payload, uv=uv):
        assert calls[0][1] == repair
        assert calls[0][2]["reuse_generations"] is False
    assert si.current_version(root) == repair


def test_source_identity_survives_payload_assembly(tmp_path, monkeypatch):
    adapter = si._runtime_installer()
    payload, root, uv = _source(tmp_path)
    (payload / "plugin.json").write_text(
        '{"name":"worktree-manager","version":"1.2.3-dev1"}', encoding="utf-8",
    )
    digest = adapter.runtime_source.assembly_hash(payload)
    generation = adapter.runtime_source.runtime_version("1.2.3-dev1", "a" * 40, digest)
    identity = {
        "schema": adapter.runtime_source.SOURCE_SCHEMA, "version": 1,
        "plugin": "worktree-manager", "package_version": "1.2.3-dev1",
        "source_commit": "a" * 40, "source_tree": "b" * 40,
        "assembly_hash": digest, "runtime_version": generation,
    }
    (payload / "runtime-source.json").write_text(json.dumps(identity), encoding="utf-8")
    calls = _capture(adapter, monkeypatch)
    with adapter.candidate(payload, root, assemble=si._copy_payload, uv=uv):
        assert calls[0][1] == generation
        assert calls[0][2]["expected_source_identity"]["runtime_version"] == generation
        prepared = calls[0][2]["plugin"]
        assert calls[0][2]["construction_data"] == {"source_payload": str(prepared)}
        assert adapter.runtime_source.read_source_identity(
            prepared, "1.2.3-dev1", plugin="worktree-manager",
        ).runtime_version == generation
        assert not (prepared / ".install-complete").exists()


def _attribute_source(adapter, payload, version="1.2.3-dev1"):
    (payload / "plugin.json").write_text(json.dumps({
        "name": "worktree-manager", "version": version,
    }), encoding="utf-8")
    digest = adapter.runtime_source.assembly_hash(payload)
    generation = adapter.runtime_source.runtime_version(version, "a" * 40, digest)
    (payload / "runtime-source.json").write_text(json.dumps({
        "schema": adapter.runtime_source.SOURCE_SCHEMA, "version": 1,
        "plugin": "worktree-manager", "package_version": version,
        "source_commit": "a" * 40, "source_tree": "b" * 40,
        "assembly_hash": digest, "runtime_version": generation,
    }), encoding="utf-8")
    return generation


@contextmanager
def _closed_inputs(tmp_path, content=b"constructor fixture", extra_wheels=()):
    from installer_wheel_snapshot import WheelInstallInputs, retain_wheel_inputs

    filename = "copilot_extensions_worktree_manager-1.2.3.dev1-py3-none-any.whl"
    archives, expected, distributions = {}, {}, []
    for name, version, filename, content in (
        ("copilot-extensions-worktree-manager", "1.2.3.dev1", filename, content), *extra_wheels,
    ):
        wheel = tmp_path / filename
        wheel.write_bytes(content)
        digest = "sha256:" + hashlib.sha256(content).hexdigest()
        archives[filename], expected[filename] = wheel, digest
        distributions.append((name, version, filename, digest))
    with retain_wheel_inputs(
        archives, expected, max_wheels=len(archives),
        max_archive_bytes=1 << 20, max_total_bytes=1 << 20,
        max_snapshot_bytes=1 << 20, deadline=time.monotonic() + 120,
    ) as snapshot:
        yield WheelInstallInputs(
            snapshot, '{"fixture":"complete-manager-closure"}',
            tuple(distributions),
        )


@pytest.mark.parametrize("old_marker", [None, b"\xff\xfe\n", b"../foreign\n"])
def test_source_wheels_bind_generation_and_retain_existing_candidate_contract(
    tmp_path, monkeypatch, old_marker,
):
    adapter = si._runtime_installer()
    payload, root, uv = _source(tmp_path)
    generation = _attribute_source(adapter, payload)
    if old_marker is not None:
        root.mkdir()
        (root / "current-version").write_bytes(old_marker)
    version_dir = adapter.runtime.version_dir

    def ordinary_slot(actual_root, version):
        adapter.runtime._validate_dev_previous_version(version)
        return version_dir(actual_root, version)

    monkeypatch.setattr(adapter.runtime, "version_dir", ordinary_slot)
    calls = _capture(adapter, monkeypatch)
    local = ("copilot-extensions-worktree-manager",)
    validate = lambda slot: None
    app = tmp_path / "application"
    with _closed_inputs(tmp_path) as wheels, adapter.candidate(
        payload, root, assemble=si._copy_payload, uv=uv,
        wheel_inputs=wheels, local_packages=local, validate_candidate=validate,
        application=app,
    ) as ready:
        assert ready.slot.name == generation + ".profile." + wheels.receipt()["lock_id"][7:23]
        options = calls[0][2]
        assert options["wheel_inputs"] is wheels and options["local_packages"] == local
        assert options["validate_candidate"] is validate
        assert options["application"] == app
        assert options["process_retention"] is True and options["reuse_generations"] is False
        assert options["expected_source_identity"]["runtime_version"] == generation
        assert options["plugin"] != payload
        marker = root / "current-version"
        assert (marker.read_bytes() if marker.exists() else None) == old_marker


def test_unattributed_manager_wheels_refuse_before_construction(tmp_path, monkeypatch):
    adapter = si._runtime_installer()
    payload, root, uv = _source(tmp_path)
    calls = _capture(adapter, monkeypatch)
    with _closed_inputs(tmp_path) as wheels, pytest.raises(ValueError, match="source identity"):
        with adapter.candidate(payload, root, assemble=si._copy_payload, uv=uv, wheel_inputs=wheels):
            pytest.fail("unattributed source reached the constructor")
    assert not calls and not (root / "current-version").exists()


def test_source_wheel_health_repair_preserves_profile_generation_family(tmp_path, monkeypatch):
    adapter = si._runtime_installer()
    payload, root, uv = _source(tmp_path)
    generation = _attribute_source(adapter, payload)
    calls = []

    @contextmanager
    def unhealthy(root, version, **options):
        calls.append((version, options))
        if len(calls) == 1:
            raise adapter.engine.HealthRepairRequired("fixture")
        yield SimpleNamespace(slot=root / "versions" / version)

    monkeypatch.setattr(adapter.engine, "numbered_candidate", unhealthy)
    with _closed_inputs(tmp_path) as wheels, adapter.candidate(
        payload, root, assemble=si._copy_payload, uv=uv, wheel_inputs=wheels,
        local_packages=("copilot-extensions-worktree-manager",),
    ) as ready:
        expected = generation + ".profile." + wheels.receipt()["lock_id"][7:23]
        assert calls[0][0] == expected
        assert ready.slot.name.startswith(expected + ".repair.")
        assert calls[1][1]["wheel_inputs"] is wheels
    assert not (root / "current-version").exists()


@pytest.mark.parametrize("selected", [None, "fixture", "foreign"])
def test_self_install_forwards_source_inputs_without_replacing_completion_owner(tmp_path, monkeypatch, selected):
    adapter = si._runtime_installer()
    payload, root, _ = _source(tmp_path)
    root.mkdir()
    if selected is not None:
        (root / "current-version").write_text(selected + "\n", encoding="utf-8")
    calls = []
    result = si.SelfInstallResult("1.2.3-dev1", "installed", str(root))
    validate = lambda slot: None
    before = lambda: None
    python = tmp_path / "selected-python"
    uv = tmp_path / "selected-uv"
    app = tmp_path / "application"

    @contextmanager
    def candidate(payload, root, **options):
        calls.append(options)
        yield SimpleNamespace(slot=root / "versions" / "fixture")

    def complete(ready, actual_payload, actual_root, **options):
        assert actual_payload == payload and actual_root == root
        assert options["before_publish"] is before
        assert options["previous"] == selected
        assert options["was_current"] is (selected == "fixture")
        return result

    monkeypatch.setattr(adapter, "candidate", candidate)
    monkeypatch.setattr(si, "_complete_candidate", complete)
    with _closed_inputs(tmp_path) as wheels:
        actual = si.self_install(
            payload, root=root, dry_run=False, _wheel_inputs=wheels,
            _local_packages=("copilot-extensions-worktree-manager",),
            _validate_candidate=validate, _before_publish=before,
            _base_python=python, _uv=uv,
            _application=app,
        )
        assert actual is result
        assert calls == [{
            "assemble": si._copy_payload, "wheel_inputs": wheels,
            "local_packages": ("copilot-extensions-worktree-manager",),
            "validate_candidate": validate,
            "python": python,
            "uv": uv,
            "application": app,
        }]


@pytest.mark.parametrize("reused", [False, True])
@pytest.mark.parametrize("readiness_error", [None, "passive readiness refused"])
def test_stage_only_checks_readiness_under_custody_without_publication(
    tmp_path, monkeypatch, reused, readiness_error,
):
    adapter = si._runtime_installer()
    payload, root, _ = _source(tmp_path)
    root.mkdir()
    paths = [
        root / "current-version", root / "last-known-good",
        tmp_path / "command", tmp_path / "provider.json", root / "mux-route.json",
    ]
    for path in paths:
        path.write_bytes(b"incumbent\n")
    before = {path: path.read_bytes() for path in paths}
    slot = root / "versions" / "1.2.3-dev1"
    events = []
    ready = SimpleNamespace(slot=slot, reused=reused)

    @contextmanager
    def candidate(actual_payload, actual_root, **options):
        assert actual_payload == payload and actual_root == root
        assert options["wheel_inputs"] is wheels
        events.append("retained")
        try:
            yield ready
        finally:
            events.append("released")

    def verify(actual_root, actual_ready, *, wheel_inputs):
        assert actual_root == root and actual_ready is ready and wheel_inputs is wheels
        assert events == ["retained"]
        events.append("readiness")
        if readiness_error:
            raise RuntimeError(readiness_error)

    def forbidden(*args, **kwargs):
        pytest.fail("staging reached publication or activation")

    monkeypatch.setattr(adapter, "candidate", candidate)
    monkeypatch.setattr(adapter, "verify_stage_readiness", verify, raising=False)
    monkeypatch.setattr(si, "needs_install", lambda *args: True)
    monkeypatch.setattr(si, "_complete_candidate", forbidden)
    monkeypatch.setattr(si, "_deploy_binstubs", forbidden)
    monkeypatch.setattr(si, "_write_control_plane_provider_manifest", forbidden)
    with _closed_inputs(tmp_path) as wheels:
        result = si.self_install(
            payload, root=root, dry_run=False, stage_only=True,
            _wheel_inputs=wheels, _before_publish=forbidden, _after_activate=forbidden,
        )
    assert result.action == ("error" if readiness_error else "staged")
    assert result.slot == str(slot)
    if readiness_error:
        assert result.reason == readiness_error
    assert events == ["retained", "readiness", "released"]
    assert {path: path.read_bytes() for path in paths} == before


@pytest.mark.parametrize("changes", [{}, {"version": True}, {"ready": 1}, {"generation": "foreign"}])
def test_stage_readiness_uses_owned_installed_child_and_exact_result(tmp_path, monkeypatch, changes):
    adapter = si._runtime_installer()
    payload, root, _ = _source(tmp_path)
    slot = root / "versions" / "1.2.3-dev1"
    python = slot / "Scripts" / "python.exe"
    wheels = object()
    ready = SimpleNamespace(
        slot=slot, frozen_plugin=payload, payload_hash="a" * 64, descriptor=23,
    )
    record = {
        "schema": "copilot-extensions.manager-stage-readiness", "version": 1,
        "generation": slot.name, "ready": True, **changes,
    }
    calls = []

    def child(command, **options):
        assert command == [
            str(python), "-I", "-S", "-B", "-X", "utf8",
            str(slot / ".runtime-bootstrap" / "runtime_launcher.py"),
            "-m", "worktree_manager.runtime_stage", "--root", str(root),
        ]
        assert options["descriptor"] == ready.descriptor
        assert options["candidate"] == slot and options["cwd"] == root
        assert options["timeout"] == 60
        assert options["inputs"].wheel_inputs is wheels
        calls.append(command)
        return SimpleNamespace(stdout=json.dumps(record))

    monkeypatch.setattr(adapter.runtime, "slot_python", lambda *args: python)
    monkeypatch.setattr(adapter.engine, "_package_roots", lambda path: (path,))
    monkeypatch.setattr(adapter.engine, "_child", child)
    monkeypatch.setattr(adapter, "_installation_child_options", lambda: {})
    if changes:
        with pytest.raises(RuntimeError, match="exact readiness result"):
            adapter.verify_stage_readiness(root, ready, wheel_inputs=wheels)
    else:
        adapter.verify_stage_readiness(root, ready, wheel_inputs=wheels)
    assert len(calls) == 1


def test_damaged_repair_uses_a_new_generation_of_the_original_package(tmp_path, monkeypatch):
    adapter = si._runtime_installer()
    payload, root, uv = _source(tmp_path)
    legacy = root / "versions" / "1.2.3-dev1"
    legacy.mkdir(parents=True)
    selected = "1.2.3-dev1+repair." + "a" * 32
    adapter.runtime.mark_complete(root, selected, payload_hash="a" * 64)
    si._write_marker(root, selected)
    calls = []
    @contextmanager
    def candidate(root, version, **kwargs):
        calls.append(version)
        if len(calls) == 1:
            raise adapter.engine.HealthRepairRequired("fixture installed health failed")
        yield SimpleNamespace(slot=root / "versions" / version, reused=False)
    monkeypatch.setattr(adapter.engine, "numbered_candidate", candidate)
    with adapter.candidate(payload, root, assemble=si._copy_payload, uv=uv) as ready:
        assert calls[0] == selected
        assert calls[1] != selected
        assert adapter.runtime.package_version(ready.slot.name) == "1.2.3-dev1"
        assert ".repair." not in ready.slot.name
    assert si.current_version(root) == selected


def test_foreign_source_identity_refuses_before_engine_construction(tmp_path, monkeypatch):
    adapter = si._runtime_installer()
    payload, root, uv = _source(tmp_path)
    (payload / "runtime-source.json").write_text('{"schema":"foreign"}', encoding="utf-8")
    calls = _capture(adapter, monkeypatch)
    with pytest.raises(adapter.runtime_source.SourceIdentityError, match="Source identity"):
        with adapter.candidate(payload, root, assemble=si._copy_payload, uv=uv):
            pytest.fail("foreign source identity was admitted")
    assert calls == []
    assert not (root / "current-version").exists()


@pytest.mark.parametrize("had_current", [False, True])
@pytest.mark.parametrize("had_last_good", [False, True])
@pytest.mark.parametrize("recovery_fault", [False, True])
def test_partial_selector_publication_restores_both_states(
    tmp_path, monkeypatch, had_current, had_last_good, recovery_fault,
):
    adapter = si._runtime_installer()
    root = tmp_path / "root"
    for version in ("0.9.0", "1.0.0", "2.0.0"):
        adapter.runtime.mark_complete(root, version, payload_hash="a" * 64)
    current, last_good = root / "current-version", root / "last-known-good"
    if had_current:
        current.write_bytes(b"1.0.0\r\n")
    if had_last_good:
        last_good.write_bytes(b"0.9.0\n")
    before = {path: path.read_bytes() if path.exists() else None for path in (current, last_good)}
    receipts = {path: path.read_bytes() for path in root.glob("versions/*/.install-complete.json")}
    publication = adapter.runtime._runtime_component("versioned_runtime_publication")
    replace = publication.replace_selector
    faults = []
    retained = set()
    retain = adapter.runtime.retention_lease
    held_publication = []
    lease = adapter.runtime._publication_lease
    recover = publication._recover_uncontested_locked
    recoveries = []

    @contextmanager
    def retaining(root, generation, **kwargs):
        with retain(root, generation, **kwargs) as slot:
            retained.add(generation)
            try:
                yield slot
            finally:
                retained.remove(generation)

    @contextmanager
    def publishing(root):
        with lease(root):
            held_publication.append(True)
            try:
                yield
            finally:
                held_publication.pop()

    def recovering(*args):
        assert held_publication == [True]
        assert retained == {
            "2.0.0", *(["1.0.0"] if had_current else []), *(["0.9.0"] if had_last_good else []),
        }
        recoveries.append(True)
        if recovery_fault and len(recoveries) == 1:
            raise PermissionError("transient recovery failure")
        return recover(*args)

    def fail_second(source, target):
        native = adapter.engine._native_retained_path
        if Path(native(target)) == Path(native(last_good)) and not faults:
            assert current.read_bytes() == b"2.0.0\n"
            faults.append(True)
            raise PermissionError("injected second-selector failure")
        return replace(source, target)

    monkeypatch.setattr(publication, "replace_selector", fail_second)
    monkeypatch.setattr(adapter.runtime, "retention_lease", retaining)
    monkeypatch.setattr(adapter.runtime, "_publication_lease", publishing)
    monkeypatch.setattr(publication, "_recover_uncontested_locked", recovering)
    failure = None
    try:
        adapter.activate(root, "2.0.0", expected="1.0.0" if had_current else None)
    except PermissionError as error:
        failure = error
    assert faults == [True]
    assert {path: path.read_bytes() if path.exists() else None for path in before} == before
    assert all(path.read_bytes() == content for path, content in receipts.items())
    assert failure is not None
    assert len(recoveries) == (2 if recovery_fault else 1)
    assert retained == set()


@pytest.mark.parametrize("old_last_good", [None, b"\xff\n"])
def test_selector_snapshot_is_rechecked_after_retention_acquisition(tmp_path, monkeypatch, old_last_good):
    adapter = si._runtime_installer()
    root = tmp_path / "root"
    adapter.runtime.mark_complete(root, "2.0.0", payload_hash="a" * 64)
    if old_last_good is not None:
        (root / "last-known-good").write_bytes(old_last_good)
    concurrent = b"external\n" if old_last_good is None else b"\xfe\n"
    retain = adapter.runtime.retention_lease

    @contextmanager
    def concurrent_selection(root, generation, **kwargs):
        with retain(root, generation, **kwargs) as slot:
            (root / "last-known-good").write_bytes(concurrent)
            yield slot

    monkeypatch.setattr(adapter.runtime, "retention_lease", concurrent_selection)
    with pytest.raises(RuntimeError, match="selectors changed"):
        adapter.activate(root, "2.0.0", expected=None)
    assert not (root / "current-version").exists()
    assert (root / "last-known-good").read_bytes() == concurrent


def test_unchanged_raw_selector_with_missing_slot_allows_bootstrap_recovery(tmp_path):
    adapter = si._runtime_installer()
    root = tmp_path / "root"
    adapter.runtime.mark_complete(root, "2.0.0", payload_hash="a" * 64)
    (root / "current-version").write_text("1.0.0\n")
    assert not (root / "versions" / "1.0.0").exists()
    assert si.current_version(root) == "1.0.0"
    assert adapter.runtime.current_version(root) is None
    adapter.activate(root, "2.0.0", expected="1.0.0")
    assert si.current_version(root) == "2.0.0"
    assert (root / "last-known-good").read_text().strip() == "2.0.0"


@pytest.mark.parametrize("current,last_good", [
    (b"\xff\xfe\r\n", b"0.9.0\r\n"),
    (b"../foreign\n", b"\xff\n"),
    (None, b"../foreign\r\n"),
])
@pytest.mark.parametrize("failure", [None, "partial", "after"])
def test_unknown_old_selectors_allow_valid_target_without_following_them(
    tmp_path, monkeypatch, current, last_good, failure,
):
    adapter = si._runtime_installer()
    root = tmp_path / "root"
    for generation in ("0.9.0", "2.0.0"):
        adapter.runtime.mark_complete(root, generation, payload_hash="a" * 64)
    sentinel = root / "foreign" / "keep"
    sentinel.parent.mkdir()
    sentinel.write_bytes(b"unrelated content")
    paths = [root / "current-version", root / "last-known-good"]
    before = dict(zip(paths, (current, last_good)))
    for path, content in before.items():
        if content is not None:
            path.write_bytes(content)
    receipts = {path: path.read_bytes() for path in root.glob("versions/*/.install-complete.json")}
    retained = set()
    retain, version_dir = adapter.runtime.retention_lease, adapter.runtime.version_dir
    expected_retained = {"2.0.0", *(["0.9.0"] if last_good == b"0.9.0\r\n" else [])}
    publication = adapter.runtime._runtime_component("versioned_runtime_publication")
    replace = publication.os.replace
    io_error = PermissionError("injected partial publication failure")
    faults = []

    def ordinary_slot(actual_root, generation):
        assert generation in expected_retained
        return version_dir(actual_root, generation)

    @contextmanager
    def retaining(actual_root, generation, **kwargs):
        with retain(actual_root, generation, **kwargs) as slot:
            retained.add(generation)
            try:
                yield slot
            finally:
                retained.remove(generation)

    def fail_second(source, target):
        if failure == "partial" and Path(target) == paths[1] and not faults:
            assert paths[0].read_bytes() == b"2.0.0\n"
            assert retained == expected_retained
            faults.append(True)
            raise io_error
        return replace(source, target)

    def after_publish():
        assert retained == expected_retained
        assert all(path.read_bytes() == b"2.0.0\n" for path in paths)
        if failure == "after":
            raise RuntimeError("injected uncommitted cutover failure")

    monkeypatch.setattr(adapter.runtime, "version_dir", ordinary_slot)
    monkeypatch.setattr(adapter.runtime, "retention_lease", retaining)
    monkeypatch.setattr(publication.os, "replace", fail_second)
    expected = current.decode().strip() if current == b"../foreign\n" else None
    if failure:
        with pytest.raises(
            PermissionError if failure == "partial" else RuntimeError, match="injected",
        ) as caught:
            adapter.activate(root, "2.0.0", expected=expected, after_publish=after_publish)
        if failure == "partial":
            assert caught.value is io_error and faults == [True]
        assert {path: path.read_bytes() if path.exists() else None for path in paths} == before
    else:
        adapter.activate(root, "2.0.0", expected=expected, after_publish=after_publish)
        assert all(path.read_bytes() == b"2.0.0\n" for path in paths)
    assert not retained
    assert all(path.read_bytes() == content for path, content in receipts.items())
    assert sentinel.read_bytes() == b"unrelated content"


def test_unsafe_incoming_selector_refuses_before_leases_or_publication(tmp_path, monkeypatch):
    adapter = si._runtime_installer()
    root = tmp_path / "root"
    root.mkdir()
    marker = root / "current-version"
    marker.write_bytes(b"\xff\n")

    def forbidden(*args, **kwargs):
        pytest.fail("invalid incoming target reached mutation custody")

    monkeypatch.setattr(adapter.runtime, "_publication_lease", forbidden)
    monkeypatch.setattr(adapter.runtime, "retention_lease", forbidden)
    with pytest.raises(ValueError):
        adapter.activate(root, "../foreign", expected=None)
    assert marker.read_bytes() == b"\xff\n"
    assert not (root / "last-known-good").exists()


@pytest.mark.parametrize("current", [None, b"", b"1.0.0\r\n"])
@pytest.mark.parametrize("last_good", [None, b" \t\n", b"0.9.0\n"])
def test_after_publication_failure_restores_selection_with_runtime_custody(
    tmp_path, monkeypatch, current, last_good,
):
    adapter = si._runtime_installer()
    root = tmp_path / "root"
    for generation in ("0.9.0", "1.0.0", "2.0.0"):
        adapter.runtime.mark_complete(root, generation, payload_hash="a" * 64)
    paths = [root / "current-version", root / "last-known-good"]
    for path, contents in zip(paths, (current, last_good)):
        if contents is not None:
            path.write_bytes(contents)
    before = {path: path.read_bytes() if path.exists() else None for path in paths}
    receipts = {path: path.read_bytes() for path in root.glob("versions/*/.install-complete.json")}
    retained = set()
    retain = adapter.runtime.retention_lease

    @contextmanager
    def retaining(root, generation, **kwargs):
        with retain(root, generation, **kwargs) as slot:
            retained.add(generation)
            try:
                yield slot
            finally:
                retained.remove(generation)

    monkeypatch.setattr(adapter.runtime, "retention_lease", retaining)

    def failed_cutover():
        assert all(path.read_bytes() == b"2.0.0\n" for path in paths)
        assert "2.0.0" in retained
        assert current is None or not current.strip() or "1.0.0" in retained
        assert last_good is None or not last_good.strip() or "0.9.0" in retained
        raise RuntimeError("target cutover refused")

    with pytest.raises(RuntimeError, match="cutover refused"):
        adapter.activate(
            root, "2.0.0",
            expected=current.decode().strip() or None if current is not None else None,
            after_publish=failed_cutover,
        )
    assert {path: path.read_bytes() if path.exists() else None for path in paths} == before
    assert all(path.read_bytes() == value for path, value in receipts.items())
    assert not retained
