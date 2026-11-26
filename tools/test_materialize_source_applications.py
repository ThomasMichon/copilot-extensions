"""Canonical source application emission, independent of wheels or installation."""
from __future__ import annotations

import json
import importlib
import hashlib
from collections.abc import Callable
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace
from typing import Any
import zipfile

import pytest

import materialize_source_applications as applications
import source_preparation_process as processes
from standalone_python_artifacts import injected_resources
from standalone_build import APPLICATION_SOURCE_FALLBACKS, RUNTIME_RESOURCES

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[1]


def _write(root: Path, name: str, content: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def _seal(root: Path) -> dict[Path, tuple[str, int]]:
    return {
        path.relative_to(root): (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
        for path in root.rglob("*") if path.is_file()
    }


def _run_entry(
    path: Path, *arguments: str, executable: str, shell: str,
    environment: dict[str, str], cwd: Path, timeout: float = 90,
) -> subprocess.CompletedProcess[str]:
    if shell == "cmd":
        command = [executable, "/d", "/c", str(path.with_suffix(".cmd")), *arguments]
    elif shell == "sh":
        command = [executable, str(path.with_suffix(".sh")), *arguments]
    else:
        command = [executable, "-NoProfile", "-NonInteractive", "-File",
                   str(path.with_suffix(".ps1")), *arguments]
    return processes.run(
        command, cwd=cwd, env=environment, text=True, deadline=time.monotonic() + timeout,
    )


@pytest.fixture
def candidate(tmp_path: Path) -> Path:
    root = tmp_path / "candidate"
    project = root / "worktree-manager"
    canonical = ROOT / "libs/versioned-runtime"
    for path in (canonical / "standalone_build.py", *(
        canonical / name for name in RUNTIME_RESOURCES
    )):
        target = root / "libs/versioned-runtime" / path.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    for name in ("runtime_application_binstubs.py", "runtime_application_install.py"):
        path = root / "libs/installer-engine" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / "libs/installer-engine" / name, path)
    for destination, relative, names in APPLICATION_SOURCE_FALLBACKS["worktree_manager"]:
        for name in names:
            target = project / destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / "worktree-manager" / relative / name, target)
    _write(project, "pyproject.toml", (
        '[project]\nname = "copilot-extensions-worktree-manager"\nversion = "1.2.3"\n'
        'requires-python = ">=3.11"\ndependencies = ["external>=1"]\n'
        '[project.scripts]\nworktree-manager = "worktree_manager.cli:main"\n'
        'install-helper = "worktree_manager.cli:main"\n'
        '[dependency-groups]\nfirst-party-core = ["example-core"]\ndev = ["pytest"]\n'
        '[tool.uv.sources]\nexample-core = {path = "libs/example", editable = false}\n'
        'external = {path = "vendor/external-1.0-py3-none-any.whl"}\n'
        '[tool.setuptools.packages.find]\nwhere = ["src"]\n'
        '[tool.setuptools.package-data]\nworktree_manager = ["data/*.toml"]\n'
    ))
    shutil.copyfile(ROOT / "worktree-manager/setup.py", project / "setup.py")
    for name in ("README.md", "LICENSE"):
        _write(project, name, name + "\n")
    _write(project, "src/worktree_manager/__init__.py", "VALUE = 'manager'\n")
    _write(project, "src/worktree_manager/cli.py", (
        "import json, sys\n"
        "def main():\n"
        "    print(json.dumps(sys.argv[1:]))\n"
        "    return 7 if '--exit-seven' in sys.argv else 0\n"
    ))
    _write(project, "src/worktree_manager/data/plugins.toml", "version = 1\n")
    _write(project, "scripts/runtime_entry.py", "VALUE = 'resource'\n")
    for _destination, relative, names in APPLICATION_SOURCE_FALLBACKS["worktree_manager"]:
        for name in names:
            target = project / relative / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / "worktree-manager" / relative / name, target)
    _write(project, "vendor/external-1.0-py3-none-any.whl", "retained vendor archive\n")
    _write(project, "application-source.json", json.dumps({
        "schema": "copilot-extensions.application-source", "version": 1,
        "name": "worktree-manager",
    }))
    _write(project, "libs/example/pyproject.toml", (
        '[project]\nname = "example-core"\nversion = "1.0"\n'
        '[tool.setuptools.packages.find]\nwhere = ["src"]\n'
        '[tool.setuptools.package-data]\nexample_core = ["data/*.txt"]\n'
    ))
    _write(project, "libs/example/src/example_core/__init__.py", "VALUE = 'core'\n")
    _write(project, "libs/example/src/example_core/data/notice.txt", "package data\n")
    _write(project, "libs/example/LICENSE", "core license\n")
    return root


