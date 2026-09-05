"""Immutable managed companion runtime materialization."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

from agent_dispatch.managed_runtime import (
    RECEIPT_NAME,
    ManagedRuntimeError,
    ManagedRuntimeMaterializer,
    ManagedRuntimePolicy,
    _python_path,
    _subprocess_environment,
)


def _project(root: Path, *, content: str = "VALUE = 1\n") -> Path:
    project = root / "plugin"
    package = project / "example_service"
    package.mkdir(parents=True)
    (project / "pyproject.toml").write_text(
        "[project]\nname='example-service'\nversion='1.0.0'\n",
        encoding="utf-8",
    )
    (package / "__init__.py").write_text(content, encoding="utf-8")
    return project


def _registration(plugin_root: Path) -> dict:
    managed = {
        "schema_version": 1,
        "runtimes": [
            {
                "name": "service",
                "version": "2.0.0",
                "profile": "host",
                "python_env": "EXAMPLE_MANAGED_PYTHON",
                "projects": [{"path": ".", "extras": ["service"]}],
                "imports": ["example_service"],
            }
        ],
    }
    source_path = str(plugin_root / "registrar" / "service.json")
    return {
        "id": "declared:plugin@example:service",
        "logical_id": "service",
        "kind": "plugin-companion",
        "source": "declared",
        "owner": "plugin@example",
        "spec": {
            "command": ["bin/serve"],
            "managed_runtime": managed,
        },
        "plugin": {
            "root": str(plugin_root),
            "source_path": source_path,
            "version": "2.0.0",
            "activation_scopes": ["global"],
        },
        "runtime_revision": {
            "plugin_root": str(plugin_root),
            "plugin_owner": "plugin@example",
            "plugin_source_path": source_path,
            "plugin_version": "2.0.0",
            "activation_scopes": ["global"],
            "managed_runtime": managed,
        },
    }


class FakeRunner:
    def __init__(self, *, windows: bool = False):
        self.windows = windows
        self.calls: list[tuple[list[str], Path | None, dict[str, str]]] = []
        self.install_count = 0
        self.fail_install = False
        self.install_started: threading.Event | None = None
        self.install_release: threading.Event | None = None
        self.after_install = None
        self.mutate_install_source = False
        self.mutate_validation = False

    def __call__(self, argv, cwd, environment):
        args = list(argv)
        self.calls.append((args, cwd, dict(environment)))
        if args[1:5] == ["-I", "-m", "venv", "--copies"]:
            environment_root = Path(args[5])
            python = _python_path(environment_root, windows=self.windows)
            python.parent.mkdir(parents=True)
            python.write_bytes(b"python")
            return
        if args[1:3] == ["pip", "install"]:
            self.install_count += 1
            if self.install_started is not None:
                self.install_started.set()
            if self.install_release is not None:
                assert self.install_release.wait(timeout=5)
            if self.fail_install:
                raise ManagedRuntimeError("install failed")
            if self.mutate_install_source:
                source = Path(args[-1].split("[", 1)[0])
                (source / "build").mkdir()
                (source / "build" / "artifact.txt").write_text(
                    "build output", encoding="utf-8"
                )
            if self.after_install is not None:
                self.after_install(Path(args[args.index("--python") + 1]).parent)
            return
        if args[1:3] == ["-I", "-c"] and self.mutate_validation:
            Path(args[0]).write_bytes(b"mutated by import")


def _policy(tmp_path: Path, *, windows: bool = False) -> ManagedRuntimePolicy:
    tools = tmp_path / "tools"
    tools.mkdir()
    python = tools / ("python.exe" if windows else "python")
    uv = tools / ("uv.exe" if windows else "uv")
    python.write_bytes(b"base-python")
    uv.write_bytes(b"uv")
    runtime_paths: tuple[Path, ...] = ()
    if windows:
        versioned_dll = (
            tools / f"python{sys.version_info.major}{sys.version_info.minor}.dll"
        )
        versioned_dll.write_bytes(b"python-dll")
        standard_library = tools / "Lib"
        standard_library.mkdir()
        (standard_library / "os.py").write_text("# stdlib\n", encoding="utf-8")
    else:
        standard_library = tools / "lib" / "python"
        standard_library.mkdir(parents=True)
        (standard_library / "os.py").write_text("# stdlib\n", encoding="utf-8")
        runtime_paths = (standard_library,)
    return ManagedRuntimePolicy(
        root=tmp_path / "runtimes",
        base_python=python,
        package_manager=uv,
        windows=windows,
        environment={"PATH": str(tools), "PYTHONNOUSERSITE": "1"},
        base_runtime_paths=runtime_paths,
    )


def test_materializes_snapshot_and_reuses_valid_cell(tmp_path):
    plugin = _project(tmp_path)
    runner = FakeRunner()
    materializer = ManagedRuntimeMaterializer(_policy(tmp_path), runner=runner)

    first = materializer.materialize(_registration(plugin))[0]
    second = materializer.materialize(_registration(plugin))[0]

    assert first == second
    assert first.cell.is_dir()
    assert first.receipt == first.cell / RECEIPT_NAME
    assert first.python.is_file()
    assert runner.install_count == 1
    receipt = json.loads(first.receipt.read_text(encoding="utf-8"))
    assert receipt["content_digest"] == first.content_digest
    assert receipt["snapshot"]["projects"] == [
        {"path": ".", "extras": ["service"]}
    ]
    assert {
        entry["path"]
        for entry in receipt["snapshot"]["files"]
        if entry["type"] == "file"
    } == {
        "projects/000/example_service/__init__.py",
        "projects/000/pyproject.toml",
    }
    assert (
        first.cell / "snapshot" / "projects" / "000" / "example_service" / "__init__.py"
    ).read_text(encoding="utf-8") == "VALUE = 1\n"


def test_changed_content_publishes_a_new_digest_cell(tmp_path):
    plugin = _project(tmp_path)
    runner = FakeRunner()
    materializer = ManagedRuntimeMaterializer(_policy(tmp_path), runner=runner)
    first = materializer.materialize(_registration(plugin))[0]

    (plugin / "example_service" / "__init__.py").write_text(
        "VALUE = 2\n", encoding="utf-8"
    )
    second = materializer.materialize(_registration(plugin))[0]

    assert second.content_digest != first.content_digest
    assert second.cell != first.cell
    assert first.cell.is_dir()
    assert second.cell.is_dir()
    assert runner.install_count == 2


def test_empty_directory_changes_snapshot_digest(tmp_path):
    plugin = _project(tmp_path)
    runner = FakeRunner()
    materializer = ManagedRuntimeMaterializer(_policy(tmp_path), runner=runner)
    first = materializer.materialize(_registration(plugin))[0]
    (plugin / "empty_namespace").mkdir()

    second = materializer.materialize(_registration(plugin))[0]

    assert first.content_digest != second.content_digest


def test_changed_authority_publishes_alongside_prior_cell(tmp_path):
    plugin = _project(tmp_path)
    runner = FakeRunner()
    materializer = ManagedRuntimeMaterializer(_policy(tmp_path), runner=runner)
    first_registration = _registration(plugin)
    first = materializer.materialize(first_registration)[0]
    second_registration = _registration(plugin)
    second_registration["plugin"]["version"] = "2.0.1"
    second_registration["runtime_revision"]["plugin_version"] = "2.0.1"

    second = materializer.materialize(second_registration)[0]

    assert first.content_digest == second.content_digest
    assert first.cell != second.cell
    assert first.cell.is_dir()
    assert second.cell.is_dir()
    assert runner.install_count == 2


def test_changed_toolchain_bytes_publish_alongside_prior_cell(tmp_path):
    plugin = _project(tmp_path)
    policy = _policy(tmp_path)
    runner = FakeRunner()
    materializer = ManagedRuntimeMaterializer(policy, runner=runner)
    first = materializer.materialize(_registration(plugin))[0]
    original = policy.package_manager.stat()
    policy.package_manager.write_bytes(b"UV")
    os.utime(
        policy.package_manager,
        ns=(original.st_atime_ns, original.st_mtime_ns),
    )
    assert policy.package_manager.stat().st_size == original.st_size

    second = materializer.materialize(_registration(plugin))[0]

    assert first.content_digest == second.content_digest
    assert first.cell != second.cell
    assert first.cell.is_dir()
    assert second.cell.is_dir()


def test_posix_base_runtime_change_publishes_new_generation(tmp_path):
    plugin = _project(tmp_path)
    policy = _policy(tmp_path)
    runner = FakeRunner()
    materializer = ManagedRuntimeMaterializer(policy, runner=runner)
    first = materializer.materialize(_registration(plugin))[0]
    (policy.base_runtime_paths[0] / "os.py").write_text(
        "# changed stdlib\n", encoding="utf-8"
    )

    second = materializer.materialize(_registration(plugin))[0]

    assert first.content_digest == second.content_digest
    assert first.cell != second.cell


def test_failed_install_preserves_published_cell_and_cleans_staging(tmp_path):
    plugin = _project(tmp_path)
    runner = FakeRunner()
    materializer = ManagedRuntimeMaterializer(_policy(tmp_path), runner=runner)
    published = materializer.materialize(_registration(plugin))[0]
    (plugin / "example_service" / "__init__.py").write_text(
        "VALUE = 2\n", encoding="utf-8"
    )
    runner.fail_install = True

    with pytest.raises(ManagedRuntimeError, match="install failed"):
        materializer.materialize(_registration(plugin))

    assert published.cell.is_dir()
    assert list((tmp_path / "runtimes" / ".staging").iterdir()) == []


def test_installer_mutation_is_confined_to_disposable_working_copy(tmp_path):
    plugin = _project(tmp_path)
    runner = FakeRunner()
    runner.mutate_install_source = True
    result = ManagedRuntimeMaterializer(
        _policy(tmp_path), runner=runner
    ).materialize(_registration(plugin))[0]

    assert not (result.cell / "snapshot" / "projects" / "000" / "build").exists()
    assert not (result.cell / "build-inputs").exists()


def test_import_validation_must_not_modify_staged_cell(tmp_path):
    plugin = _project(tmp_path)
    runner = FakeRunner()
    runner.mutate_validation = True

    with pytest.raises(ManagedRuntimeError, match="validation modified"):
        ManagedRuntimeMaterializer(
            _policy(tmp_path), runner=runner
        ).materialize(_registration(plugin))


def test_incomplete_existing_cell_is_never_repaired_in_place(tmp_path):
    plugin = _project(tmp_path)
    runner = FakeRunner()
    materializer = ManagedRuntimeMaterializer(_policy(tmp_path), runner=runner)
    published = materializer.materialize(_registration(plugin))[0]
    published.receipt.unlink()

    with pytest.raises(ManagedRuntimeError, match="incomplete or invalid"):
        materializer.materialize(_registration(plugin))

    assert runner.install_count == 1


def test_rejects_unattributed_or_inconsistent_registration_before_writes(tmp_path):
    plugin = _project(tmp_path)
    policy = _policy(tmp_path)
    registration = _registration(plugin)
    registration["source"] = "direct"

    with pytest.raises(ManagedRuntimeError, match="attributed plugin declaration"):
        ManagedRuntimeMaterializer(policy, runner=FakeRunner()).materialize(registration)

    registration = _registration(plugin)
    registration["runtime_revision"]["plugin_version"] = "other"
    with pytest.raises(ManagedRuntimeError, match="provenance"):
        ManagedRuntimeMaterializer(policy, runner=FakeRunner()).materialize(registration)

    assert not policy.root.exists()


def test_rejects_linked_project_content(tmp_path):
    plugin = _project(tmp_path)
    target = tmp_path / "outside.py"
    target.write_text("SECRET = True\n", encoding="utf-8")
    link = plugin / "example_service" / "linked.py"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable")

    with pytest.raises(ManagedRuntimeError, match="link or reparse point"):
        ManagedRuntimeMaterializer(
            _policy(tmp_path), runner=FakeRunner()
        ).materialize(_registration(plugin))


def test_rejects_linked_publication_descendant(tmp_path):
    plugin = _project(tmp_path)
    policy = _policy(tmp_path)
    policy.root.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    try:
        (policy.root / "cells").symlink_to(external, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory links are unavailable")

    with pytest.raises(ManagedRuntimeError, match="link or reparse point"):
        ManagedRuntimeMaterializer(policy, runner=FakeRunner()).materialize(
            _registration(plugin)
        )

    assert list(external.iterdir()) == []


def test_rejects_linked_python_inside_reused_cell(tmp_path):
    plugin = _project(tmp_path)
    policy = _policy(tmp_path)
    materializer = ManagedRuntimeMaterializer(policy, runner=FakeRunner())
    published = materializer.materialize(_registration(plugin))[0]
    external = tmp_path / "external-python"
    external.write_bytes(b"python")
    published.python.unlink()
    try:
        published.python.symlink_to(external)
    except (OSError, NotImplementedError):
        pytest.skip("file links are unavailable")

    with pytest.raises(ManagedRuntimeError, match="link or reparse point"):
        materializer.materialize(_registration(plugin))


def test_rejects_modified_regular_file_inside_reused_cell(tmp_path):
    plugin = _project(tmp_path)
    policy = _policy(tmp_path)
    materializer = ManagedRuntimeMaterializer(policy, runner=FakeRunner())
    published = materializer.materialize(_registration(plugin))[0]
    published.python.write_bytes(b"modified")

    with pytest.raises(ManagedRuntimeError, match="incomplete or invalid"):
        materializer.materialize(_registration(plugin))


def test_cell_layout_stays_shallow_for_windows_paths(tmp_path):
    plugin = _project(tmp_path)
    result = ManagedRuntimeMaterializer(
        _policy(tmp_path, windows=True),
        runner=FakeRunner(windows=True),
        trust_verifier=lambda _path: True,
    ).materialize(_registration(plugin))[0]

    relative = result.cell.relative_to(tmp_path / "runtimes")
    assert len(relative.parts) == 3
    assert len(str(relative)) < 80


def test_root_lock_serializes_concurrent_builders(tmp_path):
    plugin = _project(tmp_path)
    policy = _policy(tmp_path)
    runner = FakeRunner()
    runner.install_started = threading.Event()
    runner.install_release = threading.Event()
    materializer_a = ManagedRuntimeMaterializer(policy, runner=runner)
    materializer_b = ManagedRuntimeMaterializer(policy, runner=runner)
    results = []
    errors = []

    def run(materializer):
        try:
            results.append(materializer.materialize(_registration(plugin))[0])
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    first = threading.Thread(target=run, args=(materializer_a,))
    second = threading.Thread(target=run, args=(materializer_b,))
    first.start()
    assert runner.install_started.wait(timeout=5)
    second.start()
    time.sleep(0.2)
    assert runner.install_count == 1
    runner.install_release.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert errors == []
    assert len(results) == 2
    assert results[0].cell == results[1].cell
    assert runner.install_count == 1


def test_stale_lock_file_does_not_block_materialization(tmp_path):
    plugin = _project(tmp_path)
    policy = _policy(tmp_path)
    policy.root.mkdir()
    (policy.root / ".materialize.lock").write_text("999999", encoding="ascii")

    result = ManagedRuntimeMaterializer(
        policy, runner=FakeRunner()
    ).materialize(_registration(plugin))[0]

    assert result.cell.is_dir()


def test_concurrent_root_creation_is_idempotent(tmp_path):
    plugin = _project(tmp_path)
    policy = _policy(tmp_path)
    runner = FakeRunner()
    runner.install_started = threading.Event()
    runner.install_release = threading.Event()
    results = []

    def materialize():
        results.append(
            ManagedRuntimeMaterializer(policy, runner=runner).materialize(
                _registration(plugin)
            )[0]
        )

    first = threading.Thread(target=materialize)
    second = threading.Thread(target=materialize)
    first.start()
    second.start()
    assert runner.install_started.wait(timeout=5)
    runner.install_release.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert len(results) == 2
    assert results[0].cell == results[1].cell


def test_build_environment_drops_ambient_package_authority(tmp_path, monkeypatch):
    monkeypatch.setenv("PIP_INDEX_URL", "https://secret.example")
    monkeypatch.setenv("UV_INDEX_URL", "https://secret.example")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "injected"))
    monkeypatch.setenv("AGENT_DISPATCH_TOKEN", "secret")
    python = tmp_path / "python"
    uv = tmp_path / "uv"

    environment = _subprocess_environment(base_python=python, package_manager=uv)

    assert "PIP_INDEX_URL" not in environment
    assert "UV_INDEX_URL" not in environment
    assert "PYTHONPATH" not in environment
    assert "AGENT_DISPATCH_TOKEN" not in environment
    assert environment["PIP_CONFIG_FILE"]
    assert environment["UV_NO_CONFIG"] == "1"


def test_windows_verifies_base_and_copied_python_before_install(tmp_path):
    plugin = _project(tmp_path)
    policy = _policy(tmp_path, windows=True)
    runner = FakeRunner(windows=True)
    verified: list[Path] = []

    def verify(path: Path) -> bool:
        verified.append(path)
        return True

    result = ManagedRuntimeMaterializer(
        policy, runner=runner, trust_verifier=verify
    ).materialize(_registration(plugin))[0]

    install_call = next(call for call in runner.calls if call[0][1:3] == ["pip", "install"])
    assert verified[0] == policy.base_python
    assert any(".staging" in str(path) for path in verified[1:])
    assert result.python in verified
    assert runner.calls.index(install_call) == 0


def test_windows_untrusted_base_prevents_environment_creation(tmp_path):
    plugin = _project(tmp_path)
    runner = FakeRunner(windows=True)

    with pytest.raises(ManagedRuntimeError, match="untrusted Windows base Python"):
        ManagedRuntimeMaterializer(
            _policy(tmp_path, windows=True),
            runner=runner,
            trust_verifier=lambda _path: False,
        ).materialize(_registration(plugin))

    assert runner.calls == []


def test_windows_does_not_require_signatures_for_installed_launchers(tmp_path):
    plugin = _project(tmp_path)
    policy = _policy(tmp_path, windows=True)
    runner = FakeRunner(windows=True)

    def add_unsigned_launcher(runtime_root: Path) -> None:
        scripts = runtime_root / "Scripts"
        scripts.mkdir()
        (scripts / "example.exe").write_bytes(b"unsigned launcher")

    runner.after_install = add_unsigned_launcher

    def verify(path: Path) -> bool:
        return path.name != "example.exe"

    result = ManagedRuntimeMaterializer(
        policy,
        runner=runner,
        trust_verifier=verify,
    ).materialize(_registration(plugin))[0]

    assert (result.cell / "runtime" / "Scripts" / "example.exe").is_file()


def test_windows_trust_manifest_excludes_uncopied_site_packages(tmp_path):
    plugin = _project(tmp_path)
    policy = _policy(tmp_path, windows=True)
    excluded = policy.base_python.parent / "Lib" / "site-packages" / "tool"
    excluded.mkdir(parents=True)
    (excluded / "launcher.exe").write_bytes(b"unsigned")

    result = ManagedRuntimeMaterializer(
        policy,
        runner=FakeRunner(windows=True),
        trust_verifier=lambda path: path.name != "launcher.exe",
    ).materialize(_registration(plugin))[0]

    assert not (
        result.cell / "runtime" / "Lib" / "site-packages" / "tool" / "launcher.exe"
    ).exists()
