"""Standalone release identities reuse the locked builder and source verifier."""
from __future__ import annotations

import io
import json
import hashlib
from pathlib import Path
import subprocess
import shutil
import sys
import zipfile
import os
import runpy
import venv
import uuid

from types import ModuleType, SimpleNamespace

import pytest

import build_python_artifacts as builder
import promotion_python_artifacts as artifacts
import publish_python_artifacts as publisher
import standalone_python_artifacts as standalone
from standalone_consumers import STANDALONE_CONSUMERS
from test_promotion_python_artifacts import _wheel_bytes

ROOT = Path(__file__).resolve().parent.parent
LOCK = builder.ToolchainLock(Path("."), {
    "setuptools": "80.9.0", "wheel": "0.45.1", "packaging": "25.0",
}, provenance={
    "source_policy": "governed", "python": "/python",
    "validated_index_identity": "sha256:" + "f" * 64,
})

def test_resource_inspection_imports_without_a_build_backend(tmp_path):
    script = """
import runpy, sys
helper = runpy.run_path(sys.argv[1])
assert callable(helper["embedded_members"])
assert callable(helper["embedded_projects"])
assert not any(name == "setuptools" or name.startswith("setuptools.") for name in sys.modules)
"""
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", script,
         str(ROOT / "libs/versioned-runtime/standalone_build.py")],
        cwd=tmp_path, capture_output=True, text=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr


WHEEL_STAGING_NATIVE_PROBE = r"""
import errno, os, runpy, sys
from pathlib import Path
from unittest.mock import patch
from setuptools import Distribution
helper, root = map(Path, sys.argv[1:])
commands = runpy.run_path(str(helper))["commands"](root, "fixture")
distribution = Distribution({"name": "fixture", "version": "1", "cmdclass": commands})
wheel = distribution.get_command_obj("bdist_wheel")
wheel._runtime_staging_root = root / "wheel"
command = distribution.get_command_obj("install_lib")
source = root / "build"
source.mkdir(parents=True)
original = source / "module.py"
original.write_bytes(b"VALUE = 7\n")
command.build_dir = str(source)
command.force = True
for name in ("linked", "copy-fallback", "ordinary"):
    destination = root / ("ordinary" if name == "ordinary" else "wheel/" + name)
    if name == "ordinary":
        wheel._runtime_staging_root = None
    with patch("os.link", side_effect=OSError(errno.EXDEV, "link unavailable")) if name == "copy-fallback" else patch("os.link", wraps=os.link):
        outputs = command.copy_tree(str(source), str(destination))
    selected = destination / original.name
    assert str(selected) in outputs
    assert selected.read_bytes() == original.read_bytes() == b"VALUE = 7\n"
    assert os.path.samefile(original, selected) == (name == "linked")
print("native-wheel-staging-link-copy-fallback-ordinary-ok")
"""


@pytest.mark.timeout(30)
def test_native_wheel_staging_preserves_bytes_and_copy_fallback(tmp_path):
    pytest.importorskip("setuptools.command.bdist_wheel")
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", WHEEL_STAGING_NATIVE_PROBE,
         str(ROOT / "libs/versioned-runtime/standalone_build.py"), str(tmp_path)],
        capture_output=True, text=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "native-wheel-staging-link-copy-fallback-ordinary-ok" in result.stdout


@pytest.fixture
def resource_build_commands(tmp_path, monkeypatch):
    class Build:
        def run(self):
            pass

    class Wheel:
        def run(self):
            return self.action()

    class Install:
        def copy_tree(self, *args):
            return ["ordinary-copy"]

        def get_exclusions(self):
            return self.exclusions

    for name in ("setuptools", "setuptools.command"):
        module = ModuleType(name)
        module.__path__ = []
        monkeypatch.setitem(sys.modules, name, module)
    for name, base in (
        ("bdist_wheel", Wheel), ("build_py", Build), ("install_lib", Install), ("sdist", object),
    ):
        module = ModuleType("setuptools.command." + name)
        setattr(module, name, base)
        monkeypatch.setitem(sys.modules, module.__name__, module)

    class Distribution:
        def get_command_class(self, name):
            module = sys.modules.get("setuptools.command." + name)
            if module is None:
                raise ImportError(name)
            return getattr(module, name)

    sys.modules["setuptools"].Distribution = Distribution
    helper = runpy.run_path(str(ROOT / "libs/versioned-runtime/standalone_build.py"))
    commands = helper["commands"](tmp_path, "fixture")
    wheel = commands["bdist_wheel"]()
    wheel.resource_helper = helper
    wheel.bdist_dir = str(tmp_path / "wheel")
    install = commands["install_lib"]()
    install.distribution = SimpleNamespace(get_command_obj=lambda *args, **kwargs: wheel)
    install.build_dir = str(tmp_path / "build")
    install.dry_run = False
    install.exclusions = set()
    return wheel, install


def test_resource_builder_keeps_older_backend_commands(tmp_path, monkeypatch, resource_build_commands):
    monkeypatch.delitem(sys.modules, "setuptools.command.bdist_wheel")
    helper = runpy.run_path(str(ROOT / "libs/versioned-runtime/standalone_build.py"))
    expected = {"build_py", "sdist"}
    if os.name == "nt":
        expected.add("install_lib")
    assert set(helper["commands"](tmp_path, "fixture")) == expected


@pytest.mark.parametrize("replace_parent", [False, True])
def test_resource_collection_batches_parent_checks_and_refuses_replacement(
    tmp_path, monkeypatch, resource_build_commands, replace_parent,
):
    wheel, _install = resource_build_commands
    helper = wheel.resource_helper
    for destination in helper["RUNTIME_RESOURCES"].values():
        path = tmp_path / "src/fixture" / destination
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"primitive")
    (tmp_path / helper["HELPER_NAME"]).write_bytes(b"helper")
    shared = tmp_path / "shared"
    shared.mkdir()
    resources = {}
    for index in range(3):
        source = shared / f"asset{index}.py"
        source.write_bytes(b"asset")
        resources[f"_runtime_assets/asset{index}.py"] = source
    commands = helper["commands"](tmp_path, "fixture", companion_resources=resources)
    command = commands["build_py"]()
    command.build_lib = str(tmp_path / "output")
    counts = []
    actual_stat, actual_read = Path.lstat, Path.read_bytes

    def observe_stat(path, *args, **kwargs):
        if path == shared:
            counts.append(path)
        return actual_stat(path, *args, **kwargs)

    def read(path):
        data = actual_read(path)
        if replace_parent and path == shared / "asset2.py":
            shared.rename(tmp_path / "replaced")
            shared.mkdir()
        return data

    monkeypatch.setattr(Path, "lstat", observe_stat)
    monkeypatch.setattr(Path, "read_bytes", read)
    if replace_parent:
        with pytest.raises(RuntimeError, match="ancestor changed during collection"):
            command.run()
        assert not (tmp_path / "output").exists()
    else:
        command.run()
        assert (tmp_path / "output/fixture/_runtime_assets/asset2.py").read_bytes() == b"asset"
    assert len(counts) == 2


