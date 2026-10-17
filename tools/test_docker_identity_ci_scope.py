"""Scope, execution and minimum-runtime contract for the fixture-only lane."""
from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path

import pytest
import yaml

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("docker_identity_ci_scope", HERE / "docker_identity_ci_scope.py")
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@pytest.mark.parametrize("path", [
    "libs/docker-resource-producer/src/docker_resource_producer/models.py",
    "libs/docker-resource-producer/tests/test_identity.py",
    "libs/docker-resource-producer/pyproject.toml",
    "tools/docker_identity_ci_scope.py", "tools/test_docker_identity_ci_scope.py",
    ".github/workflows/ci.yml",
    "tools/accumulate_bumps.py", "tools/test_accumulate_bumps.py",
])
def test_selects_contract_surface(path: str) -> None:
    assert MODULE.needs_docker_identity_smoke([path])


@pytest.mark.parametrize("paths", [[], ["docs/architecture.md"], ["plugins/agent-mcp/README.md"]])
def test_unrelated_changes_do_not_select_fixture_processes(paths: list[str]) -> None:
    assert not MODULE.needs_docker_identity_smoke(paths)


def test_manual_dispatch_does_not_need_git(monkeypatch, capsys) -> None:
    monkeypatch.setattr(MODULE, "_git", lambda *a: pytest.fail("unexpected Git"))
    assert MODULE.main(["--event", "workflow_dispatch"]) == 0
    assert capsys.readouterr().out == "true\n"


def test_unavailable_base_expands_scope_visibly(monkeypatch, capsys) -> None:
    monkeypatch.setattr(MODULE, "_git", lambda *a: subprocess.CompletedProcess([], 128, "", "missing\n"))
    assert MODULE.main(["--base", "missing"]) == 0
    output = capsys.readouterr()
    assert output.out == "true\n" and "base revision unavailable" in output.err


def test_diff_error_is_not_a_successful_unrelated_verdict(monkeypatch, capsys) -> None:
    results = iter([subprocess.CompletedProcess([], 0, "", ""),
                    subprocess.CompletedProcess([], 128, "", "invalid head\n")])
    monkeypatch.setattr(MODULE, "_git", lambda *a: next(results))
    assert MODULE.main(["--base", "base", "--head", "bad"]) == 1
    output = capsys.readouterr()
    assert output.out == "" and "invalid head" in output.err


def test_git_scope_ignores_ambient_repository_overrides(monkeypatch, tmp_path, capsys) -> None:
    def git(root, *args):
        kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}  # headless-guard: allow fixture Git commands
        return subprocess.run(
            ["git", "-C", str(root), *args], check=True, capture_output=True, text=True,
            timeout=10, env={k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")},
            **kwargs,
        ).stdout.strip()

    roots = [tmp_path / "selected", tmp_path / "unrelated"]
    for root in roots:
        root.mkdir()
        git(root, "init", "--quiet")
        (root / "initial.txt").write_text("initial\n")
        git(root, "add", ".")
        git(root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "initial")
    selected, unrelated = roots
    base = git(selected, "rev-parse", "HEAD")
    library = selected / "libs/docker-resource-producer"
    library.mkdir(parents=True)
    (library / "changed.txt").write_text("selected\n")
    git(selected, "add", ".")
    git(selected, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "selected")
    monkeypatch.chdir(selected)
    for name, value in {
        "GIT_DIR": str(unrelated / ".git"), "GIT_WORK_TREE": str(unrelated),
        "GIT_INDEX_FILE": str(unrelated / ".git/index"), "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.bare", "GIT_CONFIG_VALUE_0": "true",
        "GIT_OBJECT_DIRECTORY": str(unrelated / ".git/objects"),
    }.items():
        monkeypatch.setenv(name, value)
    assert MODULE._git("rev-parse", "--show-toplevel").stdout.strip() == str(selected).replace("\\", "/")
    assert MODULE.main(["--base", base, "--head", "HEAD"]) == 0
    assert capsys.readouterr().out == "true\n"


def test_workflow_runs_scoped_minimum_without_changing_guard_python() -> None:
    workflow = yaml.safe_load((HERE.parent / ".github/workflows/ci.yml").read_text())
    steps = workflow["jobs"]["checks"]["steps"]
    scope = next(s for s in steps if s.get("id") == "docker-identity")
    runtime = next(s for s in steps if s.get("id") == "docker-identity-python")
    installer = next(s for s in steps if s.get("id") == "docker-identity-uv")
    smoke = next(s for s in steps if s.get("name") == "Docker identity fixture contracts (Python 3.10)")
    promotion = next(s for s in steps if s.get("name") == "Docker identity promotion compatibility")
    assert "docker_identity_ci_scope.py" in scope["run"]
    assert scope["env"]["HEAD_SHA"] == "${{ github.event.pull_request.head.sha || github.sha }}"
    assert runtime["if"] == smoke["if"] == "steps.docker-identity.outputs.run == 'true'"
    assert installer["if"] == smoke["if"]
    assert promotion["if"] == smoke["if"]
    assert "tools/test_accumulate_bumps.py::test_docker_resource_producer_canonical_version_is_promotable" in promotion["run"]
    assert installer["uses"].startswith("astral-sh/setup-uv@")
    assert runtime["with"]["python-version"] == "3.10"
    assert runtime["with"]["update-environment"] is False
    assert smoke["timeout-minutes"] <= 3
    assert "steps.docker-identity-python.outputs.python-path" in smoke["run"]
    assert "libs/docker-resource-producer/tests" in smoke["run"]
    assert "--timeout=20" in smoke["run"]
    assert "python tools/seasoned_uv.py --uv uv -- --python" in smoke["run"]
    assert "uv pip install" not in smoke["run"] and "-m pip install" not in smoke["run"]
    manifest = (HERE.parent / "libs/docker-resource-producer/pyproject.toml").read_text()
    assert '[tool.uv]\nexclude-newer = "7 days"' in manifest
