"""Prepared Windows whole-suite replacement evidence, never ordinary unit evidence.

The native owner invokes --prepare/--exercise with a finally-owned --settle in a
disposable hosted Windows VM. Selecting these tests without its evidence is a
failure, not a skipped or reduced-portfolio success.
"""
from __future__ import annotations

import argparse
import ast
from contextlib import contextmanager, nullcontext
import json
import os
from pathlib import Path
import runpy
import socket
import sys
import shutil
import subprocess
import time
from types import ModuleType, SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from manager_suite_fixture import SCHEMA, exercise, prepare, require_venue, settle
from manager_suite_fixture import _record
import manager_suite_fixture as fixture
import manager_suite_diagnostics as diagnostics


def test_fullsuite_refuses_execution_outside_the_disposable_venue(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    with pytest.raises(RuntimeError, match="disposable GitHub-hosted Windows VM"):
        require_venue()


def test_baseline_owner_demand_uses_the_installed_startup_contract(monkeypatch):
    import inspect

    tree = ast.parse(inspect.getsource(fixture.prepare))
    script = next(node.value for node in ast.walk(tree) if (
        isinstance(node, ast.Constant) and isinstance(node.value, str)
        and "hold('native-suite', 'manager-suite')" in node.value
    ))
    package = ModuleType("agent_codespaces")
    package.__path__ = []
    owner = ModuleType("agent_codespaces.connection_owner")
    calls = []
    owner.hold = lambda name, tenant: calls.append(("hold", name, tenant))

    def ensure(config):
        calls.append(("ensure", config))
        return True

    owner.ensure_owner_running = ensure
    monkeypatch.setitem(sys.modules, package.__name__, package)
    monkeypatch.setitem(sys.modules, owner.__name__, owner)
    exec(compile(script, "installed-baseline", "exec"), {})
    assert calls == [("hold", "native-suite", "manager-suite"), ("ensure", None)]


@pytest.mark.parametrize("reported_version", ("0.1.1.dev6", "0.1.1.dev5", "0.1.1.dev60"))
def test_machines_frontdoor_qualifies_its_actual_version_without_a_resolver_trace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reported_version: str,
) -> None:
    command = tmp_path / "h" / ".local" / "bin" / "agent-machines.cmd"
    command.parent.mkdir(parents=True)
    command.write_text("fixture", encoding="utf-8")
    after = {
        "commands": {"agent-machines": [{"command": "agent-machines",
                                        "module": "agent_machines", "variants": ["cmd"]}]},
        "origins": {"agent-machines": {"version": "0.1.1.dev6"}},
    }
    calls = []

    def run(argv: list[str], case: Path, **kwargs) -> SimpleNamespace:
        calls.append(argv[-1])
        return SimpleNamespace(returncode=0, stdout=f"agent-machines {reported_version}\n")

    monkeypatch.setattr(fixture, "_run", run)
    if reported_version == "0.1.1.dev6":
        row = fixture._frontdoors(tmp_path, after)["agent-machines"]["agent-machines.cmd"]
        assert row["routes_current"] is True
    else:
        with pytest.raises(RuntimeError, match="no actual V2 routing proof"):
            fixture._frontdoors(tmp_path, after)
    assert calls == ["--version"]


