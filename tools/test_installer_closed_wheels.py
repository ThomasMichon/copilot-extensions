"""Closed assembly consumes retained bytes, never ambient resolver inputs."""
from __future__ import annotations

import hashlib
import importlib.util
from contextlib import contextmanager
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import time
import zipfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libs" / "installer-engine"))
import installer_dependency_policy as policy
from installer_wheel_snapshot import retain_wheel_bundle, retain_wheel_inputs
from installer_wheel_inputs import WheelError
from wheel_input_fixture import wheel_bytes


@pytest.fixture
def engine(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root / "plugins" / "agent-worktrees" / "scripts"))
    monkeypatch.syspath_prepend(str(root / "libs" / "installer-engine"))
    path = root / "libs" / "installer-engine" / "installer_engine.py"
    spec = importlib.util.spec_from_file_location("closed_wheel_installer_engine", path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def plugin(tmp_path, engine):
    root = tmp_path / "sources"
    frozen = engine.freeze_inputs(
        Path(__file__).resolve().parents[1] / "libs" / "agent-procutil", root,
    )
    plugin = root / "plugin"
    plugin.mkdir()
    (plugin / "pyproject.toml").write_text(
        '[project]\nname="demo-pkg"\nversion="1.0"\ndependencies=[]\n'
        '[project.optional-dependencies]\nbootstrap=["agent-procutil"]\n'
        '[tool.uv.sources]\nagent-procutil={path=' +
        json.dumps(os.path.relpath(frozen.plugin, plugin).replace(os.sep, "/")) + '}\n',
        encoding="utf-8",
    )
    return plugin


@pytest.fixture
def negative_source_plugin(tmp_path):
    root = tmp_path / "negative-sources"
    declarations = (
        ("plugin", "demo-pkg", "demo_pkg", "fixture-middle", "../middle"),
        ("middle", "fixture-middle", "fixture_middle", "fixture-leaf", "../leaf"),
        ("leaf", "fixture-leaf", "fixture_leaf", None, None),
    )
    for directory, name, package, dependency, relative in declarations:
        project = root / directory
        data = project / "src" / package / "data"
        data.mkdir(parents=True)
        metadata = (
            '[project]\nname=' + json.dumps(name) + '\nversion="1.0"\ndependencies=[]\n'
        )
        if dependency is not None:
            metadata += (
                '[project.optional-dependencies]\nbootstrap=[' + json.dumps(dependency) + ']\n'
                '[tool.uv.sources]\n' + dependency + '={path=' + json.dumps(relative)
                + ',editable=true}\n'
            )
        metadata += (
            '[tool.setuptools.package-data]\n' + package + '=["data/*"]\n'
        )
        (project / "pyproject.toml").write_text(metadata, encoding="utf-8")
        (data.parent / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
        (data / "payload.bin").write_bytes(b"source resource\x00" + name.encode())
        (data / "mode.txt").write_text("ordinary source mode\n", encoding="utf-8")
    return root / "plugin"


def test_negative_source_graph_freezes_complete_transitive_resources(
    tmp_path, engine, negative_source_plugin,
):
    plugin = negative_source_plugin
    roots = engine._package_roots(plugin)
    assert {engine._package_name(root) for root in roots} == {
        "demo-pkg", "fixture-middle", "fixture-leaf",
    }
    common = plugin.parent
    mode_resource = common / "leaf/src/fixture_leaf/data/mode.txt"
    mode_resource.chmod(0o444)
    files = engine._source_files(roots)
    original = {path.relative_to(common): path.read_bytes() for path in files}
    modes = {path.relative_to(common): stat.S_IMODE(path.stat().st_mode) for path in files}
    first = engine.freeze_inputs(plugin, tmp_path / "first-snapshot")
    assert first.plugin == tmp_path / "first-snapshot/plugin"
    assert engine._package_roots(first.plugin) == first.packages
    assert len(first.packages) == 3
    copied = engine._source_files(first.packages)
    assert {
        path.relative_to(tmp_path / "first-snapshot"): path.read_bytes() for path in copied
    } == original
    assert {
        path.relative_to(tmp_path / "first-snapshot"): stat.S_IMODE(path.stat().st_mode)
        for path in copied
    } == modes
    assert {path.relative_to(common): path.read_bytes() for path in files} == original
    leaf_resource = common / "leaf/src/fixture_leaf/data/payload.bin"
    original_leaf = leaf_resource.read_bytes()
    leaf_resource.write_bytes(original_leaf + b"changed")
    second = engine.freeze_inputs(plugin, tmp_path / "second-snapshot")
    assert second.payload_hash != first.payload_hash
    assert (
        tmp_path / "first-snapshot/leaf/src/fixture_leaf/data/payload.bin"
    ).read_bytes() == original_leaf
    assert (
        tmp_path / "second-snapshot/leaf/src/fixture_leaf/data/payload.bin"
    ).read_bytes() == leaf_resource.read_bytes()


def inspected(retained, **changes):
    from packaging.markers import default_environment
    from runtime_profile import SCHEMA, validate_profile
    from runtime_profile_wheels import profile_wheel_inputs

    has_helper = "helper-1.0-py3-none-any.whl" in retained.paths
    record = {
        "schema": SCHEMA, "version": 1,
        "release": {"identity": "fixture-release", "sha256": "sha256:" + "2" * 64},
        "mode": "source", "artifact": None, "component": "index-engine", "backend": "cpu",
        "approval_source": "fixture-authority", "acquisition_policy": "fixture-policy",
        "target": {"device": "cpu", "environment": default_environment(), "tags": ["py3-none-any"]},
        "roots": ["demo-pkg==1.0"],
        "wheels": [{
            "name": filename.split("-", 1)[0].replace("_", "-"),
            "version": "1.0", "filename": filename,
            "sha256": retained.digests[filename], "requires_python": "",
            "provides_extras": [], "selected_extras": [],
            "requires_dist": ["helper==1.0"] if filename.startswith("demo_pkg-") and has_helper else [],
        } for filename in retained.paths],
        **changes,
    }
    return profile_wheel_inputs(
        validate_profile(record), retained, max_archive_bytes=100_000,
        max_unpacked_bytes=100_000, max_metadata_bytes=10_000,
        deadline=time.monotonic() + 60,
    )


def candidate(engine, root, plugin, wheels, **options):
    uv = shutil.which("uv")
    assert uv, "native UV required"
    return engine.numbered_candidate(
        root, "1.0.0", plugin=plugin, uv=Path(uv), base_python=Path(sys.executable),
        health_module="demo_pkg", local_packages=("demo-pkg",), wheel_inputs=wheels, **options,
    )


@pytest.mark.skipif(os.name == "nt", reason="Actual single-thread POSIX umask022 child")
def test_fresh_private_runtime_root_under_standard_posix_umask(tmp_path):
    """Integration, not eligible-unit CI: no controller umask mutation."""
    repository = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repository / "libs/agent-procutil/src"))
    from agent_procutil.owned_process import run_caller_owned
    script = r"""
import os
os.umask(0o022)
import site
site.main()
import importlib.util,json,pathlib,stat,sys,threading
from contextlib import contextmanager
assert threading.active_count()==1
repository,base=map(pathlib.Path,sys.argv[1:])
sys.path[:0]=[str(repository/'libs/installer-engine'),str(repository/'libs/versioned-runtime'),
              str(repository/'fleet-manager/src')]
import installer_engine as engine
from fleet_manager.private_state import private_directory
engine._assert_source_pin=lambda *args:None
engine._selected_extras=lambda *args:()
engine._package_roots=lambda path:(path,)
engine._selected_local_packages=lambda *args:()
root=base/'fresh-engine'
class SourceReached(Exception):pass
@contextmanager
def phase(*args):
    assert stat.S_IMODE(root.stat().st_mode)==0o700
    private_directory(root)
    raise SourceReached()
    yield
engine._source_phase=phase
try:
    with engine.numbered_candidate(root,'1.0',plugin=base,uv=pathlib.Path(sys.executable),
                                   base_python=pathlib.Path(sys.executable),health_module='fixture'):pass
except SourceReached:pass
leases=engine.runtime._runtime_component('versioned_runtime_leases')
for kind in ('construction','retention'):
    path=base/kind
    with leases._kernel_lease(path,'fixture',kind=kind,shared=kind=='retention',timeout=1):
        private_directory(path)
        assert stat.S_IMODE(path.stat().st_mode)==0o700
existing=base/'existing'
existing.mkdir(mode=0o755)
before=existing.stat()
with leases._kernel_lease(existing,'fixture',kind='construction',shared=False,timeout=1):pass
try:private_directory(existing)
except ValueError:pass
else:raise AssertionError('existing broad root was accepted')
after=existing.stat()
assert (before.st_ino,before.st_uid,before.st_mode)==(after.st_ino,after.st_uid,after.st_mode)
assert threading.active_count()==1
print(json.dumps({'actual_POSIX_umask':'022','fresh_root_modes':'0700',
                  'existing0755_refused_unchanged':True,'single_threaded_child':True}))
"""
    response = run_caller_owned(
        [sys.executable, "-I", "-S", "-B", "-c", script, str(repository), str(tmp_path)],
        cwd=repository, timeout=20, max_stdout_bytes=65536, max_stderr_bytes=65536)
    assert response.returncode == 0, response.stderr
    assert json.loads(response.stdout)["existing0755_refused_unchanged"]