@pytest.mark.parametrize("fail", [False, True])
def test_wheel_staging_links_only_generated_output_and_clears_scope(
    tmp_path, monkeypatch, resource_build_commands, fail,
):
    wheel, install = resource_build_commands
    copied = []

    def copy_file(source, destination, **options):
        copied.append((source, destination, options["link"]))
        return destination, True

    def copy_tree(source, destination, *, copy_function, dirs_exist_ok):
        assert dirs_exist_ok
        copy_function(str(Path(source) / "module.py"), str(Path(destination) / "module.py"))
        if fail:
            raise OSError("staging refused")

    install.copy_file = copy_file
    monkeypatch.setattr(shutil, "copytree", copy_tree)
    wheel.action = lambda: install.copy_tree(install.build_dir, wheel.bdist_dir)
    if fail:
        with pytest.raises(OSError, match="staging refused"):
            wheel.run()
    else:
        assert wheel.run() is None
    assert copied == [(
        str(tmp_path / "build/module.py"), str(tmp_path / "wheel/module.py"), "hard",
    )]
    assert wheel._runtime_staging_root is None


@pytest.mark.parametrize("ordinary", ["not-wheel", "namespace", "foreign-output", "traversal", "dry-run"])
def test_wheel_staging_keeps_other_install_paths_on_standard_copy(
    tmp_path, monkeypatch, resource_build_commands, ordinary,
):
    wheel, install = resource_build_commands
    if ordinary == "namespace":
        install.exclusions = {"namespace/__init__.py"}
    if ordinary == "dry-run":
        install.dry_run = True
    destination = str(tmp_path / "outside") if ordinary == "foreign-output" else wheel.bdist_dir
    if ordinary == "traversal":
        destination = str(tmp_path / "wheel/../outside")
    monkeypatch.setattr(shutil, "copytree", lambda *args, **kwargs: pytest.fail("unexpected link staging"))
    results = []
    wheel.action = lambda: results.append(install.copy_tree(install.build_dir, destination))
    if ordinary == "not-wheel":
        wheel.action()
    else:
        wheel.run()
    assert results == [["ordinary-copy"]]


@pytest.mark.parametrize("component", [
    "chat-service", "dispatch-service", "ssh-service", "worktree-manager",
])
def test_existing_core_standalone_roots_have_explicit_artifact_identities(component):
    assert component in standalone.COMPONENTS
    assert standalone.COMPONENTS[component] in STANDALONE_CONSUMERS


def test_manager_embedded_metadata_covers_the_canonical_third_party_projection(tmp_path):
    import re
    import tomllib
    import bundle_lock
    from packaging.requirements import Requirement
    from packaging.specifiers import SpecifierSet
    from packaging.utils import canonicalize_name

    helper = runpy.run_path(str(ROOT / "libs/versioned-runtime/standalone_build.py"))
    manager = ROOT / "worktree-manager"
    selected = helper["embedded_projects"](manager)
    assert {"agent-copilot-driver-core", "agent-repository-policy"} <= selected.keys()
    assert "agent_remote_driver/mux_observation.py" in helper["embedded_members"](manager)
    for source in [manager, *(directory for directory, _data in selected.values())]:
        target = tmp_path / source.relative_to(ROOT) / "pyproject.toml"
        target.parent.mkdir(parents=True, exist_ok=True)
        text = (source / "pyproject.toml").read_text(encoding="utf-8")
        # This fixture compares declarations, not the existing local-wheel/feed policy.
        if source == manager:
            text = re.sub(r"(?m)^dbus-next = .*\n", "", text)
        target.write_text(text, encoding="utf-8")
    data = tomllib.loads((manager / "pyproject.toml").read_text(encoding="utf-8"))
    projected, ranges = bundle_lock.collect_inputs(
        tmp_path, root_names=(data["project"]["name"],),
    )

    def key(text):
        requirement = Requirement(text)
        return (
            canonicalize_name(requirement.name), frozenset(requirement.extras),
            requirement.specifier, str(requirement.marker) if requirement.marker else None,
        )

    assert {key(text) for text in projected["core"]} == {
        key(text) for text in data["project"]["dependencies"]
    }
    assert "pyyaml>=6.0.3" in data["project"]["dependencies"]
    for version in ("3.10", "3.11", "3.12", "3.13", "3.14", "4"):
        assert SpecifierSet(ranges["core"]).contains(version) == SpecifierSet(
            data["project"]["requires-python"],
        ).contains(version)
    with pytest.raises((bundle_lock.InputError, OSError), match="dbus"):
        bundle_lock.collect_inputs(ROOT, root_names=(data["project"]["name"],))


def test_standalone_embedded_helpers_preserve_closed_release_source_admission():
    from promotion_release_helpers import helper_closure

    sources, _decisions = helper_closure(ROOT, entrypoints=("standalone_python_artifacts",))
    assert "libs/versioned-runtime/standalone_build.py" in sources


def _embedded_source(tmp_path):
    source = _source(tmp_path, "worktree-manager")
    library = tmp_path / "libs/agent-procutil"
    (library / "src/agent_procutil").mkdir(parents=True)
    (library / "src/agent_procutil/__init__.py").write_bytes(b"VALUE = 1\n")
    (library / "tests").mkdir()
    (library / "tests/test_retained.py").write_bytes(b"# retained source identity member\n")
    (library / "README.md").write_bytes(b"retained documentation\n")
    (library / "LICENSE").write_bytes(b"MIT\n")
    (library / "pyproject.toml").write_text(
        '[project]\nname = "agent-procutil"\nversion = "1.0"\n'
        '[tool.setuptools.packages.find]\nwhere = ["src"]\n', encoding="utf-8",
    )
    with (source / "pyproject.toml").open("a", encoding="utf-8") as stream:
        stream.write(
            '\n[dependency-groups]\nfirst-party-core = ["agent-procutil"]\n'
            '[tool.uv.sources]\nagent-procutil = {path = "../libs/agent-procutil", editable = true}\n'
        )
    return source, library


def test_embedded_sources_remain_complete_build_resources_not_installed_distributions(tmp_path, monkeypatch):
    source, library = _embedded_source(tmp_path)
    monkeypatch.setattr(builder, "resolve_vendored_libs", lambda _source: [("agent-procutil", library)])
    selected, installed, resources = standalone.source_closure("worktree-manager", tmp_path, builder)
    assert selected == source and installed == []
    assert library in resources
    assert (library / "tests/test_retained.py").read_bytes() == b"# retained source identity member\n"
    assert (library / "README.md").read_bytes() == b"retained documentation\n"
    monkeypatch.setattr(builder, "resolve_vendored_libs", lambda _source: [])
    with pytest.raises(builder.ArtifactBuildError, match="differs from source closure"):
        standalone.source_closure("worktree-manager", tmp_path, builder)


