"""Actor-free decisions of the candidate-local checker that gates signing."""

from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

import typecheck as checker
from tools import _admission_protocol as admission
from tools import typecheck_source_views as views


@pytest.fixture(autouse=True)
def fixed_checker_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(checker, "uuid4", lambda: SimpleNamespace(hex=""))


@pytest.fixture(autouse=True)
def _isolated_scratch(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_SCRATCH_ROOT", str(tmp_path_factory.mktemp("scratch")))


@pytest.fixture
def guard_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> tuple[checker.Project, tuple[str, ...], Path, list[str]]:
    source = tmp_path / views.GUARD_ENTRY
    source.parent.mkdir(parents=True)
    source.write_bytes(
        b"def accept(value: int) -> int:\r\n    return value\r\n"
        b"raise RuntimeError('unit source must never execute')\r\n"
    )
    extra = source.with_name("new_owned_member.py")
    extra.write_bytes(b"value: int = 1\n")
    (tmp_path / "libs/installer-engine").mkdir(parents=True)
    project = checker.Project(
        "copilot-extensions-harness", ("plugins/copilot-extensions-harness/scripts",), (),
        "pending", "3.10", ("linux",), "requirements.txt",
        import_roots=("plugins/copilot-extensions-harness/scripts",),
    )
    (tmp_path / "requirements.txt").write_bytes(b"mypy==1.18.2\n")
    environment, digest = checker.environment_path(tmp_path, project)
    python = checker.environment_python(environment)
    python.parent.mkdir(parents=True)
    python.write_bytes(b"inert unit interpreter; never executed")
    (environment / ".typecheck-prepared").write_bytes(digest.encode() + b"\n")
    events: list[str] = []

    class Lease:
        def __init__(self, path: Path, *, service: str) -> None:
            assert service == admission.ADMISSION_SERVICE and path.name

        def acquire(self) -> None:
            events.append("acquired")

        def release(self) -> None:
            events.append("released")

    monkeypatch.setattr(admission, "SingleInstance", Lease)
    return project, (views.GUARD_ENTRY, extra.relative_to(tmp_path).as_posix()), environment, events


def _installation_inputs(root: Path) -> tuple[str, ...]:
    directory = Path(views.INSTALLATION_ENTRY).parent
    canonical = Path(__file__).resolve().parents[2] / directory
    sources = [
        path for path in canonical.glob("*.py")
        if path.name == "installation_context.py" or path.name.startswith("_installation_context_")
    ]
    for source in sources:
        destination = root / directory / source.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
    return tuple((directory / source.name).as_posix() for source in sources)


@pytest.mark.parametrize("scope", ["owned", "other-project", "incomplete"])
def test_candidate_guard_alias_exact_owned_scope_and_N_to_N(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    guard_scope: tuple[checker.Project, tuple[str, ...], Path, list[str]], scope: str,
) -> None:
    project, files, environment, events = guard_scope
    all_paths = files
    original = (tmp_path / views.GUARD_ENTRY).read_bytes()
    if scope == "other-project":
        (tmp_path / "sample.py").write_bytes(b"value: int = 1\n")
        project = replace(project, name="other", include=("sample.py",), import_roots=())
        files = ("sample.py",)
        all_paths = (*all_paths, *files)
    elif scope == "incomplete":
        files = files[:1]
    calls: list[tuple[str, ...]] = []
    mkdir = Path.mkdir
    stage = environment.parent / ("source-views-" + environment.name) / project.name
    creation_races: list[str] = []
    scratch = Path(os.environ["AGENT_SCRATCH_ROOT"]).resolve()

    def owned_mkdir(
        path: Path, mode: int = 0o777, parents: bool = False, exist_ok: bool = False,
    ) -> None:
        resolved = path.resolve()
        assert resolved.is_relative_to(tmp_path) or resolved.is_relative_to(scratch), (
            "mkdir outside synthetic unit storage"
        )
        if scope == "owned" and path == stage and not creation_races:
            mkdir(path, mode=mode, parents=parents, exist_ok=exist_ok)
            creation_races.append("ordinary directory won creation race")
            raise FileExistsError(path)
        mkdir(path, mode=mode, parents=parents, exist_ok=exist_ok)

    monkeypatch.setattr(Path, "mkdir", owned_mkdir)

    def run(
        argv: list[str], root: Path, *, env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        targets = tuple(Path(argv[-1][1:]).read_text().splitlines())
        calls.append(targets)
        assert root == tmp_path and len(targets) == len(files)
        if scope == "other-project":
            assert targets == files and "source-views-" not in env["MYPYPATH"]
            return subprocess.CompletedProcess(argv, 0, "", "")
        alias = Path(targets[0])
        assert alias.name == "copilot_mention_guard.py" and alias.read_bytes() == original
        assert targets[1:] == files[1:] and views.GUARD_ENTRY not in targets
        assert env["MYPYPATH"].split(os.pathsep).count(str(alias.parent)) == 1
        return subprocess.CompletedProcess(argv, 0, f"{alias}:2:7: error: unit correspondence\n", "")

    monkeypatch.setattr(checker, "_run", run)
    if scope == "incomplete":
        with pytest.raises(checker.TypecheckError, match="complete owned physical binding"):
            checker.check_project(tmp_path, project, files, all_paths=all_paths)
        assert not calls
    else:
        assert checker.check_project(tmp_path, project, files, all_paths=all_paths) == 0
        assert len(calls) == 1
        if scope == "owned":
            assert creation_races == ["ordinary directory won creation race"]
            assert f"{views.GUARD_ENTRY}:2:7: error: unit correspondence" in capsys.readouterr().out
    assert events == ["acquired", "released"]
    assert (tmp_path / views.GUARD_ENTRY).read_bytes() == original
    assert not list(environment.glob("typecheck-*"))
    assert not list(environment.parent.glob(f"source-views-{environment.name}/{project.name}/*.py"))


@pytest.mark.parametrize("change", ["provider", "stub", "source", "source-identity", "view", "replaced-view"])
def test_candidate_guard_alias_refuses_provider_and_identity_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    guard_scope: tuple[checker.Project, tuple[str, ...], Path, list[str]], change: str,
) -> None:
    project, files, environment, events = guard_scope
    all_paths = files
    source = tmp_path / views.GUARD_ENTRY
    alias = environment.parent / ("source-views-" + environment.name) / project.name / "copilot_mention_guard.py"
    calls: list[str] = []
    if change in {"provider", "stub"}:
        suffix = ".py" if change == "provider" else ".pyi"
        provider = tmp_path / ("copilot_mention_guard" + suffix)
        provider.write_bytes(b"foreign: int = 1\n")
        if change == "provider":
            all_paths = (*all_paths, provider.name)

    def run(
        argv: list[str], root: Path, *, env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        calls.append("called")
        assert alias.read_bytes() == source.read_bytes()
        if change == "source":
            source.write_bytes(source.read_bytes() + b"# changed\n")
        elif change == "source-identity":
            content = source.read_bytes()
            source.rename(source.with_suffix(".saved"))
            source.write_bytes(content)
        elif change == "view":
            alias.write_bytes(b"changed: int = 1\n")
        elif change == "replaced-view":
            alias.rename(alias.with_suffix(".saved"))
            alias.write_bytes(b"foreign: int = 1\n")
        else:
            pytest.fail("A competing provider cannot reach the inert analyzer")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(checker, "_run", run)
    with pytest.raises((checker.TypecheckError, views.SourceViewError)):
        checker.check_project(tmp_path, project, files, all_paths=all_paths)
    assert bool(calls) == (change not in {"provider", "stub"})
    assert events == ["acquired", "released"]
    assert not list(environment.glob("typecheck-*"))
    if change == "replaced-view":
        assert alias.read_bytes() == b"foreign: int = 1\n"
        assert alias.with_suffix(".saved").exists()
    else:
        assert not alias.exists()


@pytest.mark.parametrize("change", ["unchanged", "guard-drift", "fragment-drift"])
def test_candidate_guard_and_installation_views_keep_independent_custody(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    guard_scope: tuple[checker.Project, tuple[str, ...], Path, list[str]], change: str,
) -> None:
    project, files, environment, events = guard_scope
    installation = _installation_inputs(tmp_path)
    worker = "libs/installation-context/_installation_context_worker.py"
    project = replace(project, include=(*project.include, worker))
    files = (*files, worker)
    all_paths = (*files[:2], *installation)
    worker_bytes = (tmp_path / worker).read_bytes()
    stage = environment.parent / ("source-views-" + environment.name) / project.name
    calls: list[str] = []

    def run(
        argv: list[str], root: Path, *, env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        calls.append("called")
        alias, composed = stage / "copilot_mention_guard.py", stage / "installation_context.py"
        assert alias.is_file() and composed.is_file()
        assert env["MYPYPATH"].split(os.pathsep).count(str(stage)) == 1
        targets = Path(argv[-1][1:]).read_text().splitlines()
        assert targets == [str(alias), files[1], worker]
        assert (tmp_path / worker).read_bytes() == worker_bytes
        if change == "guard-drift":
            source = tmp_path / views.GUARD_ENTRY
            source.write_bytes(source.read_bytes() + b"# changed\n")
        elif change == "fragment-drift":
            fragment = next(name for name in installation if "_installation_context_" in name and name != worker)
            source = tmp_path / fragment
            source.write_bytes(source.read_bytes() + b"\n")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(checker, "_run", run)
    if change == "unchanged":
        assert checker.check_project(tmp_path, project, files, all_paths=all_paths) == 0
    else:
        with pytest.raises(views.SourceViewError, match="source changed"):
            checker.check_project(tmp_path, project, files, all_paths=all_paths)
    assert calls == ["called"] and events == ["acquired", "released"]
    if change == "unchanged":
        assert not stage.exists()
    else:
        assert stage.is_dir() and not list(stage.iterdir())
    assert not list(environment.glob("typecheck-*"))
    assert (tmp_path / worker).read_bytes() == worker_bytes


def test_candidate_checker_refuses_unclassified_pending_and_failed_scopes(
    tmp_path, monkeypatch
):
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "typecheck-projects.json").write_text(
        json.dumps(
            {
                "version": 1,
                "python_version": "3.10",
                "platforms": ["linux", "win32"],
                "requirements": "requirements.txt",
                "projects": [
                    {
                        "name": name,
                        "include": [name + ".py"],
                        "exclude": [],
                        "level": level,
                    }
                    for name, level in (("admitted", "strict"), ("pending", "pending"))
                ],
            }
        ),
        encoding="utf-8",
    )
    projects = checker.load_projects(tmp_path)
    with pytest.raises(checker.TypecheckError, match="unclassified"):
        checker.inventory(("unclassified.py",), projects)
    monkeypatch.setattr(checker, "load_projects", lambda: projects)
    monkeypatch.setattr(checker, "python_paths", lambda: ("admitted.py", "pending.py"))
    calls = []

    def check(_root, project, files, **_kwargs):
        calls.append((project.name, files))
        return 1

    monkeypatch.setattr(checker, "check_project", check)
    assert checker.main(["--enforced"]) == 1
    assert calls == [("admitted", ("admitted.py",))]
    monkeypatch.setattr(checker, "check_project", lambda *_args, **_kwargs: 0)
    assert checker.main(["--all"]) == 1
    assert checker.main(["--enforced", "--require-complete"]) == 2


@pytest.mark.parametrize("prepare", [False, True])
def test_all_projects_continues_past_unmaterialized_pending_copies(monkeypatch, capsys, prepare):
    projects = tuple(
        checker.Project(name, (name + ".py",), (), level, "3.10", ("linux",), "requirements.txt")
        for name, level in (("admitted", "strict"), ("pending-copy", "pending"))
    )
    monkeypatch.setattr(checker, "load_projects", lambda: projects)
    monkeypatch.setattr(checker, "python_paths", lambda: ("admitted.py",))
    calls = []

    def check(_root, project, files, **_kwargs):
        assert files == ("admitted.py",)
        calls.append(project.name)
        return 0

    monkeypatch.setattr(checker, "check_project", check)
    monkeypatch.setattr(checker, "prepare", lambda _root, project: calls.append(project.name))
    monkeypatch.setattr(checker, "environment_path", lambda _root, _project: (Path("fixture"), "digest"))
    argv = ["--all", "--prepare"] if prepare else ["--all"]
    assert checker.main(argv) == (0 if prepare else 1)
    assert calls == ["admitted"]
    assert "pending-copy: pending, no physical Python files" in capsys.readouterr().out


@pytest.mark.parametrize("with_dependency", [False, True])
def test_candidate_checker_preserves_checker_failure_and_environment_custody(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, with_dependency: bool,
) -> None:
    project = checker.Project(
        "admitted",
        ("admitted.py",),
        (),
        "strict",
        "3.10",
        ("linux", "win32"),
        "requirements.txt",
    )
    (tmp_path / "requirements.txt").write_bytes(b"mypy==1.18.2\n")
    with pytest.raises(checker.TypecheckError, match="--prepare"):
        checker.check_project(
            tmp_path, project, ("admitted.py",), all_paths=("admitted.py",)
        )
    environment, digest = checker.environment_path(tmp_path, project)
    python = checker.environment_python(environment)
    python.parent.mkdir(parents=True)
    python.write_bytes(b"fixture; never executed")
    (environment / ".typecheck-prepared").write_bytes(digest.encode() + b"\n")
    events = []
    paths = ("admitted.py",)
    if with_dependency:
        root = Path(__file__).resolve().parents[2]
        directory = Path(views.INSTALLATION_ENTRY).parent
        inputs = [
            path for path in (root / directory).glob("*.py")
            if path.name == "installation_context.py"
            or path.name.startswith("_installation_context_")
        ]
        for source in inputs:
            destination = tmp_path / source.relative_to(root)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read_bytes())
        paths = (*paths, *(path.relative_to(root).as_posix() for path in inputs))

    class Lease:
        def __init__(self, path: Path, *, service: str):
            assert service == admission.ADMISSION_SERVICE
            assert path.name

        def acquire(self):
            events.append("acquired")

        def release(self):
            events.append("released")

    def run(argv, root, *, env):
        assert root == tmp_path
        assert argv[:6] == [str(python), "-I", "-X", "utf8", "-m", "mypy"]
        assert "--strict" in argv and "--check-untyped-defs" in argv
        assert Path(argv[-1][1:]).read_text(encoding="utf-8") == "admitted.py\n"
        assert "PYTHONPATH" not in env and "PYTHONHOME" not in env
        if with_dependency:
            view = environment.parent / ("source-views-" + environment.name) / "admitted"
            assert env["MYPYPATH"].startswith(str(view))
            assert (view / "installation_context.py").is_file()
        return subprocess.CompletedProcess(argv, 7, "fixture checker refusal\n", "")

    monkeypatch.setattr(admission, "SingleInstance", Lease)
    monkeypatch.setattr(checker, "_run", run)
    assert (
        checker.check_project(
            tmp_path,
            project,
            ("admitted.py",),
            all_paths=paths,
        )
        == 7
    )
    assert events == ["acquired", "released"]
    assert not (environment / "typecheck-targets.txt").exists()
    assert not (environment / "typecheck-mypy.ini").exists()
    view = environment.parent / ("source-views-" + environment.name) / "admitted"
    assert not (view / "installation_context.py").exists()


def test_candidate_projects_reuse_dependency_cache_without_reusing_verdicts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    guard_scope: tuple[checker.Project, tuple[str, ...], Path, list[str]],
) -> None:
    project, files, environment, events = guard_scope
    project = replace(
        project, name="first", include=files[:1], import_roots=(), level="strict",
        platforms=("linux", "win32"), additional_python_versions=("3.11",),
    )
    other = replace(project, name="second", include=files[1:])
    calls: list[tuple[str, str, Path, tuple[str, ...]]] = []

    def run(
        argv: list[str], root: Path, *, env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        version = argv[argv.index("--python-version") + 1]
        platform = argv[argv.index("--platform") + 1]
        cache = Path(argv[argv.index("--cache-dir") + 1])
        targets = tuple(Path(argv[-1][1:]).read_text().splitlines())
        assert root == tmp_path and "--strict" in argv
        assert "PYTHONPATH" not in env and "PYTHONHOME" not in env
        calls.append((version, platform, cache, targets))
        cache.mkdir(parents=True, exist_ok=True)
        (cache / "inert-unit-cache").write_bytes(b"not an analyzer result")
        return subprocess.CompletedProcess(argv, int(targets == files[1:]), "", "")

    monkeypatch.setattr(checker, "_run", run)
    assert checker.check_project(tmp_path, project, files[:1], all_paths=files) == 0
    assert checker.check_project(tmp_path, other, files[1:], all_paths=files) == 1
    assert len(calls) == 8
    assert len({cache for _version, _platform, cache, _targets in calls}) == 4
    for first, second in zip(calls[:4], calls[4:]):
        assert first[:3] == second[:3]
        assert first[3] == files[:1] and second[3] == files[1:]
    assert events == ["acquired", "released", "acquired", "released"]
    assert not list(environment.glob("typecheck-*"))


def test_candidate_source_view_binds_fragment_membership_bytes_and_diagnostics(tmp_path):
    root = Path(__file__).resolve().parents[2]
    directory = Path(views.INSTALLATION_ENTRY).parent
    inputs = [
        path for path in (root / directory).glob("*.py")
        if path.name == "installation_context.py" or path.name.startswith("_installation_context_")
    ]
    paths = tuple(path.relative_to(root).as_posix() for path in inputs)
    for path in inputs:
        destination = tmp_path / path.relative_to(root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(path.read_bytes())
    view = views.installation_view(tmp_path, paths, tmp_path / "view" / "installation_context.py")
    view.verify(tmp_path)
    entry = dict(view.inputs)[views.INSTALLATION_ENTRY].splitlines()
    helper_line = next(index for index, text in enumerate(entry, 1) if b"def _native_fragment(" in text)
    mapped_line = next(
        index for index, source in enumerate(view.lines, 1)
        if source.path == views.INSTALLATION_ENTRY and source.number == helper_line
    )
    assert view.content.splitlines()[mapped_line - 1] == entry[helper_line - 1]
    assert view.diagnostics(f"{view.output}:{mapped_line}: error: sample\n", tmp_path) == (
        f"{views.INSTALLATION_ENTRY}:{helper_line}: error: sample\n"
    )
    loader = tmp_path / views.INSTALLATION_ENTRY
    original = loader.read_bytes()
    altered = original.replace(b"_native_fragment(fragment_path).read_text", b"fragment_path.read_text")
    assert altered != original
    loader.write_bytes(altered)
    with pytest.raises(views.SourceViewError, match="loader changed"):
        views.installation_view(tmp_path, paths, tmp_path / "refused" / "installation_context.py")
    loader.write_bytes(original)
    line = view.lines[-1]
    assert view.diagnostics(f"{view.output}:{len(view.lines)}: note: sample\n", tmp_path) == (
        f"{line.path}:{line.number}: note: sample\n"
    )
    fragment = tmp_path / view.inputs[1][0]
    fragment.write_bytes(fragment.read_bytes() + b"\nextra: int = 1\n")
    with pytest.raises(views.SourceViewError, match="source changed"):
        view.verify(tmp_path)
    fragment.write_bytes(view.inputs[1][1])
    new = fragment.with_name("_installation_context_extra.py")
    new.write_bytes(b"extra: int = 1\n")
    with pytest.raises(views.SourceViewError, match="membership"):
        view.verify(tmp_path)
    with pytest.raises(views.SourceViewError, match="membership"):
        views.installation_view(
            tmp_path, (*paths, new.relative_to(tmp_path).as_posix()), view.output,
        )
    new.unlink()
    for invalid in (
        b"# harmless\r# mypy: ignore-errors\rwrong: int = 'text'\r",
        b"value: int = 1\nfrom __future__ import annotations\n",
    ):
        fragment.write_bytes(invalid)
        with pytest.raises(views.SourceViewError):
            views.installation_view(tmp_path, paths, view.output)