def snapshot(tmp_path, *, closure=False, version="1.0", transport="loose"):
    path = tmp_path / "demo_pkg-1.0-py3-none-any.whl"
    if not path.exists():
        path.write_bytes(wheel_bytes(
            metadata=(
                f"Metadata-Version: 2.4\nName: demo-pkg\nVersion: {version}\n"
                + ("Requires-Dist: helper==1.0\n" if closure else "") + "\n"
            ).encode(),
        ))
    paths = {path.name: path}
    if closure:
        helper = tmp_path / "helper-1.0-py3-none-any.whl"
        if not helper.exists():
            helper.write_bytes(wheel_bytes(
                name="helper", prefix="helper-1.0.dist-info/",
                metadata=b"Metadata-Version: 2.4\nName: helper\nVersion: 1.0\n\n",
            ))
        paths[helper.name] = helper
    hashes = {
        name: "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        for name, path in paths.items()
    }
    if transport == "bundle":
        bundle = tmp_path / "transport.zip"
        with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, path in paths.items():
                archive.write(path, "wheels/" + name)
        for path in paths.values():
            path.unlink()
        return retain_wheel_bundle(
            bundle, hashes, bundle_sha256="sha256:" + hashlib.sha256(bundle.read_bytes()).hexdigest(),
            max_bundle_bytes=100_000, max_wheels=2, max_archive_bytes=100_000,
            max_total_bytes=100_000, max_snapshot_bytes=200_000, deadline=time.monotonic() + 60,
        )
    return retain_wheel_inputs(
        paths, hashes,
        max_wheels=2, max_archive_bytes=100_000, max_total_bytes=100_000,
        max_snapshot_bytes=100_000, deadline=time.monotonic() + 60,
    )


