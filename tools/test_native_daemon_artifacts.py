"""Process-owner construction and source closure remain compiler-free."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import ast
import io
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import zipfile

import pytest

import native_daemon_artifacts as native
from artifact_source_build import PromotionArtifactError, validate_static_build


ROOT = Path(__file__).resolve().parents[1]
PROCUTIL = ROOT / "libs" / "agent-procutil"
MEMBER = "agent_procutil/native/linux-x86_64/daemon-instance"
PREFIX = "agent_procutil-1.0.dist-info/"


@pytest.fixture
def project(tmp_path):
    target = tmp_path / "procutil"
    (target / native.PACKAGE).mkdir(parents=True)
    shutil.copyfile(PROCUTIL / "setup.py", target / "setup.py")
    (target / "pyproject.toml").write_text(
        '[build-system]\nrequires=["setuptools>=83"]\nbuild-backend="setuptools.build_meta"\n'
        '[project]\nname="agent-procutil"\nversion="1.0"\n'
        '[tool.setuptools.packages.find]\nwhere=["src"]\n',
    )
    return target


def _elf():
    value = bytearray(64)
    value[:6] = b"\x7fELF\x02\x01"
    value[18:20] = (62).to_bytes(2, "little")
    return bytes(value)


def _bundle(data=None, *, mode=0o100755, member=MEMBER, platform="any", pure=True):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            PREFIX + "WHEEL",
            f"Root-Is-Purelib: {str(pure).lower()}\nTag: py3-none-{platform}\n",
        )
        if data is not None:
            info = zipfile.ZipInfo(member)
            info.external_attr = mode << 16
            archive.writestr(info, data)
    return zipfile.ZipFile(output)


def test_only_exact_procutil_build_hook_is_admitted(project):
    assert validate_static_build(project)["project"]["name"] == "agent-procutil"
    (project / "setup.py").write_text("raise RuntimeError('foreign hook')\n")
    with pytest.raises(PromotionArtifactError, match="trusted construction owner"):
        validate_static_build(project)


def test_foreign_project_cannot_borrow_native_setup_authority(project):
    path = project / "pyproject.toml"
    path.write_text(path.read_text().replace('name="agent-procutil"', 'name="foreign"'))
    with pytest.raises(PromotionArtifactError, match="executable"):
        validate_static_build(project)


def test_static_procutil_requires_no_executable_setup(project):
    (project / "setup.py").unlink()
    assert validate_static_build(project)["project"]["name"] == "agent-procutil"


@pytest.mark.parametrize("fault", ("missing", "wrong-arch", "non-executable", "directory-kind", "forged"))
def test_native_assets_refuse_missing_foreign_or_self_asserted_bytes(project, monkeypatch, fault):
    expected = _elf()
    actual = expected
    if fault == "wrong-arch":
        actual = expected[:18] + (183).to_bytes(2, "little") + expected[20:]
    elif fault == "forged":
        actual = expected + b"forged but self-consistently hashed bytes"
    mode = 0o40755 if fault == "directory-kind" else 0o100644 if fault == "non-executable" else 0o100755
    with _bundle(
        None if fault == "missing" else actual,
        mode=mode, platform="linux_x86_64", pure=False,
    ) as bundle, pytest.raises(PromotionArtifactError):
        native.generated_members(
            bundle, source_dir=project, data=validate_static_build(project), prefix=PREFIX,
        )


def test_pure_source_wheel_has_no_native_generated_members(project, monkeypatch):
    def refuse(*args, **kwargs):
        pytest.fail("pure source verification cannot execute a compiler")

    monkeypatch.setattr(subprocess, "run", refuse)
    with _bundle() as bundle:
        assert native.generated_members(
            bundle, source_dir=project, data=validate_static_build(project), prefix=PREFIX,
        ) == {}
    assert not hasattr(native, "_compile_expected")


def test_process_owner_has_no_native_source_or_executable_build_hook():
    package = PROCUTIL / "src" / "agent_procutil"
    assert not (package / "_build_native.py").exists()
    assert not any(package.glob("*.c"))
    for pattern in (
        "plugins/*/scripts/daemon_instance_native.c",
        "libs/*/scripts/daemon_instance_native.c",
        "worktree-manager/scripts/daemon_instance_native.c",
    ):
        assert not any(ROOT.glob(pattern))
    tree = ast.parse((PROCUTIL / "setup.py").read_text())
    assert all(isinstance(node, (ast.Expr, ast.ImportFrom)) for node in tree.body)
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    assert len(calls) == 1
    assert isinstance(calls[0].func, ast.Name) and calls[0].func.id == "setup"
    assert not calls[0].args and not calls[0].keywords


@pytest.mark.parametrize("module_path", (
    "tools/materialize_main.py",
    "tools/uv_editable_ref.py",
    "tools/sync-vendored-libs.py",
    "worktree-manager/src/worktree_manager/_trusted_pointer_materializer.py",
))
def test_payload_copies_ignore_owned_test_scratch_without_following_its_links(tmp_path, module_path):
    source = tmp_path / "library"
    (source / "src").mkdir(parents=True)
    (source / "src" / "module.py").write_text("VALUE=1\n")
    scratch = source / ".test-runs"
    scratch.mkdir()
    proof = tmp_path / "unrelated-proof"
    proof.write_text("preserve\n")
    (scratch / "pytest-current").symlink_to(proof)
    spec = importlib.util.spec_from_file_location("native_copy_noise", ROOT / module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    assert module._find_symlink(source) is None
    ignore = getattr(module, "_ignore", getattr(module, "_uv_editable_ignore", None))
    if ignore is not None:
        assert ".test-runs" in ignore(str(source), [".test-runs", "src"])
    if hasattr(module, "_file_hashes"):
        assert module._file_hashes(source).keys() == {"src/module.py"}
    assert proof.read_text() == "preserve\n"


def test_construction_input_inventory_omits_owned_test_scratch(tmp_path):
    source = ROOT / "libs" / "installer-engine" / "installer_engine.py"
    tree = ast.parse(source.read_text())
    generated = next(
        ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "_GENERATED_DIRECTORIES" for target in node.targets)
    )
    function = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_source_files"
    )
    namespace = {"Path": Path, "os": os, "stat": stat, "_GENERATED_DIRECTORIES": generated}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"), namespace)
    (tmp_path / ".test-runs").mkdir()
    (tmp_path / ".test-runs" / "pytest-current").symlink_to(tmp_path / "missing")
    package = tmp_path / "module.py"
    package.write_text("VALUE=1\n")
    assert namespace["_source_files"]((tmp_path,)) == (package,)


def test_release_payload_fingerprint_does_not_adopt_test_scratch(tmp_path):
    import build_python_artifacts as builder

    (tmp_path / "module.py").write_text("VALUE=1\n")
    before = builder.directory_content_hash(tmp_path)
    scratch = tmp_path / ".test-runs"
    scratch.mkdir()
    (scratch / "changing-output").write_text("not a package input\n")
    (scratch / "pytest-current").symlink_to(tmp_path / "missing")
    assert builder.directory_content_hash(tmp_path) == before


def test_python_engine_scoped_sync_preserves_unrelated_support_edits(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("native_engine_sync", ROOT / "tools" / "sync-installer-engine.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    canonical = tmp_path / "canonical"
    canonical.mkdir()
    consumer = tmp_path / "consumer"
    (consumer / "scripts").mkdir(parents=True)
    for name in module.ier.PYTHON_FILES:
        (canonical / name).write_text(f"canonical {name}\n")
    support = consumer / "scripts" / "daemon_instances.ps1"
    support.write_text("concurrent native edit\n")
    monkeypatch.setattr(module, "REPO", tmp_path)
    monkeypatch.setattr(module, "CANONICAL_DIR", canonical)
    monkeypatch.setattr(module.ier, "python_adopter_dirs", lambda *args, **kwargs: (consumer,))
    monkeypatch.setattr(sys, "argv", [str(spec.origin), "--only-python-engine"])
    assert module.main() == 0
    assert support.read_text() == "concurrent native edit\n"
    for name in module.ier.PYTHON_FILES:
        assert (consumer / "scripts" / name).read_bytes() == (canonical / name).read_bytes()


def test_session_host_capsule_contains_instance_closure_without_site_packages(tmp_path, monkeypatch):
    source = ROOT / "plugins" / "agent-bridge" / "src" / "agent_bridge" / "session_host" / "bundle.py"
    spec = importlib.util.spec_from_file_location("native_capsule_distribution", source)
    assert spec and spec.loader
    monkeypatch.setitem(sys.modules, "agent_procutil", SimpleNamespace(
        __file__=str(PROCUTIL / "src" / "agent_procutil" / "__init__.py"),
    ))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    procutil = PROCUTIL / "src" / "agent_procutil"
    for name in module._PROCUTIL_MODULES:
        for node in ast.walk(ast.parse((procutil / name).read_text())):
            if isinstance(node, ast.ImportFrom) and node.level == 1:
                imports = [node.module.split(".")[0]] if node.module else [alias.name for alias in node.names]
                for dependency in imports:
                    if (procutil / (dependency + ".py")).is_file():
                        assert dependency + ".py" in module._PROCUTIL_MODULES
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    archive, _digest = module.build_session_host_bundle(tmp_path / "bundles")
    with zipfile.ZipFile(archive) as bundle:
        assert "agent_procutil/daemon_instances.py" in bundle.namelist()
        assert "agent_procutil/_daemon_instance_store.py" in bundle.namelist()
        assert "agent_procutil/_daemon_instance_identity.py" in bundle.namelist()
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c",
         "import sys;sys.path.insert(0,sys.argv[1]);import agent_procutil.daemon_instances;"
         "import agent_bridge.session_host.launcher;print('capsule-import-ok')", str(archive)],
        text=True, capture_output=True, timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "capsule-import-ok"


def _gateway_source():
    path = ROOT / "tools" / "clean-room" / "gateway-role-tunnels" / "source.py"
    spec = importlib.util.spec_from_file_location("native_gateway_source_contract", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_gateway_pinned_context_and_docker_allowlist_include_exact_procutil_closure():
    from fnmatch import fnmatchcase

    source = _gateway_source()
    package = PROCUTIL / "src" / "agent_procutil"
    assert source.PROCUTIL_MODULES == {path.stem for path in package.glob("*.py")}
    assert source.PROCUTIL_RESOURCES == set(
        validate_static_build(PROCUTIL)["tool"]["setuptools"]["package-data"]["agent_procutil"]
    )
    recipe_root = ROOT / "fleet-manager" / "containers" / "gateway"
    patterns = (recipe_root / "Dockerfile.dockerignore").read_text().splitlines()

    def allowed(name):
        result = True
        for pattern in patterns:
            if pattern and not pattern.startswith("#") and fnmatchcase(name, pattern.removeprefix("!").rstrip("/")):
                result = pattern.startswith("!")
        return result

    required = {name for name in source.REQUIRED if name.startswith("libs/agent-procutil/")}
    expected = {
        "libs/agent-procutil/pyproject.toml", "libs/agent-procutil/setup.py",
        "libs/agent-procutil/README.md",
        *(f"libs/agent-procutil/src/agent_procutil/{name}.py" for name in source.PROCUTIL_MODULES),
        *(f"libs/agent-procutil/src/agent_procutil/{name}" for name in source.PROCUTIL_RESOURCES),
    }
    assert required == expected
    assert all((ROOT / name).is_file() and allowed(name) for name in required)
    assert not allowed("libs/agent-procutil/src/agent_procutil/unreviewed.py")
    assert not allowed("libs/agent-procutil/src/agent_procutil/native/linux-x86_64/daemon-instance")
    recipe = (recipe_root / "Dockerfile").read_text().replace("\\\n", " ")
    assert "COPY libs/agent-procutil/src/agent_procutil/ " in recipe
    assert "--out-dir /wheels ./libs/agent-procutil" in recipe
    assert '"${wheel}[service-gateway]" /wheels/agent_procutil-*.whl' in recipe
    assert "gcc libc6-dev" not in recipe
    assert "! command -v cc && ! command -v gcc" in recipe
    assert "from agent_procutil._daemon_instance_linux import check; check()" in recipe


@pytest.mark.parametrize("name,code", (
    ("libs/agent-procutil/src/agent_procutil/unreviewed.py", "unknown-procutil-source"),
    ("fleet-manager/src/fleet_manager/unreviewed.py", "unknown-package-source"),
))
def test_gateway_git_materialization_keeps_unknown_modules_closed(tmp_path, monkeypatch, name, code):
    source = _gateway_source()

    def git(repository, *args):
        if args[0] == "rev-parse":
            return b"a" * 40 + b"\n"
        if args[0] == "ls-tree":
            return b"100644 blob " + b"b" * 40 + b"\t" + name.encode() + b"\0"
        pytest.fail("unknown source must refuse before any blob or live-tree read")

    monkeypatch.setattr(source, "git", git)
    with pytest.raises(source.ProofError, match=code):
        source.materialize(tmp_path, "HEAD", tmp_path / "run")


def test_dispatch_source_image_builds_local_procutil_wheel_only_in_builder():
    recipe = (ROOT / "services" / "dispatch" / "Dockerfile").read_text().replace("\\\n", " ")
    builder, runtime = recipe.split("\nFROM python:3.12-slim\n", 1)
    assert "COPY libs/agent-procutil libs/agent-procutil" in builder
    assert "gcc libc6-dev" not in builder
    assert "! command -v cc && ! command -v gcc" in builder
    assert "--out-dir /wheels /src/libs/agent-procutil" in builder
    assert "/wheels/agent_procutil-*.whl /src/plugins/agent-dispatch" in builder
    assert "gcc" not in runtime and "libc6-dev" not in runtime
    assert "useradd --system --create-home --home-dir /home/dispatch" in runtime


@pytest.mark.skipif(
    not sys.platform.startswith("linux")
    or (hasattr(os, "getuid") and os.getuid() == 0)
    or os.environ.get("PROCUTIL_NATIVE_ARTIFACT_ACCEPTANCE") != "1",
    reason="requires the explicit non-root Linux artifact construction lane",
)
def test_real_pure_wheel_and_source_archive_need_only_selected_python(tmp_path, monkeypatch):
    import build_python_artifacts as builder
    from build_toolchain_lock import ToolchainLock
    from promotion_python_artifacts import validate_wheel

    owned = tmp_path / "work"
    owned.mkdir()
    monkeypatch.setenv("TMPDIR", str(owned))
    monkeypatch.setenv("UV_CACHE_DIR", str(owned / "uv-cache"))
    monkeypatch.setenv("UV_OFFLINE", "1")
    monkeypatch.setattr(tempfile, "tempdir", str(owned))
    root = tmp_path / "source"
    source = root / "libs" / "agent-procutil"
    shutil.copytree(
        PROCUTIL, source,
        ignore=shutil.ignore_patterns(".test-runs", ".pytest_cache", "__pycache__", "build", "dist", "*.egg-info", "native"),
    )
    stale = source / native.PACKAGE / "native" / "linux-unused" / "daemon-instance"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"stale foreign-architecture source output must not be adopted\n")
    for library in ("versioned-runtime", "installer-engine"):
        destination = root / "libs" / library
        destination.mkdir(parents=True)
        for path in (ROOT / "libs" / library).iterdir():
            if path.is_file():
                shutil.copyfile(path, destination / path.name)
    scripts = root / "plugins" / "fixture" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "install.sh").write_text("# source artifact consumer\n")
    (scripts.parent / "pyproject.toml").write_text('[project]\nname="fixture"\n')
    toolchain = ToolchainLock(Path(sys.executable), {
        name: importlib.metadata.version(name) for name in ("setuptools", "wheel", "packaging")
    })
    wheel = builder.build_wheel(source, tmp_path / "wheels", toolchain=toolchain)
    assert wheel.name.endswith("-py3-none-any.whl")
    with zipfile.ZipFile(wheel) as archive:
        assert not any(name.startswith("agent_procutil/native/") for name in archive.namelist())
    assert stale.read_bytes() == b"stale foreign-architecture source output must not be adopted\n"
    with wheel.open("rb") as stream:
        validate_wheel(
            stream, {"filename": wheel.name, "generator": toolchain.generator}, source_dir=source,
        )
    spec = importlib.util.spec_from_file_location("native_asset_fanout", ROOT / "tools" / "sync-versioned-runtime.py")
    assert spec and spec.loader
    sync = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sync)
    for key, value in {
        "REPO": root, "PLUGINS_DIR": root / "plugins",
        "CANONICAL": root / "libs" / "versioned-runtime" / "versioned_runtime.py",
        "RESOLVER_DIR": root / "libs" / "versioned-runtime",
        "DEPENDENCY_POLICY": root / "libs" / "installer-engine" / "installer_dependency_policy.py",
        "DEPENDENCY_CONFIG": root / "libs" / "installer-engine" / "installer_dependency_config.py",
    }.items():
        monkeypatch.setattr(sync, key, value)
    monkeypatch.setattr(sys, "argv", [
        str(spec.origin), "--only-installer-instances",
    ])
    assert sync.main() == 0
    from daemon_lifecycle_assets import ARCHIVE_NAME
    assert (scripts / ARCHIVE_NAME).is_file()
    uv = shutil.which("uv")
    assert uv
    venv = tmp_path / "installed"
    subprocess.run([uv, "venv", str(venv), "--python", sys.executable, "--offline"], check=True)
    python = venv / "bin" / "python"
    subprocess.run(
        [uv, "pip", "install", "--python", str(python), "--no-deps", "--no-index", str(wheel)],
        check=True, umask=0o077,
    )
    ambient = tmp_path / "ambient-imports"
    ambient.mkdir()
    (ambient / "agent_procutil.py").write_text(
        "raise RuntimeError('ambient imports cannot qualify the installed wheel')\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PYTHONPATH", str(ambient))
    installed = subprocess.check_output(
        [str(python), "-I", "-c", "import pathlib,agent_procutil;print(pathlib.Path(agent_procutil.__file__).parent)"],
        text=True,
    ).strip()
    assert Path(installed).resolve().is_relative_to(venv.resolve())
    runtime_bin = tmp_path / "runtime-bin"
    runtime_bin.mkdir()
    for name in ("bash", "dirname", "uname", "readlink", "stat", "id"):
        executable = shutil.which(name)
        assert executable
        (runtime_bin / name).symlink_to(executable)
    for binding in (scripts / "daemon_instances.sh", Path(installed) / "daemon_instances.sh"):
        result = subprocess.run(
            [str(runtime_bin / "bash"), "--noprofile", "--norc", "-c",
             '. "$1"; ! command -v cc && ! command -v python && ! command -v python3 || exit 81; '
             '_di_lifecycle check && printf compiler-free-ok',
             "runtime-proof", str(binding)],
            env={"PATH": str(runtime_bin), "COPILOT_EXTENSIONS_DAEMON_PYTHON": str(python)},
            text=True, capture_output=True, timeout=15,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == "compiler-free-ok"
    denied = tmp_path / "unreadable-registry"
    denied.mkdir()
    denied_proof = denied / ("a" * 32 + ".instance")
    denied_proof.write_text("unreadable inventory proof")
    denied.chmod(0)
    try:
        permission = subprocess.run(
            [str(runtime_bin / "bash"), "--noprofile", "--norc", "-c",
             '. "$1"; reconcile_installer_instances "$2" "$3"', "permission-proof",
             str(ROOT / "libs" / "installer-engine" / "installer-instance-reconcile.sh"),
             installed, str(denied)],
            text=True, capture_output=True, timeout=15,
        )
    finally:
        denied.chmod(0o700)
    assert permission.returncode == 0, permission.stderr
    assert "deferred: native-reconcile-failed" in permission.stderr
    assert denied_proof.read_text() == "unreadable inventory proof"
    tests = tmp_path / "installed-native-tests"
    tests.mkdir()
    selected = [
        tests / "test_daemon_instances_linux.py",
        tests / "test_daemon_reconcile_cursor.py",
        tests / "test_daemon_instance_clock.py",
    ]
    for path in selected:
        shutil.copyfile(PROCUTIL / "tests" / path.name, path)
    shutil.copyfile(PROCUTIL / "tests" / "conftest.py", tests / "conftest.py")
    qualified = subprocess.run(
        [str(python), "-I", "-c",
         "import sys;from pathlib import Path;"
         "sys.path.append(sys.argv.pop(1));expected=Path(sys.argv.pop(1)).resolve();"
         "import agent_procutil;"
         "assert Path(agent_procutil.__file__).resolve().parent==expected;"
         "import pytest;"
         "raise SystemExit(pytest.main(sys.argv[1:]))",
         str(Path(pytest.__file__).parent.parent), installed, *(str(path) for path in selected),
         "--basetemp", str(tmp_path / "lifecycle-roots"), "-q"],
        env={
            **{key: value for key, value in os.environ.items() if not key.startswith("PYTHON")},
            "COPILOT_EXTENSIONS_TEST_CONTAINED": "1",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONDONTWRITEBYTECODE": "1",
        },
        text=True, capture_output=True, timeout=60,
    )
    assert qualified.returncode == 0, qualified.stdout + qualified.stderr
    print(qualified.stdout, end="")
    export = os.environ.get("NATIVE_ARTIFACT_PROBE_OUT")
    if export:
        output = Path(export)
        output.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(scripts / ARCHIVE_NAME, output / ARCHIVE_NAME)
        shutil.copyfile(scripts / "daemon_instances.sh", output / "daemon_instances.sh")
        shutil.copyfile(wheel, output / wheel.name)
