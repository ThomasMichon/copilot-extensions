"""Real standalone artifact/installation boundaries, without hosting effects."""

from __future__ import annotations

import base64
import csv
import fnmatch
import hashlib
import importlib.util
import inspect
import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name

PROJECT = Path(__file__).absolute().parents[1]
LIBRARY = PROJECT.parent / "libs" / "versioned-runtime"
FLAGS = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
COMPANIONS = (
    "versioned_runtime_leases.py", "versioned_runtime_construction.py",
    "versioned_runtime_source.py", "versioned_runtime_publication.py",
)
PROCESS_RESOURCES = {
    "_runtime_launcher.py": Path("installer-engine") / "runtime_launcher.py",
    "_installer_process.py": Path("installer-engine") / "installer_engine_process.py",
    "_construction_procutil.py": Path("agent-procutil") / "src" / "agent_procutil" / "__init__.py",
}


def run(argv, *, cwd=None, env=None):
    return subprocess.run(
        argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=30, check=False, **FLAGS,
    )


def project_at(root, *, nested=False):
    project = root / ("services" if nested else "") / "fleet-manager"
    project.mkdir(parents=True)
    for name in ("pyproject.toml", "README.md", "setup.py"):
        shutil.copy2(PROJECT / name, project / name)
    shutil.copytree(PROJECT / "src", project / "src", ignore=shutil.ignore_patterns("__pycache__"))
    library = root / "libs" / "versioned-runtime"
    library.mkdir(parents=True)
    for name in ("versioned_runtime.py", "standalone_build.py", *COMPANIONS):
        shutil.copy2(LIBRARY / name, library / name)
    for relative in PROCESS_RESOURCES.values():
        target = library.parent / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(LIBRARY.parent / relative, target)
    return project, library


def build_path(path):
    if os.name != "nt":
        return str(path)
    # The owned worktree sandbox can exceed setuptools' relative MAX_PATH paths.
    # Use an existing filesystem alias; never change host long-path policy.
    import ctypes
    from ctypes import wintypes

    function = ctypes.WinDLL("kernel32", use_last_error=True).GetShortPathNameW
    function.argtypes = (wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD)
    function.restype = wintypes.DWORD
    buffer = ctypes.create_unicode_buffer(32768)
    size = function(str(path), buffer, len(buffer))
    assert 0 < size < len(buffer), "owned fixture path cannot be resolved"
    return buffer.value


def build(project, output, operation="build_wheel"):
    output.mkdir(exist_ok=True)
    return run(
        [sys.executable, "-I", "-c",
         f"from setuptools.build_meta import {operation}; {operation}({build_path(output)!r})"],
        cwd=build_path(project),
    )


@pytest.fixture(scope="module")
def wheel(tmp_path_factory):
    root = tmp_path_factory.mktemp("client-wheel")
    project, library = project_at(root)
    output = root / "artifacts"
    result = build(project, output)
    assert result.returncode == 0, result.stderr
    return next(output.glob("*.whl")), library


