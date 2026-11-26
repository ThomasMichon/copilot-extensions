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
