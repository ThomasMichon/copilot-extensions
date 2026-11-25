"""Independent programs retain whole-file strict checks and physical source custody."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import sys

import pytest

from tools import typecheck
from tools.test_typecheck import Lease, _isolated_scratch as _isolated_scratch
from tools.typecheck_programs import InstallerOverlay, Program


def test_materialized_consumer_resolves_own_package_before_retained_copy(tmp_path: Path) -> None:
    source, emitted = tmp_path / "source", tmp_path / "emitted"
    retained = source / "older" / "src"
    current = source / "manager" / "src"
    imports = InstallerOverlay(emitted, "manager").imports(
        source, [str(retained), str(current)], tmp_path / "adapter",
    )
    assert imports.index(str(emitted / "manager" / "src")) < imports.index(str(retained))
    assert str(current) not in imports


def _project() -> typecheck.Project:
    return typecheck.Project(
        "sample", ("ordinary", "retained"), (), "strict", "3.10",
        ("linux", "win32"), "requirements.txt",
        import_roots=("ordinary",), program_roots=(Program("retained", ("retained",)),),
    )


def _sources(root: Path) -> tuple[str, ...]:
    files = ("ordinary/contract.py", "retained/contract.py")
    for relative in files:
        path = root / relative
        path.parent.mkdir(parents=True)
        path.write_text("def value() -> int:\n    return 1\n", encoding="utf-8")
    return files


@pytest.mark.parametrize("invalid", ("none", "ordinary", "retained", "both"))
def test_actual_mypy_independent_programs_check_every_file_and_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    invalid: str,
) -> None:
    root = tmp_path / "source"
    files = _sources(root)
    for directory, argument, value_type in (("ordinary", "1", "int"), ("retained", "'fixture'", "str")):
        (root / directory / "helper.py").write_text(
            f"def accept(value: {value_type}) -> int:\n    return 1\n", encoding="utf-8",
        )
        (root / directory / "contract.py").write_text(
            f"from helper import accept\ndef value() -> int:\n    return accept({argument})\n",
            encoding="utf-8",
        )
    all_paths = (*files, "ordinary/helper.py", "retained/helper.py")
    for relative in files:
        if invalid in (relative.split("/")[0], "both"):
            (root / relative).write_text(
                "def value() -> int:\n    return 'not an integer'\n", encoding="utf-8",
            )
    environment = tmp_path / "environment"
    environment.mkdir()
    (environment / ".typecheck-prepared").write_text("test-context\n", encoding="utf-8")
    leases: list[Lease] = []

    def environment_path(
        _root: Path, _project: typecheck.Project, *, captured_lock: bytes | None = None,
    ) -> tuple[Path, str]:
        return environment, "test-context"

    def acquire(_wait: float, _path: Path) -> Lease:
        lease = Lease()
        leases.append(lease)
        return lease

    monkeypatch.setattr(typecheck, "environment_path", environment_path)
    monkeypatch.setattr(typecheck, "environment_python", lambda _path: Path(sys.executable))
    monkeypatch.setattr(typecheck, "acquire_environment", acquire)
    item = _project()
    assert typecheck.check_project(
        root, replace(item, program_roots=(), import_roots=("ordinary", "retained")),
        files, all_paths=all_paths,
    ) != 0
    assert "Duplicate module" in capsys.readouterr().out
    assert typecheck.check_project(root, item, files, all_paths=all_paths) == int(invalid != "none")
    output = capsys.readouterr().out
    assert output.count("[strict] sample (Python 3.10, linux, 1 physical files") == 2
    assert output.count("[strict] sample (Python 3.10, win32, 1 physical files") == 2
    assert "Duplicate module" not in output
    assert output.count("[return-value]") == 2 * (
        2 if invalid == "both" else int(invalid != "none")
    )
    assert leases and all(lease.released for lease in leases)
    assert not list(environment.parent.glob("source-views-*/**/typecheck-targets.txt"))


@pytest.mark.parametrize("mutation", ("bytes", "identity", "directory"))
def test_independent_programs_refuse_source_change_between_programs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str,
) -> None:
    files = _sources(tmp_path)
    calls: list[tuple[str, ...]] = []

    def check(
        root: Path, project: typecheck.Project, selected: tuple[str, ...], *,
        all_paths: tuple[str, ...], strict: bool = False, program_root: str | None = None,
    ) -> int:
        assert project == _project() and all_paths == files and strict
        calls.append(selected)
        target = root / files[1]
        if mutation == "bytes":
            target.write_bytes(b"def value() -> int:\n    return 2\n")
        elif mutation == "identity":
            replacement = target.with_suffix(".replacement")
            replacement.write_bytes(target.read_bytes())
            replacement.replace(target)
        else:
            target.parent.rename(root / "retained-original")
            target.parent.mkdir()
            target.write_text("def value() -> int:\n    return 1\n", encoding="utf-8")
        return 0

    monkeypatch.setattr(typecheck, "_check_program", check)
    with pytest.raises(typecheck.TypecheckError, match="(identity|source) changed"):
        typecheck.check_project(tmp_path, _project(), files, all_paths=files, strict=True)
    assert calls == [(files[0],)]


def test_manifest_retains_optional_program_roots_without_changing_policy(tmp_path: Path) -> None:
    _sources(tmp_path)
    tools = tmp_path / "tools"
    tools.mkdir()
    row: dict[str, object] = {
        "name": "sample", "include": ["ordinary", "retained"], "exclude": [],
        "level": "strict", "import_roots": ["ordinary"],
    }
    manifest: dict[str, object] = {
        "version": 1, "python_version": "3.10", "platforms": ["linux", "win32"],
        "requirements": "requirements.txt", "projects": [row],
    }
    path = tools / "typecheck-projects.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    original, = typecheck.load_projects(tmp_path)
    assert original == replace(_project(), program_roots=())
    row["program_roots"] = [{"root": "retained", "import_roots": ["retained"]}]
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert typecheck.load_projects(tmp_path) == (_project(),)


def test_retained_program_uses_actual_runtime_import_roots() -> None:
    project = next(item for item in typecheck.load_projects() if item.name == "agent-worktrees-admission-toolkit")
    program, = project.program_roots
    declaration = json.loads((typecheck.ROOT / program.root / "toolkit.json").read_bytes())
    assert program.import_roots == tuple(
        program.root + "/" + relative for relative in declaration["importRoots"]
    )


@pytest.mark.parametrize("programs", [
    None, ["retained"], [{"root": "retained", "import_roots": []}],
    [{"root": "retained", "import_roots": ["ordinary"]}],
    [{"root": "../retained", "import_roots": ["retained"]}],
    [{"root": "retained", "import_roots": ["retained"], "level": "pending"}],
])
def test_manifest_refuses_invalid_or_cross_program_imports(
    tmp_path: Path, programs: object,
) -> None:
    _sources(tmp_path)
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "typecheck-projects.json").write_text(json.dumps({
        "version": 1, "python_version": "3.10", "platforms": ["linux", "win32"],
        "requirements": "requirements.txt", "projects": [{
            "name": "sample", "include": ["ordinary", "retained"], "exclude": [],
            "level": "strict", "program_roots": programs,
        }],
    }), encoding="utf-8")
    with pytest.raises(typecheck.TypecheckError):
        typecheck.load_projects(tmp_path)