@pytest.mark.skipif(os.name != "nt" or shutil.which("pwsh") is None, reason="Native Index role readers")
def test_fixture_configures_host_for_native_and_dependency_free_index_readers(tmp_path: Path) -> None:
    from agent_procutil import no_window_kwargs

    (tmp_path / "project").mkdir()
    env = fixture._env(tmp_path)
    fixture._configuration(tmp_path, {})
    scripts = fixture.REPO / "plugins" / "agent-index" / "scripts"
    initialized = subprocess.run(
        [shutil.which("git"), "init", "-q", "--template=", "-b", "main", str(tmp_path / "project")],
        env=env, capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert initialized.returncode == 0, initialized.stdout + initialized.stderr

    def quote(value: object) -> str:
        return "'" + str(value).replace("'", "''") + "'"

    script = (
        "$ErrorActionPreference='Stop'; $tokens=$null; $errors=$null;"
        "$ast=[System.Management.Automation.Language.Parser]::ParseFile("
        f"{quote(scripts / 'install.ps1')},[ref]$tokens,[ref]$errors);"
        "$function=$ast.Find({param($node)"
        "$node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and "
        "$node.Name -eq 'Get-MachineRole'},$true);"
        ". ([scriptblock]::Create($function.Extent.Text));"
        f"$InstallDir={quote(tmp_path / 'h' / '.agent-index')}; Get-MachineRole"
    )
    commands = [
        [shutil.which("pwsh"), "-NoProfile", "-NonInteractive", "-Command", script],
        [sys.executable, "-I", "-S", "-B", str(scripts / "resolve-activation-role.py"),
         "--repo", str(tmp_path / "project"),
         "--machine", socket.gethostname().split(".")[0].strip().lower()],
    ]
    for command in commands:
        result = subprocess.run(
            command, cwd=tmp_path / "project", env=env, capture_output=True,
            text=True, timeout=30, **no_window_kwargs(),
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.strip() == "host", f"{command}: {result.stdout}{result.stderr}"


@pytest.mark.parametrize("name,routing_name,lock_name", (
    ("worktree-manager", "mux-daemon-routing", "mux-daemon.lock"),
    ("agent-worktrees", "status-monitor-routing", "status-monitor.lock"),
    ("agent-bridge", "", "running-version.json"),
    ("agent-dispatch", "", "running-version.json"),
    ("agent-index", "", "running-version.json"),
))
def test_observer_follows_successor_before_incumbent_releases_work_lease(
    tmp_path: Path, name: str, routing_name: str, lock_name: str,
) -> None:
    from zdd import routing

    (tmp_path / lock_name).write_text(json.dumps({"pid": 101}), encoding="utf-8")
    routing.publish_active(
        tmp_path / routing_name, bind="127.0.0.1", port=12345, pid=os.getpid(), version="successor",
    )
    assert _record(tmp_path, name)[0]["pid"] == os.getpid()
    assert json.loads((tmp_path / lock_name).read_text(encoding="utf-8"))["pid"] == 101


@pytest.mark.parametrize("exit_code", (259, 0))
def test_retained_engine_attests_original_health_and_native_lifetime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exit_code: int,
) -> None:
    (tmp_path / "stop").touch()
    health = {"pid": 101, "version": "engine-v1", "ready": True,
              "runtime_generation": "retained", "dependencies": {}}
    probes = []

    def probe(name: str, root: Path, pid: int) -> dict:
        probes.append((name, root, pid))
        return health

    samples = iter(((1234, 0, 259), (1234, 0, exit_code)))
    monkeypatch.setattr(fixture, "_resident_probe", probe)
    monkeypatch.setattr(fixture, "closing_process",
                        lambda pid: nullcontext(SimpleNamespace(times=lambda: next(samples))))
    report = fixture._watch_resident("agent-index/engine", tmp_path, 101, tmp_path)
    assert probes == [("agent-index/engine", tmp_path, 101)] * 2
    assert report == {"retention": {"before": health, "after": health,
                                    "created_ns": 1234, "exit_code": exit_code}}
    assert json.loads((tmp_path / "ready.json").read_text()) == health


def test_retained_engine_does_not_accept_a_replacement_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    health = {"pid": 101, "ready": True}
    monkeypatch.setattr(fixture, "_resident_probe", lambda *args: health)
    monkeypatch.setattr(fixture, "_record", lambda *args: ({"pid": 202}, 0))
    monkeypatch.setattr(fixture, "closing_process",
                        lambda pid: nullcontext(SimpleNamespace(times=lambda: (1234, 0, 259))))
    assert fixture._watch_resident("agent-index/engine", tmp_path, 101, tmp_path) == {
        "error": "Retained engine process changed during the update",
    }


@pytest.mark.parametrize("final_code", (259, 0))
def test_observer_pins_successor_until_final_native_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, final_code: int,
) -> None:
    held = set()
    reads = []
    health = {"pid": 101, "version": "v1", "ready": True,
              "runtime_generation": "g1", "dependencies": {}, "ready_observed_ns": 10}

    @contextmanager
    def process(pid):
        held.add(pid)

        def times():
            reads.append((pid, (tmp_path / "stop").exists()))
            assert pid in held
            return (pid, 0, final_code if pid == 202 and (tmp_path / "stop").exists() else 259)

        try:
            yield SimpleNamespace(times=times)
        finally:
            held.remove(pid)

    def stop_after_ready(_delay):
        assert held == {101, 202}
        (tmp_path / "stop").touch()

    monkeypatch.setattr(fixture, "closing_process", process)
    monkeypatch.setattr(fixture, "_resident_probe", lambda *args: health)
    monkeypatch.setattr(fixture, "_record", lambda *args: ({"pid": 202}, 0))
    monkeypatch.setattr(fixture.time, "sleep", stop_after_ready)
    row = fixture._watch_resident("agent-dispatch", tmp_path, 101, tmp_path)["observation"]
    assert row["new_exit_code_at_ready"] == 259
    assert row["new_exit_code"] == final_code
    assert reads.count((202, False)) == 1 and reads.count((202, True)) == 1
    assert held == set()


