"""Actual source-built closures and the existing offline immutable constructor."""
from __future__ import annotations

from dataclasses import replace
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
import time
import venv
import zipfile

from packaging.markers import default_environment
import pytest

import runtime_source_wheels as completion
from build_toolchain_lock import ToolchainLock
from runtime_profile import ProfileError
from installer_wheel_inputs import WheelError
from runtime_source_lock import SourceLockCandidate
from test_installer_dependency_policy import source_project
from test_runtime_source_lock import (
    _selected_identity, candidate as source_lock, plan,
    adopter as adopter,
)
from wheel_input_fixture import wheel_bytes

BACKEND = """
import base64, csv, hashlib, io, pathlib, tomllib, zipfile
def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    project = tomllib.loads(pathlib.Path("pyproject.toml").read_text())["project"]
    name, version = project["name"].replace("-", "_"), project["version"].replace("-", ".")
    filename = name + "-" + version + "-py3-none-any.whl"
    prefix = name + "-" + version + ".dist-info/"
    metadata = "Metadata-Version: 2.3\\nName: " + project["name"] + "\\nVersion: " + version + "\\n"
    if "requires-python" in project:
        metadata += "Requires-Python: " + project["requires-python"] + "\\n"
    for requirement in project.get("dependencies", []):
        metadata += "Requires-Dist: " + requirement + "\\n"
    for extra, requirements in project.get("optional-dependencies", {}).items():
        metadata += "Provides-Extra: " + extra + "\\n"
        for requirement in requirements:
            metadata += "Requires-Dist: " + requirement + "; extra == '" + extra + "'\\n"
    files = {
        prefix + "METADATA": (metadata + "\\n").encode(),
        prefix + "WHEEL": b"Wheel-Version: 1.0\\nGenerator: setuptools (0)\\nRoot-Is-Purelib: true\\nTag: py3-none-any\\n",
    }
    source = pathlib.Path("src") / name
    if source.is_dir():
        files.update({path.relative_to("src").as_posix(): path.read_bytes()
                      for path in source.rglob("*") if path.is_file() and "__pycache__" not in path.parts})
    else:
        files[name + "/__init__.py"] = pathlib.Path("module.py").read_bytes()
    rows = [[path, "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode(), str(len(data))]
            for path, data in files.items()]
    rows.append([prefix + "RECORD", "", ""])
    record = io.StringIO(newline="")
    csv.writer(record, lineterminator="\\n").writerows(rows)
    files[prefix + "RECORD"] = record.getvalue().encode()
    with zipfile.ZipFile(pathlib.Path(wheel_directory) / filename, "w") as wheel:
        for path, data in files.items():
            wheel.writestr(zipfile.ZipInfo(path, (2000, 1, 1, 0, 0, 0)), data)
    return filename
"""


@pytest.fixture
def source(tmp_path):
    payload = source_project(
        tmp_path / "payload", dependencies='"fixture-helper[metrics]==1.0", "agent-procutil"',
    )
    (payload / "fixture_backend.py").write_text(BACKEND, encoding="utf-8")
    (payload / "module.py").write_text("from fixture_helper import VALUE\n", encoding="utf-8")
    helper = source_project(payload / "libs" / "helper", name="fixture-helper")
    (helper / "fixture_backend.py").write_text(BACKEND, encoding="utf-8")
    (helper / "module.py").write_text("VALUE = 42\n", encoding="utf-8")
    with (helper / "pyproject.toml").open("a") as stream:
        stream.write('\n[project.optional-dependencies]\nmetrics=[]\n')
    procutil = payload / "libs" / "agent-procutil"
    shutil.copytree(
        Path(__file__).resolve().parents[1] / "libs" / "agent-procutil", procutil,
        ignore=shutil.ignore_patterns("tests", "__pycache__", ".pytest_cache", "*.egg-info"),
    )
    manifest = procutil / "pyproject.toml"
    manifest.write_text(
        manifest.read_text().replace(
            'requires = ["setuptools>=83.0.0"]\nbuild-backend = "setuptools.build_meta"',
            'requires = []\nbuild-backend = "fixture_backend"\nbackend-path = ["."]',
        ), encoding="utf-8",
    )
    (procutil / "fixture_backend.py").write_text(BACKEND, encoding="utf-8")
    with (payload / "pyproject.toml").open("a") as stream:
        stream.write(
            '\n[project.optional-dependencies]\nbootstrap=["agent-procutil"]\n'
            '[tool.uv.sources]\nfixture-helper={path="libs/helper"}\n'
            'agent-procutil={path="libs/agent-procutil"}\n',
        )
    manifest = payload / "pyproject.toml"
    manifest.write_text(manifest.read_text().replace('version="1.0"', 'version="1.0.0"'), encoding="utf-8")
    (payload / "plugin.json").write_text(json.dumps({"name": "fixture-source", "version": "1.0.0"}))
    return _selected_identity(payload)


