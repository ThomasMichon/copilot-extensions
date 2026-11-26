"""Pure archive export work counts and original refusal controls; no Git child."""
from __future__ import annotations

import io
from pathlib import Path
import sys
import tarfile
import time

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import local_install_export as export
import local_install_source as source
import prepare_runtime_source as preparation
from test_local_install_export import (
    test_export_never_falls_back_around_archive_or_deadline_refusals
    as test_export_never_falls_back_around_archive_or_deadline_refusals,
)


def test_export_never_writes_unrelated_payloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = {
        "pyproject.toml": b'[tool.copilot-extensions.python-projects]\ndemo="apps/demo"\n',
        "apps/demo/pyproject.toml": (
            b'[project]\nname="demo"\nversion="1.0"\n'
            b'[tool.uv.sources]\nshared={path="../../libs/shared",editable=true}\n'
        ),
        "apps/demo/src/demo/__init__.py": b"VALUE = 1\n",
        "libs/shared/pyproject.toml": b'[project]\nname="shared"\nversion="1.0"\n',
        "libs/shared/src/shared/__init__.py": b"VALUE = 2\n",
        **{f"unrelated/{index}.txt": b"excluded payload\n" for index in range(32)},
    }
    archive = tmp_path / "source.tar"
    with tarfile.open(archive, "w") as stream:
        for name, content in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(content)
            stream.addfile(member, io.BytesIO(content))
    real_extract = preparation._extract_archive
    writes: set[str] = set()

    def extract(
        archive: Path, target: Path, *, deadline: float,
        include_paths: tuple[str, ...] | None = None,
    ) -> None:
        assert include_paths is not None
        real_extract(archive, target, deadline=deadline, include_paths=include_paths)
        writes.update(path.relative_to(target).as_posix() for path in target.rglob("*") if path.is_file())
        assert not (target / "unrelated").exists()

    monkeypatch.setattr(preparation, "_extract_archive", extract)
    destination = tmp_path / "source"
    destination.mkdir()
    export.extract_application(
        archive, destination, project_paths=("apps/demo",), projects=("demo",),
        deadline=time.monotonic() + 30, select=source._application_export_paths,
    )
    expected = {name for name in files if not name.startswith("unrelated/")}
    assert writes == expected
    assert {path.relative_to(destination).as_posix() for path in destination.rglob("*") if path.is_file()} == expected
    assert all((destination / name).read_bytes() == files[name] for name in expected)


def test_unsupported_export_retains_data_without_executing_setup(tmp_path: Path) -> None:
    files = {
        "pyproject.toml": b'[tool.copilot-extensions.python-projects]\ndemo="apps/demo"\n',
        "apps/demo/pyproject.toml": b'[project]\nname="demo"\nversion="1.0"\n',
        "apps/demo/setup.py": b'raise RuntimeError("candidate setup executed")\n',
        "unrelated/retained.txt": b"original data-only source\n",
    }
    archive = tmp_path / "source.tar"
    with tarfile.open(archive, "w") as stream:
        for name, content in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(content)
            stream.addfile(member, io.BytesIO(content))
    destination = tmp_path / "source"
    destination.mkdir()
    export.extract_application(
        archive, destination, project_paths=("apps/demo",), projects=("demo",),
        deadline=time.monotonic() + 30, select=source._application_export_paths,
    )
    assert {path.relative_to(destination).as_posix() for path in destination.rglob("*") if path.is_file()} == files.keys()
    assert all((destination / name).read_bytes() == content for name, content in files.items())


def test_standalone_include_group_retains_data_only_source(tmp_path: Path) -> None:
    files = {
        "worktree-manager/pyproject.toml": (
            b'[project]\nname="worktree-manager"\nversion="1.0"\ndependencies=[]\n'
            b'[build-system]\nrequires=["setuptools>=77"]\nbuild-backend="setuptools.build_meta"\n'
            b'[dependency-groups]\nfirst-party-core=[{include-group="shared"}]\nshared=[]\n'
        ),
        "worktree-manager/setup.py": (ROOT / "worktree-manager/setup.py").read_bytes(),
        "worktree-manager/src/worktree_manager/__init__.py": b"VALUE = 1\n",
        "libs/versioned-runtime/retained.txt": b"build owner\n",
        "libs/installer-engine/retained.txt": b"build owner\n",
        "fleet-manager/src/fleet_manager/retained.txt": b"build owner\n",
        "unrelated/retained.txt": b"original data-only source\n",
    }
    original = tmp_path / "original"
    for name, content in files.items():
        path = original / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    with pytest.raises(RuntimeError, match="first-party-core must be a flat requirement list") as error:
        source._application_export_paths(original, ("standalone:worktree-manager",))
    assert type(error.value) is RuntimeError
    archive = tmp_path / "source.tar"
    with tarfile.open(archive, "w") as stream:
        for name, content in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(content)
            stream.addfile(member, io.BytesIO(content))
    destination = tmp_path / "source"
    destination.mkdir()
    export.extract_application(
        archive, destination, project_paths=("worktree-manager",),
        projects=("standalone:worktree-manager",), deadline=time.monotonic() + 30,
        select=source._application_export_paths,
    )
    assert all((destination / name).read_bytes() == content for name, content in files.items())


def test_selector_source_preparation_error_never_becomes_a_compatibility_fallback(tmp_path: Path) -> None:
    archive = tmp_path / "source.tar"
    with tarfile.open(archive, "w") as stream:
        member = tarfile.TarInfo("app/pyproject.toml")
        content = b'[project]\nname="app"\nversion="1.0"\n'
        member.size = len(content)
        stream.addfile(member, io.BytesIO(content))
    destination = tmp_path / "source"
    destination.mkdir()

    def refuse(_source: Path, _projects: tuple[str, ...]) -> tuple[str, ...]:
        raise preparation.SourcePreparationError("selected Source deadline or integrity refusal")

    with pytest.raises(preparation.SourcePreparationError, match="deadline or integrity"):
        export.extract_application(
            archive, destination, project_paths=("app",), projects=("app",),
            deadline=time.monotonic() + 30, select=refuse,
        )
    assert not list(destination.iterdir())