@pytest.mark.parametrize("name", ("agent-worktrees", "worktree-manager"))
def test_observer_keeps_consumer_demand_after_successor_readiness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str,
) -> None:
    refreshes = []
    health = {"pid": 101, "version": "v1", "ready": True,
              "runtime_generation": "g1", "dependencies": {}, "ready_observed_ns": 10}

    @contextmanager
    def demand(component, root):
        assert (component, root) == (name, tmp_path)
        try:
            yield lambda: refreshes.append(len(refreshes))
        finally:
            refreshes.append("closed")

    def stop_after_ready(_delay):
        assert len(refreshes) >= 3
        (tmp_path / "stop").touch()

    monkeypatch.setattr(fixture, "_consumer_demand", demand, raising=False)
    monkeypatch.setattr(fixture, "closing_process",
                        lambda pid: nullcontext(SimpleNamespace(times=lambda: (pid, 0, 259))))
    monkeypatch.setattr(fixture, "_resident_probe", lambda *args: health)
    monkeypatch.setattr(fixture, "_record", lambda *args: ({"pid": 202}, 0))
    monkeypatch.setattr(fixture.time, "sleep", stop_after_ready)
    row = fixture._watch_resident(name, tmp_path, 101, tmp_path)["observation"]
    assert row["new_exit_code"] == 259
    assert refreshes[-1] == "closed"


def test_bridge_incumbent_logs_are_captured_before_successor_truncation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "h" / ".agent-bridge"
    root.mkdir(parents=True)
    stderr = root / "agent-bridge-err.log"
    stderr.write_text("incumbent failure", encoding="utf-8")
    watchdog = root / "agent-bridge-watchdog.log"
    watchdog.write_text("incumbent watchdog reason", encoding="utf-8")
    application = root / "logs" / "diagnostics" / "daemon.jsonl"
    application.parent.mkdir(parents=True)
    application.write_text("incumbent application diagnostic", encoding="utf-8")
    control = tmp_path / "observers" / "agent-bridge"
    control.mkdir(parents=True)
    health = {"pid": 101, "version": "v1", "ready": True,
              "runtime_generation": "g1", "dependencies": {}, "ready_observed_ns": 10}
    old_samples = iter(((101, 0, 259), (101, 15, 70), (101, 15, 70)))

    def record(*args):
        stderr.write_text("successor startup", encoding="utf-8")
        return {"pid": 202}, 0

    monkeypatch.setattr(fixture, "closing_process", lambda pid: nullcontext(SimpleNamespace(
        times=lambda: next(old_samples) if pid == 101 else (202, 0, 259),
    )))
    monkeypatch.setattr(fixture, "_resident_probe", lambda *args: health)
    monkeypatch.setattr(fixture, "_record", record)
    monkeypatch.setattr(fixture.time, "sleep", lambda _: (control / "stop").touch())
    fixture._watch_resident("agent-bridge", root, 101, control)
    diagnostics.retain_resident_logs(tmp_path)
    retained = tmp_path / "resident-logs" / "agent-bridge"
    assert (retained / "incumbent-agent-bridge-err.log").read_text() == "incumbent failure"
    assert (retained / "agent-bridge-err.log").read_text() == "successor startup"
    assert (retained / "incumbent-agent-bridge-watchdog.log").read_text() == "incumbent watchdog reason"
    assert (retained / "logs" / "diagnostics" / "incumbent-daemon.jsonl").read_text() == (
        "incumbent application diagnostic"
    )


