from __future__ import annotations

import argparse
import sqlite3
import subprocess
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
    monkeypatch.setattr(
        config, "load_config", lambda **_kwargs: SimpleNamespace(default_repo=repo),
    )
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
])
def test_explicit_partial_or_standalone_requests_skip_controller(project, selection):
    assert project_update_controller.run_project_update(args(**selection)) is None


def test_wsl_uses_linux_controller_when_not_overridden(project, monkeypatch):
    import update_driver

    repo, script = project
    configured = config.RepoConfig(
        anchor=repo.anchor, worktree_root=repo.worktree_root,
        update_controller={"linux": "tools/update.py"},
    )
    monkeypatch.setattr(config, "load_config", lambda: SimpleNamespace(default_repo=configured))
    monkeypatch.setattr(config, "detect_platform", lambda: "wsl")
    monkeypatch.setattr(
        update_driver, "participant_environment", lambda: {"UPDATE_DRIVER_WITNESS": "test"},
    )
    calls = []
    monkeypatch.setattr(
        project_update_controller.subprocess, "run",
        lambda argv, **_kwargs: calls.append(argv) or SimpleNamespace(returncode=0),
    )
    assert project_update_controller.run_project_update(args()) == 0
    assert calls[0][3] == str(script.resolve())


def test_explicit_runtime_sources_are_forwarded_to_controller(project, monkeypatch):
    import update_driver

    monkeypatch.setattr(
        update_driver, "participant_environment", lambda: {"UPDATE_DRIVER_WITNESS": "test"},
    )
    calls = []
    monkeypatch.setattr(
        project_update_controller.subprocess, "run",
        lambda argv, **_kwargs: calls.append(argv) or SimpleNamespace(returncode=0),
    )
    result = project_update_controller.run_project_update(
        args(no_manager=True, runtime_sources="{}"),
    )
    assert result == 0
    assert calls[0][-3:] == ["--no-manager", "--runtime-sources", "{}"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows inherited stream handling")
def test_windows_controller_preserves_stdout_and_stderr(project, monkeypatch, capfd):
    import update_driver

    _repo, script = project
    script.write_text(
        "import sys\nprint('controller stdout')\nprint('controller stderr', file=sys.stderr)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        update_driver, "participant_environment", lambda: {"UPDATE_DRIVER_WITNESS": "test"},
    )
    assert project_update_controller.run_project_update(args()) == 0
    output = capfd.readouterr()
    assert "controller stdout" in output.out
    assert "controller stderr" in output.err


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


def test_project_authority_fails_closed_if_controller_disappears(project, monkeypatch):
    from update_driver import RequestSpec
    from update_driver.authority import selection

    request = RequestSpec("engine", project="example", authority="project")
    monkeypatch.setattr(project_update_controller, "run_project_update", lambda _args: None)
    manager = lambda _args: pytest.fail("missing controller fell back to Manager")
    standalone = lambda: pytest.fail("missing controller fell back to standalone")
    assert project_update_controller.dispatch_update(
        args(_controller_request=request, _controller_authority=selection("project", "example")),
        lambda _args: 0, manager, standalone,
    ) == 1


@pytest.mark.parametrize("parent_flags", [
    ("--phase", "runtimes"), ("--target", "agent-bridge"), ("--no-project-controller",),
])
@pytest.mark.parametrize("child", [
    {"phase": "runtimes"}, {"phase": "integration"}, {"target": "agent-bridge"},
])
def test_partial_parent_request_cannot_claim_controller_delegation(project, parent_flags, child):
    from update_driver import RequestSpec
    from update_driver.authority import selection

    request = RequestSpec("engine", parent_flags, project="example", authority="project")
    assert project_update_controller.dispatch_update(
        args(**child, _controller_request=request,
             _controller_authority=selection("project", "example")),
        lambda _args: 0,
        lambda _args: pytest.fail("partial parent reached Manager"),
        lambda: pytest.fail("partial parent executed a delegated phase"),
    ) == 1


@pytest.mark.parametrize("legacy_schema", [False, True])
def test_project_authority_reaches_controller_under_real_driver_custody(
    project, monkeypatch, tmp_path, legacy_schema,
):
    import update_driver
    from agent_worktrees import update_driver_cli

    tmp_path = tmp_path.resolve()
    _repo, script = project
    driver_root = tmp_path / "driver"
    engine = tmp_path / "engine"
    result = tmp_path / "controlled.txt"
    paths = [str(Path(entry).resolve()) for entry in sys.path if entry]
    script.write_text(
        "import sys\n"
        f"sys.path[:0] = {paths!r}\n"
        "from pathlib import Path\n"
        "from update_driver import borrowed_custody, current_authority\n"
        f"root = Path({str(driver_root)!r})\n"
        "assert current_authority(root=root).mode == 'project'\n"
        "with borrowed_custody(root=root) as custody:\n"
        "    custody.assert_owned()\n"
        f"    Path({str(result)!r}).write_text(' '.join(sys.argv[1:]), encoding='utf-8')\n",
        encoding="utf-8",
    )
    update_driver.change_authority(
        "project", project="example", root=driver_root, timeout=5,
        verify_capabilities=lambda _deadline: True,
    )
    request = update_driver.enqueue(
        update_driver.RequestSpec(
            "engine", project="example", cwd=str(tmp_path), source=str(engine.resolve()),
            authority="project",
        ),
        runner=update_driver.TrustedRunner((sys.executable, "-c", "pass"), str(tmp_path)),
        root=driver_root,
    )
    if legacy_schema:
        with sqlite3.connect(driver_root / "queue.sqlite3") as db:
            db.execute("PRAGMA user_version=5")
        with pytest.raises(update_driver.StateError, match="protocol upgrade"):
            update_driver.current_authority(root=driver_root)
    monkeypatch.setattr(config, "install_dir", lambda: engine)
    borrow = update_driver.borrowed_custody

    def execute(transaction):
        monkeypatch.setattr(
            update_driver, "borrowed_custody",
            lambda: borrow(root=driver_root, environ=transaction.participant_env()),
        )
        return update_driver_cli.cmd_update_request(args(force=True))

    assert update_driver.drain(root=driver_root, executor=execute) == 0
    assert update_driver.status(request.id, root=driver_root).exit_code == 0
    assert result.read_text(encoding="utf-8") == "--force"


@pytest.mark.parametrize(("delegation", "expected"), [
    ("phase", "runtimes"), ("standalone", "standalone"), ("target", "agent-bridge"),
    ("integration", "integration:owned"),
])
def test_project_controller_can_delegate_under_real_custody(
    project, monkeypatch, tmp_path, delegation, expected,
):
    import update_driver
    from agent_worktrees import update_driver_cli

    tmp_path = tmp_path.resolve()
    _repo, script = project
    home, engine = tmp_path / "home", tmp_path / "engine"
    home.mkdir()
    driver_root = home / ".copilot-extensions" / "update-driver"
    result = tmp_path / "delegated.txt"
    paths = [str(Path(entry).resolve()) for entry in sys.path if entry]
    child = (
        "import argparse,sys\n"
        f"sys.path[:0] = {paths!r}\n"
        "from pathlib import Path\n"
        "from agent_worktrees import config,project_update_controller,update_cli,update_driver_cli\n"
        "config.set_active_project('example')\n"
        f"config.install_dir = lambda: Path({str(engine)!r})\n"
        "def phase(args):\n"
        "    value = getattr(args,'phase',None) or getattr(args,'target',None) or 'standalone'\n"
        "    if value == 'integration':\n"
        "        value += ':owned' if getattr(args,'_controller_owns_manager',None) is project_update_controller._CONTROLLER_OWNS_MANAGER else ':unowned'\n"
        f"    Path({str(result)!r}).write_text(value, encoding='utf-8')\n"
        "    return 0\n"
        "update_cli._core_helper = lambda name, local: phase if name == '_cmd_update_in_plugin' else local\n"
        "raise SystemExit(update_driver_cli.cmd_update_request(argparse.Namespace("
        + ("phase='runtimes', no_manager=True" if delegation == "phase"
           else "phase='integration', no_manager=True" if delegation == "integration"
           else "target='agent-bridge', no_manager=True" if delegation == "target"
           else "no_project_controller=True, no_manager=True")
        + ")))\n"
    )
    script.write_text(
        "import os,subprocess,sys\n"
        f"sys.path[:0] = {paths!r}\n"
        "from pathlib import Path\n"
        "from update_driver import borrowed_custody,participant_environment\n"
        f"with borrowed_custody(root=Path({str(driver_root)!r})):\n"
        f"    child = subprocess.run([sys.executable,'-I','-S','-B','-X','utf8','-c',{child!r}], "
        "env={**os.environ,**participant_environment()},capture_output=True,text=True,encoding='utf-8',timeout=20,"
        "creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))\n"
        "    if child.returncode: print((child.stdout+child.stderr).encode('ascii','backslashreplace').decode(), file=sys.stderr)\n"
        "    raise SystemExit(child.returncode)\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(config, "install_dir", lambda: engine)
    update_driver.change_authority(
        "project", project="example", root=driver_root, timeout=5,
        verify_capabilities=lambda _deadline: True,
    )
    request = update_driver.enqueue(
        update_driver.RequestSpec(
            "engine", ("--force",), project="example", cwd=str(tmp_path),
            source=str(engine.resolve()), authority="project",
        ),
        runner=update_driver.TrustedRunner((sys.executable, "-c", "pass"), str(tmp_path)),
        root=driver_root,
    )

    def execute(transaction):
        with monkeypatch.context() as environment:
            for key, value in transaction.participant_env().items():
                environment.setenv(key, value)
            return update_driver_cli.cmd_update_request(args(force=True))

    assert update_driver.drain(root=driver_root, executor=execute) == 0
    assert update_driver.status(request.id, root=driver_root).exit_code == 0
    assert result.read_text(encoding="utf-8") == expected


def test_standalone_authority_is_forwarded_to_manager(project, monkeypatch):
    from update_driver import RequestSpec
    from update_driver.authority import selection

    monkeypatch.setattr(
        project_update_controller, "run_project_update",
        lambda _args: pytest.fail("standalone mode entered the project controller"),
    )
    observed = []
    result = project_update_controller.dispatch_update(
        args(_controller_request=RequestSpec("engine", authority="standalone"),
             _controller_authority=selection("standalone")),
        lambda _args: 0,
        lambda selected: observed.extend(update_options.forwarded_flags(selected)) or 7,
        lambda: pytest.fail("Manager was not used"),
    )
    assert result == 7
    assert "--no-project-controller" in observed


@pytest.mark.parametrize(("phase", "expected"), [("integration", True), ("runtimes", False)])
def test_delegated_project_phase_exposes_manager_ownership_only_during_integration(
    project, phase, expected,
):
    request = SimpleNamespace(authority="project", project="example", argv=())
    authority = SimpleNamespace(configured=True, mode="project", project="example")
    selected = args(phase=phase, _controller_request=request, _controller_authority=authority)
    observed = []
    result = project_update_controller.dispatch_update(
        selected, lambda _args: pytest.fail("auth report ran"),
        lambda _args: pytest.fail("Manager handoff ran"),
        lambda: observed.append(
            getattr(selected, "_controller_owns_manager", None)
            is project_update_controller._CONTROLLER_OWNS_MANAGER
        ) or 0,
    )
    assert result == 0
    assert observed == [expected]
    assert not hasattr(selected, "_controller_owns_manager")


def test_delegated_integration_restores_manager_marker_after_error(project):
    request = SimpleNamespace(authority="project", project="example", argv=())
    authority = SimpleNamespace(configured=True, mode="project", project="example")
    selected = args(
        phase="integration", _controller_request=request, _controller_authority=authority,
        _controller_owns_manager="previous",
    )

    def fail():
        assert selected._controller_owns_manager is project_update_controller._CONTROLLER_OWNS_MANAGER
        raise RuntimeError("integration failed")

    with pytest.raises(RuntimeError, match="integration failed"):
        project_update_controller.dispatch_update(selected, lambda _args: 0, lambda _args: 0, fail)
    assert selected._controller_owns_manager == "previous"


def test_automatic_controller_rechecks_policy_when_enabled_after_submission(
    project, monkeypatch, tmp_path, capsys,
):
    import update_driver
    from agent_worktrees import control_plane_providers

    manager = tmp_path / ".worktree-manager"
    manager.mkdir()
    (manager / "config.toml").write_text("[maintenance]\nautomatic = false\n", encoding="utf-8")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.delenv("WORKTREE_MANAGER_ROOT", raising=False)
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(tmp_path / "cell.json"))
    monkeypatch.setattr(
        control_plane_providers, "_machine_control_plane_providers_dir",
        lambda: tmp_path / "providers",
    )
    monkeypatch.setattr(
        update_driver, "participant_environment", lambda: {"UPDATE_DRIVER_WITNESS": "test"},
    )
    monkeypatch.setattr(
        project_update_controller.subprocess, "run",
        lambda *_args, **_kwargs: pytest.fail("disabled automatic controller was launched"),
    )
    request = update_driver.RequestSpec(
        "engine", ("--auto",), cwd=str(tmp_path),
        context=(("COPILOT_EXTENSIONS_CONTEXT", str(tmp_path / "cell.json")),),
    )
    assert project_update_controller.run_project_update(
        args(auto=True, _controller_request=request),
    ) == 3
    captured = capsys.readouterr()
    assert "maintenance.automatic is false" in captured.out + captured.err


def test_submission_probe_has_a_deadline_and_fails_closed(project, monkeypatch, tmp_path):
    from agent_procutil import owned_process

    calls = []

    def timeout(command, **options):
        calls.append((command, options))
        raise subprocess.TimeoutExpired(command, options["timeout"])

    monkeypatch.setattr(owned_process, "run_caller_owned", timeout)
    with pytest.raises(ValueError, match="probe timed out"):
        project_update_controller.probe_for_submission(args(), cwd=tmp_path, timeout=0.5)
    command, options = calls[0]
    assert command[-4:] == ["--project", "example", "get", "update-controller"]
    assert options["timeout"] == 0.5
    assert options["cwd"] == str(tmp_path)


@pytest.mark.parametrize(("value", "selected"), [("tools/update.py\n", True), ("", False)])
def test_submission_probe_returns_the_owned_engine_selection(project, monkeypatch, tmp_path, value, selected):
    from agent_procutil import owned_process

    calls = []

    def run(command, **options):
        calls.append((command, options))
        return SimpleNamespace(returncode=0, stdout=value)

    monkeypatch.setenv("UPDATE_DRIVER_WITNESS", "do-not-inherit")
    monkeypatch.setattr(owned_process, "run_caller_owned", run)
    assert project_update_controller.probe_for_submission(args(), cwd=tmp_path) is selected
    assert "UPDATE_DRIVER_WITNESS" not in calls[0][1]["env"]


def test_config_and_standalone_option_round_trip(tmp_path):
    repo = config._build_repo_config(
        {"update_controller": {"windows": "tools/update.py"}}, str(tmp_path), str(tmp_path / "w"),
    )
    assert repo.update_controller == {"windows": "tools/update.py"}
    parser = argparse.ArgumentParser()
    update_options.add_update_options(parser)
    parsed = parser.parse_args(["--no-project-controller"])
    assert update_options.forwarded_flags(parsed) == ["--no-project-controller"]


def test_controller_get_key_uses_project_configuration(project, capsys):
    from agent_worktrees import context_cli

    assert context_cli.cmd_get(args(key="update-controller")) == 0
    assert capsys.readouterr().out.strip() == "tools/update.py"
