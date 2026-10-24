"""Closed assembly consumes retained bytes, never ambient resolver inputs."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libs" / "installer-engine"))
import installer_dependency_policy as policy
from installer_wheel_snapshot import retain_wheel_inputs
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
def plugin(tmp_path):
    root = tmp_path / "sources"
    procutil = root / "procutil"
    shutil.copytree(Path(__file__).resolve().parents[1] / "libs" / "agent-procutil", procutil)
    plugin = root / "plugin"
    plugin.mkdir()
    (plugin / "pyproject.toml").write_text(
        '[project]\nname="demo-pkg"\nversion="1.0"\ndependencies=[]\n'
        '[project.optional-dependencies]\nbootstrap=["agent-procutil"]\n'
        '[tool.uv.sources]\nagent-procutil={path="../procutil"}\n',
        encoding="utf-8",
    )
    return plugin


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


def snapshot(tmp_path, *, closure=False, version="1.0"):
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
    return retain_wheel_inputs(
        paths, {name: "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
                for name, path in paths.items()},
        max_wheels=2, max_archive_bytes=100_000, max_total_bytes=100_000,
        max_snapshot_bytes=100_000, deadline=time.monotonic() + 60,
    )


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
            assert wheel.as_uri() in text and "--hash=sha256:" in text
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


def test_native_existing_constructor_builds_and_reuses_exact_closed_wheels(
    tmp_path, engine, plugin, monkeypatch,
):
    monkeypatch.setenv("UV_INDEX_URL", "http://127.0.0.1:1/unavailable")
    monkeypatch.setenv("UV_CONFIG_FILE", str(tmp_path / "missing-config"))
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "poison-cache"))
    with snapshot(tmp_path, closure=True, version="1") as retained:
        wheels = inspected(retained)
        assert wheels.receipt()["distributions"]["demo-pkg"]["version"] == "1"
        slots = []
        for name in ("first-runtime", "second-runtime"):
            root = tmp_path / name
            with candidate(engine, root, plugin, wheels) as built:
                assert not built.reused
                assert engine.runtime.is_complete(root, "1.0.0", expect_hash=built.payload_hash)
                assert engine.runtime.current_version(root) is None
                slots.append(built.slot)
                receipt = json.loads((built.slot / ".runtime-input-origins.json").read_text())
                assert receipt["version"] == 2
                assert receipt["wheel_inputs"] == wheels.receipt()
        assert slots[0] != slots[1]
        assert not (tmp_path / "poison-cache").exists()
    # Re-inspect cached bytes under a new temporary path; reuse binds content,
    # not the ephemeral location used by a previous installation.
    with snapshot(tmp_path, closure=True, version="1") as retained:
        wheels = inspected(retained)

        def no_reconstruction(*args, **kwargs):
            pytest.fail("complete healthy wheel runtime must not be rebuilt")

        monkeypatch.setattr(engine, "_construct", no_reconstruction)
        with candidate(engine, tmp_path / "first-runtime", plugin, wheels) as reused:
            assert reused.reused
            assert reused.slot == slots[0]


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


@pytest.mark.parametrize("failure", ["health", "cancel"])
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
        with pytest.raises(KeyboardInterrupt if failure == "cancel" else subprocess.CalledProcessError):
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