@pytest.mark.parametrize("changed", ["digest", "metadata", "target"])
def test_native_wheel_receiver_refuses_changed_inputs(tmp_path, engine, plugin, changed):
    import install_source

    with snapshot(tmp_path) as retained:
        record = json.loads(inspected(retained).binding_json)
    if changed == "digest":
        record["wheels"][0]["sha256"] = "sha256:" + "0" * 64
    elif changed == "metadata":
        record["wheels"][0]["requires_dist"] = ["unselected-package==1.0"]
    else:
        record["target"]["environment"]["python_version"] = "0.0"
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ValueError, match="digest|metadata|target"):
        with install_source.wheel_inputs(profile, tmp_path) as inputs:
            install_source.wheel_selection(plugin, "demo-pkg", inputs)
    assert not (tmp_path / "versions").exists()


def test_native_wheel_receiver_rechecks_profile_after_native_setup(tmp_path, engine):
    import install_source

    with snapshot(tmp_path) as retained:
        record = json.loads(inspected(retained).binding_json)
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ValueError, match="profile changed"):
        with install_source.wheel_inputs(profile, tmp_path):
            record["approval_source"] = "changed-input"
            profile.write_text(json.dumps(record), encoding="utf-8")


def test_native_wheel_receiver_accepts_admitted_compatible_patch_target(tmp_path, engine, plugin):
    import install_source

    with snapshot(tmp_path) as retained:
        record = json.loads(inspected(retained).binding_json)
    environment = record["target"]["environment"]
    environment["python_full_version"] = environment["python_version"] + ".999"
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps(record), encoding="utf-8")
    with install_source.wheel_inputs(profile, tmp_path) as inputs:
        selection = install_source.wheel_selection(plugin, "demo-pkg", inputs)
        assert selection["runtime_version"].startswith("1.0.profile.")


def test_native_driver_binds_and_rechecks_actual_closed_profile(tmp_path, engine, plugin, monkeypatch):
    from types import SimpleNamespace
    import installer_driver

    monkeypatch.setitem(installer_driver.RECIPES, "demo-pkg", frozenset())
    monkeypatch.delenv(installer_driver.WHEEL_SELECTION_ENV, raising=False)
    monkeypatch.delenv(installer_driver.SOURCE_SELECTION_ENV, raising=False)
    with snapshot(tmp_path) as retained:
        record = json.loads(inspected(retained).binding_json)
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps(record), encoding="utf-8")
    args = SimpleNamespace(
        payload=plugin, root=tmp_path / "runtime", plugin="demo-pkg", action="install",
        options=[], wheel_profile=profile, wheel_directory=tmp_path,
        wheel_python=Path(sys.executable), wheel_uv=tmp_path / "uv",
    )
    arguments = installer_driver.native_arguments(args)
    assert arguments[-8:] == [
        "-WheelProfile", str(profile), "-WheelDirectory", str(tmp_path),
        "-WheelPython", sys.executable, "-WheelUv", str(tmp_path / "uv"),
    ]
    assert installer_driver.native_arguments(args) == arguments
    record["approval_source"] = "changed-after-driver-admission"
    profile.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ValueError, match="selection changed after driver admission"):
        installer_driver.native_arguments(args)
    assert not args.root.exists()


def test_owner_validation_precedes_completion_and_rechecks_read_only_reuse(
    tmp_path, engine, plugin, monkeypatch,
):
    root = tmp_path / "runtime"
    observed = []

    def validate(slot):
        observed.append((slot, engine.runtime.marker_path(root, slot.name).exists()))

    with snapshot(tmp_path) as retained:
        wheels = inspected(retained)
        with candidate(engine, root, plugin, wheels, validate_candidate=validate) as ready:
            assert observed == [(ready.slot, False)]
            assert engine.runtime.is_complete(root, ready.slot.name, expect_hash=ready.payload_hash)
            marker = engine.runtime.marker_path(root, ready.slot.name)
            completed = marker.read_bytes()

    monkeypatch.setattr(engine, "_construct", lambda *_a, **_k: pytest.fail("reuse invoked writers"))

    def reject(slot):
        validate(slot)
        raise ValueError("owner rejected reused candidate")

    with snapshot(tmp_path) as retained:
        wheels = inspected(retained)
        with pytest.raises(ValueError, match="owner rejected"):
            with candidate(engine, root, plugin, wheels, validate_candidate=reject):
                pytest.fail("rejected reuse was exposed")
        assert observed[-1] == (ready.slot, True)
        assert marker.read_bytes() == completed
        assert engine.runtime.is_complete(root, ready.slot.name, expect_hash=ready.payload_hash)
