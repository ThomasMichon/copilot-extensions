"""Focused inventory and actual-checker controls for the local typecheck front door."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from tools import typecheck
from tools import typecheck_source_views as views
from installer_dependency_policy import DependencyPolicyError


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


def test_paths_cannot_inject_response_file_arguments() -> None:
    """Git filename line separators cannot become MyPy options."""
    for separator in ("\n", "\r", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"):
        path = "libs/worktree-identity/src/benign.pyi" + separator + "--ignore-errors" + separator + "cache.py"
        with pytest.raises(typecheck.TypecheckError, match="repository-relative path"):
            typecheck._relative(path)


def test_checker_bootstrap_does_not_require_python311_imports() -> None:
    """Exercise the import closure without the modules that broke Python 3.10."""
    source = (
        "import builtins, sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "original = builtins.__import__\n"
        "def guarded(name, *args, **kwargs):\n"
        "    if name in {'tomllib', 'installer_engine'}:\n"
        "        raise ImportError('Python 3.11-only import reached')\n"
        "    return original(name, *args, **kwargs)\n"
        "builtins.__import__ = guarded\n"
        "import tools.typecheck\n"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", source, str(typecheck.ROOT)],
        capture_output=True, text=True, timeout=30,
        creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
    )
    assert result.returncode == 0, result.stderr


def test_git_inventory_ignores_foreign_repository_selectors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The actual Git process inventories the explicit source root, not a wrapper's."""
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "foreign.git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path))
    monkeypatch.setenv("GIT_INDEX_FILE", str(tmp_path / "foreign-index"))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.bare")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "true")
    assert "tools/typecheck.py" in typecheck.python_paths(typecheck.ROOT)


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


def test_git_comparison_and_physical_inventory_cover_developer_edits(tmp_path: Path) -> None:
    """Real Git covers staged/unstaged edits, pinned comparison and add/delete paths."""
    env = typecheck.git_environment()
    for command in (
        ["init"], ["config", "user.name", "Example"],
        ["config", "user.email", "example@example.com"],
        ["config", "commit.gpgsign", "true"],
        ["config", "gpg.program", "missing-fixture-signer"],
    ):
        assert typecheck._run(["git", *command], tmp_path, env=env).returncode == 0
    for name in ("staged.py", "unstaged.py", "removed.py"):
        (tmp_path / name).write_text("value: int = 1\n", encoding="utf-8")
    assert typecheck._run(["git", "add", "."], tmp_path, env=env).returncode == 0
    assert typecheck._run(
        ["git", "-c", "commit.gpgsign=false", "commit", "-m", "Synthetic base"],
        tmp_path, env=env,
    ).returncode == 0
    (tmp_path / "staged.py").write_text("value: int = 2\n", encoding="utf-8")
    assert typecheck._run(["git", "add", "staged.py"], tmp_path, env=env).returncode == 0
    (tmp_path / "unstaged.py").write_text("value: int = 3\n", encoding="utf-8")
    (tmp_path / "added.py").write_text("value: int = 4\n", encoding="utf-8")
    (tmp_path / "removed.py").unlink()
    assert set(typecheck.changed_paths(tmp_path, "HEAD")) == {"staged.py", "unstaged.py", "removed.py"}
    assert typecheck.changed_paths(tmp_path, "HEAD", "HEAD") == ()
    assert set(typecheck.python_paths(tmp_path)) == {"staged.py", "unstaged.py", "added.py"}
    with pytest.raises(typecheck.TypecheckError, match="Cannot resolve"):
        typecheck.changed_paths(tmp_path, "missing-comparison-base")