@pytest.fixture
def target():
    return {"environment": default_environment(), "tags": ["py3-none-any"], "device": "cpu"}


@pytest.fixture
def toolchain():
    # The fixture backend is stdlib-only; no tool installation or feed access.
    return ToolchainLock(
        Path(sys.executable), {"setuptools": "0", "wheel": "0", "packaging": "0"},
        provenance={"source_policy": "governed", "python": sys.executable,
                    "validated_index_identity": "fixture-stdlib-no-download"},
    )


@pytest.fixture
def built_wheels(monkeypatch):
    def build(project, output, **_options):
        data = completion._toml(project / "pyproject.toml")["project"]
        name, version = data["name"].replace("-", "_"), data["version"].replace("-", ".")
        metadata = f"Metadata-Version: 2.3\nName: {data['name']}\nVersion: {version}\n"
        if "requires-python" in data:
            metadata += f"Requires-Python: {data['requires-python']}\n"
        metadata += "".join(f"Requires-Dist: {item}\n" for item in data.get("dependencies", []))
        for extra, dependencies in data.get("optional-dependencies", {}).items():
            metadata += f"Provides-Extra: {extra}\n"
            metadata += "".join(f"Requires-Dist: {item}; extra == '{extra}'\n" for item in dependencies)
        output.mkdir(parents=True, exist_ok=True)
        wheel = output / f"{name}-{version}-py3-none-any.whl"
        wheel.write_bytes(wheel_bytes(
            name=name, prefix=f"{name}-{version}.dist-info/", metadata=metadata.encode(),
            wheel=b"Wheel-Version: 1.0\nGenerator: setuptools (0)\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        ))
        return wheel

    monkeypatch.setattr(completion, "build_wheel", build)
    return build


def arguments(toolchain, **changes):
    return dict(
        uv=shutil.which("uv"), toolchain=toolchain,
        limits=completion.WheelLimits(1 << 20, 1 << 22, 1 << 20, 1 << 18, 1000, 100, 1 << 16),
        max_wheels=4, max_total_bytes=1 << 21, max_snapshot_bytes=1 << 21,
        deadline=time.monotonic() + 60,
    ) | changes


def selected_source(source, target, adopter):
    roots = completion._package_roots(source[0])
    record = {
        "schema": "copilot-extensions.source-lock-candidate", "version": 1,
        "mode": "source", "source": source[1].document(), "trigger": "release-index-unavailable",
        "policy": plan(adopter).policy(), "target": target, "component": "fixture",
        "python": str(Path(sys.executable).resolve()),
        "extras": [], "pins": [], "requirements_sha256": "sha256:" + hashlib.sha256(b"").hexdigest(),
        "retained_source_sha256": "sha256:" + completion.runtime.fingerprint_source(
            completion._source_files(roots),
        ),
    }
    return SourceLockCandidate(
        json.dumps(record, sort_keys=True, separators=(",", ":")).encode(), b"", source[0],
    )


@pytest.mark.parametrize("uv_selection", ["absolute", "path"])
def test_native_source_lock_completes_helpers_extras_before_existing_offline_constructor(
    adopter, source, target, toolchain, tmp_path, monkeypatch, uv_selection,
):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] /
                                    "plugins" / "agent-worktrees" / "scripts"))
    deadline = time.monotonic() + 180
    with source_lock(adopter, source, target, tmp_path, deadline=deadline) as candidate:
        root = Path(candidate.record()["policy"]["owner"]["runtimeRoot"])
        version = candidate.record()["source"]["runtime_version"]
        construct = completion.numbered_candidate
        builder = completion.build_wheel
        build_executables = []

        def observed_build(*args, **options):
            build_executables.append(options["uv"])
            return builder(*args, **options)

        monkeypatch.setattr(completion, "build_wheel", observed_build)

        @contextmanager
        def observed(*args, **kwargs):
            binding = kwargs["wheel_inputs"].receipt()["binding"]
            record = binding["profile"]
            assert record["mode"] == "source" and record["artifact"] is None
            assert record["release"]["sha256"] == "sha256:" + source[1].assembly_hash
            wheels = {wheel["name"]: wheel for wheel in record["wheels"]}
            assert set(wheels) == {"fixture-source", "fixture-helper", "agent-procutil"}
            assert wheels["fixture-helper"]["selected_extras"] == ["metrics"]
            assert kwargs["local_packages"] == ("agent-procutil", "fixture-helper", "fixture-source")
            assert binding["source_lock"] == candidate.record()
            assert binding["build_toolchain"] == toolchain.record
            assert kwargs["uv"].is_absolute()
            assert set(build_executables) == {str(kwargs["uv"])}
            assert not root.exists()
            with construct(*args, **kwargs) as built:
                yield built

        monkeypatch.setattr(completion, "numbered_candidate", observed)
        with completion.source_candidate(
            candidate, third_party={}, health_module="fixture_source",
            **arguments(toolchain, deadline=deadline,
                        uv="uv" if uv_selection == "path" else shutil.which("uv")),
        ) as built:
            assert not built.reused
            assert completion.runtime.is_complete(root, version, expect_hash=built.payload_hash)
            assert completion.runtime.current_version(root) is None
            python = built.slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            proof = subprocess.run(
                [str(python), "-I", "-c", "from fixture_source import VALUE; print(VALUE)"],
                capture_output=True, text=True, timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            assert proof.returncode == 0, proof.stderr
            assert proof.stdout.strip() == "42"
            receipt = json.loads((built.slot / ".runtime-input-origins.json").read_text())
            assert receipt["wheel_inputs"]["binding"]["source_lock"]["trigger"] == "release-index-unavailable"


@pytest.mark.parametrize("change", ["requirements", "source", "missing-pin", "wrong-hash", "extra-wheel"])
def test_incomplete_or_changed_source_lock_refuses_before_build(
    adopter, source, target, toolchain, tmp_path, monkeypatch, change,
):
    candidate = selected_source(source, target, adopter)
    paths = {}
    if change == "requirements":
        candidate = replace(candidate, requirements=b"changed")
    elif change == "source":
        (source[0] / "module.py").write_text("changed")
    else:
        path = tmp_path / "fixture_registry-1.0-py3-none-any.whl"
        path.write_bytes(wheel_bytes(
            name="fixture_registry", prefix="fixture_registry-1.0.dist-info/",
            metadata=b"Metadata-Version: 2.3\nName: fixture-registry\nVersion: 1.0\n\n",
        ))
        record = candidate.record()
        if change != "extra-wheel":
            record["pins"] = [{"name": "fixture-registry", "version": "1.0", "hashes": ["sha256:" + "1" * 64]}]
            candidate = replace(candidate, canonical=json.dumps(record).encode())
        if change != "missing-pin":
            paths[path.name] = path
    monkeypatch.setattr(completion, "build_wheel", lambda *args, **kwargs: pytest.fail("must refuse before build"))
    with pytest.raises(ProfileError):
        with completion.retain_source_profile(candidate, third_party=paths, **arguments(toolchain)):
            pytest.fail("invalid source candidate reached a complete profile")


def test_actual_metadata_cannot_drop_selected_helper_extra(
    adopter, source, target, toolchain, tmp_path, monkeypatch, built_wheels,
):
    candidate = selected_source(source, target, adopter)
    original = completion._extras

    def drop(wheels, roots, environment, deadline):
        result = original(wheels, roots, environment, deadline)
        result["fixture-helper"] = set()
        return result

    monkeypatch.setattr(completion, "_extras", drop)
    with pytest.raises(ProfileError, match="extras"):
        with completion.retain_source_profile(candidate, third_party={}, **arguments(toolchain)):
            pytest.fail("dropped helper extra reached construction")


def test_built_metadata_cannot_drop_source_declared_dependency_extra(
    adopter, source, target, toolchain, monkeypatch, built_wheels,
):
    candidate = selected_source(source, target, adopter)

    def altered(project, output, **options):
        path = built_wheels(project, output, **options)
        if project == source[0]:
            with zipfile.ZipFile(path) as archive:
                prefix = "fixture_source-1.0.0.dist-info/"
                metadata = archive.read(prefix + "METADATA").replace(
                    b"fixture-helper[metrics]==1.0", b"fixture-helper==1.0",
                )
                headers = archive.read(prefix + "WHEEL")
            path.write_bytes(wheel_bytes(
                name="fixture_source", prefix=prefix, metadata=metadata, wheel=headers,
            ))
        return path

    monkeypatch.setattr(completion, "build_wheel", altered)
    with pytest.raises(ProfileError, match="runtime dependencies differ"):
        with completion.retain_source_profile(candidate, third_party={}, **arguments(toolchain)):
            pytest.fail("altered source dependency extras reached construction")


@pytest.mark.parametrize("origin", ["third-party", "built"])
def test_source_wheel_ingestion_bounds_filename_before_open_or_expansion(
    adopter, source, target, toolchain, monkeypatch, tmp_path, origin,
):
    candidate = selected_source(source, target, adopter)
    tags = ".".join("t" + str(index) for index in range(8))
    path = tmp_path / f"fixture_source-1.0.0-{tags}-{tags}-{tags}.whl"
    monkeypatch.setattr(completion, "_digest", lambda *_args: pytest.fail("input opened before tag limit"))
    monkeypatch.setattr(completion, "build_wheel", lambda *_args, **_options: path)
    with pytest.raises(WheelError, match="tag expansion"):
        with completion.retain_source_profile(
            candidate, third_party={path.name: path} if origin == "third-party" else {},
            **arguments(toolchain),
        ):
            pytest.fail("excessive filename tags reached construction")


def test_source_digest_rejects_actual_fifo_without_opening(tmp_path, monkeypatch):
    if not hasattr(os, "mkfifo"):
        pytest.skip("native FIFO creation is POSIX-only")
    path = tmp_path / "fifo-1.0-py3-none-any.whl"
    os.mkfifo(path)
    monkeypatch.setattr(Path, "open", lambda *_a, **_k: pytest.fail("FIFO opened"))
    with pytest.raises(WheelError, match="ordinary"):
        completion._digest(path, 100, time.monotonic() + 5)


def test_source_generator_is_read_only_after_bounded_metadata_inspection(
    adopter, source, target, toolchain, monkeypatch, built_wheels,
):
    candidate = selected_source(source, target, adopter)
    original_read = zipfile.ZipFile.read
    budget = arguments(toolchain)["limits"].max_metadata_member_bytes

    def altered(project, output, **options):
        path = built_wheels(project, output, **options)
        data = completion._toml(project / "pyproject.toml")["project"]
        name, version = data["name"].replace("-", "_"), data["version"].replace("-", ".")
        prefix = f"{name}-{version}.dist-info/"
        with zipfile.ZipFile(path) as archive:
            metadata = original_read(archive, prefix + "METADATA")
        path.write_bytes(wheel_bytes(
            name=name, prefix=prefix, metadata=metadata,
            wheel=b"Wheel-Version: 1.0\nGenerator: setuptools (0)\n\n" + b"x" * budget,
        ))
        return path

    def guarded_read(archive, name, *args, **options):
        if archive.getinfo(name).file_size > budget:
            pytest.fail("oversized metadata decompressed before bounded inspection")
        return original_read(archive, name, *args, **options)

    monkeypatch.setattr(completion, "build_wheel", altered)
    monkeypatch.setattr(zipfile.ZipFile, "read", guarded_read)
    with pytest.raises(WheelError, match="limit"):
        with completion.retain_source_profile(candidate, third_party={}, **arguments(toolchain)):
            pytest.fail("oversized generator metadata reached construction")


def test_build_deadline_settles_backend_descendants_before_staging_cleanup(
    tmp_path, toolchain, monkeypatch,
):
    import build_python_artifacts as builder
    from test_installer_wheel_acquisition import _running

    payload = source_project(tmp_path / "source")
    pid_file, late_write = tmp_path / "descendant.pid", tmp_path / "late-write"
    child = (
        "import os,pathlib,time;"
        f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()));"
        f"time.sleep(30);pathlib.Path({str(late_write)!r}).write_text('escaped')"
    )
    (payload / "fixture_backend.py").write_text(
        "import os,subprocess,sys,time\n"
        "def build_wheel(*args,**kwargs):\n"
        f"    subprocess.Popen([sys.executable,'-I','-c',{child!r}],"
        "creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)\n"
        "    time.sleep(60)\n",
        encoding="utf-8",
    )
    temporary = builder.tempfile.TemporaryDirectory
    staged = []

    @contextmanager
    def observed_temporary(*args, **options):
        with temporary(*args, **options) as path:
            staged.append(Path(path))
            try:
                yield path
            finally:
                if pid_file.is_file():
                    assert not _running(int(pid_file.read_text())), "custody released before descendant exit"

    monkeypatch.setattr(builder.tempfile, "TemporaryDirectory", observed_temporary)
    with pytest.raises(subprocess.TimeoutExpired):
        builder.build_wheel(
            payload, tmp_path / "built", toolchain=toolchain,
            uv=shutil.which("uv"), deadline=time.monotonic() + 15,
        )
    assert pid_file.is_file(), "the real backend must spawn its delayed writer"
    assert not _running(int(pid_file.read_text()))
    assert not late_write.exists()
    assert staged and all(not path.exists() for path in staged)


