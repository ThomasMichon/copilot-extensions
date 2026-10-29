"""Worktrees native installation retains the existing execution owner."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from update_driver import core
from update_driver.participant import ENVIRONMENT_KEY, borrowed_custody, participant

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


@pytest.fixture
def adapter(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    spec = importlib.util.spec_from_file_location("_worktrees_install_driver_test", SCRIPTS / "runtime_install_driver.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(core, "default_root", lambda: tmp_path / "driver")
    monkeypatch.delenv(ENVIRONMENT_KEY, raising=False)
    return module


def _arguments(tmp_path, **values):
    return SimpleNamespace(**{
        "payload": SCRIPTS.parent, "root": tmp_path / "runtime",
        "shell": tmp_path / "powershell.exe", "action": "install",
        "project_name": None, "bootstrap_python": None, "owner": None,
        "force": False, "zero_downtime": False, "provision_missing_runtime": False,
        "remove_config": False, "check_native": False,
        "check_removal": False, "remove_runtime": False, **values,
    })


def test_explicit_native_installation_uses_the_existing_finite_driver(adapter, tmp_path, monkeypatch):
    monkeypatch.setattr(adapter, "_source_digest", lambda payload: "a" * 64)
    args = _arguments(
        tmp_path, project_name="example", bootstrap_python=Path(sys.executable),
        owner=tmp_path / "source-owner", force=True,
    )
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(tmp_path / "context.json"))
    monkeypatch.delenv("COPILOT_EXTENSIONS_COMMAND_LIFECYCLE", raising=False)
    queued = []
    driver = SimpleNamespace(
        RequestSpec=core.RequestSpec, TrustedRunner=core.TrustedRunner,
        enqueue=lambda spec, **kwargs: queued.append((spec, kwargs["runner"])) or SimpleNamespace(id="request"),
        drain=Mock(), wait=Mock(return_value=SimpleNamespace(state="completed", exit_code=7)),
    )
    assert adapter._foreground(args, driver) == 7
    spec, runner = queued[0]
    assert spec.backend == "engine-native-install"
    assert spec.project == str(args.root)
    assert spec.source == "a" * 64
    assert spec.argv == tuple(adapter._native_arguments(args))
    assert dict(spec.context)["COPILOT_EXTENSIONS_CONTEXT"] == str(tmp_path / "context.json")
    assert "COPILOT_EXTENSIONS_COMMAND_LIFECYCLE" not in dict(spec.context)
    assert adapter.NATIVE_ENV in runner.context_keys
    assert "COPILOT_EXTENSIONS_COMMAND_LIFECYCLE" in runner.context_keys
    assert runner.argv[:6] == (
        sys.executable, "-I", "-S", "-B", "-X", "utf8",
    )
    assert runner.argv[6] == str(SCRIPTS / "runtime_install_driver.py")
    assert "--participant" in runner.argv
    driver.drain.assert_called_once_with()
    driver.wait.assert_called_once_with("request", output=sys.stdout)


@pytest.mark.parametrize("ticket", [
    "COPILOT_EXTENSIONS_COMMAND_LIFECYCLE", "COPILOT_EXTENSIONS_RUNTIME_PUBLICATION",
])
def test_cold_native_entry_never_waits_for_the_driver_under_publication_custody(
    adapter, tmp_path, monkeypatch, ticket,
):
    monkeypatch.setenv(ticket, "live-only-not-journaled")
    driver = SimpleNamespace(enqueue=Mock())
    with pytest.raises(RuntimeError, match="precede publication custody"):
        adapter._foreground(_arguments(tmp_path), driver)
    driver.enqueue.assert_not_called()


@pytest.mark.skipif(os.name != "nt", reason="Windows installed dev-release entry")
@pytest.mark.parametrize("as_json", [False, True])
def test_public_dev_release_uses_the_existing_json_execution_boundary(
    tmp_path, monkeypatch, capsys, as_json,
):
    import argparse
    from contextlib import contextmanager
    from agent_worktrees import runtime_dev_cli, update_driver_json_cli

    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    owner = tmp_path / "absent-owner"
    root = tmp_path / "runtime"
    slot = root / "versions" / "dev"
    monkeypatch.setattr(runtime_dev_cli.sys, "prefix", str(slot))
    monkeypatch.setattr(runtime_dev_cli.cfg, "install_dir", lambda: root)
    seen = []

    @contextmanager
    def retain(requested, version):
        assert requested == root and version == "dev"
        seen.append("retained")
        try:
            yield slot
        finally:
            seen.append("released")

    def run(verb, flags, args, execute, *, retained_dev):
        assert verb == "dev-release" and flags == ["--json", "--owner", str(owner)]
        assert args.json is True and args.owner == owner
        assert retained_dev and seen == ["retained"]
        seen.append(verb)
        return execute(args)

    def release(args):
        runtime_dev_cli.output._json_output({"runtime_version": "1.2.3"})
        return 0

    monkeypatch.setattr(update_driver_json_cli, "_run_request", run)
    monkeypatch.setattr(runtime_dev_cli, "_lifecycle", lambda: SimpleNamespace(
        runtime=SimpleNamespace(retention_lease=retain),
    ))
    monkeypatch.setattr(runtime_dev_cli, "_cmd_dev_release", release)
    with runtime_dev_cli.output.capture_json_output() as captured:
        assert runtime_dev_cli.cmd_dev_release(argparse.Namespace(owner=owner, json=as_json)) == 0
    output = captured.getvalue() if as_json else capsys.readouterr().out
    if as_json:
        assert json.loads(output)["runtime_version"] == "1.2.3"
    else:
        assert "current-version -> 1.2.3" in output
    assert seen == ["retained", "dev-release", "released"]


@pytest.mark.skipif(os.name != "nt", reason="Windows installed dev-release entry")
def test_public_dev_release_reports_retention_failure_as_json(tmp_path, monkeypatch):
    import argparse
    from contextlib import contextmanager
    from agent_worktrees import runtime_dev_cli, update_driver_json_cli

    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    root = tmp_path / "runtime"
    monkeypatch.setattr(runtime_dev_cli.sys, "prefix", str(root / "versions" / "dev"))
    monkeypatch.setattr(runtime_dev_cli.cfg, "install_dir", lambda: root)

    @contextmanager
    def retain(*args):
        raise RuntimeError("retention unavailable")
        yield

    monkeypatch.setattr(runtime_dev_cli, "_lifecycle", lambda: SimpleNamespace(
        runtime=SimpleNamespace(retention_lease=retain),
    ))
    monkeypatch.setattr(update_driver_json_cli, "_run_request", lambda *a, **k: pytest.fail("unretained execution"))
    with runtime_dev_cli.output.capture_json_output() as captured:
        assert runtime_dev_cli.cmd_dev_release(argparse.Namespace(json=True)) == 1
    assert json.loads(captured.getvalue())["error"] == "retention unavailable"


@pytest.mark.skipif(os.name != "nt", reason="Windows native installation")
@pytest.mark.parametrize("backend", ["engine", "engine-launch-update"])
@pytest.mark.parametrize("scoped", [False, True])
@pytest.mark.parametrize("check", [False, True])
def test_native_handoff_reexports_live_custody_without_another_queue(
    adapter, tmp_path, monkeypatch, scoped, check, backend,
):
    args = _arguments(tmp_path, check_native=check)
    request = core.enqueue(
        core.RequestSpec(backend),
        runner=core.TrustedRunner((sys.executable, "-c", "pass"), str(tmp_path)),
    )
    calls = []

    def spawn(command, **options):
        assert command[:1] == [str(args.shell)]
        assert command[-4:] == ["-Action", "install", "-InstallDir", str(args.root)]
        assert options["creationflags"] == subprocess.CREATE_NO_WINDOW
        assert options["stdout"] is sys.stdout and options["stderr"] is sys.stderr
        assert ENVIRONMENT_KEY not in os.environ
        assert options["env"][adapter.NATIVE_ENV] == "1"
        with participant(root=core.default_root(), witness=options["env"][ENVIRONMENT_KEY]) as child:
            child.assert_owned()
        calls.append(command)
        return SimpleNamespace(wait=lambda: 0)

    monkeypatch.setattr(adapter.subprocess, "Popen", spawn)
    monkeypatch.setattr(adapter, "_source_digest", lambda payload: pytest.fail("engine owner must not enqueue"))

    def execute(transaction):
        monkeypatch.setenv(ENVIRONMENT_KEY, transaction.participant_env()[ENVIRONMENT_KEY])
        if scoped:
            with borrowed_custody(root=core.default_root()):
                return adapter._execute(args)
        return adapter._execute(args)

    assert core.drain(executor=execute) == 0
    assert core.status(request.id).state == "completed"
    assert len(calls) == (0 if check else 1)


@pytest.mark.parametrize("changed", ["source", "root", "arguments", "backend", None])
def test_native_participant_checks_exact_admitted_installation(adapter, tmp_path, monkeypatch, changed):
    args = _arguments(tmp_path, check_native=True)
    monkeypatch.setattr(adapter, "_source_digest", lambda payload: "a" * 64)
    request = core.enqueue(
        core.RequestSpec(
            "unrelated" if changed == "backend" else "engine-native-install",
            ("different",) if changed == "arguments" else tuple(adapter._native_arguments(args)),
            project="different" if changed == "root" else str(args.root),
            source="different" if changed == "source" else "a" * 64,
        ),
        runner=core.TrustedRunner((sys.executable, "-c", "pass"), str(tmp_path)),
    )

    def execute(transaction):
        monkeypatch.setenv(ENVIRONMENT_KEY, transaction.participant_env()[ENVIRONMENT_KEY])
        return adapter._execute(args)

    if changed is None:
        assert core.drain(executor=execute) == 0
    else:
        with pytest.raises(RuntimeError, match="unclosed participants"):
            core.drain(executor=execute)
    assert core.status(request.id).state == ("completed" if changed is None else "interrupted")


@pytest.mark.skipif(os.name != "nt", reason="Windows native installation")
def test_missing_custody_refuses_before_native_execution(adapter, tmp_path, monkeypatch):
    spawn = Mock()
    monkeypatch.setattr(adapter.subprocess, "Popen", spawn)
    with pytest.raises(RuntimeError, match="live driver custody"):
        adapter._execute(_arguments(tmp_path))
    spawn.assert_not_called()
    assert not core.default_root().exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows native removal custody")
@pytest.mark.parametrize("check", [False, True])
@pytest.mark.parametrize("staged", [False, True])
def test_native_uninstall_admits_then_removes_broker_before_finite_code(
    adapter, tmp_path, monkeypatch, check, staged,
):
    from update_driver import windows_broker

    args = _arguments(
        tmp_path, action="uninstall", check_removal=check, remove_runtime=not check,
    )
    source = tmp_path / "original-payload" if staged else args.payload
    project = tmp_path / "original-project"
    monkeypatch.setenv("COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT", str(project))
    if staged:
        monkeypatch.setenv("COPILOT_PLUGIN_STAGED_FROM", str(source))
    else:
        monkeypatch.delenv("COPILOT_PLUGIN_STAGED_FROM", raising=False)
    phases = []

    def cleanup(**arguments):
        assert arguments["root"] == args.root
        assert arguments["profile"] == SCRIPTS / "uninstall-profile.json"
        assert arguments["profile"].is_file()
        assert arguments["project_name"] == args.project_name
        assert arguments["purge"] == args.remove_config
        assert os.environ["COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT"] == str(project)
        assert Path(os.environ.get("COPILOT_PLUGIN_STAGED_FROM") or args.payload) == source
        arguments["controller_custody"].assert_owned()
        phases.append("admit" if arguments.get("check_consumers") else "finite")
        return {"status": "ready" if arguments.get("check_consumers") else "removed"}

    monkeypatch.setattr(adapter, "_uninstall_policy", lambda: SimpleNamespace(cleanup=cleanup))
    monkeypatch.setattr(windows_broker, "remove", lambda root: phases.append("broker"))
    monkeypatch.setattr(adapter.subprocess, "Popen", lambda *a, **k: pytest.fail("another installer spawned"))
    request = core.enqueue(
        core.RequestSpec("engine"),
        runner=core.TrustedRunner((sys.executable, "-c", "pass"), str(tmp_path)),
    )

    def execute(transaction):
        monkeypatch.setenv(ENVIRONMENT_KEY, transaction.participant_env()[ENVIRONMENT_KEY])
        return adapter._execute(args)

    assert core.drain(executor=execute) == 0
    assert core.status(request.id).state == "completed"
    assert phases == (["admit"] if check else ["admit", "broker", "finite"])


@pytest.mark.skipif(os.name != "nt", reason="Windows native removal custody")
@pytest.mark.parametrize("failure", ["admit", "broker"])
def test_failed_native_uninstall_admission_or_broker_removal_retains_runtime(
    adapter, tmp_path, monkeypatch, failure,
):
    from update_driver import windows_broker

    phases = []
    args = _arguments(tmp_path, action="uninstall", remove_runtime=True)

    def phase(name):
        phases.append(name)
        if name == failure:
            raise RuntimeError("retained removal obligation")
        return {"status": "ready"}

    monkeypatch.setattr(adapter, "_uninstall_policy", lambda: SimpleNamespace(
        cleanup=lambda **arguments: phase("admit" if arguments.get("check_consumers") else "finite"),
    ))
    monkeypatch.setattr(windows_broker, "remove", lambda root: phase("broker"))
    request = core.enqueue(
        core.RequestSpec("engine"),
        runner=core.TrustedRunner((sys.executable, "-c", "pass"), str(tmp_path)),
    )

    def execute(transaction):
        monkeypatch.setenv(ENVIRONMENT_KEY, transaction.participant_env()[ENVIRONMENT_KEY])
        return adapter._execute(args)

    with pytest.raises(RuntimeError, match="unclosed participants"):
        core.drain(executor=execute)
    assert core.status(request.id).state == "interrupted"
    assert phases == (["admit"] if failure == "admit" else ["admit", "broker"])
    assert not args.root.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows adoption adapter")
@pytest.mark.parametrize("scoped", [False, True])
def test_broker_adoption_uses_the_retained_installed_entry(adapter, tmp_path, monkeypatch, scoped):
    import versioned_runtime as runtime

    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    root, slot = tmp_path / "runtime", tmp_path / "runtime" / "versions" / "1.2.3"
    interpreter = slot / "Scripts" / "python.exe"
    monkeypatch.setattr(runtime, "slot_python", lambda *args: interpreter)
    request = core.enqueue(
        core.RequestSpec("engine"),
        runner=core.TrustedRunner((sys.executable, "-c", "pass"), str(tmp_path)),
    )

    def spawn(command, **options):
        assert command == [
            str(interpreter), "-I", "-m", "update_driver.windows_broker",
            "--runtime-root", str(root), "--slot", str(slot),
            "--execution-root", str(core.default_root()),
        ]
        assert ENVIRONMENT_KEY not in os.environ
        with participant(root=core.default_root(), witness=options["env"][ENVIRONMENT_KEY]) as child:
            child.assert_owned()
        return SimpleNamespace(returncode=0, communicate=lambda: ('{"adopted":true}', ""))

    monkeypatch.setattr(adapter.subprocess, "Popen", spawn)

    def execute(transaction):
        monkeypatch.setenv(ENVIRONMENT_KEY, transaction.participant_env()[ENVIRONMENT_KEY])
        if scoped:
            with borrowed_custody(root=core.default_root()):
                adapter.adopt_broker(root, slot)
        else:
            adapter.adopt_broker(root, slot)
        return 0

    assert core.drain(executor=execute) == 0
    assert core.status(request.id).state == "completed"


def test_contained_installation_does_not_register_a_host_task(adapter, tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_CONTAINED", "1")
    spawn = Mock()
    monkeypatch.setattr(adapter.subprocess, "Popen", spawn)
    adapter.adopt_broker(tmp_path, tmp_path / "versions" / "1.2.3")
    spawn.assert_not_called()
    assert not core.default_root().exists()


def test_unowned_bootstrap_environment_never_requires_installed_driver_dependencies(adapter, monkeypatch):
    monkeypatch.delitem(sys.modules, "update_driver")
    monkeypatch.setenv("BOOTSTRAP_CONTEXT", "preserved")
    assert adapter.child_environment()["BOOTSTRAP_CONTEXT"] == "preserved"
    assert "update_driver" not in sys.modules


def test_installer_runner_reexports_only_the_scoped_witness(adapter, tmp_path, monkeypatch):
    from agent_worktrees import installer_run

    request = core.enqueue(
        core.RequestSpec("engine"),
        runner=core.TrustedRunner((sys.executable, "-c", "pass"), str(tmp_path)),
    )
    seen = []

    def spawn(argv, cwd, env, capture, **kwargs):
        assert ENVIRONMENT_KEY not in os.environ
        assert env["TARGET_CONTEXT"] == "preserved"
        with participant(root=core.default_root(), witness=env[ENVIRONMENT_KEY]) as child:
            child.assert_owned()
        seen.append(argv)
        return SimpleNamespace(returncode=0, communicate=lambda **kwargs: ("", "")), None

    monkeypatch.setattr(installer_run, "_spawn", spawn)

    def execute(transaction):
        monkeypatch.setenv(ENVIRONMENT_KEY, transaction.participant_env()[ENVIRONMENT_KEY])
        with borrowed_custody(root=core.default_root()):
            result = installer_run.run_installer(
                ["installer"], cwd=tmp_path, env={"TARGET_CONTEXT": "preserved"}, deadline_s=5,
            )
        assert result.status == "OK"
        return 0

    assert core.drain(executor=execute) == 0
    assert core.status(request.id).state == "completed"
    assert seen == [["installer"]]


@pytest.mark.skipif(os.name != "nt", reason="actual Windows native driver entry")
def test_real_cold_driver_and_native_reentry_use_the_same_owner(adapter, tmp_path, monkeypatch):
    """Use a copied fixed no-install shell, never the product installer."""
    from agent_procutil.owned_process import run_owned

    shell = shutil.which("powershell.exe")
    if shell is None:
        pytest.skip("native PowerShell is unavailable")
    engine = importlib.import_module("installer_engine")
    minimal = tmp_path / "minimal"
    scripts = minimal / "scripts"
    scripts.mkdir(parents=True)
    for source in SCRIPTS.glob("*.py"):
        if source.name.startswith(("installer_", "versioned_runtime", "runtime_install_driver")):
            shutil.copyfile(source, scripts / source.name)
    for name in ("update-driver", "agent-procutil", "single-instance-lease", "zdd"):
        shutil.copytree(
            SCRIPTS.parents[2] / "libs" / name, tmp_path / "libs" / name,
            ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", ".ruff_cache"),
        )
    (minimal / "pyproject.toml").write_text(
        '[project]\nname="agent-worktrees"\nversion="1.0.0"\n'
        '[tool.uv.sources]\n'
        'agent-update-driver = { path = "../libs/update-driver" }\n',
        encoding="utf-8",
    )
    payload = engine.freeze_inputs(minimal, tmp_path / "source").plugin
    receipt = tmp_path / "receipt.json"
    entry = payload / "scripts" / "runtime_install_driver.py"
    script = payload / "scripts" / "install.ps1"
    helper = payload / "scripts" / "versioned_runtime_native.ps1"
    shutil.copyfile(SCRIPTS / helper.name, helper)
    script.write_text(
        "param([string]$Action,[string]$InstallDir)\n"
        "$ErrorActionPreference='Stop'\n"
        f". '{helper}'\n"
        f"Invoke-Hidden -FilePath '{sys.executable}' -StreamOutput -ArgList @("
        f"'-I','-S','-B','{entry}','--payload','{payload}',"
        f"'--root',$InstallDir,'--shell','{shell}','--action',$Action,'--check-native')\n"
        "if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }\n"
        "[Console]::Out.WriteLine('fixed-native-out')\n"
        "[Console]::Error.WriteLine('fixed-native-error')\n"
        f"@{{root=$InstallDir;driver=$env:AGENT_WORKTREES_NATIVE_INSTALLATION;"
        f"witness=[bool]$env:UPDATE_DRIVER_WITNESS}} | ConvertTo-Json | Set-Content -LiteralPath '{receipt}'\n",
        encoding="utf-8",
    )
    result = run_owned([
        sys.executable, "-I", "-S", "-B", str(entry),
        "--payload", str(payload), "--root", str(tmp_path / "runtime"),
        "--shell", shell, "--action", "install",
    ], timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(receipt.read_text(encoding="utf-8-sig")) == {
        "root": str(tmp_path / "runtime"), "driver": "1", "witness": True,
    }
    rows = core.status(root=Path.home() / ".copilot-extensions" / "update-driver")
    assert len(rows) == 1 and rows[0].state == "completed"
    assert rows[0].exit_code == 0 and rows[0].error is None
    log = rows[0].log_path.read_text(encoding="utf-8")
    assert log.count("fixed-native-out") == log.count("fixed-native-error") == 1