@pytest.mark.parametrize("relative", [False, True], ids=["absolute", "relative"])
def test_source_application_contains_embedded_code_resources_and_notices(
    candidate: Path, monkeypatch: pytest.MonkeyPatch, relative: bool,
) -> None:
    source = candidate / "worktree-manager"
    before = {path.relative_to(source): path.read_bytes() for path in source.rglob("*") if path.is_file()}
    resources = injected_resources("worktree-manager", candidate)
    if relative:
        monkeypatch.chdir(candidate.parent)
    selected = Path(candidate.name) if relative else candidate
    assert all(line.startswith("OK") for line in applications.materialize(selected))
    emitted = candidate / "apps/worktree-manager"
    metadata = tomllib.loads((emitted / "pyproject.toml").read_text(encoding="utf-8"))
    assert metadata["project"]["version"] == "1.2.3"
    assert metadata["project"]["dependencies"] == ["external>=1"]
    assert metadata["dependency-groups"] == {"dev": ["pytest"]}
    assert metadata["tool"]["uv"]["sources"] == {
        "external": {"path": "vendor/external-1.0-py3-none-any.whl"},
    }
    assert (emitted / "vendor/external-1.0-py3-none-any.whl").read_bytes() == before[
        Path("vendor/external-1.0-py3-none-any.whl")
    ]
    assert metadata["tool"]["setuptools"]["package-data"]["example_core"] == [
        "LICENSE", "data/*.txt",
    ]
    assert (emitted / "src/example_core/__init__.py").read_bytes() == before[
        Path("libs/example/src/example_core/__init__.py")
    ]
    assert (emitted / "src/example_core/LICENSE").read_text() == "core license\n"
    assert (emitted / "src/example_core/data/notice.txt").read_text() == "package data\n"
    assert (emitted / "src/worktree_manager/_runtime_assets/scripts/runtime_entry.py").read_text() == (
        "VALUE = 'resource'\n"
    )
    assert (emitted / "scripts/runtime_entry.py").read_text() == "VALUE = 'resource'\n"
    assert not (emitted / "libs").exists()
    assert not (emitted / ".venv").exists()
    assert all((emitted / "src" / name).read_bytes() == path.read_bytes()
               for name, path in resources.items())
    assert before == {
        path.relative_to(source): path.read_bytes() for path in source.rglob("*") if path.is_file()
    }


def _add_context_package(candidate: Path) -> None:
    library = candidate / "worktree-manager/libs/example"
    _write(library, "src/example_context/__init__.py", "VALUE = 'context'\n")
    _write(library, "src/example_context/data/settings.json", '{"value": "context"}\n')
    with (library / "pyproject.toml").open("a", encoding="utf-8") as stream:
        stream.write('example_context = ["data/*.json"]\n')