def test_wheel_contains_record_bound_exact_primitive_and_no_runtime_dependencies(wheel):
    artifact, library = wheel
    with zipfile.ZipFile(artifact) as archive:
        assert {
            f"fleet_manager/gateway_{name}.py"
            for name in ("auth", "broker", "client", "contract", "controller", "index", "server")
        } <= set(archive.namelist())
        member = "fleet_manager/_versioned_runtime.py"
        assert archive.namelist().count(member) == 1
        data = archive.read(member)
        assert data == (library / "versioned_runtime.py").read_bytes()
        name = next(name for name in archive.namelist() if name.endswith(".dist-info/RECORD"))
        rows = list(csv.reader(io.StringIO(archive.read(name).decode())))
        for member_source in (
            "fleet_manager/client_command_removal.py", "fleet_manager/client_deactivation.py",
            "fleet_manager/client_runtime_removal.py",
        ):
            removal_source = (Path(__file__).parents[1] / "src" / member_source).read_bytes()
            assert archive.read(member_source) == removal_source
            removal_digest = "sha256=" + base64.urlsafe_b64encode(
                hashlib.sha256(removal_source).digest(),
            ).rstrip(b"=").decode()
            assert [row for row in rows if row[0] == member_source] == [
                [member_source, removal_digest, str(len(removal_source))],
            ]
        expected = "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        assert [row for row in rows if row[0] == member] == [[member, expected, str(len(data))]]
        for name in COMPANIONS:
            member = f"fleet_manager/{name}"
            content = (library / name).read_bytes()
            assert archive.namelist().count(member) == 1
            assert archive.read(member) == content
            digest = "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=").decode()
            assert [row for row in rows if row[0] == member] == [[member, digest, str(len(content))]]
        for name, source in PROCESS_RESOURCES.items():
            member = f"fleet_manager/{name}"
            content = (library.parent / source).read_bytes()
            assert archive.namelist().count(member) == 1
            assert archive.read(member) == content
            digest = "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=").decode()
            assert [row for row in rows if row[0] == member] == [[member, digest, str(len(content))]]
        metadata = next(name for name in archive.namelist() if name.endswith(".dist-info/METADATA"))
        requirements = [
            Requirement(line.removeprefix("Requires-Dist:").strip())
            for line in archive.read(metadata).decode().splitlines()
            if line.startswith("Requires-Dist:")
        ]
        assert all(requirement.marker is not None
                   and not requirement.marker.evaluate({"extra": ""})
                   and any(requirement.marker.evaluate({"extra": extra})
                           for extra in ("dev", "release-verification", "construction", "auth", "server",
                                         "service-gateway",
                                         "runtime-authentication"))
                   for requirement in requirements)
        verification = [requirement for requirement in requirements
                        if requirement.marker.evaluate({"extra": "release-verification"})]
        assert len(verification) == 1
        assert canonicalize_name(verification[0].name) == "cryptography"
        assert verification[0].specifier == SpecifierSet(">=46,<47")
        construction = [requirement for requirement in requirements
                        if requirement.marker.evaluate({"extra": "construction"})]
        assert len(construction) == 1
        assert canonicalize_name(construction[0].name) == "pip"
        assert construction[0].specifier == SpecifierSet(">=25.1,<26")
        for extra in ("auth", "server"):
            enabled = {canonicalize_name(requirement.name) for requirement in requirements
                       if requirement.marker.evaluate({"extra": extra})}
            assert enabled == {"pyjwt", "cryptography", "jwcrypto", "joserfc"}
        identity = [requirement for requirement in requirements
                    if requirement.marker.evaluate({"extra": "runtime-authentication"})]
        assert len(identity) == 1
        assert canonicalize_name(identity[0].name) == "joserfc"
        assert identity[0].specifier == SpecifierSet(">=1.7.5,<2")
        assert not any(name.endswith("/_standalone_runtime_build.py") for name in archive.namelist())


def test_sdist_rebuild_is_self_contained_after_repository_removal(tmp_path):
    project, library = project_at(tmp_path / "checkout")
    output = tmp_path / "artifacts"
    result = build(project, output, "build_sdist")
    assert result.returncode == 0, result.stderr
    expected = (library / "versioned_runtime.py").read_bytes()
    companions = {name: (library / name).read_bytes() for name in COMPANIONS}
    companions.update({name: (library.parent / path).read_bytes() for name, path in PROCESS_RESOURCES.items()})
    helper = (library / "standalone_build.py").read_bytes()
    extracted = tmp_path / "extracted"
    extracted.mkdir()
    with tarfile.open(next(output.glob("*.tar.gz"))) as archive:
        assert archive.extractfile(next(
            name for name in archive.getnames() if name.endswith("/_standalone_runtime_build.py")
        )).read() == helper
        for name, content in companions.items():
            assert archive.extractfile(next(
                member for member in archive.getnames()
                if member.endswith(f"/src/fleet_manager/{name}")
            )).read() == content
        if "filter" in inspect.signature(archive.extractall).parameters:
            archive.extractall(extracted, filter="data")
        else:
            for member in archive.getmembers():
                target = extracted / member.name
                assert target.resolve().is_relative_to(extracted.resolve())
                assert member.isdir() or member.isfile()
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with target.open("wb") as stream:
                        shutil.copyfileobj(archive.extractfile(member), stream)
    shutil.rmtree(project.parent)
    standalone = next(extracted.iterdir())
    result = build(standalone, output)
    assert result.returncode == 0, result.stderr
    with zipfile.ZipFile(next(output.glob("*.whl"))) as archive:
        assert archive.read("fleet_manager/_versioned_runtime.py") == expected
        assert "fleet_manager/client_command_removal.py" in archive.namelist()
        assert "fleet_manager/client_deactivation.py" in archive.namelist()
        assert "fleet_manager/client_runtime_removal.py" in archive.namelist()
        for name, content in companions.items():
            assert archive.read(f"fleet_manager/{name}") == content


@pytest.mark.client_distribution_full
def test_nested_service_directory_uses_same_canonical_resource(tmp_path):
    project, library = project_at(tmp_path, nested=True)
    output = tmp_path / "artifacts"
    result = build(project, output)
    assert result.returncode == 0, result.stderr
    with zipfile.ZipFile(next(output.glob("*.whl"))) as archive:
        assert archive.read("fleet_manager/_versioned_runtime.py") == (
            library / "versioned_runtime.py"
        ).read_bytes()
        for name in COMPANIONS:
            assert archive.read(f"fleet_manager/{name}") == (library / name).read_bytes()