@pytest.mark.parametrize("mutation", ["source", "binding"])
def test_expected_payload_pin_revalidates_source_and_closed_binding(
    tmp_path, engine, plugin, mutation,
):
    from dataclasses import replace

    with snapshot(tmp_path) as retained:
        wheels = inspected(retained)
        packages = engine._package_roots(plugin)
        source_hash = engine._construction_hash(
            engine._source_files(packages), (), ("demo-pkg",),
        )
        inputs = engine.FrozenInputs(
            plugin.resolve(), packages, engine._wheel_construction_hash(source_hash, wheels),
            local_packages=("demo-pkg",), wheel_inputs=wheels,
        )
        engine._revalidate_input_content(inputs)
        if mutation == "source":
            manifest = plugin / "pyproject.toml"
            manifest.write_text(manifest.read_text(encoding="utf-8") + "\n# changed source\n")
        else:
            inputs = replace(
                inputs, wheel_inputs=inspected(retained, approval_source="other-fixture-authority"),
            )
        with pytest.raises(RuntimeError, match="expected frozen content"):
            engine._revalidate_input_content(inputs)


@pytest.mark.parametrize("executable", ["path", "command"])
def test_closed_transaction_is_canonical_and_cleans_owned_inputs(tmp_path, executable):
    uv = shutil.which("uv")
    assert uv, "native UV required"
    target = tmp_path / "candidate" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    with snapshot(tmp_path) as retained:
        with policy.retain_closed_wheel_install(
            uv if executable == "path" else "uv", retained, python=target,
            env={"UV_INDEX": "poison", "PIP_REQUIREMENT": "poison", "PYTHONPATH": "poison",
                 "VIRTUAL_ENV": "poison", "CONDA_PREFIX": "poison", "PATH": os.environ["PATH"]},
        ) as transaction:
            assert isinstance(transaction, policy.RetainedUVInstall)
            assert not any(key.startswith(("UV_", "PIP_", "PYTHON")) for key in transaction.environment)
            assert "VIRTUAL_ENV" not in transaction.environment
            assert "CONDA_PREFIX" not in transaction.environment
            assert transaction.sources == transaction.source_hashes == {}
            assert Path(transaction.argv[0]).samefile(uv)
            assert transaction.argv[1:3] == ("pip", "install")
            for flag in ("--offline", "--no-index", "--no-deps", "--no-build", "--no-cache",
                         "--no-config", "--require-hashes", "--no-sources"):
                assert flag in transaction.argv
            requirement = Path(transaction.argv[-1])
            text = requirement.read_text(encoding="utf-8")
            wheel, = retained.paths.values()
            assert policy._file_uri(wheel) in text and "--hash=sha256:" in text
            assert "file://%3F/" not in text
            assert transaction.cwd.is_dir()
        assert not requirement.exists() and not transaction.cwd.exists()
        retained.verify()


def test_closed_transaction_does_not_relax_ordinary_source_policy(tmp_path):
    with pytest.raises(policy.DependencyPolicyError, match="cannot bypass"):
        policy.prepare_uv_install("unused", ["--no-deps"], env={})


def test_closed_transaction_refuses_relative_target(tmp_path):
    with snapshot(tmp_path) as retained:
        with pytest.raises(policy.DependencyPolicyError, match="absolute"):
            with policy.retain_closed_wheel_install("unused", retained, python=Path("python")):
                pytest.fail("relative interpreter admitted")


@pytest.mark.parametrize("cancel", [False, True])
def test_closed_transaction_preserves_snapshot_on_child_failure(tmp_path, cancel):
    uv = shutil.which("uv")
    assert uv
    with snapshot(tmp_path) as retained:
        with pytest.raises(KeyboardInterrupt if cancel else RuntimeError):
            with policy.retain_closed_wheel_install(
                uv, retained, python=Path(sys.executable),
            ) as transaction:
                raise KeyboardInterrupt if cancel else RuntimeError("failed child")
        assert not transaction.cwd.exists()
        retained.verify()