@pytest.mark.parametrize("sibling", [False, True], ids=["local-library", "canonical-reference"])
def test_source_application_retains_multiple_embedded_packages(
    candidate: Path, sibling: bool,
) -> None:
    _add_context_package(candidate)
    source = candidate / "worktree-manager"
    library = source / "libs/example"
    if sibling:
        destination = candidate / "libs/example"
        shutil.move(library, destination)
        library = destination
        manifest = source / "pyproject.toml"
        manifest.write_text(manifest.read_text().replace(
            'path = "libs/example", editable = false',
            'path = "../libs/example", editable = true',
        ), encoding="utf-8")
        from materialize_main import materialize_uv_editable_ref_into

        report = materialize_uv_editable_ref_into(
            source_consumer_dir=source, dest_consumer_dir=source, canonical_root=candidate,
        )
        assert report and all(line.startswith("OK") for line in report), report
        library = source / "libs/example"
    before = _seal(source)
    library_before = _seal(library)
    report = applications.materialize(candidate)
    assert report and all(line.startswith("OK") for line in report), report
    emitted = candidate / "apps/worktree-manager"
    metadata = tomllib.loads((emitted / "pyproject.toml").read_text(encoding="utf-8"))
    assert metadata["tool"]["setuptools"]["package-data"]["example_context"] == [
        "LICENSE", "data/*.json",
    ]
    for package in ("example_core", "example_context"):
        assert (emitted / "src" / package / "__init__.py").read_bytes() == (
            library / "src" / package / "__init__.py"
        ).read_bytes()
        assert (emitted / "src" / package / "LICENSE").read_bytes() == (
            library / "LICENSE"
        ).read_bytes()
    assert (emitted / "src/example_context/data/settings.json").read_text() == (
        '{"value": "context"}\n'
    )
    assert _seal(source) == before
    assert _seal(library) == library_before


def test_source_application_retains_build_bin_assets_alongside_setup(candidate: Path) -> None:
    source = candidate / "worktree-manager"
    _write(source, "bin/runtime-entry.sh", "#!/bin/sh\nexit 0\n")
    report = applications.materialize(candidate)
    assert report and all(line.startswith("OK") for line in report), report
    emitted = candidate / "apps/worktree-manager"
    assert (emitted / "bin/runtime-entry.sh").read_bytes() == (
        source / "bin/runtime-entry.sh"
    ).read_bytes()
    assert (emitted / "src/worktree_manager/_runtime_assets/bin/runtime-entry.sh").read_bytes() == (
        source / "bin/runtime-entry.sh"
    ).read_bytes()
    assert all((emitted / "bin" / ("setup" + suffix)).is_file() for suffix in (".ps1", ".cmd", ".sh"))


@pytest.mark.parametrize("fault", [
    "declaration", "missing-input", "missing-fallback", "collision", "script-shape",
    "command-setup", "command-license", "command-src", "command-bin", "command-build", "command-dist",
])
def test_source_application_rejects_bad_inputs_before_creating_output(
    candidate: Path, fault: str,
) -> None:
    source = candidate / "worktree-manager"
    if fault == "declaration":
        (source / "application-source.json").write_text("{}")
    elif fault == "missing-input":
        (source / "libs/example/LICENSE").unlink()
    elif fault == "missing-fallback":
        for destination, _relative, names in APPLICATION_SOURCE_FALLBACKS["worktree_manager"]:
            for name in names:
                (source / destination / name).unlink()
        (candidate / "fleet-manager/src/fleet_manager/__init__.py").unlink()
    elif fault == "script-shape":
        manifest = source / "pyproject.toml"
        manifest.write_text(manifest.read_text().replace('"worktree_manager.cli:main"', "true"))
    elif fault.startswith("command-"):
        manifest = source / "pyproject.toml"
        manifest.write_text(manifest.read_text().replace(
            'install-helper = "worktree_manager.cli:main"',
            fault.removeprefix("command-") + ' = "worktree_manager.cli:main"',
        ))
    else:
        _write(source, "src/example_core/__init__.py", "VALUE = 'foreign'\n")
    assert any(line.startswith("SKIP") for line in applications.materialize(candidate))
    assert not (candidate / "apps").exists()


def test_source_application_retains_declared_sibling_resources(
    candidate: Path,
) -> None:
    source = candidate / "worktree-manager"
    for destination, _relative, names in APPLICATION_SOURCE_FALLBACKS["worktree_manager"]:
        for name in names:
            (source / destination / name).unlink()
    before = _seal(source)
    sibling_before = _seal(candidate / "fleet-manager")
    result = applications.materialize(candidate)
    assert result and all(line.startswith("OK") for line in result), result
    emitted = candidate / "apps/worktree-manager"
    for destination, relative, names in APPLICATION_SOURCE_FALLBACKS["worktree_manager"]:
        for name in names:
            expected = (source / relative / name).read_bytes()
            assert (emitted / destination / name).read_bytes() == expected
            assert (
                emitted / "src/worktree_manager/_runtime_assets" / destination / name
            ).read_bytes() == expected
    assert _seal(source) == before
    assert _seal(candidate / "fleet-manager") == sibling_before