@pytest.mark.parametrize("linked", [None, "file", "directory"])
def test_embedded_sdist_preserves_full_inputs_and_refuses_links(
    tmp_path, monkeypatch, native_file_symlink, linked,
):
    from setuptools import Distribution

    source, library = _embedded_source(tmp_path)
    if linked == "file":
        target = tmp_path / "foreign.md"
        target.write_bytes(b"synthetic foreign content\n")
        native_file_symlink(library / "tests/link.md", target)
    elif linked == "directory":
        target = tmp_path / "foreign"
        target.mkdir()
        native_file_symlink(library / "docs", target, target_is_directory=True)
    helpers = runpy.run_path(str(ROOT / "libs/versioned-runtime/standalone_build.py"))
    classes = helpers["commands"](
        source, "worktree_manager", tmp_path / "libs/versioned-runtime",
    )
    distribution = Distribution({
        "name": "manager-fixture", "version": "1.0", "packages": ["worktree_manager"],
        "package_dir": {"": "src"}, "cmdclass": classes,
    })
    distribution.script_name = "setup.py"
    command = classes["sdist"](distribution)
    command.ensure_finalized()
    monkeypatch.chdir(source)
    destination = tmp_path / "sdist"
    files = ["pyproject.toml", "setup.py", "src/worktree_manager/__init__.py"]
    if linked:
        with pytest.raises(RuntimeError, match="must not be linked"):
            command.make_release_tree(str(destination), files)
    else:
        command.make_release_tree(str(destination), files)
        copied = destination / "_first_party/agent-procutil"
        assert (copied / "tests/test_retained.py").read_bytes() == b"# retained source identity member\n"
        assert (copied / "README.md").read_bytes() == b"retained documentation\n"
        assert (copied / "LICENSE").read_bytes() == b"MIT\n"
        assert (destination / helpers["HELPER_NAME"]).read_bytes() == (
            ROOT / "libs/versioned-runtime/standalone_build.py"
        ).read_bytes()


def test_embedded_sdist_rebuild_prefers_archived_inputs_over_matching_neighbor(tmp_path, monkeypatch):
    import tarfile
    import setuptools.build_meta as backend

    source, library = _embedded_source(tmp_path / "original")
    (source / "scripts").mkdir()
    (source / "scripts/installer_fixture.py").write_bytes(b"# controlled runtime asset\n")
    expected = (library / "src/agent_procutil/__init__.py").read_bytes()
    archives = tmp_path / "archives"
    archives.mkdir()
    monkeypatch.chdir(source)
    filename = backend.build_sdist(str(archives))
    unpacked = tmp_path / "unpacked"
    with tarfile.open(archives / filename) as archive:
        archive.extractall(unpacked, filter="data")
    rebuilt_source = next(unpacked.iterdir())
    neighbor = unpacked / "libs/agent-procutil"
    shutil.copytree(library, neighbor)
    (neighbor / "src/agent_procutil/__init__.py").write_bytes(b"FOREIGN = True\n")
    monkeypatch.chdir(rebuilt_source)
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    filename = backend.build_wheel(str(wheels))
    with zipfile.ZipFile(wheels / filename) as archive:
        assert archive.read("agent_procutil/__init__.py") == expected


def test_embedded_editable_build_reinitializes_registered_packages(tmp_path, monkeypatch):
    import setuptools.build_meta as backend

    source, _library = _embedded_source(tmp_path)
    (source / "scripts").mkdir()
    (source / "scripts/installer_fixture.py").write_bytes(b"# controlled runtime asset\n")
    monkeypatch.chdir(source)
    output = tmp_path / "editable"
    output.mkdir()
    filename = backend.build_editable(str(output))
    assert (output / filename).is_file()


def test_embedded_registration_still_refuses_application_package_collision(tmp_path):
    from setuptools import Distribution

    source, _library = _embedded_source(tmp_path)
    helpers = runpy.run_path(str(ROOT / "libs/versioned-runtime/standalone_build.py"))
    classes = helpers["commands"](source, "worktree_manager", tmp_path / "libs/versioned-runtime")
    distribution = Distribution({
        "name": "fixture", "version": "1.0",
        "packages": ["worktree_manager", "agent_procutil"], "package_dir": {"": "src"},
    })
    with pytest.raises(RuntimeError, match="collides with application"):
        classes["build_py"](distribution).ensure_finalized()


@pytest.mark.parametrize("fail", [False, True])
def test_embedded_package_copy_is_bounded_and_settled_before_return(tmp_path, monkeypatch, fail):
    import threading
    import time
    from setuptools import Distribution

    source, _library = _embedded_source(tmp_path)
    helpers = runpy.run_path(str(ROOT / "libs/versioned-runtime/standalone_build.py"))
    classes = helpers["commands"](source, "worktree_manager", tmp_path / "libs/versioned-runtime")
    distribution = Distribution({
        "name": "manager-fixture", "version": "1.0", "packages": ["worktree_manager"],
        "package_dir": {"": "src"}, "cmdclass": classes,
    })
    command = classes["build_py"](distribution)
    command.ensure_finalized()
    command.build_lib = str(tmp_path / "output")
    monkeypatch.setattr(command, "find_package_modules", lambda package, _path: [
        (package, str(index), "synthetic.py") for index in range(4)
    ])
    lock, entered = threading.Lock(), threading.Event()
    active, completed, peak = [], [], [0]

    def copy(module, _path, package):
        with lock:
            active.append((package, module))
            peak[0] = max(peak[0], len(active))
            if len(active) >= 2:
                entered.set()
        try:
            assert entered.wait(5)
            time.sleep(0.01)
            if fail and module == "0":
                raise RuntimeError("controlled package copy failure")
        finally:
            with lock:
                active.remove((package, module))
                completed.append((package, module))

    monkeypatch.setattr(command, "build_module", copy)
    if fail:
        with pytest.raises(RuntimeError, match="controlled package copy failure"):
            command.build_packages()
    else:
        command.build_packages()
    assert 1 < peak[0] <= 4
    assert not active
    assert 1 <= len(completed) <= 8 if fail else len(completed) == 8


