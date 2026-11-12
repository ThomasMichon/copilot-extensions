"""Static deployment admission never executes installed import hooks."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import venv

import pytest

LIBRARY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIBRARY))
import versioned_runtime as runtime


@pytest.fixture(params=["Lib/site-packages", "lib/python3.14/site-packages"])
def installed(tmp_path, request):
    slot = runtime.slot(tmp_path / "runtime", "2.0")
    site = slot.joinpath(*request.param.split("/"))
    site.mkdir(parents=True)
    (slot / "pyvenv.cfg").write_text("include-system-site-packages = false\n")
    package = site / "sample"
    package.mkdir()
    (package / "__init__.py").write_text("VALUE = 'frozen'\n")
    metadata = site / "sample-1.0.dist-info"
    metadata.mkdir()
    (metadata / "direct_url.json").write_text(json.dumps({
        "url": (tmp_path / "removed-build-input").as_uri(), "dir_info": {},
    }))
    return slot.parent.parent, slot, site


def damage(slot, site, case, tmp_path):
    origin = site / "sample-1.0.dist-info" / "direct_url.json"
    if case in {"editable-origin", "editable-origin-case"}:
        if case == "editable-origin-case":
            upper = origin.with_name("DIRECT_URL.JSON")
            origin.rename(upper)
            origin = upper
        origin.write_text(json.dumps({"url": tmp_path.as_uri(), "dir_info": {"editable": True}}))
    elif case == "invalid-origin":
        origin.write_text('{"dir_info": {}, "dir_info": {"editable": false}}')
    elif case == "invalid-editable-type":
        origin.write_text('{"dir_info": {"editable": 0}}')
    elif case == "egg-link":
        (site / "sample.egg-link").write_text(str(tmp_path) + "\n.")
    elif case == "editable-finder":
        (site / "__editable___sample_finder.py").write_text("raise RuntimeError('must not execute')")
    elif case == "external-path":
        (site / "sample.pth").write_text(str(tmp_path / "checkout"))
    elif case == "relative-external-path":
        (site / "sample.pth").write_text("../../../../checkout")
    elif case == "executable-hook":
        (site / "sample.pth").write_text(
            f"import pathlib; pathlib.Path({str(tmp_path / 'executed')!r}).touch()\n"
        )
    elif case == "fake-virtualenv-hook":
        (site / "_virtualenv.pth").write_text("import _virtualenv\n")
    elif case == "modified-virtualenv-hook":
        (site / "_virtualenv.py").write_text("")
        (site / "_virtualenv.pth").write_text("import _virtualenv; import editable\n")
    elif case in {"fake-pywin32-hook", "modified-pywin32-hook"}:
        (site / "pywin32.pth").write_text("import pywin32_bootstrap\n")
        if case == "modified-pywin32-hook":
            library = site / "win32" / "lib"
            library.mkdir(parents=True)
            (library / "pywin32_bootstrap.py").write_text("")
            (site / "pywin32_system32").mkdir()
            (site / "pywin32.pth").write_text("import pywin32_bootstrap; import editable\n")
    elif case == "system-site-packages":
        (slot / "pyvenv.cfg").write_text("include-system-site-packages = true\n")
    elif case == "modified-immutable-hook":
        (site / "immutable_runtime.pth").write_text(
            "import sys; sys.dont_write_bytecode = True; import editable\n"
        )
    elif case == "invalid-venv-encoding":
        (slot / "pyvenv.cfg").write_bytes(b"\xff")
    elif case == "invalid-path-encoding":
        (site / "sample.pth").write_bytes(b"\xff")
    else:
        raise AssertionError(case)


@pytest.mark.parametrize("case", [
    "editable-origin", "editable-origin-case", "invalid-origin", "invalid-editable-type", "egg-link",
    "editable-finder", "external-path", "relative-external-path", "executable-hook",
    "fake-virtualenv-hook", "modified-virtualenv-hook", "fake-pywin32-hook",
    "modified-pywin32-hook", "modified-immutable-hook", "system-site-packages",
    "invalid-venv-encoding", "invalid-path-encoding",
])
@pytest.mark.parametrize("published", [False, True])
def test_refuse_nonimmutable_environment_without_rewriting(installed, tmp_path, case, published):
    root, slot, site = installed
    if published:
        runtime.mark_complete(root, "2.0", payload_hash="same")
    damage(slot, site, case, tmp_path)
    before = {p.relative_to(slot): p.read_bytes() for p in slot.rglob("*") if p.is_file()}
    if published:
        assert runtime.check_admission(root, "2.0", payload_hash="same") == runtime.ADMIT_HEALTH_REPAIR_REQUIRED
    with pytest.raises(RuntimeError):
        runtime.mark_complete(root, "2.0", payload_hash="same")
    assert {p.relative_to(slot): p.read_bytes() for p in slot.rglob("*") if p.is_file()} == before
    assert not (tmp_path / "executed").exists()
    assert runtime.current_version(root) is None


def test_standard_bootstrap_hooks_and_slot_local_paths(installed):
    root, slot, site = installed
    (site / "_virtualenv.py").write_text("")
    (site / "_virtualenv.pth").write_text("import _virtualenv\n")
    (site / "immutable_runtime.pth").write_text("import sys; sys.dont_write_bytecode = True\n")
    package = site / "_distutils_hack"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (site / "distutils-precedence.pth").write_text(
        "import os; var = 'SETUPTOOLS_USE_DISTUTILS'; "
        "enabled = os.environ.get(var, 'local') == 'local'; "
        "enabled and __import__('_distutils_hack').add_shim();\n"
    )
    (site / "paths.pth").write_text("# comment\n\nsample\n" + str(slot / "resources") + "\n")
    marker = runtime.mark_complete(root, "2.0", payload_hash="same")
    before = marker.read_bytes()
    assert runtime.check_admission(root, "2.0", payload_hash="same") == runtime.ADMIT_REUSE
    assert runtime.mark_complete(root, "2.0", payload_hash="same") == marker
    assert marker.read_bytes() == before


def _retained_reader():
    source = LIBRARY.parents[1] / "libs/installer-engine/runtime_launcher.py"
    spec = importlib.util.spec_from_file_location("_completion_retained_reader", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _bootstrap(slot):
    directory = slot / ".runtime-bootstrap"
    directory.mkdir()
    shutil.copyfile(
        LIBRARY.parents[1] / "libs/installer-engine/runtime_launcher.py",
        directory / "runtime_launcher.py",
    )
    return directory


def test_completion_is_usable_by_retained_reader_and_preserves_exact_reuse(installed):
    root, slot, _site = installed
    bootstrap = _bootstrap(slot)
    marker = runtime.mark_complete(root, "2.0", payload_hash="same")
    reader = _retained_reader()
    identity = reader.runtime_identity(slot)
    before = {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in (marker, bootstrap / "runtime-generation")
    }
    assert runtime.mark_complete(root, "2.0", payload_hash="same") == marker
    assert reader.runtime_identity(slot) == identity
    assert {
        path: (path.read_bytes(), path.stat().st_mtime_ns) for path in before
    } == before


@pytest.mark.parametrize("damage_case", ["malformed", "hardlink", "missing-launcher", "oversized", "binary-eof"])
def test_invalid_incoming_identity_is_not_certified_or_overwritten(installed, damage_case):
    root, slot, _site = installed
    bootstrap = _bootstrap(slot)
    generation = bootstrap / "runtime-generation"
    if damage_case == "missing-launcher":
        (bootstrap / "runtime_launcher.py").unlink()
    else:
        content = b"not-a-generation\n" if damage_case == "malformed" else b"a" * (
            65 if damage_case == "oversized" else 32
        )
        if damage_case == "binary-eof":
            content += b"\x1a"
        generation.write_bytes(content)
        if damage_case == "hardlink":
            os.link(generation, slot / "foreign-generation")
    before = {path: path.read_bytes() for path in slot.rglob("*") if path.is_file()}
    with pytest.raises((OSError, ValueError, RuntimeError)):
        runtime.mark_complete(root, "2.0", payload_hash="same")
    assert not runtime.marker_path(root, "2.0").exists()
    assert {path: path.read_bytes() for path in before} == before


def test_completed_legacy_slot_is_not_backfilled_by_completion_reuse(installed):
    root, slot, _site = installed
    marker = runtime.mark_complete(root, "2.0", payload_hash="same")
    bootstrap = _bootstrap(slot)
    before = {path: path.read_bytes() for path in slot.rglob("*") if path.is_file()}
    assert runtime.mark_complete(root, "2.0", payload_hash="same") == marker
    assert not (bootstrap / "runtime-generation").exists()
    assert {path: path.read_bytes() for path in before} == before


def test_mutable_dev_completion_does_not_validate_numbered_generation(tmp_path):
    owner = "worktree:development"
    runtime.claim_dev(tmp_path, owner)
    slot = runtime.slot(tmp_path, "dev")
    marker = runtime.mark_complete(tmp_path, "dev", payload_hash="first", dev_owner=owner)
    generation = _bootstrap(slot) / "runtime-generation"
    generation.write_bytes(b"not-a-numbered-generation\n")
    original = generation.read_bytes(), generation.stat().st_mtime_ns
    assert runtime.mark_complete(
        tmp_path, "dev", payload_hash="replacement", dev_owner=owner,
    ) == marker
    assert runtime.read_marker(tmp_path, "dev")["payload_hash"] == "replacement"
    assert (generation.read_bytes(), generation.stat().st_mtime_ns) == original


def test_group_writable_bootstrap_is_protected_before_completion(installed):
    root, slot, _site = installed
    launcher = _bootstrap(slot) / "runtime_launcher.py"
    original = launcher.read_bytes()
    launcher.chmod(0o664)
    runtime.mark_complete(root, "2.0", payload_hash="same")
    assert _retained_reader().runtime_identity(slot)
    assert launcher.read_bytes() == original
    if os.name != "nt":
        assert stat.S_IMODE(launcher.stat().st_mode) == 0o644


@pytest.mark.timeout(30)
@pytest.mark.parametrize("alias_kind", ["ancestor", "slot"])
def test_completion_accepts_ancestor_alias_without_following_slot_alias(tmp_path, alias_kind):
    physical = tmp_path / "physical"
    physical.mkdir()
    root = tmp_path / "runtime"
    if alias_kind == "ancestor":
        alias = tmp_path / "alias"
        root = alias / "runtime"
    else:
        alias = runtime.slot(root, "2.0")
        alias.rmdir()
    if os.name == "nt":
        subprocess.run(
            [os.environ["COMSPEC"], "/c", "mklink", "/J", str(alias), str(physical)],
            capture_output=True, check=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    else:
        alias.symlink_to(physical, target_is_directory=True)
    try:
        slot = runtime.slot(root, "2.0") if alias_kind == "ancestor" else physical
        _bootstrap(slot)
        if alias_kind == "ancestor":
            runtime.mark_complete(root, "2.0", payload_hash="same")
            assert _retained_reader().runtime_identity(slot)
            assert runtime.marker_path(physical / "runtime", "2.0").is_file()
        else:
            with pytest.raises((RuntimeError, ValueError)):
                runtime.mark_complete(root, "2.0", payload_hash="same")
            assert not (physical / ".install-complete.json").exists()
            assert not (physical / ".runtime-bootstrap/runtime-generation").exists()
    finally:
        if os.name == "nt":
            alias.rmdir()
        else:
            alias.unlink()


@pytest.mark.timeout(60)
@pytest.mark.parametrize("publisher", ["plugin", "embedded-toolkit"])
@pytest.mark.parametrize("gui", [False, True])
def test_real_constructed_environment_launches_with_its_published_identity(tmp_path, publisher, gui):
    if gui and os.name != "nt":
        pytest.skip("physical Windows GUI interpreter")
    root = tmp_path / "runtime"
    slot = runtime.slot(root, "2.0")
    environment = slot / ".venv"
    venv.EnvBuilder(with_pip=False).create(environment)
    source = LIBRARY.parents[1] / "plugins/agent-worktrees/scripts/versioned_runtime.py"
    spec = importlib.util.spec_from_file_location("_native_completion_owner", source)
    owner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(owner)
    owner._runtime_component("versioned_runtime_construction").retain_helpers(slot, complete=False)
    shutil.copyfile(
        LIBRARY.parents[1] / "libs/installer-engine/runtime_launcher.py",
        slot / ".runtime-bootstrap/runtime_launcher.py",
    )
    if publisher == "embedded-toolkit":
        source = source.parent / "_runtime_admission/libs/versioned-runtime/versioned_runtime.py"
        spec = importlib.util.spec_from_file_location("_embedded_completion_owner", source)
        owner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(owner)
    site = environment / (
        "Lib/site-packages" if os.name == "nt"
        else f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
    )
    sentinel = tmp_path / "executed"
    (site / "completion_fixture.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        f"assert Path(sys.prefix).samefile({str(environment)!r})\n"
        f"assert Path(sys.executable).samefile({str(environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python'))!r})\n"
        f"Path({str(sentinel)!r}).write_text('ran')\n",
    )
    if os.name != "nt":
        (slot / ".runtime-bootstrap/runtime_launcher.py").chmod(0o664)
    generation = slot / ".runtime-bootstrap/runtime-generation"
    original_generation = b"a" * 32 + b"\n"
    generation.write_bytes(original_generation)
    generation.chmod(0o664)
    owner.mark_complete(root, "2.0", payload_hash="same")
    assert generation.read_bytes() == original_generation
    if os.name != "nt":
        assert stat.S_IMODE(generation.stat().st_mode) == 0o644
    identity = _retained_reader().runtime_identity(slot)
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    command = [
        str(python), "-I", "-S", "-B", str(slot / ".runtime-bootstrap/runtime_launcher.py"),
        *(["--gui-runtime"] if gui else []),
        "--runtime-identity", identity, "-m", "completion_fixture",
    ]
    executable = str(Path(sys.base_prefix) / "pythonw.exe") if gui else str(python)
    command[0] = executable

    def invoke():
        return subprocess.run(
            command, executable=executable, capture_output=True, text=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )

    completed = invoke()
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert sentinel.read_text() == "ran"
    sentinel.unlink()
    if gui:
        wrong_host = subprocess.run(
            [str(python), *command[1:]], executable=str(python),
            capture_output=True, text=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert wrong_host.returncode != 0
        assert not sentinel.exists()
    generation = slot / ".runtime-bootstrap/runtime-generation"
    generation.write_text(("b" if not identity.startswith("b" * 32) else "c") * 32)
    refused = invoke()
    assert refused.returncode != 0
    if not gui:
        assert "build identity changed" in refused.stderr
    assert not sentinel.exists()


def test_packaged_pywin32_bootstrap_is_validated_without_execution(installed):
    root, slot, site = installed
    library = site / "win32" / "lib"
    library.mkdir(parents=True)
    (library / "pywin32_bootstrap.py").write_text("raise AssertionError('must not execute')\n")
    (site / "pywin32_system32").mkdir()
    (site / "pythonwin").mkdir()
    (site / "pywin32.pth").write_text(
        "# .pth file for the PyWin32 extensions\nwin32\nwin32\\lib\npythonwin\n"
        "import pywin32_bootstrap\n",
    )
    runtime.mark_complete(root, "2.0", payload_hash="same")
    assert runtime.check_admission(root, "2.0", payload_hash="same") == runtime.ADMIT_REUSE


@pytest.mark.parametrize("layout", ["Lib/site-packages", "lib/python3.14/site-packages"])
@pytest.mark.parametrize("damage_case", [None, "system-site-packages", "editable-origin", "external-path"])
def test_nested_application_environment_admission(tmp_path, layout, damage_case):
    root = tmp_path / "runtime"
    slot = runtime.slot(root, "2.0")
    environment = slot / ".venv"
    site = environment.joinpath(*layout.split("/"))
    site.mkdir(parents=True)
    (environment / "pyvenv.cfg").write_text("include-system-site-packages = false\n")
    metadata = site / "sample-1.0.dist-info"
    metadata.mkdir()
    (metadata / "direct_url.json").write_text(json.dumps({"dir_info": {}}))
    interpreter = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    interpreter.parent.mkdir(parents=True)
    interpreter.write_bytes(b"not executed")
    assert runtime.slot_python(root, "2.0") == interpreter
    runtime.mark_complete(root, "2.0", payload_hash="same")
    if damage_case is None:
        assert runtime.check_admission(root, "2.0", payload_hash="same") == runtime.ADMIT_REUSE
    else:
        damage(environment, site, damage_case, tmp_path)
        assert runtime.check_admission(root, "2.0", payload_hash="same") == runtime.ADMIT_HEALTH_REPAIR_REQUIRED
        with pytest.raises(RuntimeError):
            runtime.mark_complete(root, "2.0", payload_hash="same")
    assert runtime.current_version(root) is None


@pytest.mark.skipif(os.name != "nt", reason="Windows legacy path-length behavior")
@pytest.mark.parametrize("damage_case", [None, "editable-origin", "egg-link"])
def test_long_installed_content_retains_immutable_admission(tmp_path, monkeypatch, damage_case):
    root = tmp_path / "runtime"
    slot = runtime.slot(root, "2.0")
    environment = slot / ".venv"
    site = environment / "Lib" / "site-packages"
    directory = site
    while len(str(directory)) < 270:
        directory /= "nested-package"
    native_directory = Path("\\\\?\\" + str(directory))
    native_directory.mkdir(parents=True)
    (native_directory / "sample.py").write_text("VALUE = 'frozen'\n")
    metadata = native_directory / "sample-1.0.dist-info"
    metadata.mkdir()
    (metadata / "direct_url.json").write_text(json.dumps({
        "dir_info": {"editable": damage_case == "editable-origin"},
    }))
    if damage_case == "egg-link":
        (native_directory / "sample.egg-link").write_text("external source\n")
    (environment / "pyvenv.cfg").write_text("include-system-site-packages = false\n")
    original_lstat = Path.lstat
    original_open = Path.open

    def legacy_call(operation):
        def call(path, *args, **kwargs):
            if len(str(path)) >= 260 and not str(path).startswith("\\\\?\\"):
                raise FileNotFoundError("legacy Windows path exceeds MAX_PATH")
            return operation(path, *args, **kwargs)
        return call

    monkeypatch.setattr(Path, "lstat", legacy_call(original_lstat))
    monkeypatch.setattr(Path, "open", legacy_call(original_open))
    if damage_case is None:
        runtime.mark_complete(root, "2.0", payload_hash="same")
        assert runtime.check_admission(root, "2.0", payload_hash="same") == runtime.ADMIT_REUSE
    else:
        with pytest.raises(RuntimeError, match="Editable installation"):
            runtime.mark_complete(root, "2.0", payload_hash="same")
        assert not runtime.marker_path(root, "2.0").exists()
    assert runtime.current_version(root) is None


@pytest.mark.parametrize("published", [False, True])
def test_linked_installed_package_is_refused(installed, tmp_path, published):
    root, slot, site = installed
    if published:
        runtime.mark_complete(root, "2.0", payload_hash="same")
    external = tmp_path / "checkout.py"
    external.write_text("VALUE = 'mutable'\n")
    link = site / "live.py"
    try:
        link.symlink_to(external)
    except OSError as exc:
        if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows file symlinks require an unavailable privilege")
        raise
    with pytest.raises(RuntimeError):
        runtime.mark_complete(root, "2.0", payload_hash="same")
    if published:
        assert runtime.check_admission(root, "2.0", payload_hash="same") == runtime.ADMIT_HEALTH_REPAIR_REQUIRED
    assert link.is_symlink()
    assert external.read_text() == "VALUE = 'mutable'\n"


@pytest.mark.parametrize("published", [False, True])
def test_cached_wheel_hardlinks_survive_cache_retirement(installed, tmp_path, published):
    root, slot, site = installed
    if published:
        runtime.mark_complete(root, "2.0", payload_hash="same")
    cache = tmp_path / "wheel-cache"
    cache.mkdir()
    source = cache / "package.py"
    source.write_text("VALUE = 'immutable wheel content'\n")
    os.link(source, site / "live.py")
    runtime.mark_complete(root, "2.0", payload_hash="same")
    assert runtime.check_admission(root, "2.0", payload_hash="same") == runtime.ADMIT_REUSE
    assert (site / "live.py").stat().st_nlink == 2
    source.unlink()
    assert (site / "live.py").read_text() == "VALUE = 'immutable wheel content'\n"
    assert runtime.check_admission(root, "2.0", payload_hash="same") == runtime.ADMIT_REUSE


@pytest.mark.parametrize("metadata", ["origin", "config", "path"])
def test_metadata_io_failure_is_not_absence(installed, monkeypatch, metadata):
    root, slot, site = installed
    target = {
        "origin": site / "sample-1.0.dist-info" / "direct_url.json",
        "config": slot / "pyvenv.cfg",
        "path": site / "sample.pth",
    }[metadata]
    if metadata == "path":
        target.write_text("# valid path metadata\n")
    runtime.mark_complete(root, "2.0", payload_hash="same")
    original = Path.read_text

    def denied(path, *args, **kwargs):
        if Path(runtime._normalize_windows_extended_path(str(path))) == target:
            raise PermissionError("metadata denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", denied)
    with pytest.raises(PermissionError, match="metadata denied"):
        runtime.check_admission(root, "2.0", payload_hash="same")


@pytest.mark.parametrize("version", ["dev", "dev-" + "a" * 32])
def test_explicit_development_completion_also_refuses_editable_content(tmp_path, version):
    owner = "worktree:development"
    runtime.claim_dev(tmp_path, owner)
    slot = runtime.slot(tmp_path, version)
    site = slot / "Lib" / "site-packages"
    site.mkdir(parents=True)
    (site / "live.egg-link").write_text(str(tmp_path / "checkout"))
    with pytest.raises(RuntimeError, match="Editable installation"):
        runtime.mark_complete(tmp_path, version, payload_hash="same", dev_owner=owner)
    assert not runtime.marker_path(tmp_path, version).exists()
    assert (site / "live.egg-link").is_file()


def test_all_vendored_adopters_share_environment_admission():
    repository = LIBRARY.parents[1]
    destinations = [
        *repository.glob("plugins/*/scripts/versioned_runtime.py"),
        repository / "worktree-manager" / "scripts" / "versioned_runtime.py",
        repository / "libs" / "update-driver" / "scripts" / "versioned_runtime.py",
    ]
    assert len(destinations) >= 15
    for index, source in enumerate(destinations):
        spec = importlib.util.spec_from_file_location(f"admission_adopter_{index}", source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert callable(module.require_immutable_environment), source
        assert source.read_bytes() == (LIBRARY / "versioned_runtime.py").read_bytes()
