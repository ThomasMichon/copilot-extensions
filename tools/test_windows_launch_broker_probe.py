"""Credential-free protocol and custody checks; never register a host task."""

from __future__ import annotations

import json
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import uuid
from unittest.mock import Mock
from types import SimpleNamespace

import pytest
import windows_broker_protocol_acceptance as protocol_probe
import windows_broker_fixture as broker_fixture
import windows_launch_broker_probe as probe
from windows_broker_desktop import DesktopWitness
from windows_broker_cell_acceptance import qualify as qualify_cell
import windows_broker_consumer_acceptance as consumer_probe
import windows_broker_picker_acceptance as picker_probe
import windows_normal_process_lifetime as normal_probe


@pytest.mark.parametrize("birth", ["expected", "reused"])
def test_native_fixture_pins_birth_before_granting_cleanup_custody(monkeypatch, birth):
    child = Mock(_handle=123)
    monkeypatch.setitem(sys.modules, "agent_procutil", SimpleNamespace(__path__=[]))
    monkeypatch.setitem(
        sys.modules, "agent_procutil.daemon_broker",
        SimpleNamespace(WindowsDaemonProcess=Mock(return_value=child)),
    )
    monkeypatch.setitem(
        sys.modules, "agent_procutil._daemon_instance_identity",
        SimpleNamespace(_windows_birth=lambda handle: birth),
    )
    if birth == "expected":
        assert normal_probe._verified_process(321, "expected", "fixture") is child
        child.close.assert_not_called()
    else:
        with pytest.raises(RuntimeError, match="identity changed"):
            normal_probe._verified_process(321, "expected", "fixture")
        child.close.assert_called_once_with()
    child.kill.assert_not_called()
    child.wait.assert_not_called()


@pytest.mark.parametrize("plugin", sorted(protocol_probe.NATIVE_PLUGINS))
def test_native_adoption_program_uses_copied_helpers_lexical_root(tmp_path, plugin):
    root = tmp_path / "fixture's runtime"
    script, program = protocol_probe.native_adoption_program(root, "first", plugin=plugin)
    helpers = protocol_probe.controller_root(root) / ".runtime-bootstrap"
    assert script == helpers / f"{plugin}-adopt.ps1"
    assert "$PSScriptRoot=" not in program
    assert str(helpers).replace("'", "''") in program
    for function in (
        "Invoke-VersionedSlotClean", "Invoke-VersionedMarkComplete", "Invoke-RuntimeBrokerAdoption",
    ):
        assert f"function {function} {{" in program
    assert "Test-Path -LiteralPath 'Function:\\Stop-RuntimeConstruction'" in program
    assert "$NoService=$false" in program
    assert "function Test-SourceRuntimeUnchanged {}" in program
    assert not helpers.is_relative_to(root)
    if plugin == "agent-mcp":
        assert "$SrcVersion='1.0'" in program
        assert program.index("Invoke-VersionedSlotClean)") < program.index("Fixture candidate seeding")
        assert program.index("Fixture candidate seeding") < program.index("if (-not (Invoke-VersionedMarkComplete")
        assert "venv.EnvBuilder(with_pip=False).create(target)" in program