@pytest.mark.parametrize("fail", [False, True])
def test_embedded_install_copy_preserves_standard_files_and_settles_failure(tmp_path, monkeypatch, fail):
    import threading
    import time
    from setuptools import Distribution

    source, _library = _embedded_source(tmp_path)
    helpers = runpy.run_path(str(ROOT / "libs/versioned-runtime/standalone_build.py"))
    classes = helpers["commands"](source, "worktree_manager", tmp_path / "libs/versioned-runtime")
    distribution = Distribution({"name": "manager-fixture", "version": "1.0", "cmdclass": classes})
    command = classes["install_lib"](distribution)
    command.ensure_finalized()
    command.build_dir = str(tmp_path / "build-output")
    command.install_dir = str(tmp_path / "wheel-output")
    incoming = Path(command.build_dir) / "pkg"
    incoming.mkdir(parents=True)
    for name in ("module.py", "data.json", ".nfs-temporary"):
        (incoming / name).write_bytes(name.encode())
        os.utime(incoming / name, (1_000_000_000, 1_000_000_000))
    original = command.copy_file
    lock, active = threading.Lock(), []

    def copy(source, destination):
        with lock:
            active.append(source)
        try:
            time.sleep(0.01)
            if fail and source.endswith("module.py"):
                raise RuntimeError("controlled install copy failure")
            return original(source, destination)
        finally:
            with lock:
                active.remove(source)

    monkeypatch.setattr(command, "copy_file", copy)
    if fail:
        with pytest.raises(RuntimeError, match="controlled install copy failure"):
            command.install()
    else:
        outputs = command.install()
        assert {Path(name).name for name in outputs} == {"module.py", "data.json"}
        for name in ("module.py", "data.json"):
            copied = Path(command.install_dir) / "pkg" / name
            assert copied.read_bytes() == (incoming / name).read_bytes()
            assert copied.stat().st_mtime == (incoming / name).stat().st_mtime
        assert not (Path(command.install_dir) / "pkg/.nfs-temporary").exists()
    assert not active


@pytest.mark.parametrize("mode", ["legacy", "namespace", "dry-run"])
def test_nonembedded_and_special_install_paths_keep_the_stock_owner(tmp_path, monkeypatch, mode):
    from setuptools import Distribution
    from setuptools.command.install_lib import install_lib

    if mode == "legacy":
        source = _source(tmp_path, "fleet-manager")
        package = "fleet_manager"
    else:
        source, _library = _embedded_source(tmp_path)
        package = "worktree_manager"
    helpers = runpy.run_path(str(ROOT / "libs/versioned-runtime/standalone_build.py"))
    classes = helpers["commands"](source, package, tmp_path / "libs/versioned-runtime")
    if mode == "legacy":
        assert "egg_info" not in classes
        distribution = Distribution({"cmdclass": classes})
        selected = distribution.get_command_class("install_lib")
        if os.name == "nt":
            assert issubclass(selected, install_lib) and selected.install is install_lib.install
        else:
            assert selected is install_lib
        return
    command = classes["install_lib"](Distribution({"name": "fixture", "version": "1.0"}))
    command.ensure_finalized()
    command.build_dir = str(tmp_path / "build-output")
    Path(command.build_dir).mkdir()
    marker = ["stock-owner"]
    monkeypatch.setattr(install_lib, "install", lambda _self: marker)
    if mode == "namespace":
        monkeypatch.setattr(command, "get_exclusions", lambda: {"excluded"})
    elif mode == "dry-run":
        command.dry_run = True
    assert command.install() is marker


@pytest.mark.skipif(os.name != "nt", reason="Windows extended path syntax")
def test_native_build_paths_normalize_local_and_unc_roots():
    helper = runpy.run_path(str(ROOT / "libs/versioned-runtime/standalone_build.py"))
    native = helper["_native_build_path"]
    assert native("C:\\project", "build\\lib\\.\\pkg") == "\\\\?\\C:\\project\\build\\lib\\pkg"
    assert native("C:\\project", "\\\\?\\C:\\other\\build\\.\\pkg") == "\\\\?\\C:\\other\\build\\pkg"
    assert native("C:\\project", "\\\\server\\share\\build\\.\\pkg") == (
        "\\\\?\\UNC\\server\\share\\build\\pkg"
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows wheel command selection")
def test_native_metadata_loads_when_wheel_command_is_unavailable(tmp_path, monkeypatch):
    from setuptools import Distribution
    from distutils.errors import DistutilsModuleError

    original = Distribution.get_command_class

    def command_class(self, name):
        if name == "bdist_wheel":
            raise DistutilsModuleError("wheel command unavailable")
        return original(self, name)

    monkeypatch.setattr(Distribution, "get_command_class", command_class)
    helper = runpy.run_path(str(ROOT / "libs/versioned-runtime/standalone_build.py"))
    classes = helper["commands"](tmp_path, "fleet_manager", tmp_path)
    assert "bdist_wheel" not in classes
    assert "build_py" in classes and "sdist" in classes


def _source(root, component):
    source = root / standalone.COMPONENTS[component]
    source.mkdir(parents=True)
    package = {
        "fleet-manager": "fleet_manager", "pr-service": "agent_pull_requests_service",
        "chat-service": "agent_chat_service", "dispatch-service": "agent_dispatch_service",
        "ssh-service": "agent_ssh_service", "worktree-manager": "worktree_manager",
        "index-service": "agent_index_service", "index-engine": "agent_index_engine",
    }[component]
    (source / "src" / package).mkdir(parents=True)
    (source / "src" / package / "__init__.py").write_bytes(b"VALUE = 'first-party fixture'\n")
    (source / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["setuptools>=68"]\nbuild-backend = "setuptools.build_meta"\n'
        f'[project]\nname = "{package}"\nversion = "1.0"\n'
        '[tool.setuptools.packages.find]\nwhere = ["src"]\n', encoding="utf-8",
    )
    resources = standalone.standalone_build_resources(
        standalone.COMPONENTS[component], source_root=root,
    )
    for resource in resources:
        resource.mkdir(parents=True, exist_ok=True)
    helper = root / "libs" / "versioned-runtime" / "standalone_build.py"
    helper.parent.mkdir(parents=True, exist_ok=True)
    helper.write_bytes((ROOT / "libs" / "versioned-runtime" / "standalone_build.py").read_bytes())
    if component in {"fleet-manager", "chat-service", "index-service", "worktree-manager", "pr-service"}:
        (source / "setup.py").write_bytes(
            (ROOT / standalone.COMPONENTS[component] / "setup.py").read_bytes()
        )
        for path in standalone.injected_resources(component, root).values():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"# controlled canonical runtime resource\n")
    if component in {"index-service", "pr-service"}:
        (source / "_build_runtime_resource.py").write_bytes(
            (ROOT / standalone.COMPONENTS[component] / "_build_runtime_resource.py").read_bytes()
        )
    return source


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
@pytest.mark.parametrize("modified", [False, True])
def test_trusted_manager_setup_accepts_git_line_endings_but_not_changed_body(
    tmp_path, newline, modified,
):
    from build_toolchain_lock import ArtifactBuildError

    source = _source(tmp_path, "worktree-manager")
    setup = source / "setup.py"
    content = setup.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", newline)
    setup.write_bytes(content + (b"\nprint('untrusted build action')\n" if modified else b""))
    if modified:
        with pytest.raises(ArtifactBuildError, match="unsupported executable"):
            standalone.validate_build(source, component="worktree-manager", root=tmp_path)
    else:
        assert standalone.validate_build(
            source, component="worktree-manager", root=tmp_path,
        )["project"]["version"] == "1.0"


