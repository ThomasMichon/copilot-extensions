from __future__ import annotations

import argparse
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_worktrees import config, project_update_controller, update_cli, update_options


def args(**values):
    return argparse.Namespace(**values)


@pytest.fixture
def project(monkeypatch, tmp_path):
    script = tmp_path / "tools" / "update.py"
    script.parent.mkdir()
    script.write_text("pass\n", encoding="utf-8")
    repo = config.RepoConfig(
        anchor=str(tmp_path), worktree_root=str(tmp_path / "worktrees"),
        update_controller={"windows": "tools/update.py"},
    )
    monkeypatch.setattr(config, "active_project", lambda: "example")
    monkeypatch.setattr(config, "load_config", lambda: SimpleNamespace(default_repo=repo))
    monkeypatch.setattr(config, "detect_platform", lambda: "windows")
    return repo, script


def test_ordinary_update_runs_controller_under_driver_custody(project, monkeypatch):
    import update_driver

    repo, script = project
    calls = []
    monkeypatch.setattr(
        update_driver, "participant_environment", lambda: {"UPDATE_DRIVER_WITNESS": "test"},
    )
    monkeypatch.setattr(
        project_update_controller.subprocess, "run",
        lambda argv, **kwargs: calls.append((argv, kwargs)) or SimpleNamespace(returncode=7),
    )
    monkeypatch.setattr(
        update_cli, "manager_handoff", lambda _args: pytest.fail("Manager was started"),
    )
    assert update_cli.cmd_update(args(force=True)) == 7
    argv, options = calls[0]
    assert argv == [sys.executable, "-I", "-B", str(script.resolve()), "--force"]
    assert options["cwd"] == Path(repo.anchor)
    assert options["env"]["UPDATE_DRIVER_WITNESS"] == "test"


@pytest.mark.parametrize("selection", [
    {"no_project_controller": True},
    {"phase": "runtimes"},
    {"target": "agent-bridge"},
    {"runtime_sources": "{}"},
])
def test_explicit_partial_or_standalone_requests_skip_controller(project, selection):
    assert project_update_controller.run_project_update(args(**selection)) is None


def test_missing_or_escaping_controller_fails_without_fallback(project, monkeypatch, tmp_path):
    repo, script = project
    monkeypatch.setattr(
        update_cli, "manager_handoff", lambda _args: pytest.fail("Manager was started"),
    )
    script.unlink()
    assert update_cli.cmd_update(args()) == 1
    outside = tmp_path.parent / "outside-update.py"
    monkeypatch.setattr(
        config, "load_config", lambda: SimpleNamespace(default_repo=config.RepoConfig(
            anchor=repo.anchor, worktree_root=repo.worktree_root,
            update_controller={"windows": str(outside)},
        )),
    )
    assert update_cli.cmd_update(args()) == 1


def test_controller_requires_driver_custody(project, monkeypatch):
    import update_driver

    monkeypatch.setattr(update_driver, "participant_environment", lambda: {})
    assert project_update_controller.run_project_update(args()) == 1


def test_real_driver_passes_custody_to_controller_process(project, tmp_path):
    import update_driver

    _repo, script = project
    driver_root = tmp_path / "driver"
    result = tmp_path / "result.txt"
    paths = [str(Path(entry).resolve()) for entry in sys.path if entry]
    script.write_text(
        "import sys\n"
        f"sys.path[:0] = {paths!r}\n"
        "from pathlib import Path\n"
        "from update_driver import borrowed_custody\n"
        f"with borrowed_custody(root=Path({str(driver_root)!r})) as custody:\n"
        "    custody.assert_owned()\n"
        f"    Path({str(result)!r}).write_text(' '.join(sys.argv[1:]), encoding='utf-8')\n",
        encoding="utf-8",
    )
    request = update_driver.enqueue(
        update_driver.RequestSpec("project-controller-test"),
        runner=update_driver.TrustedRunner((sys.executable, "-c", "pass"), str(tmp_path)),
        root=driver_root,
    )

    def execute(transaction):
        with update_driver.borrowed_custody(
            root=driver_root, environ=transaction.participant_env(),
        ):
            return update_cli.cmd_update(args(force=True))

    assert update_driver.drain(root=driver_root, executor=execute) == 0
    assert update_driver.status(request.id, root=driver_root).exit_code == 0
    assert result.read_text(encoding="utf-8") == "--force"


def test_no_controller_preserves_existing_updater(project, monkeypatch):
    repo, _script = project
    monkeypatch.setattr(
        config, "load_config", lambda: SimpleNamespace(default_repo=config.RepoConfig(
            anchor=repo.anchor, worktree_root=repo.worktree_root,
        )),
    )
    assert project_update_controller.run_project_update(args()) is None


def test_config_and_standalone_option_round_trip(tmp_path):
    repo = config._build_repo_config(
        {"update_controller": {"windows": "tools/update.py"}}, str(tmp_path), str(tmp_path / "w"),
    )
    assert repo.update_controller == {"windows": "tools/update.py"}
    parser = argparse.ArgumentParser()
    update_options.add_update_options(parser)
    parsed = parser.parse_args(["--no-project-controller"])
    assert update_options.forwarded_flags(parsed) == ["--no-project-controller"]