def test_copied_bootstrap_has_the_real_mcp_footprint_without_ambient_imports(tmp_path):
    bootstrap = tmp_path / "bootstrap"
    protocol_probe.prepare_bootstrap(bootstrap)
    code = (
        "import json,sys;from pathlib import Path;"
        "sys.path.insert(0,sys.argv[1]);import installer_engine_footprint as f;"
        "root=Path(sys.argv[1]);"
        "manifest=f.parse(f.decode((root/'uninstall-profile.json').read_text()),'agent-mcp');"
        "assert manifest.effects['legacy-runtime-processes']['disposition']=='retire'"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", code, str(bootstrap)],
        capture_output=True, text=True, timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("memberships", [(False, False), (True, True), (True, False)])
def test_retirement_topology_checks_each_caller_job_not_a_helpers_own_lease(memberships):
    observed = []

    class Api:
        def IsProcessInJob(self, process, job, output):
            assert process == 42
            observed.append(job)
            output._obj.value = memberships[job - 1]
            return True

    jobs = [SimpleNamespace(handle=1), SimpleNamespace(handle=2)]
    result = broker_fixture.caller_job_memberships(Api(), SimpleNamespace(_handle=42), jobs)
    assert result == list(memberships)
    assert observed == [1, 2]


@pytest.mark.skipif(os.name != "nt", reason="Native Job query")
def test_native_leased_helper_can_belong_to_a_different_job(tmp_path, monkeypatch):
    import agent_procutil as pu
    from agent_procutil.daemon_broker import _kernel32, _outside_job
    from agent_procutil.daemon_instances import daemon_test_lifecycle
    from agent_procutil.retirement_custody import RetirementCustody

    monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_CONTAINED", "1")
    jobs = []
    with daemon_test_lifecycle(
        owner="contained-retirement-job-query", registry_root=tmp_path / "instances",
    ) as environment:
        for key, value in environment.items():
            monkeypatch.setenv(key, value)
        try:
            for _ in range(2):
                broker_fixture.make_job(pu, jobs)
            with RetirementCustody(tmp_path) as custody:
                child = custody._process
                assert not _outside_job(child)
                assert broker_fixture.caller_job_memberships(_kernel32(), child, jobs) == [False, False]
        finally:
            for job in jobs:
                job.close()


@pytest.fixture
def recovery_slots(tmp_path, monkeypatch):
    source = Path(protocol_probe.__file__).resolve().parents[1] / "libs" / "versioned-runtime"
    spec = importlib.util.spec_from_file_location("broker_fixture_gc_under_test", source / "versioned_runtime.py")
    runtime = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, runtime)
    spec.loader.exec_module(runtime)
    root = tmp_path / "runtime"
    for name in ("first", "replacement", "1.0", "control"):
        slot = root / "versions" / name
        slot.mkdir(parents=True)
        (slot / "canary").write_text(name, encoding="ascii")
        runtime.mark_complete(root, name, payload_hash="a" * 64)
    runtime._leases.replace_runtime_references(
        root, "fixture/recovery", ["first", "replacement"], expected=[],
    )
    return runtime, root


def test_recovery_collection_accepts_other_disposable_constructor_candidates(recovery_slots):
    runtime, root = recovery_slots
    collected = broker_fixture.collect_generations(
        runtime, root, retired=("control",), retained=("first", "replacement"),
    )
    assert collected == ["1.0", "control"]
    for name in ("first", "replacement"):
        assert (root / "versions" / name / "canary").read_text(encoding="ascii") == name


@pytest.mark.parametrize("failure", ["retired-recovery", "missed-control", "unusable-recovery"])
def test_recovery_collection_cannot_hide_a_broken_retention_control(recovery_slots, monkeypatch, failure):
    runtime, root = recovery_slots
    original = runtime.gc

    def collect(path):
        result = original(path)
        if failure == "retired-recovery":
            result.append("first")
        elif failure == "missed-control":
            result.remove("control")
        return result

    monkeypatch.setattr(runtime, "gc", collect)
    if failure == "unusable-recovery":
        monkeypatch.setattr(runtime, "check_admission", lambda *_a, **_kw: "construct")
    with pytest.raises(RuntimeError, match="preserve usable recovery"):
        broker_fixture.collect_generations(
            runtime, root, retired=("control",), retained=("first", "replacement"),
        )


def test_native_adoption_program_refuses_an_unknown_plugin_before_reading_sources(tmp_path):
    with pytest.raises(ValueError, match="native plugin refused"):
        protocol_probe.native_adoption_program(tmp_path, "first", plugin="../unowned")


def test_native_removal_fixture_supplies_external_shell_to_real_admission_contract(monkeypatch, tmp_path):
    execute = Mock(return_value=0)
    monkeypatch.setitem(sys.modules, "installer_driver", SimpleNamespace(execute=execute))
    admitted = Mock()
    monkeypatch.setattr(protocol_probe, "admit", admitted)
    monkeypatch.setattr(protocol_probe, "imports", Mock())
    shell = str(tmp_path / "powershell.exe")
    monkeypatch.setattr(protocol_probe.shutil, "which", lambda name: shell)
    assert protocol_probe.native_remove_action(
        tmp_path, tmp_path, "first", plugin="agent-dispatch",
    ) == 0
    admitted.assert_called_once_with(tmp_path, True)
    arguments = execute.call_args.args[0]
    assert arguments.shell == Path(shell)
    assert arguments.action == "uninstall"
    assert arguments.remove_broker and arguments.check_native