def test_source_mutation_during_build_cannot_relabel_wheels(
    adopter, source, target, toolchain, monkeypatch, built_wheels,
):
    candidate = selected_source(source, target, adopter)
    original = completion.build_wheel

    def mutate(*args, **kwargs):
        result = original(*args, **kwargs)
        (source[0] / "module.py").write_text("changed")
        return result

    monkeypatch.setattr(completion, "build_wheel", mutate)
    with pytest.raises(ProfileError, match="source (changed|differs)"):
        with completion.retain_source_profile(candidate, third_party={}, **arguments(toolchain)):
            pytest.fail("changed source reached a complete profile")


@pytest.mark.parametrize("record_algorithm", ["sha256", "sha384", "sha512"])
def test_complete_source_profile_includes_exact_pinned_third_party_and_transitive_extra(
    adopter, source, target, toolchain, tmp_path, built_wheels, record_algorithm,
):
    from promotion_unit_tests.test_runtime_profile_wheels import _encoded
    manifest = source[0] / "libs" / "helper" / "pyproject.toml"
    manifest.write_text(manifest.read_text().replace('metrics=[]', 'metrics=["fixture-registry>=1"]'))
    source = _selected_identity(source[0])
    path = tmp_path / "fixture_registry-1.0-py3-none-any.whl"
    path.write_bytes(_encoded({
        "name": "fixture-registry", "version": "1.0", "filename": path.name,
        "requires_python": "", "provides_extras": [], "requires_dist": [],
    }, record_algorithm=record_algorithm))
    digest = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    requirements = f"fixture-registry==1.0 --hash={digest}\n".encode()
    candidate = selected_source(source, target, adopter)
    record = candidate.record()
    record["pins"] = [{"name": "fixture-registry", "version": "1.0", "hashes": [digest]}]
    record["requirements_sha256"] = "sha256:" + hashlib.sha256(requirements).hexdigest()
    candidate = replace(candidate, canonical=json.dumps(record).encode(), requirements=requirements)
    with completion.retain_source_profile(
        candidate, third_party={path.name: path}, **arguments(toolchain),
    ) as selected:
        wheels = {item["name"]: item for item in selected.profile.record()["wheels"]}
        assert wheels["fixture-registry"]["sha256"] == digest
        assert wheels["fixture-helper"]["selected_extras"] == ["metrics"]
        assert set(wheels) == {"fixture-source", "fixture-helper", "agent-procutil", "fixture-registry"}