def test_manager_fixture_subscriber_follows_route_and_releases_both_generations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contextlib import ExitStack
    from work_coalescing_singleton import CoalescingServer
    from worktree_manager import mux_daemon_cutover as control

    now = [0.0]
    monkeypatch.setattr(diagnostics.time, "monotonic", lambda: now[0])
    token = control.load_or_create_control_token(tmp_path)
    with ExitStack() as owned:
        servers = [
            CoalescingServer(lambda *args: {}, token=token),
            CoalescingServer(lambda *args: {}, token=token),
        ]
        for server in servers:
            server.start()
            owned.callback(server.close)

        def select(server):
            port = int(server.rendezvous()["endpoint"].rsplit(":", 1)[1])
            control.publish_route(port, root=tmp_path, pid=os.getpid(), version="fixture")

        select(servers[0])
        with diagnostics._consumer_demand("worktree-manager", tmp_path) as refresh:
            refresh()
            assert [server.subscriber_count() for server in servers] == [1, 0]
            select(servers[1])
            now[0] = 6.0
            refresh()
            assert [server.subscriber_count() for server in servers] == [0, 1]
        assert [server.subscriber_count() for server in servers] == [0, 0]


@pytest.mark.skipif(os.name != "nt" or shutil.which("pwsh") is None, reason="Native PowerShell capture")
@pytest.mark.parametrize("owned_count", (0, 1, 2))
def test_task_inventory_captures_only_owned_unicode_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, owned_count: int,
) -> None:
    (tmp_path / "project").mkdir()
    execute = fixture._run
    captured: list[str] = []
    prefix = (
        "function Get-ScheduledTask {"
        "foreach($i in 1..10){[pscustomobject]@{TaskName=\"foreign-$i\";TaskPath='\\';State='Ready'}};"
        f"for($i=0;$i -lt {owned_count};$i++){{"
        "[pscustomobject]@{TaskName=\"owned-$i\";TaskPath='\\';State='Ready'}}};"
        "function Export-ScheduledTask {param($TaskName,$TaskPath);"
        "if($TaskName.StartsWith('owned-')){"
        "\"<Task><Home>$caseHome\\owned</Home><Description>$([char]0x2014)</Description></Task>\""
        "}else{'<Task>foreign'+('x'*65536)+'</Task>'}};"
    )

    def run(argv: list[str], case: Path, **options: object) -> object:
        result = execute([*argv[:-1], prefix + argv[-1]], case, **options)
        captured.append(result.stdout)
        return result

    monkeypatch.setattr(fixture, "_run", run)
    rows = fixture._tasks(tmp_path)
    assert {row["name"] for row in rows} == {f"owned-{i}" for i in range(owned_count)}
    assert len(captured[0]) < 4096 and "foreign" not in captured[0]
    assert all("\u2014" in row["xml"] for row in rows)


def test_diagnostics_copy_only_advertised_request_logs(tmp_path: Path) -> None:
    root = tmp_path / "h" / ".copilot-extensions" / "update-driver"
    root.mkdir(parents=True)
    identifier = "a" * 32
    (root / (identifier + ".log")).write_text("actual failure", encoding="utf-8")
    (root / ("b" * 32 + ".log")).write_text("unrelated", encoding="utf-8")
    (root / "witness.json").write_text("opaque witness", encoding="utf-8")
    diagnostics.retain_driver_logs(tmp_path, f"Update request {identifier}: interrupted")
    copied = list((tmp_path / "driver-logs").iterdir())
    assert [path.name for path in copied] == [identifier + ".log"]
    assert copied[0].read_text(encoding="utf-8") == "actual failure"
    assert (root / "witness.json").read_text(encoding="utf-8") == "opaque witness"