def test_fixture_profile_is_not_a_descendant_of_removable_runtime(monkeypatch, tmp_path):
    root = tmp_path / "ce-broker-probe-owned"
    monkeypatch.setattr(protocol_probe, "admit", Mock())
    monkeypatch.setenv("HOME", "original-home")
    monkeypatch.setenv("USERPROFILE", "original-profile")
    with protocol_probe.manager_profile(root):
        profile = Path(os.environ["USERPROFILE"])
        assert profile.parent == root.parent
        assert profile != root and not profile.is_relative_to(root)
        assert os.environ["HOME"] == str(profile)
    assert os.environ["HOME"] == "original-home"
    assert os.environ["USERPROFILE"] == "original-profile"


@pytest.mark.parametrize("action", ["native-adopt", "native-remove"])
@pytest.mark.parametrize("plugin", sorted(protocol_probe.NATIVE_PLUGINS))
def test_native_fixture_cli_refuses_shared_host_before_loading_runtime(monkeypatch, tmp_path, action, plugin):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(protocol_probe, "imports", Mock())
    monkeypatch.setattr(protocol_probe.sys, "argv", [
        "fixture", action, str(tmp_path), str(tmp_path), "first", plugin,
    ])
    with pytest.raises(RuntimeError, match="ephemeral Windows CI consent"):
        protocol_probe.main()
    protocol_probe.imports.assert_not_called()


