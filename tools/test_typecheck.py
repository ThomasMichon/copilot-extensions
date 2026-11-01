"""Focused inventory and actual-checker controls for the local typecheck front door."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from tools import typecheck


def project(name: str = "sample", level: typecheck.Level = "strict") -> typecheck.Project:
    """Declare one synthetic Python input under its test workspace."""
    return typecheck.Project(
        name, ("sample.py",), (), level, "3.10", ("linux",), "requirements.txt",
    )


def test_inventory_rejects_new_or_ambiguous_python() -> None:
    """An added Python path cannot silently escape or share an admitted owner."""
    item = project()
    with pytest.raises(typecheck.TypecheckError, match="unclassified"):
        typecheck.inventory(("new.py",), (item,))
    with pytest.raises(typecheck.TypecheckError, match="expected one owner"):
        typecheck.inventory(("sample.py",), (item, project("other")))
    with pytest.raises(typecheck.TypecheckError, match="Empty Python inventory"):
        typecheck.inventory((), (item,))


def test_directory_prefixes_do_not_claim_neighboring_components() -> None:
    """An exact component prefix includes nested code but not lookalike siblings."""
    item = typecheck.Project(
        "sample", ("plugins/sample",), ("plugins/sample/tests",),
        "basic", "3.10", ("linux",), "requirements.txt",
    )
    assert item.owns("plugins/sample/src/nested/module.py")
    assert not item.owns("plugins/sample-other/module.py")
    assert not item.owns("plugins/sample/tests/test_module.py")


def test_manifest_rejects_duplicate_keys_and_path_escape(tmp_path: Path) -> None:
    """Malformed ownership cannot relax the checker policy."""
    tools = tmp_path / "tools"
    tools.mkdir()
    manifest = tools / "typecheck-projects.json"
    manifest.write_text('{"version":1,"version":1}', encoding="utf-8")
    with pytest.raises(typecheck.TypecheckError, match="Duplicate manifest field"):
        typecheck.load_projects(tmp_path)
    manifest.write_text(json.dumps({
        "version": 1, "python_version": "3.10", "platforms": ["linux"],
        "requirements": "requirements.txt",
        "projects": [{
            "name": "sample", "include": ["../outside"], "exclude": [], "level": "strict",
        }],
    }), encoding="utf-8")
    with pytest.raises(typecheck.TypecheckError, match="repository-relative path"):
        typecheck.load_projects(tmp_path)


def test_missing_preparation_is_actionable_and_does_not_create_environment(
    tmp_path: Path,
) -> None:
    """A check is not an implicit installer."""
    (tmp_path / "requirements.txt").write_text("mypy==1.18.2\n", encoding="utf-8")
    with pytest.raises(typecheck.TypecheckError, match="sample --prepare"):
        typecheck.check_project(
            tmp_path, project(), ("sample.py",), all_paths=("sample.py",),
        )
    assert not (tmp_path / ".test-venvs").exists()


def test_pending_probe_never_certifies_admission(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """A clean probe of pending code remains distinct from an enforced result."""
    item = project(level="pending")

    def load_projects() -> tuple[typecheck.Project, ...]:
        return (item,)

    def python_paths() -> tuple[str, ...]:
        return ("sample.py",)

    def check_project(
        _root: Path, _project: typecheck.Project, _files: tuple[str, ...], *,
        all_paths: tuple[str, ...], strict: bool = False,
    ) -> int:
        assert all_paths == ("sample.py",)
        return 0

    monkeypatch.setattr(typecheck, "load_projects", load_projects)
    monkeypatch.setattr(typecheck, "python_paths", python_paths)
    monkeypatch.setattr(typecheck, "check_project", check_project)
    assert typecheck.main(["--all"]) == 1
    assert "do not certify admission" in capsys.readouterr().err
    assert typecheck.main(["--inventory", "--require-complete"]) == 2


class Lease:
    """Only the release surface is substituted in checker subprocess controls."""

    def __init__(self) -> None:
        self.released = False

    def release(self) -> None:
        self.released = True


def test_preparation_refuses_replaced_leased_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preparation cannot switch filesystem identities beneath the shared lease."""
    environment = tmp_path / "environment"
    environment.mkdir()
    lease = Lease()

    def environment_path(_root: Path, _project: typecheck.Project) -> tuple[Path, str]:
        return environment, "test-context"

    def acquire(_wait: float, _path: Path) -> Lease:
        return lease

    def which(_name: str) -> str:
        return "uv"

    def run(
        argv: list[str], _root: Path, *, env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        environment.rename(tmp_path / "original-environment")
        environment.mkdir()
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(typecheck, "environment_path", environment_path)
    monkeypatch.setattr(typecheck, "acquire_environment", acquire)
    monkeypatch.setattr(shutil, "which", which)
    monkeypatch.setattr(typecheck, "_run", run)
    with pytest.raises(typecheck.TypecheckError, match="replaced the leased"):
        typecheck.prepare(tmp_path, project())
    assert lease.released
    assert not (environment / ".typecheck-prepared").exists()


def test_checker_timeout_settles_targets_and_environment_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed checker leaves neither a target-file writer nor an admitted lease."""
    (tmp_path / ".typecheck-prepared").write_text("test-context\n", encoding="utf-8")
    lease = Lease()

    def environment_path(_root: Path, _project: typecheck.Project) -> tuple[Path, str]:
        return tmp_path, "test-context"

    def environment_python(_path: Path) -> Path:
        return Path(sys.executable)

    def acquire(_wait: float, _path: Path) -> Lease:
        return lease

    def run(
        argv: list[str], _root: Path, *, env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(argv, 120)

    monkeypatch.setattr(typecheck, "environment_path", environment_path)
    monkeypatch.setattr(typecheck, "environment_python", environment_python)
    monkeypatch.setattr(typecheck, "acquire_environment", acquire)
    monkeypatch.setattr(typecheck, "_run", run)
    with pytest.raises(subprocess.TimeoutExpired):
        typecheck.check_project(
            tmp_path, project(), ("sample.py",), all_paths=("sample.py",),
        )
    assert lease.released
    assert not (tmp_path / "typecheck-targets.txt").exists()
    assert not (tmp_path / "typecheck-mypy.ini").exists()


@pytest.mark.parametrize(
    ("source", "level", "expected"),
    [
        ("def accept(value: int) -> int:\n    return value\naccept('wrong')\n", "basic", "[arg-type]"),
        ("def body(value):\n    wrong: int = 'text'\n    return wrong\n", "basic", "[assignment]"),
        ("def missing(value):\n    return value\n", "strict", "[no-untyped-def]"),
        ("def generic(value: dict) -> dict:\n    return value\n", "strict", "[type-arg]"),
    ],
)
def test_actual_mypy_rejects_type_regressions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    source: str, level: typecheck.Level, expected: str,
) -> None:
    """Exercise the real front door's MyPy argv, not a mocked checker result."""
    root = tmp_path / "source with spaces"
    root.mkdir()
    (root / "sample.py").write_text(source, encoding="utf-8")
    environment = tmp_path / "environment with spaces"
    environment.mkdir()
    (environment / ".typecheck-prepared").write_text("test-context\n", encoding="utf-8")
    lease = Lease()

    def environment_path(_root: Path, _project: typecheck.Project) -> tuple[Path, str]:
        return environment, "test-context"

    def environment_python(_path: Path) -> Path:
        return Path(sys.executable)

    def acquire(_wait: float, _path: Path) -> Lease:
        return lease

    monkeypatch.setattr(typecheck, "environment_path", environment_path)
    monkeypatch.setattr(typecheck, "environment_python", environment_python)
    monkeypatch.setattr(typecheck, "acquire_environment", acquire)
    assert typecheck.check_project(
        root, project(level=level), ("sample.py",), all_paths=("sample.py",),
    ) != 0
    assert expected in capsys.readouterr().out
    assert lease.released
    assert not (environment / "typecheck-targets.txt").exists()
    assert not (environment / "typecheck-mypy.ini").exists()
