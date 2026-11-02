"""Actor-free decisions of the candidate-local checker that gates signing."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

import typecheck as checker
from tools import _admission_protocol as admission


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