@pytest.mark.parametrize("record_algorithm", ["sha384", "sha512"])
def test_built_first_party_wheels_keep_sha256_record_policy(
    adopter, source, target, toolchain, monkeypatch, built_wheels, record_algorithm,
):
    from promotion_unit_tests.test_runtime_profile_wheels import _encoded

    candidate = selected_source(source, target, adopter)

    def altered(project, output, **options):
        path = built_wheels(project, output, **options)
        with zipfile.ZipFile(path) as archive:
            prefix = path.name.split("-py3-")[0] + ".dist-info/"
            metadata = archive.read(prefix + "METADATA").decode()
            headers = archive.read(prefix + "WHEEL").decode()
        path.write_bytes(_encoded(
            {"name": project.name, "filename": path.name}, metadata=metadata,
            wheel_header=headers, record_algorithm=record_algorithm,
        ))
        return path

    monkeypatch.setattr(completion, "build_wheel", altered)
    with pytest.raises(WheelError, match="unsupported wheel RECORD digest"):
        with completion.retain_source_profile(candidate, third_party={}, **arguments(toolchain)):
            pytest.fail("first-party RECORD policy was relaxed")


def test_installed_target_mismatch_refuses_before_completion_marker(
    adopter, source, target, toolchain, monkeypatch,
):
    candidate = selected_source(source, target, adopter)
    root = Path(candidate.record()["policy"]["owner"]["runtimeRoot"])
    version = candidate.record()["source"]["runtime_version"]
    probes = []

    def environment(python, **_options):
        probes.append(python)
        return dict(target["environment"]) if len(probes) == 1 else {}

    monkeypatch.setattr(completion, "_query_marker_environment", environment)
    with pytest.raises(ProfileError, match="installed interpreter"):
        with completion.source_candidate(
            candidate, third_party={}, health_module="fixture_source",
            **arguments(toolchain, deadline=time.monotonic() + 180),
        ):
            pytest.fail("incompatible installed target reached a ready candidate")
    assert len(probes) == 2
    assert not completion.runtime.marker_path(root, version).exists()
    assert not completion.runtime.is_complete(root, version)
    assert completion.runtime.current_version(root) is None