@pytest.mark.client_distribution_full
@pytest.mark.parametrize("resource", [
    "versioned_runtime.py", "standalone_build.py", *COMPANIONS, *PROCESS_RESOURCES,
])
@pytest.mark.parametrize("included_copy", [False, True])
def test_missing_build_resource_is_not_a_successful_partial_wheel(tmp_path, resource, included_copy):
    project, library = project_at(tmp_path)
    source = (library.parent / PROCESS_RESOURCES[resource]
              if resource in PROCESS_RESOURCES else library / resource)
    if included_copy:
        name = "_versioned_runtime.py" if resource == "versioned_runtime.py" else resource
        included = (project / "_standalone_runtime_build.py" if resource == "standalone_build.py"
                    else project / "src" / "fleet_manager" / name)
        shutil.copy2(source, included)
    source.unlink()
    result = build(project, tmp_path / "artifacts")
    assert result.returncode != 0
    assert not list((tmp_path / "artifacts").glob("*.whl"))


@pytest.mark.client_distribution_full
@pytest.mark.parametrize("resource", ["primitive", "helper"])
def test_conflicting_sdist_resource_is_refused(tmp_path, resource):
    project, _ = project_at(tmp_path)
    included = (project / "src" / "fleet_manager" / "_versioned_runtime.py"
                if resource == "primitive" else project / "_standalone_runtime_build.py")
    included.write_text("conflicting resource\n", encoding="utf-8")
    result = build(project, tmp_path / "artifacts")
    assert result.returncode != 0
    assert "differs from the canonical source" in result.stderr
    assert not list((tmp_path / "artifacts").glob("*.whl"))


def test_linked_helper_ancestor_is_rejected_before_loading_code(tmp_path):
    project, library = project_at(tmp_path)
    relocated = tmp_path / "external-resources"
    library.rename(relocated)
    signal = tmp_path / "helper-was-executed"
    (relocated / "standalone_build.py").write_text(
        f"from pathlib import Path\nPath({str(signal)!r}).write_text('executed')\n",
        encoding="utf-8",
    )
    if os.name == "nt":
        result = run([os.environ["COMSPEC"], "/d", "/c", "mklink", "/J",
                      str(library), str(relocated)])
        assert result.returncode == 0, result.stderr
    else:
        library.symlink_to(relocated, target_is_directory=True)
    try:
        result = build(project, tmp_path / "artifacts")
        assert result.returncode != 0
        assert "must not be linked" in result.stderr
        assert not signal.exists()
    finally:
        if os.name == "nt":
            os.rmdir(library)
        else:
            library.unlink()


def test_real_client_install_doctor_and_package_manager_removal_preserve_config(tmp_path, wheel):
    artifact, _ = wheel
    uv = shutil.which("uv")
    assert uv, "native client installation proof requires the runner's uv"
    environment = tmp_path / "client-runtime"
    state = tmp_path / "client-state"
    state.mkdir()
    config = state / "connection.json"
    content = b'{"endpoint":"https://controller.invalid","credential_provider":"external"}\n'
    config.write_bytes(content)
    clean = dict(os.environ)
    clean.pop("PYTHONPATH", None)
    clean["UV_OFFLINE"] = "1"
    result = run([uv, "pip", "install", "--python", sys.executable,
                  "--target", str(environment), "--no-index",
                  "--no-deps", "--link-mode", "copy", str(artifact)], env=clean)
    assert result.returncode == 0, result.stderr

    def client(*args, target=environment):
        # -S excludes the runner's installed fleet package; only this target is evidence.
        return run([
            sys.executable, "-I", "-S", "-B", "-c",
            ("import sys, importlib.util; sys.path.insert(0, sys.argv.pop(1)); "
            "assert importlib.util.find_spec('joserfc') is None; "
            "from importlib.metadata import distribution; "
            "entry = next(e for e in distribution('fleet-manager').entry_points "
            "if e.group == 'console_scripts' and e.name == 'fleet-manager'); "
             "raise SystemExit(entry.load()())"),
            str(target), *args,
        ], env=clean)

    before = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))
    result = client("doctor")
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["role"] == "client" and report["status"] == "installed"
    assert report["evidence"] == "local-installed-resource-record"
    assert report["controller_readiness_verified"] is False
    assert report["mutations_performed"] is False
    assert sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*")) == before
    result = client("--version")
    assert result.returncode == 0 and result.stdout.startswith("fleet-manager ")
    for role in ("gateway", "node"):
        result = client("doctor", "--role", role)
        assert result.returncode == 1
    linked = tmp_path / "linked-client-runtime"
    if os.name == "nt":
        result = run([os.environ["COMSPEC"], "/d", "/c", "mklink", "/J",
                      str(linked), str(environment)])
        assert result.returncode == 0, result.stderr
    else:
        linked.symlink_to(environment, target_is_directory=True)
    try:
        result = client("doctor", target=linked)
        assert result.returncode == 1
        assert "must not be linked" in result.stderr
    finally:
        if os.name == "nt":
            os.rmdir(linked)
        else:
            linked.unlink()
    package = environment / "fleet_manager"
    primitive = package / "_versioned_runtime.py"
    original = primitive.read_bytes()
    primitive.write_bytes(original + b"\n")
    result = client("doctor")
    assert result.returncode == 1
    assert "differs from its installed RECORD" in result.stderr
    primitive.write_bytes(original)
    result = run([uv, "pip", "uninstall", "--python", sys.executable,
                  "--target", str(environment), "fleet-manager"], env=clean)
    assert result.returncode == 0, result.stderr
    assert config.read_bytes() == content
    assert not package.exists()
    assert not list(environment.glob("fleet_manager-*.dist-info"))
    result = client("--version")
    assert result.returncode != 0
    assert not list(state.glob("*komodo*"))