def _build(tmp_path, monkeypatch, component, *, embedded=False, materialized=False):
    if embedded:
        source, library = _embedded_source(tmp_path)
        if materialized:
            destination = source / "libs" / library.name
            destination.parent.mkdir()
            shutil.move(str(library), destination)
            manifest = source / "pyproject.toml"
            manifest.write_text(
                manifest.read_text().replace("../libs/agent-procutil", "libs/agent-procutil")
                .replace("editable = true", "editable = false"),
                encoding="utf-8",
            )
    else:
        source = _source(tmp_path, component)
    monkeypatch.setattr(builder, "REPO", tmp_path)
    monkeypatch.setattr(builder, "LIBS_DIR", tmp_path / "libs")

    def build(source_dir, out_dir, *, toolchain, reserved_names, normalize_timestamps):
        assert toolchain is LOCK
        assert normalize_timestamps
        package = next((source_dir / "src").iterdir()).name
        members = {
            name: path.read_bytes()
            for name, path in standalone.injected_resources(
                next(name for name, relative in standalone.COMPONENTS.items()
                     if source_dir == tmp_path / relative), tmp_path,
            ).items()
        }
        out_dir.mkdir(parents=True, exist_ok=True)
        wheel = out_dir / f"{package}-1.0-py3-none-any.whl"
        wheel.write_bytes(_wheel_bytes(package, "1.0", members=members))
        assert wheel.name not in reserved_names
        reserved_names.add(wheel.name)
        return wheel

    monkeypatch.setattr(builder, "build_wheel", build)
    directory = tmp_path / "wheels"
    manifest = standalone.build_standalone_artifacts(
        component, out_dir=directory, toolchain=LOCK, builder=builder,
    )
    return source, directory, manifest


@pytest.mark.parametrize("component", list(standalone.COMPONENTS))
def test_standalone_build_bundle_candidate_roundtrip(tmp_path, monkeypatch, component):
    _source_dir, wheels, manifest = _build(tmp_path, monkeypatch, component)
    assert manifest["schema_version"] == 4 and "plugin" not in manifest
    assert manifest["component"] == component
    assert manifest["wheels"][0]["role"] == "standalone"
    directory = tmp_path / "bundles"
    directory.mkdir()
    entry = artifacts.bundle_wheels(manifest, wheels, directory)
    assert entry["filename"].startswith(f"standalone-{component}-1.0-")
    assert artifacts.validate_bundle(
        directory / entry["filename"], entry, candidate_root=tmp_path,
    ) == manifest
    index = {
        "schema": artifacts.SCHEMA, "schema_version": 2,
        "dev_head": "a" * 40, "content_tree": "b" * 40, "artifacts": [entry],
    }
    (directory / artifacts.INDEX_NAME).write_text(json.dumps(index), encoding="utf-8")
    assert artifacts.validate_artifacts(
        directory, dev_head="a" * 40, content_tree="b" * 40, main_before="c" * 40,
        candidate_root=tmp_path, projects=artifacts.candidate_projects(tmp_path),
    ) == index
    assert publisher.artifact_label(entry) == f"standalone-{component}"


@pytest.mark.parametrize("materialized", [False, True])
def test_embedded_manager_build_bundle_candidate_roundtrip(tmp_path, monkeypatch, materialized):
    _source_dir, wheels, manifest = _build(
        tmp_path, monkeypatch, "worktree-manager", embedded=True, materialized=materialized,
    )
    expected = "worktree-manager/libs/agent-procutil" if materialized else "libs/agent-procutil"
    assert manifest["build_resources"][-1] == expected
    assert len(manifest["wheels"]) == 1
    output = tmp_path / "bundles"
    output.mkdir()
    entry = artifacts.bundle_wheels(manifest, wheels, output)
    assert artifacts.validate_bundle(
        output / entry["filename"], entry, candidate_root=tmp_path,
    ) == manifest


@pytest.mark.parametrize("resource", [
    "libs/unknown", "libs/../foreign", "foreign/agent-procutil", "libs/versioned-runtime",
    "worktree-manager/libs/unknown", "services/ssh/libs/agent-procutil",
])
def test_embedded_manager_resource_closure_refuses_substitution(tmp_path, monkeypatch, resource):
    _source_dir, _wheels, manifest = _build(tmp_path, monkeypatch, "worktree-manager", embedded=True)
    manifest["build_resources"][-1] = resource
    with pytest.raises(artifacts.PromotionArtifactError, match="resource closure"):
        artifacts.validate_manifest(manifest)
        artifacts.validate_source_binding(manifest, tmp_path)


@pytest.mark.parametrize("mutation", ["unknown", "plugin-alias", "resource-omission", "duplicate-source"])
def test_standalone_identity_and_resource_refusals(tmp_path, monkeypatch, mutation):
    _source_dir, wheels, manifest = _build(tmp_path, monkeypatch, "fleet-manager")
    if mutation == "unknown":
        manifest["component"] = "../fleet-manager"
    elif mutation == "plugin-alias":
        manifest["plugin"] = "fleet-manager"
    elif mutation == "resource-omission":
        manifest["build_resources"].pop()
    else:
        manifest["wheels"].append(dict(manifest["wheels"][0]))
    with pytest.raises((artifacts.PromotionArtifactError, ValueError)):
        artifacts.validate_manifest(manifest)
        artifacts.validate_source_binding(manifest, tmp_path)


@pytest.mark.parametrize("mutation", ["source", "resource", "generator", "digest"])
def test_standalone_changed_bytes_and_toolchain_refused(tmp_path, monkeypatch, mutation):
    source, wheels, manifest = _build(tmp_path, monkeypatch, "fleet-manager")
    if mutation == "source":
        (source / "src" / "fleet_manager" / "__init__.py").write_bytes(b"changed")
    elif mutation == "resource":
        next(iter(standalone.injected_resources("fleet-manager", tmp_path).values())).write_bytes(b"changed")
    elif mutation == "generator":
        manifest["wheels"][0]["generator"] = "unlocked"
    else:
        manifest["wheels"][0]["sha256"] = "sha256:" + "0" * 64
    directory = tmp_path / "bundles"
    directory.mkdir()
    with pytest.raises(artifacts.PromotionArtifactError):
        entry = artifacts.bundle_wheels(manifest, wheels, directory)
        artifacts.validate_bundle(directory / entry["filename"], entry, candidate_root=tmp_path)