def test_native_closed_transaction_ignores_poisoned_config_and_cache(tmp_path):
    uv = shutil.which("uv")
    assert uv, "native UV required"
    poison = tmp_path / "ambient"
    poison.mkdir()
    (poison / "uv.toml").write_text("not valid TOML {{", encoding="utf-8")
    (poison / "pyproject.toml").write_text("not valid TOML {{", encoding="utf-8")
    env = dict(os.environ)
    env.update({
        "UV_CONFIG_FILE": str(poison / "uv.toml"), "UV_PROJECT": str(poison),
        "UV_INDEX_URL": "http://127.0.0.1:1/unavailable", "UV_CACHE_DIR": str(poison / "cache"),
        "UV_CONSTRAINT": str(poison / "missing"), "UV_PYTHON": "missing",
        "UV_TARGET": str(poison / "target"), "UV_BREAK_SYSTEM_PACKAGES": "true",
        "PIP_REQUIREMENT": str(poison / "missing"), "PYTHONPATH": str(poison),
    })
    with snapshot(tmp_path) as retained:
        for name in ("first", "second"):
            slot = tmp_path / name
            python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            subprocess.run(
                [sys.executable, "-I", "-m", "venv", "--without-pip", "--copies", str(slot)],
                check=True, capture_output=True, timeout=30, creationflags=policy._HEADLESS,
            )
            with policy.retain_closed_wheel_install(uv, retained, python=python, env=env) as transaction:
                result = subprocess.run(
                    transaction.argv, env=transaction.environment, cwd=transaction.cwd,
                    capture_output=True, text=True, timeout=30, creationflags=policy._HEADLESS,
                )
                assert result.returncode == 0, result.stderr
                proof = subprocess.run(
                    [str(python), "-I", "-c",
                     "import importlib.metadata as m,json; "
                     "print(json.dumps({d.metadata['Name']: d.version for d in m.distributions()}))"],
                    check=True, capture_output=True, text=True, timeout=30,
                    creationflags=policy._HEADLESS,
                )
                assert json.loads(proof.stdout) == {"demo-pkg": "1.0"}
        assert not (poison / "cache").exists() and not (poison / "target").exists()


@pytest.mark.parametrize("transport", ["loose", "bundle"])
@pytest.mark.parametrize("nested", [False, True])
def test_native_existing_constructor_builds_and_reuses_exact_closed_wheels(
    tmp_path, engine, plugin, monkeypatch, transport, nested,
):
    monkeypatch.setenv("UV_INDEX_URL", "http://127.0.0.1:1/unavailable")
    monkeypatch.setenv("UV_CONFIG_FILE", str(tmp_path / "missing-config"))
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "poison-cache"))
    leases = []

    def bounded(lease):
        @contextmanager
        def acquire(*args, **kwargs):
            remaining = wheels.deadline - time.monotonic()
            assert 0 <= kwargs["timeout"] <= remaining + 0.05
            leases.append((lease.__name__, kwargs["timeout"]))
            with lease(*args, **kwargs) as held:
                yield held
        return acquire

    monkeypatch.setattr(engine.runtime, "construction_lease", bounded(engine.runtime.construction_lease))
    monkeypatch.setattr(engine.runtime, "retention_custody", bounded(engine.runtime.retention_custody))
    with snapshot(tmp_path, closure=True, version="1", transport=transport) as retained:
        if transport == "bundle":
            (tmp_path / "transport.zip").unlink()
        original_wheels = {name: path.read_bytes() for name, path in retained.paths.items()}
        wheels = inspected(retained)
        assert wheels.receipt()["distributions"]["demo-pkg"]["version"] == "1"
        slots = []
        for name in ("first-runtime", "second-runtime"):
            root = tmp_path / name
            with candidate(engine, root, plugin, wheels, nested_environment=nested) as built:
                assert not built.reused
                assert engine.runtime.is_complete(root, "1.0.0", expect_hash=built.payload_hash)
                assert engine.runtime.current_version(root) is None
                slots.append(built.slot)
                python = engine.runtime.slot_python(root, built.slot.name)
                assert python.parent.parent == (built.slot / ".venv" if nested else built.slot)
                receipt = json.loads((built.slot / ".runtime-input-origins.json").read_text())
                assert receipt["version"] == 2
                assert receipt["wheel_inputs"] == wheels.receipt()
        assert slots[0] != slots[1]
        assert not (tmp_path / "poison-cache").exists()
    # Re-inspect cached bytes under a new temporary path; reuse binds content,
    # not the ephemeral location used by a previous installation.
    if transport == "bundle":
        for name, data in original_wheels.items():
            (tmp_path / name).write_bytes(data)
    with snapshot(tmp_path, closure=True, version="1", transport=transport) as retained:
        wheels = inspected(retained)

        def no_reconstruction(*args, **kwargs):
            pytest.fail("complete healthy wheel runtime must not be rebuilt")

        monkeypatch.setattr(engine, "_construct", no_reconstruction)
        with candidate(
            engine, tmp_path / "first-runtime", plugin, wheels, nested_environment=nested,
        ) as reused:
            assert reused.reused
            assert reused.slot == slots[0]
        if not nested:
            with pytest.raises(engine.HealthRepairRequired, match="nested environment layout"):
                with candidate(
                    engine, tmp_path / "first-runtime", plugin, wheels, nested_environment=True,
                ):
                    pytest.fail("a flat runtime cannot satisfy the native nested layout")
    assert [name for name, _timeout in leases] == [
        "construction_lease", "retention_custody",
        "construction_lease", "retention_custody", "retention_custody",
    ] + ([] if nested else ["retention_custody"])
    assert leases[1][1] < leases[0][1]