def test_changed_selection_conservatively_checks_all_admitted_consumers(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """Even an empty diff cannot omit a consumer before dependency qualification."""
    first = project()
    caller = typecheck.Project(
        "caller", ("caller.py",), (), "strict", "3.10", ("linux",), "requirements.txt",
    )
    checked: list[str] = []

    def load_projects() -> tuple[typecheck.Project, ...]:
        return first, caller

    def python_paths() -> tuple[str, ...]:
        return "sample.py", "caller.py"

    def changed_paths(_root: Path, base: str, head: str | None) -> tuple[str, ...]:
        assert base == "origin/dev" and head is None
        return ()

    def check_project(
        _root: Path, item: typecheck.Project, _files: tuple[str, ...], *,
        all_paths: tuple[str, ...], strict: bool = False,
    ) -> int:
        checked.append(item.name)
        return 0

    monkeypatch.setattr(typecheck, "load_projects", load_projects)
    monkeypatch.setattr(typecheck, "python_paths", python_paths)
    monkeypatch.setattr(typecheck, "changed_paths", changed_paths)
    monkeypatch.setattr(typecheck, "check_project", check_project)
    assert typecheck.main(["--changed"]) == 0
    assert checked == ["sample", "caller"]
    assert "conservative dependency impact" in capsys.readouterr().out


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


def test_dependency_policy_refusal_has_the_setup_error_contract(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """A supported policy refusal returns an actionable error, not a traceback."""
    item = project()

    def load_projects() -> tuple[typecheck.Project, ...]:
        return (item,)

    def python_paths() -> tuple[str, ...]:
        return ("sample.py",)

    def prepare(_root: Path, _project: typecheck.Project) -> None:
        raise DependencyPolicyError("unsupported UV configuration")

    monkeypatch.setattr(typecheck, "load_projects", load_projects)
    monkeypatch.setattr(typecheck, "python_paths", python_paths)
    monkeypatch.setattr(typecheck, "prepare", prepare)
    assert typecheck.main(["--prepare"]) == 2
    stderr = capsys.readouterr().err
    assert "typecheck: unsupported UV configuration" in stderr
    assert "Traceback" not in stderr


class Lease:
    """Only the release surface is substituted in checker subprocess controls."""

    def __init__(self) -> None:
        self.released = False

    def release(self) -> None:
        self.released = True


def test_preparation_installs_the_same_lock_bytes_that_keyed_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A changed original cannot replace the snapshot admitted under the old digest."""
    requirements = tmp_path / "requirements.txt"
    initial = b"mypy==1.18.2\n"
    requirements.write_bytes(initial)
    lease = Lease()
    captured: list[bytes] = []

    def acquire(_wait: float, _path: Path) -> Lease:
        requirements.write_bytes(b"mypy==0.1\n")
        return lease

    def which(_name: str) -> str:
        return "uv"

    def run(
        argv: list[str], _root: Path, *, env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, "", "")

    def install(
        _uv: str, arguments: list[str], *, cwd: Path,
        trusted_source_roots: tuple[Path, ...], check: bool, timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        captured.append(Path(arguments[arguments.index("-r") + 1]).read_bytes())
        assert timeout == 300
        return subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(typecheck, "acquire_environment", acquire)
    monkeypatch.setattr(shutil, "which", which)
    monkeypatch.setattr(typecheck, "_run", run)
    monkeypatch.setattr(typecheck, "run_uv_install", install)
    typecheck.prepare(tmp_path, project())
    assert captured == [initial]
    assert lease.released
    requirements.write_bytes(initial)
    path, digest = typecheck.environment_path(tmp_path, project())
    assert (path / ".typecheck-prepared").read_text(encoding="utf-8").strip() == digest


def test_failed_cache_repair_cannot_preserve_preparation_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A restored interpreter alone must not certify a failed dependency repair."""
    (tmp_path / "requirements.txt").write_bytes(b"mypy==1.18.2\n")
    path, digest = typecheck.environment_path(tmp_path, project())
    path.mkdir(parents=True)
    marker = path / ".typecheck-prepared"
    marker.write_text(digest + "\n", encoding="utf-8")
    leases: list[Lease] = []
    installs: list[bool] = []

    def acquire(_wait: float, _path: Path) -> Lease:
        lease = Lease()
        leases.append(lease)
        return lease

    def which(_name: str) -> str:
        return "uv"

    def run(
        argv: list[str], _root: Path, *, env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        assert not marker.exists()
        python = typecheck.environment_python(path)
        python.parent.mkdir(parents=True, exist_ok=True)
        python.touch()
        return subprocess.CompletedProcess(argv, 0, "", "")

    def install(
        _uv: str, arguments: list[str], *, cwd: Path,
        trusted_source_roots: tuple[Path, ...], check: bool, timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        installs.append(True)
        if len(installs) == 1:
            raise subprocess.TimeoutExpired(arguments, timeout)
        return subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(typecheck, "acquire_environment", acquire)
    monkeypatch.setattr(shutil, "which", which)
    monkeypatch.setattr(typecheck, "_run", run)
    monkeypatch.setattr(typecheck, "run_uv_install", install)
    with pytest.raises(subprocess.TimeoutExpired):
        typecheck.prepare(tmp_path, project())
    assert not marker.exists()
    with pytest.raises(typecheck.TypecheckError, match="--prepare"):
        typecheck.check_project(tmp_path, project(), ("sample.py",), all_paths=("sample.py",))
    typecheck.prepare(tmp_path, project())
    assert len(installs) == 2
    assert all(lease.released for lease in leases)
    assert marker.read_text(encoding="utf-8").strip() == digest


def test_preparation_refuses_unqualified_requirement_includes(tmp_path: Path) -> None:
    """A mutable include graph cannot pretend to be a one-file lock snapshot."""
    (tmp_path / "requirements.txt").write_text("-r elsewhere.txt\n", encoding="utf-8")
    with pytest.raises(typecheck.TypecheckError, match="flat hash-locked export"):
        typecheck.environment_path(tmp_path, project())


def test_preparation_refuses_replaced_leased_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preparation cannot switch filesystem identities beneath the shared lease."""
    (tmp_path / "requirements.txt").write_text("mypy==1.18.2\n", encoding="utf-8")
    environment = tmp_path / "environment"
    environment.mkdir()
    lease = Lease()

    def environment_path(
        _root: Path, _project: typecheck.Project, *, captured_lock: bytes | None = None,
    ) -> tuple[Path, str]:
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


def test_readiness_is_revalidated_under_the_checker_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A repair between preflight and acquisition cannot launch an unprepared checker."""
    marker = tmp_path / ".typecheck-prepared"
    marker.write_text("test-context\n", encoding="utf-8")
    lease = Lease()

    def environment_path(
        _root: Path, _project: typecheck.Project, *, captured_lock: bytes | None = None,
    ) -> tuple[Path, str]:
        return tmp_path, "test-context"

    def environment_python(_path: Path) -> Path:
        return Path(sys.executable)

    def acquire(_wait: float, _path: Path) -> Lease:
        marker.unlink()
        return lease

    def run(
        argv: list[str], _root: Path, *, env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        pytest.fail("MyPy must not start after admission was invalidated")

    monkeypatch.setattr(typecheck, "environment_path", environment_path)
    monkeypatch.setattr(typecheck, "environment_python", environment_python)
    monkeypatch.setattr(typecheck, "acquire_environment", acquire)
    monkeypatch.setattr(typecheck, "_run", run)
    with pytest.raises(typecheck.TypecheckError, match="--prepare"):
        typecheck.check_project(tmp_path, project(), ("sample.py",), all_paths=("sample.py",))
    assert lease.released
    assert not (tmp_path / "typecheck-targets.txt").exists()


def test_checker_timeout_settles_targets_and_environment_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed checker leaves neither a target-file writer nor an admitted lease."""
    (tmp_path / ".typecheck-prepared").write_text("test-context\n", encoding="utf-8")
    lease = Lease()

    def environment_path(
        _root: Path, _project: typecheck.Project, *, captured_lock: bytes | None = None,
    ) -> tuple[Path, str]:
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
    ("source", "level", "expected", "filename"),
    [
        ("def accept(value: int) -> int:\n    return value\naccept('wrong')\n", "basic", "[arg-type]", "sample.py"),
        ("def body(value):\n    wrong: int = 'text'\n    return wrong\n", "basic", "[assignment]", "sample.py"),
        ("def missing(value):\n    return value\n", "strict", "[no-untyped-def]", "sample.py"),
        ("def generic(value: dict) -> dict:\n    return value\n", "strict", "[type-arg]", "sample.py"),
        ("wrong: int = 'text'\n", "strict", "[assignment]", "caf\u00e9.py"),
    ],
)
def test_actual_mypy_rejects_type_regressions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    source: str, level: typecheck.Level, expected: str, filename: str,
) -> None:
    """Exercise the real front door's MyPy argv, not a mocked checker result."""
    root = tmp_path / "source with spaces"
    root.mkdir()
    (root / filename).write_text(source, encoding="utf-8")
    environment = tmp_path / "environment with spaces"
    environment.mkdir()
    (environment / ".typecheck-prepared").write_text("test-context\n", encoding="utf-8")
    lease = Lease()

    def environment_path(
        _root: Path, _project: typecheck.Project, *, captured_lock: bytes | None = None,
    ) -> tuple[Path, str]:
        return environment, "test-context"

    def environment_python(_path: Path) -> Path:
        return Path(sys.executable)

    def acquire(_wait: float, _path: Path) -> Lease:
        return lease

    monkeypatch.setattr(typecheck, "environment_path", environment_path)
    monkeypatch.setattr(typecheck, "environment_python", environment_python)
    monkeypatch.setattr(typecheck, "acquire_environment", acquire)
    assert typecheck.check_project(
        root, project(level=level), (filename,), all_paths=(filename,),
    ) != 0
    assert expected in capsys.readouterr().out
    assert lease.released
    assert not (environment / "typecheck-targets.txt").exists()
    assert not (environment / "typecheck-mypy.ini").exists()


def fragment_source(tmp_path: Path) -> tuple[str, ...]:
    """Use the real loader with a small, deliberately interdependent namespace."""
    directory = tmp_path / "libs" / "installation-context"
    directory.mkdir(parents=True)
    entry = (typecheck.ROOT / views.INSTALLATION_ENTRY).read_text(encoding="utf-8")
    first = entry.index("_FRAGMENT_FILES = (")
    last = entry.index("\n)", first) + 2
    entry = entry[:first] + (
        '_FRAGMENT_FILES = ("_installation_context_base.py", "_installation_context_files.py")'
    ) + entry[last:]
    (directory / "installation_context.py").write_text(entry, encoding="utf-8")
    (directory / "_installation_context_base.py").write_text(
        "from __future__ import annotations\n"
        "def shared(value: int) -> int:\n    return value\n",
        encoding="utf-8",
    )
    (directory / "_installation_context_files.py").write_text(
        "from __future__ import annotations\nresult: int = shared('wrong')\n"
        "raise RuntimeError('static checking must not execute this module')\n",
        encoding="utf-8",
    )
    return tuple(path.relative_to(tmp_path).as_posix() for path in sorted(directory.glob("*.py")))


@pytest.mark.parametrize("newline", [b"\n", b"\r\n", b"\r"])
def test_source_view_accounts_for_order_bytes_and_physical_lines(
    tmp_path: Path, newline: bytes,
) -> None:
    """Every physical line, including replaced future imports, has a source mapping."""
    paths = fragment_source(tmp_path)
    for path in paths:
        source = tmp_path / path
        content = source.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        source.write_bytes(content.replace(b"\n", newline))
    view = views.installation_view(tmp_path, paths, tmp_path / "view" / "installation_context.py")
    view.verify(tmp_path)
    assert tuple(path for path, _content in view.inputs) == (
        views.INSTALLATION_ENTRY,
        "libs/installation-context/_installation_context_base.py",
        "libs/installation-context/_installation_context_files.py",
    )
    for path, content in view.inputs:
        mapped = [line.number for line in view.lines if line.path == path]
        assert mapped == list(range(1, len(content.splitlines()) + 1))
    diagnostic_line = next(
        number for number, line in enumerate(view.lines, 1)
        if line.path.endswith("_files.py") and line.number == 2
    )
    assert view.diagnostics(f"{view.output}:{diagnostic_line}: error: example\n", tmp_path) == (
        "libs/installation-context/_installation_context_files.py:2: error: example\n"
    )


@pytest.mark.parametrize("change", ["source", "order", "view", "extra", "missing", "loader"])
def test_source_view_refuses_stale_or_incomplete_composition(tmp_path: Path, change: str) -> None:
    """Omission, reordering and changed code cannot reuse an old qualification."""
    paths = fragment_source(tmp_path)
    output = tmp_path / "view" / "installation_context.py"
    view = views.installation_view(tmp_path, paths, output)
    entry = tmp_path / views.INSTALLATION_ENTRY
    fragment = entry.with_name("_installation_context_files.py")
    if change == "source":
        fragment.write_bytes(fragment.read_bytes() + b"extra: int = 1\n")
    elif change == "order":
        content = entry.read_bytes().replace(
            b'"_installation_context_base.py", "_installation_context_files.py"',
            b'"_installation_context_files.py", "_installation_context_base.py"',
        )
        entry.write_bytes(content)
    elif change == "view":
        output.write_bytes(view.content + b"extra: int = 1\n")
    elif change == "extra":
        new = entry.with_name("_installation_context_new.py")
        new.write_bytes(b"extra: int = 1\n")
        paths = (*paths, new.relative_to(tmp_path).as_posix())
    elif change == "missing":
        paths = tuple(path for path in paths if not path.endswith("_files.py"))
    else:
        entry.write_bytes(entry.read_bytes().replace(b"for fragment_name in _FRAGMENT_FILES:", b"for fragment_name in reversed(_FRAGMENT_FILES):"))
    with pytest.raises(views.SourceViewError):
        if change in {"extra", "missing", "loader"}:
            views.installation_view(tmp_path, paths, output)
        else:
            view.verify(tmp_path)


@pytest.mark.parametrize(
    "source",
    [
        "from __future__ import annotations; wrong: int = 'text'\n",
        "# mypy: ignore-errors\nwrong: int = 'text'\n",
        "# type: ignore\nwrong: int = 'text'\n",
        "# harmless\r# mypy: ignore-errors\rwrong: int = 'text'\r",
        "# harmless\r# type: ignore\rwrong: int = 'text'\r",
        "value: int = 1\nfrom __future__ import annotations\n",
    ],
)
def test_composition_cannot_drop_code_or_spread_file_wide_suppressions(
    tmp_path: Path, source: str,
) -> None:
    paths = fragment_source(tmp_path)
    fragment = tmp_path / "libs" / "installation-context" / "_installation_context_base.py"
    fragment.write_text(source, encoding="utf-8")
    with pytest.raises(views.SourceViewError):
        views.installation_view(tmp_path, paths, tmp_path / "view" / "installation_context.py")


@pytest.mark.parametrize(
    ("level", "argument", "expected"),
    [("basic", "'wrong'", 1), ("strict", "'wrong'", 1), ("strict", "3", 0)],
)
def test_actual_mypy_sees_shared_fragment_contract_and_maps_its_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    level: typecheck.Level, argument: str, expected: int,
) -> None:
    """The actual checker sees cross-fragment definitions without executing either."""
    paths = fragment_source(tmp_path)
    fragment = tmp_path / "libs" / "installation-context" / "_installation_context_files.py"
    fragment.write_bytes(fragment.read_bytes().replace(b"'wrong'", argument.encode("ascii")))
    environment = tmp_path / "environment"
    environment.mkdir()
    (environment / ".typecheck-prepared").write_text("test-context\n", encoding="utf-8")
    lease = Lease()

    def environment_path(
        _root: Path, _project: typecheck.Project, *, captured_lock: bytes | None = None,
    ) -> tuple[Path, str]:
        return environment, "test-context"

    def environment_python(_path: Path) -> Path:
        return Path(sys.executable)

    def acquire(_wait: float, _path: Path) -> Lease:
        return lease

    monkeypatch.setattr(typecheck, "environment_path", environment_path)
    monkeypatch.setattr(typecheck, "environment_python", environment_python)
    monkeypatch.setattr(typecheck, "acquire_environment", acquire)
    assert typecheck.check_project(tmp_path, project(level=level), paths, all_paths=paths) == expected
    output = capsys.readouterr().out
    if expected:
        assert "_installation_context_files.py:2: error:" in output
        assert "[arg-type]" in output and "[name-defined]" not in output
    else:
        assert "Success: no issues found" in output
    assert lease.released
    assert not (tmp_path / "source-views-environment" / "sample" / "installation_context.py").exists()