@pytest.mark.parametrize("component", [
    "fleet-manager", "chat-service", "index-service", "worktree-manager", "pr-service",
])
@pytest.mark.parametrize("mutation", ["setup", "helper", "alternate", "included"])
def test_only_trusted_standalone_build_hook_is_admitted(tmp_path, mutation, component):
    source = _source(tmp_path, component)
    target = {
        "setup": source / "setup.py",
        "helper": tmp_path / "libs" / "versioned-runtime" / "standalone_build.py",
        "alternate": source / "setup.cfg",
        "included": next((source / "src").iterdir()) / (
            "_attribution/comment_preparation.py" if component == "pr-service" else "_versioned_runtime.py"
        ),
    }[mutation]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"raise RuntimeError('candidate build code must not run')\n")
    with pytest.raises((builder.ArtifactBuildError, artifacts.PromotionArtifactError)):
        standalone.validate_build(source, component=component, root=tmp_path)


@pytest.mark.parametrize("component", ["index-service", "pr-service"])
def test_standalone_build_selector_substitution_is_refused(tmp_path, component):
    source = _source(tmp_path, component)
    (source / "_build_runtime_resource.py").write_bytes(b"raise RuntimeError('not canonical')\n")
    with pytest.raises(builder.ArtifactBuildError, match="selector"):
        standalone.validate_build(source, component=component, root=tmp_path)


def test_pr_release_materializes_only_the_canonical_attribution_helpers(tmp_path):
    from materialize_launch_wrapper_assets import materialize_all

    source = ROOT / standalone.COMPONENTS["pr-service"]
    project = tmp_path / standalone.COMPONENTS["pr-service"]
    project.mkdir(parents=True)
    shutil.copy2(source / "packaged-assets.json", project)
    log = materialize_all(tmp_path, canonical_root=ROOT)
    assert len(log) == 2 and all(line.startswith("OK ") for line in log)
    canonical = ROOT / "plugins/ai-attribution/scripts"
    outputs = standalone.injected_resources("pr-service", ROOT)
    assert set(outputs) == {
        f"agent_pull_requests_service/_attribution/{name}"
        for name in ("attribution_profile.py", "comment_preparation.py")
    }
    for member, origin in outputs.items():
        assert origin == canonical / Path(member).name
        assert (project / "src" / member).read_bytes() == origin.read_bytes()


def test_standalone_resource_substitution_refused_even_with_fresh_manifest(tmp_path, monkeypatch):
    _source_dir, wheels, manifest = _build(tmp_path, monkeypatch, "fleet-manager")
    wheel = manifest["wheels"][0]
    archive = wheels / wheel["filename"]
    replacement = _wheel_bytes("fleet_manager", "1.0")
    wheel["sha256"] = "sha256:" + hashlib.sha256(replacement).hexdigest()
    with pytest.raises(artifacts.PromotionArtifactError, match="Python member set"):
        artifacts.validate_wheel(
            io.BytesIO(replacement), wheel, source_dir=tmp_path / "fleet-manager",
            component="fleet-manager", candidate_root=tmp_path,
        )
    assert archive.is_file()


@pytest.mark.skipif(os.name != "nt", reason="Windows native wheel staging paths")
def test_worktrees_wheel_preserves_resources_in_deep_windows_build(tmp_path):
    root = tmp_path.parent / ("d" + uuid.uuid4().hex[:8])
    padding = max(1, 105 - len(str(root)) - 3)
    project = root / ("x" * padding) / "p"
    native_project = Path("\\\\?\\" + str(project))
    shutil.copytree(
        ROOT / "plugins" / "agent-worktrees", native_project,
        ignore=shutil.ignore_patterns(
            "__pycache__", "*.egg-info", ".venv", ".test-runs", "build", "dist", "tests", "docs",
        ),
    )
    relative = (
        "agent_worktrees/_runtime_assets/scripts/_runtime_admission/libs/"
        "agent-procutil/src/agent_procutil/windows_broker_installation.py"
    )
    assert len(str(project / "build" / "bdist.win-amd64" / "wheel" / relative)) >= 260
    expected = {
        name: path.read_bytes()
        for name, path in __import__("plugin_runtime_resources").application_resources(
            ROOT / "plugins" / "agent-worktrees", "agent_worktrees",
        ).items()
    }
    assert relative in expected
    archives = root / "wheels"
    archives.mkdir()
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c",
         "import setuptools.build_meta as backend; backend.build_wheel(__import__('sys').argv[1])",
         str(archives)],
        cwd=project, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, (result.stdout + result.stderr)[-6000:]
    with zipfile.ZipFile(next(archives.glob("*.whl"))) as archive:
        assert {name: archive.read(name) for name in expected} == expected


