"""Canonical source application emission, independent of wheels or installation."""
from __future__ import annotations

import json
import importlib
from pathlib import Path
import shutil
import sys
import zipfile

import pytest

import materialize_source_applications as applications
from standalone_python_artifacts import injected_resources
from standalone_build import RUNTIME_RESOURCES

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[1]


def _write(root: Path, name: str, content: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


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
    _write(project, "pyproject.toml", (
        '[project]\nname = "copilot-extensions-worktree-manager"\nversion = "1.2.3"\n'
        'requires-python = ">=3.11"\ndependencies = ["external>=1"]\n'
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
    _write(project, "src/worktree_manager/data/plugins.toml", "version = 1\n")
    _write(project, "scripts/runtime_entry.py", "VALUE = 'resource'\n")
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


def test_source_application_contains_embedded_code_resources_and_notices(candidate: Path) -> None:
    source = candidate / "worktree-manager"
    before = {path.relative_to(source): path.read_bytes() for path in source.rglob("*") if path.is_file()}
    resources = injected_resources("worktree-manager", candidate)
    assert all(line.startswith("OK") for line in applications.materialize(candidate))
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
    assert not (emitted / "libs").exists()
    assert not (emitted / ".venv").exists()
    assert all((emitted / "src" / name).read_bytes() == path.read_bytes()
               for name, path in resources.items())
    assert before == {
        path.relative_to(source): path.read_bytes() for path in source.rglob("*") if path.is_file()
    }


@pytest.mark.parametrize("fault", ["declaration", "missing-input", "collision"])
def test_source_application_rejects_bad_inputs_before_creating_output(
    candidate: Path, fault: str,
) -> None:
    source = candidate / "worktree-manager"
    if fault == "declaration":
        (source / "application-source.json").write_text("{}")
    elif fault == "missing-input":
        (source / "libs/example/LICENSE").unlink()
    else:
        _write(source, "src/example_core/__init__.py", "VALUE = 'foreign'\n")
    assert any(line.startswith("SKIP") for line in applications.materialize(candidate))
    assert not (candidate / "apps").exists()


def test_source_application_does_not_replace_an_existing_output(candidate: Path) -> None:
    _write(candidate, "apps/worktree-manager/keep.txt", "keep\n")
    assert any(line.startswith("SKIP") for line in applications.materialize(candidate))
    assert (candidate / "apps/worktree-manager/keep.txt").read_text() == "keep\n"


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
        assert archive.read("worktree_manager/_runtime_assets/scripts/runtime_entry.py") == (
            b"VALUE = 'resource'\n"
        )
        metadata = archive.read(
            "copilot_extensions_worktree_manager-1.2.3.dist-info/METADATA",
        )
        assert b"Requires-Dist: external>=1" in metadata
        assert b"Requires-Dist: example-core" not in metadata