def test_source_application_refuses_external_resource_before_output(
    candidate: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import standalone_python_artifacts as artifacts

    _write(candidate.parent, "foreign.py", "VALUE = 'foreign'\n")
    resources = injected_resources("worktree-manager", candidate)
    resources["worktree_manager/foreign.py"] = candidate.parent / "foreign.py"
    monkeypatch.setattr(artifacts, "injected_resources", lambda *_args: resources)
    assert any(line.startswith("SKIP") for line in applications.materialize(candidate))
    assert not (candidate / "apps").exists()


def test_source_application_does_not_replace_an_existing_output(candidate: Path) -> None:
    _write(candidate, "apps/worktree-manager/keep.txt", "keep\n")
    assert any(line.startswith("SKIP") for line in applications.materialize(candidate))
    assert (candidate / "apps/worktree-manager/keep.txt").read_text() == "keep\n"


@pytest.mark.parametrize("name", ["con", "nul", "com1", "lpt0"])
def test_source_commands_refuse_device_names_before_output(
    candidate: Path, monkeypatch: pytest.MonkeyPatch, name: str,
) -> None:
    manifest = candidate / "worktree-manager/pyproject.toml"
    manifest.write_text(manifest.read_text().replace(
        'install-helper = "worktree_manager.cli:main"', name + ' = "worktree_manager.cli:main"',
    ))
    mkdir = Path.mkdir

    def reject_output(path: Path, *args: Any, **kwargs: Any) -> None:
        if path == candidate / "apps/worktree-manager":
            raise AssertionError("a device command reached output creation")
        mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", reject_output)
    assert any(line.startswith("SKIP") for line in applications.materialize(candidate))
    assert not (candidate / "apps").exists()


def test_source_application_association_uses_canonical_identity_and_distinct_local_versions(
    candidate: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import versioned_runtime_source as owner

    assert all(line.startswith("OK") for line in applications.materialize(candidate))
    duplicate = tmp_path / "second-input"
    shutil.copytree(candidate, duplicate)
    versions = []
    for root, commit in ((candidate, "a" * 40), (duplicate, "b" * 40)):
        with monkeypatch.context() as patched:
            patched.setattr(owner, "_runtime", lambda: pytest.fail("release loaded runtime activation"))
            assert all(line.startswith("OK") for line in applications.bind_source(
                root, source_commit=commit, source_tree="c" * 40,
            ))
        application = root / "apps/worktree-manager"
        identity = owner.read_source_identity(application, "1.2.3", plugin="worktree-manager")
        assert identity is not None
        assert identity.source_commit == commit and identity.source_tree == "c" * 40
        versions.append(identity.runtime_version)
        assert tomllib.loads((application / "pyproject.toml").read_text())["project"]["version"] == "1.2.3"
    assert versions[0] != versions[1]


@pytest.mark.parametrize("fault", ["commit", "tree", "version", "name"])
def test_source_application_association_rejects_bad_identity_without_replacing_inputs(
    candidate: Path, fault: str,
) -> None:
    assert all(line.startswith("OK") for line in applications.materialize(candidate))
    target = candidate / "apps/worktree-manager/runtime-source.json"
    manifest = target.with_name("pyproject.toml")
    if fault == "version":
        manifest.write_text(manifest.read_text().replace('version = "1.2.3"', "version = 3"))
    elif fault == "name":
        manifest.write_text(manifest.read_text().replace(
            'name = "copilot-extensions-worktree-manager"', 'name = "other-project"',
        ))
    assert any(line.startswith("SKIP") for line in applications.bind_source(
        candidate, source_commit="not-a-commit" if fault == "commit" else "a" * 40,
        source_tree="not-a-tree" if fault == "tree" else "c" * 40,
    ))
    assert not target.exists()
    target.write_bytes(b"existing\n")
    assert any(line.startswith("SKIP") for line in applications.bind_source(
        candidate, source_commit="a" * 40, source_tree="c" * 40,
    ))
    assert target.read_bytes() == b"existing\n"


def test_promotion_passes_its_selected_git_identity_to_the_source_application(
    candidate: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import promote_release as promotion
    import promotion_control
    import versioned_runtime_source as owner

    reader = SimpleNamespace(read_changefiles=lambda: [])
    accumulator = SimpleNamespace(compute=lambda _grouped: {})
    materializer = SimpleNamespace(
        materialize=lambda root, **_kwargs: applications.materialize(root),
    )
    modules = {
        "promote_changefile": reader, "promote_accumulate_bumps": accumulator,
        "promote_materialize_main": materializer,
    }
    monkeypatch.setattr(promotion, "_load_module", lambda _path, name: modules[name])
    import promotion_versions
    monkeypatch.setattr(promotion_versions, "seed_versions", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(promotion, "_sync_instruction_projections", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(promotion_control, "preserve_control", lambda *_args, **_kwargs: None)
    calls = []

    def git(arguments: list[str], **_kwargs: object) -> str:
        calls.append(arguments)
        assert arguments == ["rev-parse", "HEAD", "HEAD^{tree}"]
        return "a" * 40 + "\n" + "b" * 40

    monkeypatch.setattr(promotion, "_git", git)
    result = promotion.consume_pending_changes(candidate, main_head="c" * 40, repo=candidate)
    assert all(line.startswith("OK") for line in result["materialize_log"])
    identity = owner.read_source_identity(
        candidate / "apps/worktree-manager", "1.2.3", plugin="worktree-manager",
    )
    assert identity is not None and identity.source_commit == "a" * 40
    assert identity.source_tree == "b" * 40
    assert len(calls) == 1


def test_source_association_survives_actual_git_archive_and_checkout(
    candidate: Path, tmp_path: Path,
) -> None:
    import prepare_runtime_source as preparation
    import promote_release as promotion
    from promotion_source_archive import composition_budget
    import versioned_runtime_source as owner

    shutil.copyfile(ROOT / ".gitattributes", candidate / ".gitattributes")
    shutil.copyfile(ROOT / ".gitignore", candidate / ".gitignore")
    _write(
        candidate / "worktree-manager", "src/worktree_manager/production_picker/_build_info.py",
        "VALUE = 'required-source-shim'\n",
    )
    assert all(line.startswith("OK") for line in applications.materialize(candidate))
    assert all(line.startswith("OK") for line in applications.bind_source(
        candidate, source_commit="a" * 40, source_tree="b" * 40,
    ))
    deadline = time.monotonic() + 90
    preparation._git(candidate, "init", "-q", deadline=deadline)
    with composition_budget(deadline):
        promotion.build_promotion_tree(candidate)
    preparation._git(
        candidate, "-c", "user.name=Source Fixture", "-c", "user.email=fixture@example.com",
        "-c", "commit.gpgsign=false", "commit", "-qm", "source fixture", deadline=deadline,
    )
    commit = preparation._git(candidate, "rev-parse", "HEAD", deadline=deadline)
    archive = tmp_path / "source.tar"
    preparation._archive(candidate, commit, archive, deadline=deadline)
    exported = tmp_path / "exported"
    exported.mkdir()
    preparation._extract_archive(archive, exported, deadline=deadline)
    assert owner.read_source_identity(
        exported / "apps/worktree-manager", "1.2.3", plugin="worktree-manager",
    ) is not None
    checkout = promotion.add_scratch_worktree(commit, repo=candidate)
    try:
        original = candidate / "apps/worktree-manager"
        changed = [
            path.relative_to(original).as_posix()
            for path in original.rglob("*") if path.is_file()
            and path.read_bytes() != (checkout / "apps/worktree-manager" / path.relative_to(original)).read_bytes()
        ]
        assert not changed, changed
        assert owner.read_source_identity(
            checkout / "apps/worktree-manager", "1.2.3", plugin="worktree-manager",
        ) is not None
    finally:
        promotion.remove_scratch_worktree(checkout, repo=candidate)


def test_source_application_refuses_a_relative_linked_root(
    candidate: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    native_file_symlink: Callable[..., None],
) -> None:
    native_file_symlink(tmp_path / "linked", candidate, target_is_directory=True)
    monkeypatch.chdir(tmp_path)
    assert any(line.startswith("SKIP") for line in applications.materialize(Path("linked")))
    assert not (candidate / "apps").exists()


def test_source_application_materialization_refuses_a_changed_file_pointer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import materialize_main as materializer

    pointer = tmp_path / "pointer.md"
    pointer.write_bytes(b"no longer a file pointer\n")
    monkeypatch.setattr(materializer, "find_file_pointers", lambda *_args, **_kwargs: [pointer])
    assert materializer.materialize_file_pointers(tmp_path, canonical_root=tmp_path) == [
        f"SKIP {pointer}: file pointer changed during materialization",
    ]
    assert pointer.read_bytes() == b"no longer a file pointer\n"


@pytest.mark.parametrize("selected", ["worktree-manager", "plugins/agent-containers"])
def test_local_promotion_export_retains_only_the_selected_source_application(
    candidate: Path, monkeypatch: pytest.MonkeyPatch, selected: str,
) -> None:
    import local_runtime_promotion as local
    import promote_release as promotion

    assert all(line.startswith("OK") for line in applications.materialize(candidate))
    monkeypatch.setattr(promotion, "_rev_parse", lambda ref, **_kwargs: ref.removesuffix("^{commit}"))
    monkeypatch.setattr(promotion, "add_scratch_worktree", lambda *_args, **_kwargs: candidate)
    monkeypatch.setattr(promotion, "consume_pending_changes", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(promotion, "build_promotion_tree", lambda *_args, **_kwargs: "c" * 40)
    removed = []
    monkeypatch.setattr(promotion, "remove_scratch_worktree", lambda path, **_kwargs: removed.append(path))
    composed = local.compose(
        candidate, source_commit="a" * 40, main_seed="b" * 40,
        select_inputs=lambda _root: (selected,),
    )
    assert composed.export_paths is not None
    assert ("apps/worktree-manager" in composed.export_paths) == (selected == "worktree-manager")
    assert selected in composed.export_paths
    assert removed == [candidate]


def test_relocated_source_build_uses_only_embedded_inputs(
    candidate: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = importlib.import_module("setuptools.build_meta")

    _add_context_package(candidate)
    assert all(line.startswith("OK") for line in applications.materialize(candidate))
    relocated = tmp_path / "relocated"
    shutil.move(str(candidate / "apps/worktree-manager"), relocated)
    candidate.rename(tmp_path / "concealed")
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    monkeypatch.chdir(relocated)
    filename = backend.build_wheel(str(wheels))
    with zipfile.ZipFile(wheels / filename) as archive:
        assert archive.read("example_core/__init__.py") == b"VALUE = 'core'\n"
        assert archive.read("example_core/LICENSE") == b"core license\n"
        assert archive.read("example_core/data/notice.txt") == b"package data\n"
        assert archive.read("example_context/__init__.py") == b"VALUE = 'context'\n"
        assert archive.read("example_context/LICENSE") == b"core license\n"
        assert archive.read("example_context/data/settings.json") == b'{"value": "context"}\n'
        assert archive.read("worktree_manager/_runtime_assets/scripts/runtime_entry.py") == (
            b"VALUE = 'resource'\n"
        )
        metadata = archive.read(
            "copilot_extensions_worktree_manager-1.2.3.dist-info/METADATA",
        )
        assert b"Requires-Dist: external>=1" in metadata
        assert b"Requires-Dist: example-core" not in metadata


@pytest.mark.parametrize("shell", ["powershell", "pwsh", "cmd"] if os.name == "nt" else ["sh"])
def test_copied_source_setup_stages_without_selecting(
    candidate: Path, tmp_path: Path, shell: str,
) -> None:
    executable = shutil.which(shell)
    uv = shutil.which("uv")
    if executable is None or uv is None:
        pytest.skip("native shell and UV are required")
    source = candidate / "worktree-manager"
    manifest = source / "pyproject.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace('["external>=1"]', "[]")
        .replace('external = {path = "vendor/external-1.0-py3-none-any.whl"}\n', "")
        .replace('dev = ["pytest"]\n', "")
        + '\n[build-system]\nrequires = []\nbuild-backend = "_stage_backend"\n'
        'backend-path = ["src"]\n',
        encoding="utf-8",
    )
    _write(source, "src/_stage_backend.py", '''
from pathlib import Path
import base64, csv, hashlib, io, zipfile

def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    root = Path(__file__).parent
    metadata = "copilot_extensions_worktree_manager-1.2.3.dist-info"
    filename = "copilot_extensions_worktree_manager-1.2.3-py3-none-any.whl"
    members = {path.relative_to(root).as_posix(): path.read_bytes()
               for path in root.rglob("*") if path.is_file() and "__pycache__" not in path.parts}
    members[metadata + "/METADATA"] = (
        b"Metadata-Version: 2.3\\nName: copilot-extensions-worktree-manager\\nVersion: 1.2.3\\n\\n"
    )
    members[metadata + "/WHEEL"] = (
        b"Wheel-Version: 1.0\\nGenerator: fixture\\nRoot-Is-Purelib: true\\nTag: py3-none-any\\n\\n"
    )
    records = io.StringIO()
    writer = csv.writer(records)
    for name, content in members.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=").decode()
        writer.writerow([name, "sha256=" + digest, len(content)])
    writer.writerow([metadata + "/RECORD", "", ""])
    members[metadata + "/RECORD"] = records.getvalue().encode()
    with zipfile.ZipFile(Path(wheel_directory) / filename, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return filename
''')
    (candidate / "libs/installer-engine/runtime_application_binstubs.py").write_text(
        "raise RuntimeError('input source generator must not execute')\n", encoding="utf-8",
    )
    assert all(line.startswith("OK") for line in applications.materialize(candidate))
    assert all(line.startswith("OK") for line in applications.bind_source(
        candidate, source_commit="a" * 40, source_tree="b" * 40,
    ))
    identity = json.loads(
        (candidate / "apps/worktree-manager/runtime-source.json").read_text(encoding="utf-8"),
    )
    home = tmp_path / "home"
    slot = home / ".copilot-extensions/apps/worktree-manager/versions" / identity["runtime_version"]
    slot.parent.mkdir(parents=True)
    shutil.copytree(candidate / "apps/worktree-manager", slot)
    candidate.rename(tmp_path / "concealed")
    marker = slot.parent.parent / "current-version"
    marker.write_bytes(b"0.9\n")
    global_command = home / ".local/bin/worktree-manager.cmd"
    global_command.parent.mkdir(parents=True)
    global_command.write_bytes(b"legacy\n")
    poison = home / "poison"
    poison.mkdir()
    sentinel = poison / "called"
    for name in ("python", "python3", "py"):
        _write(poison, name + (".cmd" if os.name == "nt" else ""), (
            f'@echo called>"{sentinel}"\r\n@exit /b 89\r\n' if os.name == "nt" else
            '#!/bin/sh\necho called > ' + shlex.quote(str(sentinel)) + '\nexit 89\n'
        ))
        if os.name != "nt":
            (poison / name).chmod(0o700)
    environment = {
        **os.environ, "HOME": str(home), "USERPROFILE": str(home),
        "UV_CACHE_DIR": str(tmp_path / "uv-cache"), "UV_OFFLINE": "1",
        "UV_PYTHON_DOWNLOADS": "never", "UV_NO_CONFIG": "1",
        "PATH": os.pathsep.join((str(Path(uv).parent), str(poison), os.environ.get("PATH", ""))),
    }

    def run(path: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        return _run_entry(
            path, *arguments, executable=executable, shell=shell, cwd=home, environment=environment,
        )

    unstaged = _seal(slot)
    refused = run(slot / "worktree-manager", "--exit-seven", "two words")
    assert refused.returncode != 0
    assert _seal(slot) == unstaged
    baseline = None
    for _ in range(2):
        staged = run(slot / "bin/setup", "--stage")
        assert staged.returncode == 0, staged.stderr
        assert json.loads(staged.stdout)["state"] == "staged"
        assert marker.read_bytes() == b"0.9\n"
        assert global_command.read_bytes() == b"legacy\n"
        assert not sentinel.exists()
        observed = _seal(slot)
        if baseline is None:
            baseline = observed
        else:
            assert observed == baseline
    assert (slot / "uv.lock").is_file()
    assert (slot / ".venv/pyvenv.cfg").is_file()
    launched = run(slot / "worktree-manager", "--exit-seven", "two words")
    assert launched.returncode == 7, launched.stderr
    assert json.loads(launched.stdout) == ["--exit-seven", "two words"]
    helper = run(slot / "install-helper", "--exit-seven", "two words")
    assert helper.returncode == 7, helper.stderr
    assert json.loads(helper.stdout) == ["--exit-seven", "two words"]
    assert baseline == _seal(slot)


@pytest.mark.skipif(
    not os.environ.get("MANAGER_SOURCE_APPLICATION"),
    reason="requires an explicitly prepared promotion-produced Manager source app",
)
def test_actual_promoted_manager_source_app_stages_without_activation(tmp_path: Path) -> None:
    from packaging.version import Version
    from urllib.parse import urlsplit
    from urllib.request import url2pathname
    import versioned_runtime_source as owner

    source = Path(os.environ["MANAGER_SOURCE_APPLICATION"]).resolve(strict=True)
    assert not (source / ".venv").exists()
    metadata = tomllib.loads((source / "pyproject.toml").read_text(encoding="utf-8"))
    identity = owner.read_source_identity(
        source, metadata["project"]["version"], plugin="worktree-manager",
    )
    assert identity is not None
    before = _seal(source)
    home = tmp_path / "home"
    slot = home / ".copilot-extensions/apps/worktree-manager/versions" / identity.runtime_version
    slot.parent.mkdir(parents=True)
    shutil.copytree(source, slot)
    marker = slot.parent.parent / "current-version"
    marker.write_bytes(b"legacy\n")
    global_command = home / ".local/bin/worktree-manager.cmd"
    global_command.parent.mkdir(parents=True)
    global_command.write_bytes(b"legacy\n")
    shell = "powershell" if os.name == "nt" else "sh"
    executable = shutil.which(shell)
    assert executable is not None
    environment = {
        **os.environ, "UV_CACHE_DIR": str(tmp_path / "uv-cache"),
        "UV_PYTHON_DOWNLOADS": "never", "WORKTREE_MANAGER_ROOT": str(slot.parent.parent),
    }
    baseline = None
    for _ in range(2):
        result = _run_entry(
            slot / "bin/setup", "--stage", executable=executable, shell=shell,
            cwd=home, environment=environment, timeout=180,
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["state"] == "staged"
        assert marker.read_bytes() == b"legacy\n"
        assert global_command.read_bytes() == b"legacy\n"
        observed = _seal(slot)
        if baseline is None:
            baseline = observed
        else:
            assert baseline == observed
    assert (slot / ".venv/pyvenv.cfg").is_file()
    assert (slot / "uv.lock").is_file()
    assert not (slot / ".install-complete.json").exists()
    python = slot / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    observation = processes.run(
        [str(python), "-I", "-B", "-c",
         "import importlib.metadata,json,sys; "
         "d=importlib.metadata.distribution('copilot-extensions-worktree-manager'); "
         "print(json.dumps({'version':d.version,'prefix':sys.prefix,"
         "'origin':json.loads(d.read_text('direct_url.json'))}))"],
        cwd=home, env=environment, text=True, deadline=time.monotonic() + 30, check=True,
    )
    installed = json.loads(observation.stdout)
    assert Path(installed["prefix"]).samefile(slot / ".venv")
    assert Version(installed["version"]) == Version(identity.package_version)
    assert installed["origin"]["dir_info"] == {"editable": False}
    origin = urlsplit(installed["origin"]["url"])
    assert origin.scheme == "file" and Path(url2pathname(origin.path)).samefile(slot)
    assert _seal(slot) == baseline
    assert _seal(source) == before
