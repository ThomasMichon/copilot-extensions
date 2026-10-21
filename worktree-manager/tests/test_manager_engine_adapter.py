"""Small adapter contracts; real construction is in the native acceptance lane."""

from __future__ import annotations

import json
import hashlib
import os
import shutil
import subprocess
import sys
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
        assert adapter.runtime_source.read_source_identity(
            prepared, "1.2.3-dev1", plugin="worktree-manager",
        ).runtime_version == generation
        assert not (prepared / ".install-complete").exists()


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
    replace = publication.os.replace
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
        if Path(target) == last_good and not faults:
            assert current.read_bytes() == b"2.0.0\n"
            faults.append(True)
            raise PermissionError("injected second-selector failure")
        return replace(source, target)

    monkeypatch.setattr(publication.os, "replace", fail_second)
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


def test_selector_snapshot_is_rechecked_after_retention_acquisition(tmp_path, monkeypatch):
    adapter = si._runtime_installer()
    root = tmp_path / "root"
    adapter.runtime.mark_complete(root, "2.0.0", payload_hash="a" * 64)
    retain = adapter.runtime.retention_lease

    @contextmanager
    def concurrent_selection(root, generation, **kwargs):
        with retain(root, generation, **kwargs) as slot:
            (root / "last-known-good").write_text("external\n")
            yield slot

    monkeypatch.setattr(adapter.runtime, "retention_lease", concurrent_selection)
    with pytest.raises(RuntimeError, match="selectors changed"):
        adapter.activate(root, "2.0.0", expected=None)
    assert not (root / "current-version").exists()
    assert (root / "last-known-good").read_text() == "external\n"


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


def test_raw_selector_does_not_admit_an_unsafe_missing_slot(tmp_path):
    adapter = si._runtime_installer()
    root = tmp_path / "root"
    adapter.runtime.mark_complete(root, "2.0.0", payload_hash="a" * 64)
    marker = root / "current-version"
    marker.write_text("../foreign\n")
    with pytest.raises(ValueError):
        adapter.activate(root, "2.0.0", expected="../foreign")
    assert marker.read_text() == "../foreign\n"
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