def test_closed_lease_timeout_preserves_wait_limit_and_rejects_expiry(
    tmp_path, engine, plugin, monkeypatch,
):
    with snapshot(tmp_path) as retained:
        wheels = inspected(retained)
        inputs = engine.FrozenInputs(plugin, (plugin,), "fixture", wheel_inputs=wheels)
        now = time.monotonic()
        with monkeypatch.context() as clock:
            clock.setattr(time, "monotonic", lambda: now)
            assert engine._lease_timeout(inputs, 0) == 0
            assert engine._lease_timeout(inputs, 0.1) == 0.1
            assert engine._lease_timeout(inputs, 180) == wheels.deadline - now
            clock.setattr(time, "monotonic", lambda: wheels.deadline)
            with pytest.raises(WheelError, match="deadline"):
                engine._lease_timeout(inputs, 180)
            assert engine._lease_timeout(replace(inputs, wheel_inputs=None), 180) == 180


def test_closed_constructor_retains_caller_relative_uv_as_absolute(
    tmp_path, engine, plugin, monkeypatch,
):
    uv = shutil.which("uv")
    assert uv
    tool = tmp_path / "tools" / ("uv.exe" if os.name == "nt" else "uv")
    tool.parent.mkdir()
    shutil.copy2(uv, tool)
    monkeypatch.chdir(tmp_path)
    child = engine._child
    installations = []

    def absolute(argv, **kwargs):
        if argv[1:3] == ["pip", "install"]:
            assert Path(argv[0]).is_absolute()
            assert Path(argv[0]).samefile(tool)
            assert kwargs["cwd"] != tmp_path
            installations.append(argv)
        return child(argv, **kwargs)

    monkeypatch.setattr(engine, "_child", absolute)
    with snapshot(tmp_path) as retained:
        with engine.numbered_candidate(
            tmp_path / "runtime", "1.0.0", plugin=plugin,
            uv=tool.relative_to(tmp_path), base_python=Path(sys.executable),
            health_module="demo_pkg", local_packages=("demo-pkg",),
            wheel_inputs=inspected(retained),
        ) as built:
            assert not built.reused
    assert len(installations) == 1


def test_changed_closed_binding_cannot_reuse_numbered_identity(tmp_path, engine, plugin):
    root = tmp_path / "runtime"
    with snapshot(tmp_path, closure=True) as retained:
        wheels = inspected(retained)
        with candidate(engine, root, plugin, wheels):
            pass
        target = {**wheels.receipt()["binding"]["target"], "device": "other"}
        for changes in [
            {"acquisition_policy": "new-policy"}, {"approval_source": "new-authority"},
            {"release": {"identity": "other-release", "sha256": "sha256:" + "3" * 64}},
            {"component": "other-component"}, {"target": target},
            {"backend": "other", "target": target},
            {"mode": "artifact", "artifact": {
                "artifact_id": "sha256:" + "4" * 64, "manifest_sha256": "sha256:" + "5" * 64,
            }},
        ]:
            with pytest.raises(RuntimeError, match="different content"):
                with candidate(engine, root, plugin, inspected(retained, **changes)):
                    pytest.fail("changed exact binding reused a published runtime")


def test_large_closed_receipt_does_not_cross_native_command_line_limit(
    tmp_path, engine, plugin, monkeypatch,
):
    archives = {}
    entries = []
    dependencies = []
    for number in range(96):
        name = f"part{number}"
        filename = f"{name}-1.0-py3-none-any.whl"
        path = tmp_path / filename
        path.write_bytes(wheel_bytes(
            name=name, prefix=f"{name}-1.0.dist-info/",
            metadata=f"Metadata-Version: 2.4\nName: {name}\nVersion: 1.0\n\n".encode(),
        ))
        archives[filename] = path
        dependencies.append(f"{name}==1.0")
    filename = "demo_pkg-1.0-py3-none-any.whl"
    path = tmp_path / filename
    path.write_bytes(wheel_bytes(metadata=(
        "Metadata-Version: 2.4\nName: demo-pkg\nVersion: 1.0\n"
        + "".join(f"Requires-Dist: {edge}\n" for edge in dependencies) + "\n"
    ).encode()))
    archives[filename] = path
    digests = {
        name: "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        for name, path in archives.items()
    }
    for filename in archives:
        entries.append({
            "name": filename.split("-", 1)[0].replace("_", "-"), "version": "1.0",
            "filename": filename, "sha256": digests[filename], "requires_python": "",
            "provides_extras": [], "selected_extras": [],
            "requires_dist": dependencies if filename.startswith("demo_pkg-") else [],
        })
    with retain_wheel_inputs(
        archives, digests, max_wheels=100, max_archive_bytes=100_000,
        max_total_bytes=1_000_000, max_snapshot_bytes=1_000_000,
        deadline=time.monotonic() + 120,
    ) as retained:
        wheels = inspected(retained, wheels=entries)
        assert len(json.dumps(wheels.receipt())) > 32_768
        child = engine._child
        probes = []

        def bounded(argv, **kwargs):
            if "-c" in argv:
                assert len(subprocess.list2cmdline(argv)) < 16_000
                probes.append(argv)
            return child(argv, **kwargs)

        monkeypatch.setattr(engine, "_child", bounded)
        root = tmp_path / "runtime"
        with candidate(engine, root, plugin, wheels) as built:
            assert not built.reused
        with candidate(engine, root, plugin, wheels) as reused:
            assert reused.reused
        assert len(probes) == 2