def test_mcp_native_owner_companion_survives_wheel_and_source_distribution(tmp_path):
    project = tmp_path / "project"
    shutil.copytree(
        ROOT / "plugins" / "agent-mcp", project,
        ignore=shutil.ignore_patterns(
            "__pycache__", "*.egg-info", ".venv", ".test-runs", "build", "dist", "tests", "docs",
        ),
    )
    expected = {
        "agent_mcp/" + name: (project / "src" / "agent_mcp" / name).read_bytes()
        for name in ("owner_section.cs", "owner_snapshot.py")
    }
    archives = tmp_path / "archives"
    archives.mkdir()
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    result = subprocess.run(
        [sys.executable, "-I", "-c",
         "import setuptools.build_meta as backend; out=__import__('sys').argv[1]; "
         "backend.build_wheel(out); backend.build_sdist(out)", str(archives)],
        cwd=project, capture_output=True, text=True, encoding="utf-8", timeout=90, **options,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    with zipfile.ZipFile(next(archives.glob("*.whl"))) as archive:
        assert {name: archive.read(name) for name in expected} == expected
    unpacked = tmp_path / "unpacked"
    shutil.unpack_archive(next(archives.glob("*.tar.gz")), unpacked)
    rebuilt = tmp_path / "rebuilt"
    rebuilt.mkdir()
    result = subprocess.run(
        [sys.executable, "-I", "-c",
         "import setuptools.build_meta as backend; backend.build_wheel(__import__('sys').argv[1])",
         str(rebuilt)],
        cwd=next(unpacked.iterdir()), capture_output=True, text=True,
        encoding="utf-8", timeout=90, **options,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    with zipfile.ZipFile(next(rebuilt.glob("*.whl"))) as archive:
        assert {name: archive.read(name) for name in expected} == expected


def test_manager_source_materializer_imports_from_isolated_wheel_resources(tmp_path):
    from materialize_launch_wrapper_assets import materialize_into
    from plugin_runtime_resources import application_resources

    project = tmp_path / "manager"
    project.mkdir()
    log = materialize_into(
        source_consumer_dir=ROOT / "worktree-manager", dest_consumer_dir=project,
        canonical_root=ROOT, dest_root=tmp_path,
    )
    assert log and not any(line.startswith("SKIP") for line in log), log
    resources = application_resources(project, "worktree_manager")
    helper = "worktree_manager/_runtime_assets/scripts/libs/versioned-runtime/standalone_build.py"
    assert resources[helper].read_bytes() == (
        ROOT / "libs/versioned-runtime/standalone_build.py"
    ).read_bytes()
    wheel_layout = tmp_path / "wheel-resources"
    for member, source in resources.items():
        destination = wheel_layout / member
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    candidate = tmp_path / "candidate"
    application = candidate / "applications/example"
    application.mkdir(parents=True)
    (candidate / "pyproject.toml").write_text(
        '[tool.copilot-extensions.python-projects]\nexample = "applications/example"\n',
    )
    (application / "pyproject.toml").write_text('[project]\nname = "example"\nversion = "1"\n')
    (candidate / "resources").mkdir()
    (candidate / "resources/helper.py").write_text("VALUE = 1\n")
    (application / "packaged-assets.json").write_text(json.dumps({
        "schema": "copilot-extensions.packaged-assets", "version": 2,
        "groups": [{"canonicalDir": "resources", "payloadDir": "helpers", "files": ["helper.py"]}],
    }))
    program = """
import pathlib, sys
tools = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(tools))
sys.path.insert(0, str(tools / "tools"))
from source_preparation_process import procutil_module
permissions = procutil_module("python_cache_permissions")
assert pathlib.Path(permissions.__file__).is_relative_to(tools / "libs" / "agent-procutil")
import materialize_main
root = pathlib.Path(sys.argv[2])
log = materialize_main.materialize(root, canonical_root=root)
assert not any(row.startswith("SKIP") for row in log), log
assert (root / "applications/example/helpers/helper.py").read_bytes() == (root / "resources/helper.py").read_bytes()
assert pathlib.Path(materialize_main.__file__).is_relative_to(tools)
print("isolated-wheel-resource-materializer-ok")
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", program,
         str(wheel_layout / "worktree_manager/_runtime_assets/scripts/runtime-source-tools"),
         str(candidate)],
        cwd=wheel_layout, capture_output=True, text=True, timeout=60,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "isolated-wheel-resource-materializer-ok"


@pytest.mark.parametrize("relative,package", [
    ("worktree-manager", "worktree_manager"),
    ("plugins/agent-worktrees", "agent_worktrees"),
])
def test_operational_assets_survive_wheel_and_source_distribution(tmp_path, relative, package):
    project = tmp_path / "project"
    shutil.copytree(
        ROOT / relative, project,
        ignore=shutil.ignore_patterns(
            "__pycache__", "*.egg-info", ".venv", ".test-runs", "build", "dist", "tests", "docs",
        ),
    )
    embedded = {}
    if package == "worktree_manager":
        helper = runpy.run_path(str(ROOT / "libs/versioned-runtime/standalone_build.py"))
        for name, (directory, _metadata) in helper["embedded_projects"](ROOT / relative).items():
            shutil.copytree(
                directory, project / "_first_party" / name,
                ignore=shutil.ignore_patterns(
                    "__pycache__", "*.egg-info", "build", "dist", ".venv", ".pytest_cache",
                    "tests", "docs",
                ),
            )
        embedded = helper["embedded_members"](project)
    expected = {
        name: path.read_bytes()
        for name, path in __import__("plugin_runtime_resources").application_resources(
            project, package,
        ).items()
    }
    assert expected
    if package == "agent_worktrees":
        for name in ("native-seed-boundary.ps1", "native-seed-process.ps1", "native-seed-process.cs"):
            assert f"{package}/_runtime_assets/scripts/{name}" in expected
    if package == "worktree_manager":
        canonical = ROOT / "libs" / "bootstrap-killswitch"
        for name in ("bootstrap-killswitch-guard.sh", "bootstrap-killswitch-guard.ps1", "killswitch_mutation.py"):
            assert expected[f"{package}/_runtime_assets/scripts/{name}"] == (canonical / name).read_bytes()
    expected.update({name: path.read_bytes() for name, path in embedded.items()})
    before = {path: path.read_bytes() for path in project.rglob("*.py")}
    archives = tmp_path / "archives"
    archives.mkdir()
    program = (
        "import setuptools.build_meta as backend; out=__import__('sys').argv[1]; "
        "backend.build_wheel(out); backend.build_sdist(out)"
    )
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    result = subprocess.run(
        [sys.executable, "-c", program, str(archives)],
        cwd=project, capture_output=True, text=True, encoding="utf-8", timeout=90,
        **options,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    wheel = next(archives.glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        assert {name: archive.read(name) for name in expected} == expected
        assert not any("/payload/" in name for name in archive.namelist())
        if embedded:
            from email.parser import BytesParser
            from artifact_source_build import validate_runtime_metadata
            import tomllib

            infos = {name.split("/")[0] for name in archive.namelist() if ".dist-info/" in name}
            assert len(infos) == 1
            info = next(iter(infos))
            metadata = BytesParser().parsebytes(archive.read(info + "/METADATA"))
            validate_runtime_metadata(
                tomllib.loads((ROOT / relative / "pyproject.toml").read_text())["project"], metadata,
            )
            assert not any(value.startswith("agent-") for value in metadata.get_all("Requires-Dist", []))
            assert "pyyaml>=6.0.3" in metadata.get_all("Requires-Dist", [])
            record = archive.read(info + "/RECORD").decode()
            assert all(name in record for name in embedded)
    assert all(path.read_bytes() == value for path, value in before.items())
    unpacked = tmp_path / "sdist"
    source_archives = [path for path in archives.iterdir() if path != wheel]
    assert len(source_archives) == 1
    shutil.unpack_archive(source_archives[0], unpacked)
    source = next(unpacked.iterdir())
    assert not (source / "scripts").exists()
    rebuilt = tmp_path / "rebuilt"
    rebuilt.mkdir()
    result = subprocess.run(
        [sys.executable, "-c",
         "import setuptools.build_meta as backend; backend.build_wheel(__import__('sys').argv[1])",
         str(rebuilt)],
        cwd=source, capture_output=True, text=True, encoding="utf-8", timeout=90,
        **options,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    with zipfile.ZipFile(next(rebuilt.glob("*.whl"))) as archive:
        assert {name: archive.read(name) for name in expected} == expected
    slot = tmp_path / "runtime" / "versions" / "1.0"
    environment = slot / ".venv"
    venv.EnvBuilder(with_pip=False).create(environment)
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    uv = shutil.which("uv")
    assert uv, "native uv is required for the installed-wheel consumer control"
    result = subprocess.run(
        [uv, "pip", "install", "--python", str(python), "--no-deps", "--no-index", str(wheel)],
        capture_output=True, text=True, encoding="utf-8", timeout=30, **options,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    shutil.rmtree(project)
    if package == "worktree_manager":
        probe = (
            "from pathlib import Path; from worktree_manager import self_install as owner; "
            "from types import SimpleNamespace; import sys; "
            "from worktree_manager import relocated_launch; "
            "relocated_launch._core=lambda: SimpleNamespace(_is_windows=lambda: sys.platform=='win32'); "
            "assert not (Path(__import__('sys').prefix).parent/'payload').exists(); "
            "assert '_runtime_assets' in str(owner._runtime_installer().__file__); "
            "assert '_runtime_assets' in str(owner._provider_manifest_template_path()); "
            "assert owner._expected_control_plane_provider_manifest()['provider']=='worktree-manager'; "
            "launcher=relocated_launch._relocated_launch_script(); assert launcher.is_file(); "
            "assert '_runtime_assets' in str(launcher)"
            "; from worktree_manager import engine_origin; "
            "from agent_procutil.owned_process import OwnedCleanupError; "
            "assert engine_origin.OwnedCleanupError is OwnedCleanupError; "
            "from update_driver import core; from single_instance_lease.operation import OperationLock; "
            "assert core.OperationLock is OperationLock; "
            "from importlib.metadata import distributions; "
            "installed={item.metadata['Name'] for item in distributions()}; "
            "assert installed == {'copilot-extensions-worktree-manager'}"
        )
    else:
        bootstrap = slot / ".runtime-bootstrap"
        bootstrap.mkdir()
        (bootstrap / "build-info.json").write_text(json.dumps({
            "version": "1.0", "commit": "wheel-consumer-control", "runtime_generation": "a" * 32,
        }), encoding="utf-8")
        probe = (
            "from pathlib import Path; import agent_worktrees; "
            "from agent_worktrees._build_info import BUILD_INFO; "
            "assert BUILD_INFO['commit']=='wheel-consumer-control'; "
            "assets=Path(agent_worktrees.__file__).parent/'_runtime_assets'/'scripts'; "
            "assert (assets/'runtime_install.py').is_file(); "
            "assert (assets/'installation-context'/'installation_context.py').is_file(); "
            "assert (assets/'installation-context'/'json-query.awk').is_file()"
        )
    result = subprocess.run(
        [str(python), "-I", "-B", "-c", probe], cwd=tmp_path,
        capture_output=True, text=True, encoding="utf-8", timeout=30, **options,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_standalone_signature_failure_remains_blocking(tmp_path, monkeypatch):
    _source_dir, wheels, manifest = _build(tmp_path, monkeypatch, "pr-service")
    directory = tmp_path / "bundles"
    directory.mkdir()
    entry = artifacts.bundle_wheels(manifest, wheels, directory)
    (directory / artifacts.INDEX_NAME).write_text(json.dumps({
        "schema": artifacts.SCHEMA, "schema_version": 2,
        "dev_head": "a" * 40, "content_tree": "b" * 40, "artifacts": [entry],
        "admission": {
            "signer_digest": "c" * 40, "source_digest": "c" * 40, "source_ref": "refs/heads/main",
        },
    }), encoding="utf-8")
    calls = []
    def gh(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1 if str(argv[2]).endswith(".zip") else 0, "", "bad signature")
    monkeypatch.setattr(publisher, "_gh", gh)
    with pytest.raises(artifacts.PromotionArtifactError, match="bad signature"):
        publisher.verify_attestations(
            directory, repository="example/extensions", signer_digest="c" * 40,
            source_digest="c" * 40, source_ref="refs/heads/main",
        )
    assert any(str(argv[2]).endswith(".zip") for argv in calls)


def test_candidate_builder_uses_one_lock_for_registered_standalones(tmp_path, monkeypatch):
    import build_toolchain_lock
    import promote_release

    _source_dir, _wheels, _manifest = _build(tmp_path, monkeypatch, "fleet-manager")
    _source(tmp_path, "pr-service")
    monkeypatch.setattr(builder, "PLUGINS_DIR", tmp_path / "plugins")
    monkeypatch.setattr(promote_release, "_load_module", lambda *a: builder)
    locks = []
    def resolve(*args, **kwargs):
        locks.append(kwargs)
        return LOCK
    monkeypatch.setattr(build_toolchain_lock, "resolve_toolchain_lock", resolve)
    directory = tmp_path / "candidate"
    artifacts.build_candidate_bundles(
        tmp_path, directory, source_policy="governed", min_age_days=None,
    )
    entries = json.loads((directory / artifacts._BUNDLES_NAME).read_text(encoding="utf-8"))
    assert len(locks) == 1
    assert {standalone.project_key(entry) for entry in entries} == {
        "standalone:fleet-manager", "standalone:pr-service",
    }
    for entry in entries:
        manifest = artifacts.validate_bundle(
            directory / entry["filename"], entry, candidate_root=tmp_path,
        )
        assert manifest["build_toolchain"] == LOCK.record


def test_candidate_omission_and_same_name_plugin_substitution_refuse(tmp_path, monkeypatch):
    _source_dir, wheels, manifest = _build(tmp_path, monkeypatch, "pr-service")
    directory = tmp_path / "bundles"
    directory.mkdir()
    entry = artifacts.bundle_wheels(manifest, wheels, directory)
    index = {
        "schema": artifacts.SCHEMA, "schema_version": 2,
        "dev_head": "a" * 40, "content_tree": "b" * 40, "artifacts": [entry],
    }
    (directory / artifacts.INDEX_NAME).write_text(json.dumps(index), encoding="utf-8")
    for projects in ({}, {"pr-service": "1.0"}):
        with pytest.raises(artifacts.PromotionArtifactError, match="project set"):
            artifacts.validate_artifacts(
                directory, dev_head="a" * 40, content_tree="b" * 40, main_before="c" * 40,
                candidate_root=tmp_path, projects=projects,
            )


def test_standalone_cli_selects_explicit_builder_without_changing_plugin_default(tmp_path, monkeypatch, capsys):
    import artifact_builder_cli

    calls = []
    def plugin(name, **kwargs):
        calls.append(("plugin", name))
        return {}
    def component(name, **kwargs):
        calls.append(("standalone", name))
        return {}
    for standalone_flag in ([], ["--standalone"]):
        monkeypatch.setattr(
            "sys.argv", ["build_python_artifacts.py", "pr-service", "--out-dir", str(tmp_path), *standalone_flag],
        )
        assert artifact_builder_cli.main(plugin, "", standalone_builder=component) == 0
    assert calls == [("plugin", "pr-service"), ("standalone", "pr-service")]
    assert capsys.readouterr().out.count("{}") == 2


@pytest.mark.parametrize("location", ["source", "resource"])
def test_standalone_linked_source_or_resource_is_not_followed(tmp_path, monkeypatch, location):
    source = _source(tmp_path, "fleet-manager")
    path = source if location == "source" else tmp_path / "libs" / "installer-engine"
    seen = []
    monkeypatch.setattr(builder.uer, "_find_symlinked_ancestor", lambda target, root: (
        seen.append(target) or (target if target == path else None)
    ))
    with pytest.raises(builder.ArtifactBuildError, match="missing, linked or outside"):
        standalone.source_closure("fleet-manager", tmp_path, builder)
    assert path in seen