def test_diagnostics_bound_logs_without_mutating_the_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "h" / ".copilot-extensions" / "update-driver"
    root.mkdir(parents=True)
    identifier = "a" * 32
    source = root / (identifier + ".log")
    source.write_bytes(b"old lines\nactual failure")
    monkeypatch.setattr(diagnostics, "MAX_LOG_BYTES", 14)
    diagnostics.retain_driver_logs(tmp_path, f"Update request {identifier}; log:")
    assert source.read_bytes() == b"old lines\nactual failure"
    assert (tmp_path / "driver-logs" / source.name).read_bytes() == b"actual failure"
    report = json.loads((tmp_path / "driver-diagnostics.json").read_text(encoding="utf-8"))
    assert report[0]["truncated"] is True


def test_missing_driver_log_does_not_replace_the_primary_error(tmp_path: Path) -> None:
    diagnostics.retain_driver_logs(tmp_path, f"Update request {'a' * 32}: interrupted")
    report = json.loads((tmp_path / "driver-diagnostics.json").read_text(encoding="utf-8"))
    assert report[0]["error"].startswith("FileNotFoundError:")


def test_resident_diagnostics_are_bounded_and_exclude_application_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "h" / ".agent-codespaces"
    (root / "logs").mkdir(parents=True)
    (root / "logs" / "owner.log").write_bytes(b"old lines\nactual failure")
    (root / "credentials.json").write_text("not a diagnostic", encoding="utf-8")
    monkeypatch.setattr(diagnostics, "MAX_LOG_BYTES", 14)
    diagnostics.retain_resident_logs(tmp_path)
    copied = list((tmp_path / "resident-logs").rglob("*"))
    assert [path.name for path in copied if path.is_file()] == ["owner.log"]
    assert (tmp_path / "resident-logs" / "agent-codespaces" / "logs" / "owner.log").read_bytes() == b"actual failure"
    assert (root / "logs" / "owner.log").read_bytes() == b"old lines\nactual failure"


@pytest.mark.parametrize("check", (False, True))
def test_failed_commands_retain_driver_evidence_without_changing_their_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, check: bool,
) -> None:
    from agent_procutil import _caller_owned

    identifier = "a" * 32
    root = tmp_path / "h" / ".copilot-extensions" / "update-driver"
    root.mkdir(parents=True)
    (root / (identifier + ".log")).write_text("inner failure", encoding="utf-8")
    result = subprocess.CompletedProcess(["fixture"], 1, f"Update request {identifier}", "failure")
    monkeypatch.setattr(_caller_owned, "run", lambda *args, **kwargs: result)
    if check:
        with pytest.raises(AssertionError, match="failure"):
            fixture._run(["fixture"], tmp_path, check=True)
    else:
        assert fixture._run(["fixture"], tmp_path, check=False) is result
    assert (tmp_path / "driver-logs" / (identifier + ".log")).read_text(encoding="utf-8") == "inner failure"


@pytest.mark.skipif(os.name != "nt", reason="Native Windows scoped handoff pipes")
def test_scoped_engine_handoff_keeps_inherited_pipes_without_showing_windows() -> None:
    import mux_windows_observer as observer
    from agent_procutil import no_window_kwargs

    child = (
        "import sys,time;"
        "print('engine progress',flush=True);"
        "print('engine failure',file=sys.stderr,flush=True);"
        "time.sleep(.25);sys.exit(7)"
    )
    parent = (
        "import sys,update_driver;"
        "from worktree_manager import engine_client as ec;"
        "update_driver.participant_environment=lambda:{'UPDATE_DRIVER_WITNESS':'fixture'};"
        f"ec.set_engine_command([sys.executable,'-I','-X','utf8','-c',{child!r}]);"
        "results=[ec.run_engine_passthrough(None,['update']) for _ in range(2)];"
        "assert results==[7,7],results"
    )
    with subprocess.Popen(
        [sys.executable, "-I", "-X", "utf8", "-c", parent],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", **no_window_kwargs(),
    ) as process:
        owned, shown, focused = {process.pid}, set(), set()
        deadline = time.monotonic() + 15
        while process.poll() is None and time.monotonic() < deadline:
            owned.update(observer.descendants(process.pid, observer.processes()))
            windows, foreground = observer.windows()
            shown.update(set(windows.values()) & owned)
            if foreground in owned:
                focused.add(foreground)
            time.sleep(.025)
        stdout, stderr = process.communicate(timeout=2)
        assert process.returncode == 0, stdout + stderr
    assert stdout.splitlines() == ["engine progress"] * 2
    assert stderr.splitlines() == ["engine failure"] * 2
    assert not shown and not focused, (shown, focused)