@pytest.mark.parametrize("corrupt", [
    "extra-distribution", "archive-digest", "missing-digest", "receipt",
])
def test_closed_reuse_checks_installed_closure_origins_and_binding(tmp_path, engine, plugin, corrupt):
    root = tmp_path / "runtime"
    with snapshot(tmp_path, closure=True) as retained:
        wheels = inspected(retained)
        with candidate(engine, root, plugin, wheels) as built:
            slot = built.slot
        engine.runtime.activate(root, "1.0.0", link_free=True)
        site = slot / ("Lib/site-packages" if os.name == "nt" else
                       f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages")
        if corrupt == "extra-distribution":
            extra = site / "intruder-1.0.dist-info"
            extra.mkdir()
            (extra / "METADATA").write_text("Name: intruder\nVersion: 1.0\n", encoding="utf-8")
        elif corrupt in ("archive-digest", "missing-digest"):
            origin = site / "demo_pkg-1.0.dist-info" / "direct_url.json"
            record = json.loads(origin.read_text())
            if corrupt == "archive-digest":
                record["archive_info"]["hashes"] = {"sha256": "0" * 64}
            else:
                record["url"] = record["url"].split("#", 1)[0]
            origin.write_text(json.dumps(record), encoding="utf-8")
        else:
            origin = slot / ".runtime-input-origins.json"
            record = json.loads(origin.read_text())
            record["wheel_inputs"]["binding"]["acquisition_policy"] = "tampered"
            origin.write_text(json.dumps(record), encoding="utf-8")
        with pytest.raises(engine.HealthRepairRequired, match="isolated installed health"):
            with candidate(engine, root, plugin, wheels):
                pytest.fail("corrupt installed evidence reused")
        assert engine.runtime.current_version(root) == "1.0.0"


@pytest.mark.parametrize("failure", ["health", "cancel", "deadline"])
def test_closed_failure_preserves_selected_prior_and_settles_input_custody(
    tmp_path, engine, plugin, monkeypatch, failure,
):
    root = tmp_path / "runtime"
    with snapshot(tmp_path, closure=True) as retained:
        wheels = inspected(retained)
        with candidate(engine, root, plugin, wheels):
            pass
        engine.runtime.activate(root, "1.0.0", link_free=True)
        uv = shutil.which("uv")
        construct = engine._construct
        freeze = engine.freeze_inputs
        frozen_paths = []

        def frozen(*args, **kwargs):
            result = freeze(*args, **kwargs)
            frozen_paths.append(args[1])
            return result

        def cancelled(*args, **kwargs):
            construct(*args, **kwargs)
            raise KeyboardInterrupt

        monkeypatch.setattr(engine, "freeze_inputs", frozen)
        if failure == "cancel":
            monkeypatch.setattr(engine, "_construct", cancelled)
        started = time.monotonic()
        marker = tmp_path / "deadline-child"
        timeouts = []
        if failure == "deadline":
            wheels = inspected(replace(retained, _deadline=started + 8))
            owned = engine.run_owned

            def blocked(argv, **options):
                timeouts.append(options["timeout"])
                return owned([
                    sys.executable, "-I", "-c",
                    "import os,pathlib,sys,time;pathlib.Path(sys.argv[1]).write_text(str(os.getpid()));time.sleep(30)",
                    str(marker),
                ], **options)

            monkeypatch.setattr(engine, "run_owned", blocked)
        expected = {
            "health": subprocess.CalledProcessError, "cancel": KeyboardInterrupt,
            "deadline": (subprocess.TimeoutExpired, WheelError),
        }[failure]
        with pytest.raises(expected) as caught:
            with engine.numbered_candidate(
                root, "1.0.1", plugin=plugin, uv=Path(uv), base_python=Path(sys.executable),
                health_module="demo_pkg", health_callable="missing",
                local_packages=("demo-pkg",), wheel_inputs=wheels,
            ):
                pytest.fail("failed installed health yielded a complete candidate")
        assert engine.runtime.current_version(root) == "1.0.0"
        assert not engine.runtime.marker_path(root, "1.0.1").exists()
        assert not tuple((root / ".installer-inputs").iterdir())
        assert frozen_paths and all(not path.exists() for path in frozen_paths)
        with engine.runtime.construction_lease(root, "1.0.1", timeout=0):
            pass
        retained.verify()
        if failure == "deadline":
            from test_installer_wheel_acquisition import _running
            if marker.is_file():
                assert not _running(int(marker.read_text()))
            if not isinstance(caught.value, subprocess.TimeoutExpired):
                assert "deadline" in str(caught.value)
            assert all(0 < timeout <= 8 for timeout in timeouts)
            assert time.monotonic() - started < 13


def test_prepared_closed_child_uses_actual_remaining_deadline(tmp_path, engine, plugin, monkeypatch):
    from test_installer_wheel_acquisition import _running

    frozen = engine.freeze_inputs(plugin, tmp_path / "prepared")
    procutil = engine._procutil(frozen)
    monkeypatch.setattr(engine, "_procutil", lambda _inputs: procutil)
    run_owned = engine.run_owned

    def contained_child(argv, **options):
        assert options.get("allow_breakaway", False) is False
        return run_owned(argv, **options)

    monkeypatch.setattr(engine, "run_owned", contained_child)
    marker = tmp_path / "owned-child"
    root = tmp_path / "runtime"
    with snapshot(tmp_path) as retained:
        wheels = inspected(retained)
        with engine.runtime.construction_lease(root, "1.0.0", timeout=0) as descriptor:
            started = time.monotonic()
            selected = replace(wheels, snapshot=replace(retained, _deadline=started + 3))
            with pytest.raises(subprocess.TimeoutExpired):
                engine._child([
                    sys.executable, "-I", "-c",
                    "import os,pathlib,sys,time;pathlib.Path(sys.argv[1]).write_text(str(os.getpid()));time.sleep(30)",
                    str(marker),
                ], inputs=replace(frozen, wheel_inputs=selected), descriptor=descriptor, timeout=30)
            assert marker.is_file() and not _running(int(marker.read_text()))
            assert time.monotonic() - started < 6
        with engine.runtime.construction_lease(root, "1.0.0", timeout=0):
            pass


def test_freeze_deadline_expiry_during_copy_removes_only_owned_partial_inputs(
    tmp_path, engine, negative_source_plugin, monkeypatch,
):
    plugin = negative_source_plugin
    destination = tmp_path / "partial"
    before = (plugin / "pyproject.toml").read_bytes()
    source_files = engine._source_files(engine._package_roots(plugin))
    original = {path: path.read_bytes() for path in source_files}
    sentinel = tmp_path / "unrelated.txt"
    sentinel.write_bytes(b"preserved")
    deadline = time.monotonic() + 60
    check_deadline = engine._check_deadline
    copy_mode = engine.shutil.copymode
    calls = []

    def copied(source, target, **kwargs):
        result = copy_mode(source, target, **kwargs)
        calls.append(target)
        return result

    def expired(observed_deadline):
        assert observed_deadline == deadline
        return check_deadline(time.monotonic() - 1 if calls else observed_deadline)

    monkeypatch.setattr(engine.shutil, "copymode", copied)
    monkeypatch.setattr(engine, "_check_deadline", expired)
    with pytest.raises(WheelError, match="deadline"):
        engine.freeze_inputs(plugin, destination, deadline=deadline)
    assert calls and not destination.exists()
    assert (plugin / "pyproject.toml").read_bytes() == before
    assert {path: path.read_bytes() for path in source_files} == original
    assert sentinel.read_bytes() == b"preserved"


@pytest.mark.parametrize("deadline", [0, float("inf"), True])
def test_invalid_freeze_deadline_refuses_before_creating_inputs(tmp_path, engine, deadline):
    plugin = tmp_path / "absent-source"
    destination = tmp_path / "must-not-exist"
    with pytest.raises(WheelError, match="deadline"):
        engine.freeze_inputs(plugin, destination, deadline=deadline)
    assert not destination.exists()
    assert not plugin.exists()


@pytest.mark.skipif(os.name != "nt", reason="native Windows absolute-deadline guardian")
def test_retained_guardian_startup_cannot_renew_an_absolute_deadline(engine, tmp_path, monkeypatch):
    from test_installer_wheel_acquisition import worker_procutil
    import installer_engine_process as guardian

    source = Path(guardian.__file__)
    delayed = tmp_path / "delayed-guardian.py"
    guardian_marker = tmp_path / "guardian-started"
    delayed.write_text(source.read_text().replace(
        "def _supervise_windows(args, argv):",
        "def _supervise_windows(args, argv):\n"
        "    Path(" + repr(str(guardian_marker)) + ").write_text('entered')\n    time.sleep(1.5)",
    ))
    spec = importlib.util.spec_from_file_location("absolute_deadline_guardian", delayed)
    selected = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, selected)
    spec.loader.exec_module(selected)
    runtime = engine.runtime
    root = tmp_path / "runtime"
    root.mkdir()
    slot = runtime.slot(root, "probe")
    marker = tmp_path / "must-not-start"
    procutil = worker_procutil()
    with runtime.construction_lease(root, "probe") as descriptor:
        started = time.monotonic()
        with pytest.raises(selected.ChildTimeoutExpired):
            selected.run_owned(
                [sys.executable, "-I", "-c",
                 "import pathlib,sys;pathlib.Path(sys.argv[1]).write_text('started')", str(marker)],
                cwd=slot, env=dict(os.environ), timeout=10, deadline=started + 0.5,
                procutil=procutil, construction_fd=descriptor,
                runtime_root=root, runtime_version="probe", runtime_path=Path(runtime.__file__),
            )
        assert guardian_marker.is_file() and not marker.exists()
        assert time.monotonic() - started < 8
        with runtime._cleanup_lease(root, "probe", allow_construction_owner=True) as admitted:
            assert admitted