def test_real_marker_probe_cannot_run_installed_startup_hooks(tmp_path):
    root = tmp_path / "venv"
    venv.EnvBuilder(with_pip=False).create(root)
    python = root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    packages = Path(sysconfig.get_path("purelib", vars={"base": str(root), "platbase": str(root)}))
    packages.mkdir(parents=True, exist_ok=True)
    marker = tmp_path / "startup-executed"
    (packages / "probe-startup.pth").write_text(
        f"import pathlib; pathlib.Path({str(marker)!r}).write_text('executed')\n",
        encoding="utf-8",
    )
    control = subprocess.run(
        [str(python), "-I", "-c", "pass"], capture_output=True, text=True, timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert control.returncode == 0, control.stderr
    assert marker.is_file(), "the control must prove site initialization executes this hook"
    marker.unlink()
    assert completion._query_marker_environment(python, deadline=time.monotonic() + 15) == default_environment()
    assert not marker.exists()


def test_clean_source_profile_import_never_loads_native_governance():
    script = """
import sys
sys.path[:0] = [sys.argv[1], sys.argv[2]]
if "ctypes" in sys.modules:
    raise RuntimeError("clean caller unexpectedly preloaded ctypes")
def audit(event, args):
    if event == "ctypes.dlopen":
        raise RuntimeError("profile import attempted a native loader")
sys.addaudithook(audit)
import runtime_source_wheels
if "ctypes" in sys.modules:
    raise RuntimeError("profile import loaded native governance")
print("source-profile-clean-import-ok")
"""
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", script, str(Path(__file__).resolve().parent),
         sysconfig.get_paths()["purelib"]],
        capture_output=True, text=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "source-profile-clean-import-ok"