@pytest.mark.skipif(os.name != "nt" or shutil.which("pwsh") is None, reason="Native PowerShell pipes")
@pytest.mark.parametrize("operation", ("Complete-RuntimeConstruction", "Stop-RuntimeConstruction"))
def test_construction_owner_failure_survives_closed_input_pipe(tmp_path: Path, operation: str) -> None:
    from agent_procutil import no_window_kwargs

    child = tmp_path / "owner.py"
    child.write_text(
        "import sys\nprint('{\"admission\":\"construct\"}',flush=True)\n"
        "print('fixture owner failure',file=sys.stderr,flush=True)\nsys.exit(9)\n",
        encoding="utf-8",
    )
    helpers = runpy.run_path(str(
        fixture.REPO / "libs" / "versioned-runtime" / "versioned_runtime_construction.py",
    ))["construction_shell_helpers"]("ps1")
    def quote(value: object) -> str:
        return "'" + str(value).replace("'", "''") + "'"

    script = helpers + (
        "\n$ErrorActionPreference='Stop'\n"
        f"Start-RuntimeConstruction -Python {quote(sys.executable)} -Helper {quote(child)} "
        f"-Root {quote(tmp_path)} -Version '1.0' -PayloadHash '{'a' * 64}' -LinkName 'current' | Out-Null\n"
        "$script:RuntimeConstructionSession.Process.WaitForExit()\n"
        f"try {{ {operation} }} catch {{ Write-Output $_.Exception.Message }}\n"
        "if ($script:RuntimeConstructionSession) {"
        "$script:RuntimeConstructionSession.Process.Dispose()}\n"
    )
    result = subprocess.run(
        [shutil.which("pwsh"), "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Construction owner exited (9): fixture owner failure" in result.stdout


@pytest.mark.parametrize("mode", ("normal", "repair"))
def test_installed_manager_replaces_the_whole_applicable_suite(mode: str) -> None:
    raw = os.environ.get("MANAGER_SUITE_ACCEPTANCE_DATA")
    assert raw, "Prepare/exercise both modes on a disposable GitHub-hosted Windows runner"
    directory = Path(raw).resolve()
    preparation_error = directory / mode / "preparation-error.json"
    assert not preparation_error.exists(), (
        preparation_error.read_text(encoding="utf-8") if preparation_error.exists() else ""
    )
    report = json.loads((directory / mode / "result.json").read_text(encoding="utf-8"))
    assert report["schema"] == SCHEMA and report["mode"] == mode
    assert report["venue"] == "github-hosted-windows"
    assert not report["errors"], json.dumps(report["errors"], indent=2)
    assert report["update"]["returncode"] == 0, (
        report["update"]["stdout"] + report["update"]["stderr"]
    )
    arguments = report["update"]["arguments"]
    if mode == "normal":
        assert arguments == ["update", "--project", "example"]
    else:
        assert arguments == [
            "--bootstrap-update", "update", "--force", "--project", "example",
        ]
        assert report["update"]["command"][1:] == [
            "-NoProfile", "-File", str(Path(report["source"]) / "worktree-manager" / "bootstrap.ps1"),
            *arguments,
        ]

    declared = set(report["declared"])
    assert declared == set(report["before"]["portfolio"]) == set(report["after"]["portfolio"])
    assert len(declared) > 2, "A component/subset fixture is not whole-suite acceptance"
    assert set(report["applicability"]) == declared
    assert all(value["eligible"] for value in report["applicability"].values())
    assert report["before"]["engine"]["prefix"] != report["after"]["engine"]["prefix"]
    for generation in ("before", "after"):
        engine = report[generation]["engine"]
        assert Path(engine["module"]).is_relative_to(Path(engine["prefix"]))
        assert Path(engine["root"]) == directory / mode / "h" / ".agent-worktrees"
        assert set(report[generation]["versions"]) == declared | {"worktree-manager"}
    for name in declared | {"worktree-manager"}:
        assert report["before"]["versions"][name] != report["after"]["versions"][name]
        assert report["retained"][name], f"{name}: V1 sealed content changed or disappeared"
        expected = {
            entry["command"] + ("." + extension if extension else "")
            for entry in report["after"]["commands"][name] for extension in entry["variants"]
        }
        assert set(report["commands"][name]) == expected
        assert all(row["returncode"] == 0 and row["routes_current"] and row["proof"]
                   for row in report["commands"][name].values())
        before_origin = report["before"]["origins"][name]
        after_origin = report["after"]["origins"][name]
        assert before_origin["prefix"] != after_origin["prefix"]
        assert Path(after_origin["module"]).is_relative_to(Path(after_origin["prefix"]))
        assert report["exclusive_writers"][name], f"{name}: publication lease was not exclusive"

    assert report["durable_before"] == report["durable_after"]
    assert report["witness"] == {"rows": [["retained"]], "schema": 7}
    assert report["registrations"], "No actual scheduled-registration evidence"
    assert all(row["routes_current"] for row in report["registrations"])
    assert all(row["resolver_proofs"] for row in report["registrations"])
    assert report["index_engine_retained"], "The separate V1 embedding-engine slot changed"
    assert report["exclusive_work"], "The resident Manager did not retain exclusive-work custody"
    assert set(report["retained_residents"]) == {"agent-index/engine"}
    assert set(report["residents"]) | set(report["retained_residents"]) == set(report["required_residents"])
    retained = report["retained_residents"]["agent-index/engine"]
    assert retained["exit_code"] == 259
    before_engine = report["before"]["resident_observations"]["agent-index/engine"]
    assert retained["created_ns"] == before_engine["created_ns"]
    for field in ("pid", "version", "runtime_generation", "ready"):
        assert retained["before"][field] == retained["after"][field] == before_engine[field]
    for name, row in report["residents"].items():
        assert row["old_pid"] != row["new_pid"], name
        assert row["old_version"].replace("-dev", ".dev") == (
            report["before"]["resident_targets"][name].replace("-dev", ".dev")
        ), name
        assert row["old_exit_code"] == 0, name
        assert row["old_exit_code_at_ready"] == 259, name
        assert row["new_exit_code_at_ready"] == row["new_exit_code"] == 259, name
        assert row["new_ready_observed_ns"] <= row["old_exit_ns"], name
        assert row["old_created_ns"] < row["new_created_ns"], name
        assert row["new_version"].replace("-dev", ".dev") == (
            report["after"]["resident_targets"][name].replace("-dev", ".dev")
        ), name
        if name == "worktree-manager":
            assert row["old_runtime_generation"] == report["before"]["versions"][name]
            assert row["new_runtime_generation"] == report["after"]["versions"][name]
    assert not report["cleanup_errors"], json.dumps(report["cleanup_errors"], indent=2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    stages = parser.add_mutually_exclusive_group(required=True)
    stages.add_argument("--prepare", type=Path)
    stages.add_argument("--exercise", type=Path)
    stages.add_argument("--settle", type=Path)
    parser.add_argument("--mode", choices=("normal", "repair"), required=True)
    options = parser.parse_args()
    if options.prepare is not None:
        prepare(options.prepare, options.mode)
    elif options.exercise is not None:
        exercise(options.exercise, options.mode)
    else:
        settle(options.settle, options.mode)