@pytest.mark.parametrize("context_kind", ["repository", "pinned"])
def test_gateway_build_inputs_deliver_the_complete_runtime_family(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, context_kind: str,
) -> None:
    repository = PROJECT.parent
    recipe = repository / "fleet-manager" / "containers" / "gateway"
    if context_kind == "repository":
        project, library = project_at(tmp_path / "context")
    else:
        path = repository / "tools" / "clean-room" / "gateway-role-tunnels" / "source.py"
        spec = importlib.util.spec_from_file_location("gateway_build_source", path)
        assert spec and spec.loader
        source = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(source)
        checkout = tmp_path / "checkout"
        for name in source.REQUIRED:
            target = checkout / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(repository / name, target)
        hooks = tmp_path / "empty-hooks"
        hooks.mkdir()
        environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)

        def git(root: Path, *arguments: str) -> bytes:
            return subprocess.run(
                ["git", "-C", str(root), "-c", "core.autocrlf=false",
                 "-c", f"core.hooksPath={hooks}", "-c", "commit.gpgsign=false",
                 "-c", "user.name=Gateway test", "-c", "user.email=gateway-test@example.invalid",
                 *arguments],
                env=environment, capture_output=True, timeout=30, check=True, **FLAGS,
            ).stdout

        git(checkout, "init", "-q", "--template", str(hooks))
        (checkout / ".git" / "info").mkdir(exist_ok=True)
        (checkout / ".git" / "info" / "attributes").write_text(
            "* -text -filter -ident -working-tree-encoding\n", encoding="utf-8",
        )
        git(checkout, "add", "--all")
        git(checkout, "commit", "-qm", "Gateway build input fixture")
        run_root = tmp_path / "pinned"
        run_root.mkdir()
        monkeypatch.setattr(source, "git", git)
        manifest = source.materialize(checkout, "HEAD", run_root)
        assert manifest["source_commit"] == git(checkout, "rev-parse", "HEAD").decode().strip()
        context = run_root / "context"
        project, library = context / "fleet-manager", context / "libs" / "versioned-runtime"
        recipe = context / "fleet-manager" / "containers" / "gateway"

    copied = set()
    for line in (recipe / "Dockerfile").read_text(encoding="utf-8").replace("\\\n", " ").splitlines():
        if not line.startswith("COPY "):
            continue
        words = shlex.split(line)
        copied.update(name for name in words[1:-1] if name.startswith("libs/"))
    patterns = (recipe / "Dockerfile.dockerignore").read_text(encoding="utf-8").splitlines()
    resources = {
        f"libs/versioned-runtime/{resource.name}": resource
        for resource in library.iterdir()
    }
    resources.update({
        f"libs/{relative.as_posix()}": library.parent / relative
        for relative in PROCESS_RESOURCES.values()
    })
    for name, resource in resources.items():
        included = True
        for pattern in patterns:
            if pattern and not pattern.startswith("#"):
                allow = pattern.startswith("!")
                if fnmatch.fnmatchcase(name, pattern.removeprefix("!").rstrip("/")):
                    included = allow
        if name not in copied or not included:
            resource.unlink()

    output = tmp_path / "artifacts"
    result = build(project, output)
    assert result.returncode == 0, result.stderr
    spec = importlib.util.spec_from_file_location("gateway_build_runtime", LIBRARY / "standalone_build.py")
    assert spec and spec.loader
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    with zipfile.ZipFile(next(output.glob("*.whl"))) as artifact:
        for name, member in runtime.RUNTIME_RESOURCES.items():
            assert artifact.read(f"fleet_manager/{member}") == (LIBRARY / name).read_bytes()