def test_protocol_fixture_refuses_shared_host_before_any_native_action(monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(protocol_probe, "inspect", Mock(side_effect=AssertionError("host mutation")))
    monkeypatch.setattr(
        protocol_probe.sys, "argv",
        ["fixture", "--ephemeral-ci-consent", str(tmp_path / "ce-broker-probe-owned")],
    )
    with pytest.raises(RuntimeError, match="ephemeral Windows CI consent"):
        protocol_probe.main()
    protocol_probe.inspect.assert_not_called()


def test_desktop_witness_refuses_shared_host_before_native_calls(monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    with pytest.raises(RuntimeError, match="ephemeral Windows CI consent"):
        DesktopWitness(tmp_path)


def test_actual_cell_fixture_refuses_shared_host_before_loading_or_mutating(monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    with pytest.raises(RuntimeError, match="ephemeral Windows CI consent"):
        qualify_cell(tmp_path, tmp_path, None, None, [], [], [])


def test_real_consumer_fixture_refuses_shared_host_before_loading_or_mutating(monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    with pytest.raises(RuntimeError, match="ephemeral Windows CI consent"):
        consumer_probe.qualify(tmp_path)


def test_actual_picker_fixture_refuses_shared_host_before_copy_or_spawn(monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    with pytest.raises(RuntimeError, match="ephemeral Windows CI consent"):
        picker_probe.qualify(tmp_path, tmp_path, [], [], manager_slot=tmp_path,
                             environment={}, run=Mock(), procutil=Mock())
    assert not (tmp_path / "manager-reader").exists()


def test_real_consumer_entry_refuses_before_creating_a_profile(monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    create = Mock(side_effect=AssertionError("shared-host fixture write"))
    monkeypatch.setattr(consumer_probe.tempfile, "mkdtemp", create)
    with pytest.raises(RuntimeError, match="ephemeral Windows CI consent"):
        consumer_probe.main(["--ephemeral-ci-consent"])
    create.assert_not_called()


@pytest.mark.parametrize("plugin", ["agent-worktrees", "agent-ssh"])
def test_real_consumer_source_closure_materializes_without_a_stale_vendor_shadow(
    tmp_path, consumer_source_snapshot, plugin,
):
    import prepare_runtime_source as preparation

    payload = tmp_path / plugin
    preparation._materialize(consumer_source_snapshot, plugin, payload)
    assert (payload / "scripts" / "install.ps1").is_file()
    if plugin == "agent-worktrees":
        canonical = consumer_probe.REPOSITORY / "libs" / "zdd" / "src" / "zdd" / "private_windows.py"
        assert (payload / "libs" / "zdd" / "src" / "zdd" / "private_windows.py").read_bytes() == (
            canonical.read_bytes()
        )


@pytest.fixture(scope="module")
def consumer_source_snapshot(tmp_path_factory):
    source = tmp_path_factory.mktemp("broker-consumer-source")
    result = subprocess.run(
        ["git", "-c", "core.longpaths=true", "checkout-index", "--all", "--prefix", str(source) + os.sep],
        cwd=consumer_probe.REPOSITORY, capture_output=True, text=True, timeout=60,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return source


def test_fixture_controller_receives_root_without_a_rooted_consumer_command(monkeypatch, tmp_path):
    root = tmp_path / "ce-broker-probe-owned"
    monkeypatch.setattr(protocol_probe.sys, "argv", ["fixture", "--ephemeral-ci-consent"])
    monkeypatch.setattr(protocol_probe.sys, "stdin", io.StringIO(str(root) + "\n"))
    admission = Mock()
    inspect = Mock(return_value={"supported": True, "cleanup_confirmed": True})
    monkeypatch.setattr(protocol_probe, "admit", admission)
    monkeypatch.setattr(protocol_probe, "inspect", inspect)
    assert protocol_probe.main() == 0
    admission.assert_called_once_with(root, True)
    inspect.assert_called_once_with(root)


def test_protocol_caller_refuses_shared_host_before_loading_runtime(monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(protocol_probe, "imports", Mock())
    with pytest.raises(RuntimeError, match="ephemeral Windows CI consent"):
        protocol_probe.caller(tmp_path, tmp_path)
    protocol_probe.imports.assert_not_called()


@pytest.mark.parametrize("entry", [
    "adopt_action", "manager_adopt_action", "worktrees_adopt_action", "bridge_adopt_action",
    "codespaces_adopt_action", "native_adopt_action", "native_remove_action",
    "_adopt_manager_generation", "adopt_generation",
])
def test_driver_adoption_fixture_refuses_shared_host_before_loading_runtime(
    monkeypatch, tmp_path, entry,
):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(protocol_probe, "imports", Mock())
    with pytest.raises(RuntimeError, match="ephemeral Windows CI consent"):
        options = {"plugin": "agent-codespaces"} if entry in {"native_adopt_action", "native_remove_action"} else {}
        getattr(protocol_probe, entry)(tmp_path, tmp_path, "first", **options)
    protocol_probe.imports.assert_not_called()


def test_action_construction_fixture_refuses_shared_host_before_creating_venv(monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    builder = Mock(side_effect=AssertionError("shared host venv"))
    monkeypatch.setattr(protocol_probe.venv, "EnvBuilder", builder)
    with pytest.raises(RuntimeError, match="ephemeral Windows CI consent"):
        protocol_probe.prepare_action(tmp_path, tmp_path, "first", None)
    builder.assert_not_called()


@pytest.mark.parametrize("action", ["submit-worker", "drain-worker", "worker-product"])
def test_worker_fixture_entrypoints_refuse_shared_host_before_imports(monkeypatch, tmp_path, action):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(protocol_probe, "imports", Mock())
    monkeypatch.setattr(protocol_probe.sys, "argv", ["fixture", action, str(tmp_path), str(tmp_path)])
    with pytest.raises(RuntimeError, match="ephemeral Windows CI consent"):
        protocol_probe.main()
    protocol_probe.imports.assert_not_called()


def test_worker_inspection_refuses_shared_host_before_native_work(monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    with pytest.raises(RuntimeError, match="ephemeral Windows CI consent"):
        protocol_probe.inspect_worker(tmp_path, tmp_path, None, [], [], [])


def test_publication_crash_fixture_refuses_shared_host_before_loading_runtime(monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(protocol_probe, "imports", Mock())
    with pytest.raises(RuntimeError, match="ephemeral Windows CI consent"):
        protocol_probe.crash_publication(tmp_path, tmp_path, "{}", "{}", "task")
    protocol_probe.imports.assert_not_called()


def test_publication_interruption_owner_refuses_shared_host_before_loading_runtime(
    monkeypatch, tmp_path,
):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(protocol_probe, "imports", Mock())
    with pytest.raises(RuntimeError, match="ephemeral Windows CI consent"):
        protocol_probe.interrupt_publication(tmp_path, tmp_path, {}, {}, "task")
    protocol_probe.imports.assert_not_called()


@pytest.mark.skipif(os.name != "nt", reason="Native nested Windows adapter input and read-only lookup")
def test_production_adapter_reaches_missing_task_with_two_jobs_and_live_outer_stdin(tmp_path):
    import agent_procutil as pu
    import zdd
    from agent_procutil.windows_task_broker import Registration
    from zdd.private_windows_native import native

    tmp_path = tmp_path.resolve()
    entry = tmp_path / "windows_broker_entry.py"
    entry.write_text("raise AssertionError('no broker may be activated')\n", encoding="utf-8")
    config = Registration(
        "missing-native-fixture-" + uuid.uuid4().hex, str(tmp_path),
        str((Path(sys.base_prefix) / "pythonw.exe").resolve(strict=True)),
        str(entry), native().current_sid(),
    )
    paths = [str(Path(module.__file__).resolve().parents[1]) for module in (pu, zdd)]
    program = (
        f"import json,sys;sys.path[:0]={paths!r};"
        "from agent_procutil import DaemonSpawnError;"
        "from agent_procutil.windows_task_broker import _registration,task_operation;"
        "from zdd.private_protocol import Deadline;"
        "assert sys.stdin.readline()=='launch\\n';"
        f"config=_registration(json.loads({json.dumps(config.frame())!r}));"
        "\ntry: task_operation('activate',config,Deadline(10))"
        "\nexcept DaemonSpawnError as error:"
        "\n assert 'broker-adoption-required' in str(error),str(error)"
        "\n print('missing-task-refused',flush=True)"
        "\nelse: raise AssertionError('unexpected task activation')"
    )
    owners = []
    process = None
    try:
        process, outer = pu.spawn_sync_in_kill_on_close_job(
            [str(Path(sys.base_prefix) / "python.exe"), "-I", "-c", program],
            require_job=True, allow_breakaway=False,
            env=protocol_probe.fixture_environment(dict(os.environ), control=True),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", creationflags=0x08000000,
        )
        assert outer is not None
        owners.append(outer)
        inner = protocol_probe.make_job(pu, owners)
        api = pu._kernel32()
        pu._configure_job_api(api)
        assert api.AssignProcessToJobObject(inner.handle, int(process._handle))
        process.stdin.write("launch\n")
        process.stdin.flush()
        process.wait(timeout=15)
        assert process.returncode == 0, process.stderr.read()
        assert process.stdout.read().strip() == "missing-task-refused"
    finally:
        for owner in reversed(owners):
            if owner.handle is not None:
                owner.terminate_and_wait(5)
        if process is not None:
            process.wait(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()


def test_protocol_direct_inspection_cannot_bypass_shared_host_refusal(monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    copy = Mock(side_effect=AssertionError("fixture escaped admission"))
    monkeypatch.setattr(protocol_probe.shutil, "copytree", copy)
    with pytest.raises(RuntimeError, match="ephemeral Windows CI consent"):
        protocol_probe.inspect(tmp_path)
    copy.assert_not_called()


@pytest.mark.parametrize("name", ["SYSTEMROOT", "SystemRoot", "systemroot"])
def test_protocol_fixture_preserves_case_insensitive_windows_prerequisites(name):
    result = protocol_probe.fixture_environment({
        name: r"C:\Windows", "PATH": r"C:\runtime", "GITHUB_ACTIONS": "true",
        "PathExt": ".COM;.EXE;.BAT;.CMD",
        "UNRELATED_CREDENTIAL": "must-not-cross",
    }, control=False)
    assert result == {
        name: r"C:\Windows", "PATH": r"C:\runtime", "PathExt": ".COM;.EXE;.BAT;.CMD",
    }
    assert protocol_probe.fixture_environment(
        {"GITHUB_ACTIONS": "true"}, control=True,
    ) == {"GITHUB_ACTIONS": "true"}


def test_protocol_caller_preserves_os_profile_context_but_never_arbitrary_credentials():
    source = {
        "USERPROFILE": r"C:\fixture-profile",
        "LocalAppData": r"C:\fixture-profile\AppData\Local",
        "ProgramFiles": r"C:\Program Files",
        "PSModulePath": r"C:\Windows\System32\WindowsPowerShell\v1.0\Modules",
        "GITHUB_TOKEN": "must-not-cross",
        "UNRELATED_CREDENTIAL": "must-not-cross",
    }
    assert protocol_probe.fixture_environment(source, control=True) == {
        key: value for key, value in source.items() if "must-not-cross" != value
    }
    assert protocol_probe.fixture_environment(source, control=False) == {}


@pytest.mark.skipif(os.name != "nt", reason="Native Windows executable-extension discovery")
@pytest.mark.parametrize("host", ["pwsh", "powershell"])
def test_real_consumer_environment_preserves_powershell_git_discovery(host):
    import shutil

    shell, git = shutil.which(host), shutil.which("git")
    if not shell or not git:
        pytest.skip("native PowerShell and Git are required")
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-Command",
         "$ErrorActionPreference='Stop'; (Get-Command git -CommandType Application).Source"],
        env=protocol_probe.fixture_environment(dict(os.environ), control=True),
        capture_output=True, text=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert Path(result.stdout.strip()).samefile(git)


def test_probe_refuses_inspection_outside_authorized_ci(monkeypatch, tmp_path):
    monkeypatch.setattr(probe.sys, "argv", ["probe", "inspect", str(tmp_path), "a" * 32])
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(probe, "Client", lambda *a, **kw: pytest.fail("host pipe opened"))
    with pytest.raises(RuntimeError, match="ephemeral CI"):
        probe.main()


@pytest.mark.parametrize("wire", [b"[]", b"null", b"bad-json"])
def test_probe_rejects_non_object_frames(wire):
    connection = Mock()
    connection.recv_bytes.return_value = wire
    with pytest.raises(ValueError):
        probe.receive(connection)
    connection.recv_bytes.assert_called_once_with(4096)


def test_probe_deadline_precedes_read():
    connection = Mock()
    connection.poll.return_value = False
    with pytest.raises(TimeoutError):
        probe.receive(connection)
    connection.recv_bytes.assert_not_called()


def test_probe_bounds_outgoing_frames():
    connection = Mock()
    with pytest.raises(ValueError, match="budget"):
        probe.send(connection, {"value": "x" * 4096})
    connection.send_bytes.assert_not_called()
    probe.send(connection, {"adopt": 42})
    assert json.loads(connection.send_bytes.call_args.args[0]) == {"adopt": 42}


def native(monkeypatch):
    api = Mock()
    api.OpenProcess.return_value = 100
    api.CloseHandle.return_value = True

    def times(handle, birth, *_):
        birth._obj.dwLowDateTime = 123
        return True

    api.GetProcessTimes.side_effect = times
    monkeypatch.setattr(probe, "kernel", lambda: api)
    monkeypatch.setattr(probe.ctypes, "get_last_error", lambda: 5, raising=False)
    monkeypatch.setattr(probe.ctypes, "WinError", lambda code: OSError(code, "refused"), raising=False)
    return api


def test_probe_pins_birth_before_control(monkeypatch):
    api = native(monkeypatch)
    with pytest.raises(RuntimeError, match="generation"):
        probe.Process(42, expected_birth=456)
    api.CloseHandle.assert_called_once_with(100)
    api.TerminateProcess.assert_not_called()


def test_probe_never_reopens_pid_for_retirement(monkeypatch):
    api = native(monkeypatch)
    api.WaitForSingleObject.side_effect = [0x102, 0]
    process = probe.Process(42, expected_birth=123)
    process.retire()
    process.close()
    api.OpenProcess.assert_called_once()
    api.TerminateProcess.assert_called_once_with(100, 1)
    api.CloseHandle.assert_called_once_with(100)


def test_probe_retains_custody_on_failed_close(monkeypatch):
    api = native(monkeypatch)
    process = probe.Process(42)
    api.CloseHandle.return_value = False
    with pytest.raises(OSError):
        process.close()
    assert process.handle == 100
    api.CloseHandle.return_value = True
    process.close()
    assert process.handle is None
