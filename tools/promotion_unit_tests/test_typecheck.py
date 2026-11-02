"""Actor-free decisions of the candidate-local checker that gates signing."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

import typecheck as checker
from tools import _admission_protocol as admission
from tools import typecheck_source_views as views


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


def test_candidate_checker_preserves_checker_failure_and_environment_custody(
    tmp_path, monkeypatch
):
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
        return subprocess.CompletedProcess(argv, 7, "fixture checker refusal\n", "")

    monkeypatch.setattr(admission, "SingleInstance", Lease)
    monkeypatch.setattr(checker, "_run", run)
    assert (
        checker.check_project(
            tmp_path,
            project,
            ("admitted.py",),
            all_paths=("admitted.py",),
        )
        == 7
    )
    assert events == ["acquired", "released"]
    assert not (environment / "typecheck-targets.txt").exists()
    assert not (environment / "typecheck-mypy.ini").exists()


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
