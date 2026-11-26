"""Canonical export and data-only compatibility controls."""
from __future__ import annotations

import io
from pathlib import Path
import tarfile
import time

import pytest

import local_install_export as export
import local_install_source as source
import prepare_runtime_source as preparation


@pytest.fixture
def repository(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    files = {
        "pyproject.toml": '[tool.copilot-extensions.python-projects]\ndemo="apps/demo"\n',
        "apps/demo/pyproject.toml": (
            '[project]\nname="demo"\nversion="1.0"\n'
            '[tool.uv.sources]\nfirst={path="../../libs/first",editable=true}\n'
        ),
        "apps/demo/src/demo/__init__.py": "VALUE = 1\n",
        "libs/first/pyproject.toml": (
            '[project]\nname="first"\nversion="1.0"\n'
            '[tool.uv.sources]\nsecond={path="../second",editable=true}\n'
        ),
        "libs/first/src/first/__init__.py": "VALUE = 2\n",
        "libs/second/pyproject.toml": '[project]\nname="second"\nversion="1.0"\n',
        "libs/second/src/second/__init__.py": "VALUE = 3\n",
        "plugins/unselected/pyproject.toml": '[project]\nname="unselected"\nversion="1.0"\n',
        "plugins/unselected/src/unselected/__init__.py": "UNRELATED = True\n",
        "unrelated/large.txt": "not part of the application\n",
    }
    for relative, content in files.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    git = source.preparation._git
    git(repo, "init", "--quiet")
    git(repo, "add", ".")
    git(repo, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "--quiet", "-m", "Export fixture")
    return repo, git(repo, "rev-parse", "HEAD")


def test_closed_source_exports_actual_transitive_closure_and_committed_bytes(
    repository: tuple[Path, str], tmp_path: Path,
) -> None:
    repo, commit = repository
    payload = repo / "libs/second/src/second/__init__.py"
    original = payload.read_bytes()
    payload.write_text("DIRTY = True\n")
    output = tmp_path / "prepared"
    record = source.compose(
        repo, commit, None, output, projects=("demo",),
        closed_wheel_source=True, deadline=time.monotonic() + 30,
    )
    retained = output / "source"
    assert record["source_commit"] == commit
    assert record["source_tree"] == source.preparation._git(repo, "rev-parse", f"{commit}^{{tree}}")
    assert (retained / "libs/second/src/second/__init__.py").read_bytes() == original
    assert (retained / "libs/first/src/first/__init__.py").is_file()
    assert (retained / "plugins/unselected/pyproject.toml").is_file()
    assert not (retained / "plugins/unselected/src").exists()
    assert not (retained / "unrelated").exists()
    assert not list(output.glob("export-*"))
    assert source._application_export_paths(retained, ("demo",)) == source._application_export_paths(repo, ("demo",))


def test_standalone_export_retains_its_canonical_build_resources(
    repository: tuple[Path, str], tmp_path: Path,
) -> None:
    repo, _commit = repository
    dispatch = repo / "services/dispatch"
    dispatch.mkdir(parents=True)
    (dispatch / "pyproject.toml").write_text('[project]\nname="dispatch"\nversion="1.0"\n')
    helper = repo / "libs/versioned-runtime/standalone_build.py"
    helper.parent.mkdir(parents=True)
    helper.write_bytes(b"# retained build resource\n")
    source.preparation._git(repo, "add", ".")
    source.preparation._git(
        repo, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "--quiet", "-m", "Standalone build resource",
    )
    commit = source.preparation._git(repo, "rev-parse", "HEAD")
    output = tmp_path / "prepared"
    source.compose(
        repo, commit, None, output, projects=("standalone:dispatch-service",),
        closed_wheel_source=True, deadline=time.monotonic() + 30,
    )
    assert (output / "source/libs/versioned-runtime/standalone_build.py").read_bytes() == helper.read_bytes()
    assert not (output / "source/unrelated").exists()
    assert not (output / "source/apps/demo/src").exists()


def test_prefetch_clamps_escaping_metadata_before_resolving_in_tree_references(
    repository: tuple[Path, str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    import build_python_artifacts as builder

    repo, _commit = repository
    (repo / "apps/demo/pyproject.toml").write_text(
        '[project]\nname="demo"\nversion="1.0"\n'
        '[tool.uv.sources]\nexternal={path="../../../outside",editable=true}\n',
    )

    def forbidden(_project: Path) -> list[tuple[str, str, str]]:
        raise AssertionError("filesystem-resolving reader ran before containment")

    monkeypatch.setattr(builder, "find_in_tree_lib_sources", forbidden)
    with pytest.raises(ValueError, match="outside the source"):
        export._prefetch_paths(repo, ("apps/demo",), ("pyproject.toml",), time.monotonic() + 30)


@pytest.mark.parametrize("fault", ["unsafe", "collision", "link", "changed", "deadline"])
def test_export_never_falls_back_around_archive_or_deadline_refusals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str,
) -> None:
    archive = tmp_path / "source.tar"
    with tarfile.open(archive, "w") as stream:
        for name in ("app/pyproject.toml", "unselected/file"):
            member = tarfile.TarInfo(name)
            member.size = 1
            stream.addfile(member, io.BytesIO(b"x"))
        if fault in {"unsafe", "collision", "link"}:
            member = tarfile.TarInfo({
                "unsafe": "../escape", "collision": "unselected/file", "link": "unselected/link",
            }[fault])
            if fault == "link":
                member.type = tarfile.SYMTYPE
                member.linkname = "file"
            stream.addfile(member)
    destination = tmp_path / "source"
    destination.mkdir()
    real_extract = preparation._extract_archive
    calls: list[tuple[str, ...] | None] = []
    deadline = time.monotonic() + 30

    def extract(
        archive: Path, target: Path, *, deadline: float,
        include_paths: tuple[str, ...] | None = None,
        archive_stream: tarfile.TarFile | None = None,
    ) -> None:
        calls.append(include_paths)
        real_extract(
            archive, target, deadline=deadline, include_paths=include_paths,
            archive_stream=archive_stream,
        )
        if fault == "changed":
            with archive.open("ab") as stream:
                stream.write(b"changed")
        if fault == "deadline":
            monkeypatch.setattr(time, "monotonic", lambda: deadline + 1)

    monkeypatch.setattr(preparation, "_extract_archive", extract)
    with pytest.raises(preparation.SourcePreparationError):
        export.extract_application(
            archive, destination, project_paths=("app",), projects=("app",),
            deadline=deadline, select=source._application_export_paths,
        )
    assert calls and all(selection is not None for selection in calls)
    assert not list(destination.iterdir())
    assert not list(tmp_path.glob("export-*"))


def test_unsupported_build_metadata_remains_data_only(
    repository: tuple[Path, str], tmp_path: Path,
) -> None:
    repo, _commit = repository
    metadata = repo / "apps/demo/pyproject.toml"
    metadata.write_text(
        '[project]\nname="demo"\nversion="1.0"\n'
        '[tool.uv.sources]\nlegacy={path="legacy.whl"}\n'
        '[build-system]\nrequires=[]\nbuild-backend="never_execute"\n',
    )
    backend = repo / "apps/demo/never_execute.py"
    backend.write_text('raise RuntimeError("candidate backend executed")\n')
    (repo / "apps/demo/legacy.whl").write_bytes(b"development data")
    source.preparation._git(repo, "add", ".")
    source.preparation._git(
        repo, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "--quiet", "-m", "Legacy development metadata",
    )
    commit = source.preparation._git(repo, "rev-parse", "HEAD")
    output = tmp_path / "prepared"
    source.compose(repo, commit, None, output, projects=("demo",), closed_wheel_source=True,
                   deadline=time.monotonic() + 30)
    assert (output / "source/apps/demo/pyproject.toml").read_bytes() == metadata.read_bytes()
    assert (output / "source/apps/demo/never_execute.py").read_bytes() == backend.read_bytes()
    assert (output / "source/apps/demo/legacy.whl").read_bytes() == b"development data"
