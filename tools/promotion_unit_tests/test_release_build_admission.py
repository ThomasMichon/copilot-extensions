"""Source-bound release helper decisions without build or native actors."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import os
from pathlib import Path
import stat
import subprocess
import sys
from types import ModuleType
import zipfile

import pytest

import native_daemon_artifacts as native
import plugin_runtime_resources as resources
from artifact_source_build import PromotionArtifactError, validate_static_build


ROOT = Path(__file__).resolve().parents[2]
PROCUTIL = ROOT / "libs/agent-procutil"
BUILDER = PROCUTIL / "src/agent_procutil/_build_native.py"
PREFIX = "agent_procutil-1.0.dist-info/"
RESOURCE_NAMES = {
    "versioned_runtime.py": "agent_containers/_versioned_runtime.py",
    "versioned_runtime_leases.py": "agent_containers/versioned_runtime_leases.py",
    "versioned_runtime_construction.py": "agent_containers/versioned_runtime_construction.py",
    "versioned_runtime_publication.py": "agent_containers/versioned_runtime_publication.py",
    "versioned_runtime_source.py": "agent_containers/versioned_runtime_source.py",
    "runtime_profile.py": "agent_containers/_runtime_profile.py",
}
STATIC_BUILD = (
    '[build-system]\nrequires = ["setuptools>=68"]\n'
    'build-backend = "setuptools.build_meta"\n'
)

_spec = importlib.util.spec_from_file_location("release_unit_native_builder", BUILDER)
assert _spec and _spec.loader
builder = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(builder)


def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


@pytest.fixture
def container_project(tmp_path):
    source = tmp_path / "plugins/agent-containers"
    _write(
        source / "pyproject.toml",
        (STATIC_BUILD + '[project]\nname = "agent-containers"\nversion = "1.0"\n').encode(),
    )
    _write(source / "setup.py", (ROOT / "plugins/agent-containers/setup.py").read_bytes())
    _write(
        source / "scripts/standalone_build.py",
        (ROOT / "libs/versioned-runtime/standalone_build.py").read_bytes(),
    )
    for name in RESOURCE_NAMES:
        _write(source / "scripts" / name, f"# fixture resource: {name}\n".encode())
    return source


@pytest.mark.parametrize("included", [False, True], ids=["canonical", "matching-included"])
def test_containers_admits_exact_six_source_owned_resources(container_project, tmp_path, included):
    source = container_project
    expected = {
        destination: source / "scripts" / name
        for name, destination in RESOURCE_NAMES.items()
    }
    if included:
        for destination, path in expected.items():
            _write(source / "src" / destination, path.read_bytes())
        _write(
            source / "_standalone_runtime_build.py",
            (source / "scripts/standalone_build.py").read_bytes(),
        )
    data, admitted = resources.build_inputs(source, root=tmp_path)
    assert data["project"] == {"name": "agent-containers", "version": "1.0"}
    assert admitted == expected
    assert len(admitted) == 6
    assert all(path.is_relative_to(source / "scripts") for path in admitted.values())


@pytest.mark.parametrize("name", list(RESOURCE_NAMES), ids=list(RESOURCE_NAMES))
@pytest.mark.parametrize("fault", ["missing", "included-tamper"])
def test_containers_refuses_each_missing_or_tampered_resource(
    container_project, tmp_path, name, fault,
):
    source = container_project
    if fault == "missing":
        (source / "scripts" / name).unlink()
        message = "missing runtime resource: " + RESOURCE_NAMES[name]
    else:
        _write(source / "src" / RESOURCE_NAMES[name], b"# foreign included bytes\n")
        message = "included runtime resource differs from its source"
    with pytest.raises(PromotionArtifactError, match=message):
        resources.build_inputs(source, root=tmp_path)


@pytest.mark.parametrize("fault", [
    "helper-missing", "helper-tamper", "included-helper-tamper", "setup-tamper", "linked-source",
])
def test_containers_refuses_foreign_build_ownership(container_project, tmp_path, fault):
    source = container_project
    message = "runtime build helper differs"
    if fault == "helper-missing":
        (source / "scripts/standalone_build.py").unlink()
    elif fault == "helper-tamper":
        (source / "scripts/standalone_build.py").write_bytes(b"raise AssertionError\n")
    elif fault == "included-helper-tamper":
        _write(source / "_standalone_runtime_build.py", b"raise AssertionError\n")
        message = "included runtime build helper differs"
    elif fault == "setup-tamper":
        (source / "setup.py").write_bytes(b"raise AssertionError\n")
        message = "unsupported executable"
    else:
        actual = source.with_name("actual")
        source.rename(actual)
        source.symlink_to(actual, target_is_directory=True)
        message = "must not be linked"
    with pytest.raises(PromotionArtifactError, match=message):
        resources.build_inputs(source, root=tmp_path)


@pytest.mark.parametrize("root_kind", ["absent", "different"])
def test_noncanonical_containers_location_does_not_borrow_hook_authority(
    container_project, tmp_path, root_kind,
):
    root = None if root_kind == "absent" else tmp_path / "other"
    with pytest.raises(PromotionArtifactError, match="unsupported executable"):
        resources.build_inputs(container_project, root=root)


def test_ordinary_static_package_needs_no_runtime_resources(tmp_path):
    _write(
        tmp_path / "pyproject.toml",
        (STATIC_BUILD + '[project]\nname = "ordinary"\nversion = "1.0"\n').encode(),
    )
    data, admitted = resources.build_inputs(tmp_path, root=None)
    assert data["project"]["name"] == "ordinary"
    assert admitted == {}


@pytest.fixture
def native_project(tmp_path):
    source = tmp_path / "procutil"
    for relative in (Path("setup.py"), native.PACKAGE / native.BUILD, native.PACKAGE / native.SOURCE):
        _write(source / relative, (PROCUTIL / relative).read_bytes())
    _write(
        source / "pyproject.toml",
        (STATIC_BUILD + '[project]\nname = "agent-procutil"\nversion = "1.0"\n').encode(),
    )
    return source


@pytest.mark.parametrize("name", ["agent-procutil", "Agent_Procutil"])
def test_native_setup_admits_exact_registered_hook(native_project, name):
    assert native.trusted_setup(native_project, {"project": {"name": name}}) == (
        PROCUTIL / "setup.py"
    ).read_bytes()
    assert validate_static_build(native_project)["project"]["name"] == "agent-procutil"


@pytest.mark.parametrize("project", [None, {}, {"name": []}, {"name": "foreign"}],
                         ids=["non-table", "missing-name", "non-string-name", "foreign-name"])
def test_unregistered_project_has_no_native_hook_authority(tmp_path, project):
    assert native.trusted_setup(tmp_path, {"project": project}) is None


def test_legacy_procutil_without_native_inputs_has_no_build_hook(tmp_path):
    assert native.trusted_setup(tmp_path, {"project": {"name": "agent-procutil"}}) is None


@pytest.mark.parametrize("relative", [
    "setup.py", "src/agent_procutil/_build_native.py", "src/agent_procutil/daemon_instance_native.c",
], ids=["setup", "builder", "c-source"])
@pytest.mark.parametrize("fault", ["missing", "directory", "tamper"])
def test_native_setup_binds_hook_and_requires_ordinary_c_source(native_project, relative, fault):
    path = native_project / relative
    if fault in {"missing", "directory"}:
        path.unlink()
        if fault == "directory":
            path.mkdir()
    else:
        path.write_bytes(b"/* changed source, never executed */\n")
    if fault == "tamper" and relative.endswith(".c"):
        assert native.trusted_setup(native_project, {"project": {"name": "agent-procutil"}})
    else:
        with pytest.raises(PromotionArtifactError, match="incomplete|ordinary file|trusted construction owner"):
            native.trusted_setup(native_project, {"project": {"name": "agent-procutil"}})


@pytest.mark.parametrize("fault", ["escape", "linked-parent"])
def test_native_input_refuses_escape_and_redirected_ancestor(tmp_path, fault):
    root = tmp_path / "source"
    root.mkdir()
    target = tmp_path / "outside.c"
    target.write_bytes(b"/* fixture */\n")
    if fault == "escape":
        path = target
    else:
        actual = root / "actual"
        actual.mkdir()
        (actual / "input.c").write_bytes(target.read_bytes())
        (root / "linked").symlink_to(actual, target_is_directory=True)
        path = root / "linked/input.c"
    with pytest.raises(PromotionArtifactError, match="escapes|redirected"):
        native._ordinary(path, root)


def _elf(arch, source):
    data = bytearray(64)
    data[:6] = b"\x7fELF\x02\x01"
    data[18:20] = {"x86_64": 62, "aarch64": 183}[arch].to_bytes(2, "little")
    return bytes(data) + hashlib.sha256(source).digest()


def _archive(actual, *, arch="x86_64", tags=None, pure=False, mode=stat.S_IFREG | 0o755,
             extra=False):
    stream = io.BytesIO()
    member = f"agent_procutil/native/linux-{arch}/daemon-instance"
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(
            PREFIX + "WHEEL",
            f"Root-Is-Purelib: {'true' if pure else 'false'}\n"
            + "".join(f"Tag: py3-none-{tag}\n" for tag in (
                tags if tags is not None else [f"linux_{arch}"]
            )),
        )
        if actual is not None:
            info = zipfile.ZipInfo(member)
            info.external_attr = mode << 16
            archive.writestr(info, actual)
        if extra:
            archive.writestr("agent_procutil/native/unexpected", b"foreign")
    stream.seek(0)
    return zipfile.ZipFile(stream)


def _compiler(monkeypatch, arch):
    calls = []
    monkeypatch.setattr(native.sys, "platform", "linux")
    monkeypatch.setattr(native.platform, "machine", lambda: arch)

    def run(command, *, check, timeout, env):
        assert check is True and timeout == 60
        assert command[-2] == "-o"
        source, pending = Path(command[-3]), Path(command[-1])
        assert env["TMPDIR"] == str(pending.parent)
        assert source.name == native.SOURCE
        source_bytes = source.read_bytes()
        pending.write_bytes(_elf(arch, source_bytes))
        calls.append((command, source_bytes))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", run)
    return calls


@pytest.mark.parametrize("arch", ["x86_64", "aarch64"])
@pytest.mark.parametrize("fault", ["matching", "stale-source"])
def test_native_artifact_rebuilds_current_source_at_compiler_boundary(
    native_project, tmp_path, monkeypatch, arch, fault,
):
    source = native_project / native.PACKAGE / native.SOURCE
    original = source.read_bytes()
    actual = _elf(arch, original)
    if fault == "stale-source":
        source.write_bytes(original + b"\n/* changed candidate */\n")
    calls = _compiler(monkeypatch, arch)
    monkeypatch.chdir(tmp_path)
    with _archive(actual, arch=arch) as archive:
        kwargs = dict(
            source_dir=native_project, data=validate_static_build(native_project), prefix=PREFIX,
        )
        if fault == "matching":
            assert native.generated_members(archive, **kwargs) == {
                f"agent_procutil/native/linux-{arch}/daemon-instance": actual,
            }
        else:
            with pytest.raises(PromotionArtifactError, match="source-bound explicit build"):
                native.generated_members(archive, **kwargs)
    assert len(calls) == 1
    assert calls[0][1] == source.read_bytes()
    assert not list((tmp_path / ".test-runs").iterdir())


@pytest.mark.parametrize("fault,message", [
    ("missing", "missing or unexpected"),
    ("extra", "missing or unexpected"),
    ("non-executable", "ordinary executable"),
    ("directory", "ordinary executable"),
    ("short", "wrong ELF platform"),
    ("magic", "wrong ELF platform"),
    ("class", "wrong ELF platform"),
    ("endian", "wrong ELF platform"),
    ("machine", "wrong ELF platform"),
    ("pure", "platform wheel installation mode"),
    ("unsupported", "unsupported native daemon artifact target"),
    ("ambiguous", "ambiguous native daemon artifact target"),
])
def test_native_artifact_refuses_invalid_metadata_before_compilation(
    native_project, monkeypatch, fault, message,
):
    calls = _compiler(monkeypatch, "x86_64")
    actual = _elf("x86_64", b"fixture")
    options = {}
    if fault == "missing":
        actual = None
    elif fault == "extra":
        options["extra"] = True
    elif fault in {"non-executable", "directory"}:
        options["mode"] = stat.S_IFREG | 0o644 if fault == "non-executable" else stat.S_IFDIR | 0o755
    elif fault == "short":
        actual = actual[:63]
    elif fault in {"magic", "class", "endian", "machine"}:
        value = bytearray(actual)
        value[{"magic": 0, "class": 4, "endian": 5, "machine": 18}[fault]] = 0
        actual = bytes(value)
    elif fault == "pure":
        options["pure"] = True
    elif fault == "unsupported":
        options["tags"] = ["linux_riscv64"]
    elif fault == "ambiguous":
        options["tags"] = ["linux_x86_64", "any"]
    with _archive(actual, **options) as archive, pytest.raises(PromotionArtifactError, match=message):
        native.generated_members(
            archive, source_dir=native_project, data=validate_static_build(native_project),
            prefix=PREFIX,
        )
    assert calls == []


@pytest.mark.parametrize("registered", [False, True], ids=["foreign", "pure-procutil"])
def test_pure_wheels_have_no_generated_native_assets(native_project, monkeypatch, registered):
    calls = _compiler(monkeypatch, "x86_64")
    data = {"project": {"name": "agent-procutil" if registered else "ordinary"}}
    with _archive(None, tags=["any"], pure=True) as archive:
        assert native.generated_members(
            archive, source_dir=native_project, data=data, prefix=PREFIX,
        ) == {}
    assert calls == []


@pytest.mark.parametrize("platform,machine", [
    ("win32", "x86_64"), ("linux", "riscv64"),
], ids=["non-linux", "unsupported-machine"])
def test_native_builder_refuses_unsupported_host_before_output(
    tmp_path, monkeypatch, platform, machine,
):
    calls = _compiler(monkeypatch, machine)
    monkeypatch.setattr(builder.sys, "platform", platform)
    output = tmp_path / "absent/daemon-instance"
    with pytest.raises(RuntimeError, match="supported Linux"):
        builder.build_native(output)
    assert calls == []
    assert not output.parent.exists()


@pytest.mark.parametrize("compiler,cc,expected", [
    (None, None, ["cc"]),
    (None, "env-cc --sysroot='fixture root'", ["env-cc", "--sysroot=fixture root"]),
    ("explicit-cc -g", "ignored-cc", ["explicit-cc", "-g"]),
], ids=["default", "environment", "explicit"])
def test_native_builder_preserves_compiler_argv_and_atomic_output(
    tmp_path, monkeypatch, compiler, cc, expected,
):
    calls = _compiler(monkeypatch, "x86_64")
    if cc is None:
        monkeypatch.delenv("CC", raising=False)
    else:
        monkeypatch.setenv("CC", cc)
    output = tmp_path / "native/daemon-instance"
    _write(output, b"previous output")
    chmod = Path.chmod
    modes = []

    def record_mode(path, mode, **options):
        modes.append((path, mode))
        return chmod(path, mode, **options)

    monkeypatch.setattr(Path, "chmod", record_mode)
    assert builder.build_native(output, compiler=compiler) == output.absolute()
    assert len(calls) == 1
    command, source = calls[0]
    assert command[:-4] == expected + ["-O2", "-std=c11", "-Wall"]
    assert command[-4] == "-Wextra"
    assert Path(command[-3]) == BUILDER.with_name(native.SOURCE)
    assert source == BUILDER.with_name(native.SOURCE).read_bytes()
    assert output.read_bytes() == _elf("x86_64", source)
    assert modes == [(output.with_name(output.name + f".{os.getpid()}.pending"), 0o755)]
    if os.name != "nt":
        assert stat.S_IMODE(output.stat().st_mode) == 0o755
    assert not list(output.parent.glob("*.pending"))


@pytest.mark.parametrize("failure", ["exit", "timeout"])
def test_native_builder_propagates_compiler_failure_and_cleans_pending(
    tmp_path, monkeypatch, failure,
):
    _compiler(monkeypatch, "x86_64")
    output = tmp_path / "native/daemon-instance"
    _write(output, b"preserved output")
    error_type = subprocess.CalledProcessError if failure == "exit" else subprocess.TimeoutExpired
    error = error_type(1, ["fixture-cc"]) if failure == "exit" else error_type(["fixture-cc"], 60)

    def run(command, **kwargs):
        Path(command[-1]).write_bytes(b"partial compiler output")
        raise error

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(error_type) as raised:
        builder.build_native(output)
    assert raised.value is error
    assert output.read_bytes() == b"preserved output"
    assert not list(output.parent.glob("*.pending"))


@pytest.fixture
def runtime_builder(monkeypatch):
    events = []

    class BuildPyActor:
        def run(self):
            events.append(("build-py",))

        def get_outputs(self, include_bytecode=1):
            events.append(("get-outputs", include_bytecode))
            return list(self.existing_outputs)

    class SdistActor:
        def make_release_tree(self, base_dir, files):
            events.append(("sdist", base_dir, files))

    # Only setuptools' superclass actors are replaced; the resource decisions run unchanged.
    for name in ("setuptools", "setuptools.command"):
        package = ModuleType(name)
        package.__path__ = []
        monkeypatch.setitem(sys.modules, name, package)
    for name, attribute, actor in (
        ("setuptools.command.build_py", "build_py", BuildPyActor),
        ("setuptools.command.sdist", "sdist", SdistActor),
    ):
        module = ModuleType(name)
        setattr(module, attribute, actor)
        monkeypatch.setitem(sys.modules, name, module)
    path = ROOT / "libs/versioned-runtime/standalone_build.py"
    specification = importlib.util.spec_from_file_location("release_unit_runtime_builder", path)
    assert specification and specification.loader
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module, events


@pytest.mark.parametrize("kind", ["ordinary", "missing", "directory", "linked-file", "linked-parent"])
def test_runtime_resource_ordinary_file_decides_without_packaging_actor(
    tmp_path, runtime_builder, kind,
):
    module, events = runtime_builder
    path = tmp_path / "input.py"
    if kind != "missing":
        path.write_bytes(b"# fixture\n")
    if kind == "directory":
        path.unlink()
        path.mkdir()
    elif kind == "linked-file":
        target = tmp_path / "target.py"
        path.rename(target)
        path.symlink_to(target)
    elif kind == "linked-parent":
        actual = tmp_path / "actual"
        actual.mkdir()
        path.rename(actual / path.name)
        (tmp_path / "linked").symlink_to(actual, target_is_directory=True)
        path = tmp_path / "linked" / path.name
    if kind in {"directory", "linked-file", "linked-parent"}:
        with pytest.raises(RuntimeError, match="regular file|must not be linked"):
            module.ordinary_file(path)
    else:
        assert module.ordinary_file(path) is (kind == "ordinary")
    assert events == []


@pytest.mark.parametrize("case", [
    "matching", "missing-required", "empty-required", "missing-optional",
    "required-tamper", "optional-tamper",
])
def test_runtime_resource_selection_requires_all_canonical_bytes(
    tmp_path, runtime_builder, case,
):
    module, events = runtime_builder
    canonical, second, included = [tmp_path / name for name in ("canonical", "second", "included")]
    for path in (canonical, second, included):
        path.write_bytes(b"canonical resource\n")
    required = [canonical, second]
    if case == "missing-required":
        second.unlink()
    elif case == "empty-required":
        required = []
    elif case == "missing-optional":
        included.unlink()
    elif case == "required-tamper":
        second.write_bytes(b"foreign required resource\n")
    elif case == "optional-tamper":
        included.write_bytes(b"foreign included resource\n")
    if case in {"matching", "missing-optional"}:
        assert module.select_bytes(
            required, "fixture resource", optional_paths=[included],
        ) == b"canonical resource\n"
    else:
        with pytest.raises(RuntimeError, match="missing|differs from the canonical source"):
            module.select_bytes(required, "fixture resource", optional_paths=[included])
    assert events == []


@pytest.mark.parametrize("layout", ["canonical", "sdist"])
def test_runtime_commands_deliver_six_bound_resources_and_helper(
    tmp_path, runtime_builder, layout,
):
    module, events = runtime_builder
    project = tmp_path / "project"
    canonical = tmp_path / "canonical"
    helper_bytes = (ROOT / "libs/versioned-runtime/standalone_build.py").read_bytes()
    expected = {
        destination: f"# bound resource: {name}\n".encode()
        for name, destination in RESOURCE_NAMES.items()
    }
    for name, destination in RESOURCE_NAMES.items():
        _write(project / "src" / destination, expected[destination])
        if name != "runtime_profile.py":
            _write(canonical / name, expected[destination])
    _write(project / "_standalone_runtime_build.py", helper_bytes)
    _write(canonical / "standalone_build.py", helper_bytes)
    companion = project / "runtime_profile.py"
    companion.write_bytes(expected["agent_containers/_runtime_profile.py"])
    commands = module.commands(
        project, "agent_containers", canonical if layout == "canonical" else None,
        companion_resources={
            "_runtime_profile.py": companion if layout == "canonical" else None,
        },
    )
    build = commands["build_py"]()
    build.build_lib = str(tmp_path / "build")
    duplicate = str(Path(build.build_lib) / "agent_containers/_runtime_profile.py")
    build.existing_outputs = ["already-built.py", duplicate]
    build.run()
    for destination, content in expected.items():
        assert (Path(build.build_lib) / destination).read_bytes() == content
    outputs = build.get_outputs(include_bytecode=0)
    assert set(outputs) == {
        "already-built.py", *(str(Path(build.build_lib) / name) for name in expected),
    }
    assert len(outputs) == 7
    release = tmp_path / "release"
    commands["sdist"]().make_release_tree(str(release), ["pyproject.toml"])
    assert (release / "_standalone_runtime_build.py").read_bytes() == helper_bytes
    for destination, content in expected.items():
        assert (release / "src" / destination).read_bytes() == content
    assert events == [
        ("build-py",), ("get-outputs", 0), ("sdist", str(release), ["pyproject.toml"]),
    ]


@pytest.mark.parametrize("fault", [
    "missing-resource", "included-resource-tamper", "included-helper-tamper", "companion-tamper",
])
def test_runtime_commands_refuse_before_setuptools_actor(tmp_path, runtime_builder, fault):
    module, events = runtime_builder
    project, canonical = tmp_path / "project", tmp_path / "canonical"
    helper_bytes = (ROOT / "libs/versioned-runtime/standalone_build.py").read_bytes()
    for name, destination in RESOURCE_NAMES.items():
        _write(project / "src" / destination, b"# canonical resource\n")
        if name != "runtime_profile.py":
            _write(canonical / name, b"# canonical resource\n")
    _write(canonical / "standalone_build.py", helper_bytes)
    companion = project / "runtime_profile.py"
    companion.write_bytes(b"# canonical resource\n")
    if fault == "missing-resource":
        (canonical / "versioned_runtime_source.py").unlink()
    elif fault == "included-resource-tamper":
        (project / "src/agent_containers/_versioned_runtime.py").write_bytes(b"foreign\n")
    elif fault == "included-helper-tamper":
        (project / "_standalone_runtime_build.py").write_bytes(b"foreign\n")
    else:
        companion.write_bytes(b"foreign\n")
    commands = module.commands(
        project, "agent_containers", canonical,
        companion_resources={"_runtime_profile.py": companion},
    )
    build = commands["build_py"]()
    build.build_lib = str(tmp_path / "never-built")
    with pytest.raises(RuntimeError, match="missing|differs from the canonical source"):
        build.run()
    assert events == []
    assert not Path(build.build_lib).exists()
