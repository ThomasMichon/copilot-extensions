"""Scheduled entry and ensure share real native custody; business work is inert."""
from __future__ import annotations

from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
import time

import pytest

import agent_dispatch

INSTALLER = Path(__file__).resolve().parents[1] / "scripts" / "install.ps1"
pytestmark = pytest.mark.skipif(os.name != "nt", reason="contained Windows scheduled entry")


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


@contextmanager
def application(tmp_path, shell, *, master=False, source=False, inert_business=True):
    root = tmp_path / "application"
    version = agent_dispatch.__version__
    if source:
        version += "+source." + "a" * 40 + "." + "b" * 16 + ".profile." + "c" * 16
    slot = root / "versions" / version
    prefix = slot / ".venv" if source else slot
    scripts = prefix / "Scripts"
    site = prefix / "Lib" / "site-packages"
    scripts.mkdir(parents=True)
    site.mkdir(parents=True)
    python = scripts / "python.exe"
    shutil.copyfile(sys.executable, python)
    (prefix / "pyvenv.cfg").write_text(
        f"home = {sys.base_prefix}\ninclude-system-site-packages = false\nversion = {sys.version.split()[0]}\n",
    )
    shutil.copytree(Path(agent_dispatch.__file__).parent, site / "agent_dispatch",
                    ignore=shutil.ignore_patterns("__pycache__"))
    (site / "fixture_host_boundary.py").write_text("""
from agent_procutil import owned_process
original=owned_process.run_caller_owned
def scheduling_boundary(command, **options):
    if '-Command' in command and 'Read-DispatchInstallationPolicy' in command[-1]:
        prefix="function Get-ScheduledTask {param($TaskName,$ErrorAction)}; "
        return original([*command[:-1],prefix+command[-1]],**options)
    return original(command,**options)
owned_process.run_caller_owned=scheduling_boundary
""")
    (site / "fixture_business.py").write_text("""
import os,time,sys
from pathlib import Path
root=Path(os.environ['AGENT_DISPATCH_INSTALL_DIR'])
if os.environ.get('FIXTURE_SOURCE')=='1':
    from agent_dispatch import pool_source_snapshot,registrar_discovery
    cohort=lambda *args,**options: pool_source_snapshot.DeclarationRead((),True)
    pool_source_snapshot.read_supervisor_declarations=cohort
    registrar_discovery.read_legacy_env_profiles=cohort
if '-m' in sys.argv and 'supervise' in sys.argv:
    from agent_dispatch import supervisor, diagnostic_logs
    def poll(self,**options):
        with (root/'cycles').open('a') as stream: stream.write(str(os.getpid())+'\\n')
        return []
    def wait(self,interval):
        if (root/'release').exists(): raise KeyboardInterrupt()
        time.sleep(.02)
    supervisor.Supervisor.poll_once=poll
    supervisor.Supervisor._recheck_governance=lambda *args: None
    supervisor.Supervisor.wait_for_turn_end=wait
    diagnostic_logs.maintain_file_logging=lambda: None
    if os.environ.get('FIXTURE_MASTER')=='1':
        import contextlib,json,logging
        from types import SimpleNamespace
        from agent_dispatch import procutil,supervise_cli,supervisor_daemon
        original_init=supervisor_daemon.SupervisorDaemon.__init__
        original_spawn=procutil.spawn_dispatch_daemon
        original_release=supervisor_daemon.SupervisorDaemon.release_singleton
        def spawn(*args,**options):
            child=original_spawn(*args,**options)
            if options.get('log_name')=='logs/supervisor-successor.log':
                while not (root/'release').exists(): time.sleep(.02)
            return child
        def initialize(self,*args,**options):
            def sleep(interval):
                if (root/'release').exists(): raise KeyboardInterrupt()
                time.sleep(min(interval,.1))
            options['sleep']=sleep
            original_init(self,*args,**options)
            logging.getLogger('agent-dispatch').setLevel(logging.DEBUG)
        def reconcile(self):
            poll(self)
            with (root/'worker-markers').open('a') as stream:
                stream.write(json.dumps([os.environ.get('AGENT_DISPATCH_SUPERVISOR_PROFILE'),
                                         os.environ.get('AGENT_DISPATCH_SUPERVISOR_PROFILE_GENERATION'),
                                         os.environ.get('AGENT_DISPATCH_SUPERVISOR_PROFILE_OWNER')])+'\\n')
            return supervisor_daemon.ReconcileSummary()
        supervisor_daemon.SupervisorDaemon.__init__=initialize
        supervisor_daemon.SupervisorDaemon.reconcile_once=reconcile
        supervisor_daemon.SupervisorDaemon._recheck_governance=lambda *args: None
        supervise_cli._client=lambda *args,**options: contextlib.nullcontext(SimpleNamespace(close=lambda:None))
        procutil.spawn_dispatch_daemon=spawn
        if os.environ.get('FIXTURE_SOURCE')=='1':
            from agent_dispatch import source_start,config
            from zdd.diagnostics import process_start_time
            class Client:
                def close(self): pass
                def health(self):
                    return dict(status='ok',runtime_config_identity=source_start.config_identity(config.load_config(),base_dir=root),
                                slot=dict(pid=os.getpid(),process_start_time=process_start_time(os.getpid())))
            supervise_cli._client=lambda *args,**options: contextlib.nullcontext(Client())
            def release(self):
                original_release(self)
                state=self._source_handoff
                if state is not None and state['phase']=='withdrawing':
                    while not (root/'release').exists(): time.sleep(.02)
            supervisor_daemon.SupervisorDaemon.release_singleton=release
""")
    (site / "fixture.pth").write_text(
        "import site; site.addsitedir(" + repr(sysconfig.get_paths()["purelib"]) + ")"
        + "; import fixture_host_boundary"
        + ("; import fixture_business" if inert_business else "") + "\n",
    )
    (root / "current-version").write_text(version)
    if source:
        # Construction evidence is fixture input; selected-Source/completion
        # readers and the native candidate lifecycle are exercised unchanged.
        bootstrap = slot / ".runtime-bootstrap"
        bootstrap.mkdir()
        for helper in INSTALLER.parent.glob("versioned_runtime*.py"):
            shutil.copyfile(helper, bootstrap / helper.name)
        (slot / ".install-complete.json").write_text(json.dumps({
            "version": version, "completed_at": "fixture", "pid": os.getpid(),
        }))
    (root / "supervisor.env").write_text(
        "AGENT_DISPATCH_SUPERVISE_LABELS=fixture\n"
        "AGENT_DISPATCH_SUPERVISE_EMBODY_BACKEND=script\n"
        "AGENT_DISPATCH_SUPERVISE_INTERVAL=.05\n"
        "AGENT_DISPATCH_SUPERVISE_EXTRA_ARGS=--no-reactive --no-heartbeat\n",
    )
    (root / "installation.json").write_text(json.dumps({
        "version": 1, "installation": {"root": str(root), "local": {
            "windows_scheduled_task": "preserve", "start_coordinator": False,
            "start_supervisor": True,
        }},
    }))
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("AGENT_DISPATCH", "PYTHON"))
    }
    environment.update(AGENT_DISPATCH_INSTALL_DIR=str(root), AGENT_DISPATCH_RUN_DIR=str(root / "run"))
    if not inert_business:
        profile = root / "profile"
        profile.mkdir()
        environment.update(
            HOME=str(profile), USERPROFILE=str(profile),
            APPDATA=str(profile / "roaming"), LOCALAPPDATA=str(profile / "local"),
            AGENT_DISPATCH_ROUTING_DIR=str(root / "routes"),
            AGENT_DISPATCH_DB=str(root / "data" / "tasks.db"),
            AGENT_DISPATCH_HOST="127.0.0.1",
            AGENT_DISPATCH_TOKEN="disposable-data-token",
            AGENT_DISPATCH_CONTROL_TOKEN="disposable-control-token",
            AGENT_DISPATCH_GC_INTERVAL="0", AGENT_DISPATCH_SELF_UPDATE="0",
        )
        for key in ("COPILOT_PLUGIN_ROOT", "COPILOT_AGENT_SESSION_ID", "COPILOT_EXTENSIONS_CONTEXT"):
            environment.pop(key, None)
    if master:
        environment.update(FIXTURE_MASTER="1", AGENT_DISPATCH_SUPERVISOR_SELF_UPDATE_POLL_S="1")
        (root / "supervisor.env").write_text(
            "AGENT_DISPATCH_SUPERVISE_MODE=serve\nAGENT_DISPATCH_SUPERVISE_MACHINE=fixture\n"
            "AGENT_DISPATCH_SUPERVISE_INTERVAL=.05\n",
        )
    if source:
        environment["FIXTURE_SOURCE"] = "1"
    publisher = root / "publish.ps1"
    publisher.write_text(f"""
$ErrorActionPreference='Stop'
$script:GlobalActivationLockTimeoutSeconds=5
$InstallDir={quote(root)};$LinkDir={quote(root / '.venv')};$LinkPython={quote(python)}
$utf8NoBom=[Text.UTF8Encoding]::new($false)
$tokens=$null;$errors=$null
$tree=[Management.Automation.Language.Parser]::ParseFile({quote(INSTALLER)},[ref]$tokens,[ref]$errors)
if($errors.Count){{throw 'source parse failed'}}
$definition=@($tree.FindAll({{param($node)
 $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -in @(
  'Enter-PluginSnapshotLock','Publish-FileAtomically','Publish-ServiceLauncher','Write-SupervisorLauncher')
}},$true))
if($definition.Count -ne 4){{throw 'writer and publication helper boundaries are ambiguous'}}
foreach($function in $definition){{ . ([scriptblock]::Create($function.Extent.Text)) }}
Write-SupervisorLauncher|Out-Null
""")
    if not master:
        result = subprocess.run(
            [shell, "-NoProfile", "-NonInteractive", "-File", str(publisher)],
            env=environment, capture_output=True, text=True, timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert result.returncode == 0, result.stderr
    from agent_procutil.daemon_instances import REGISTRY_ENV, daemon_test_lifecycle
    cleanup = []
    with daemon_test_lifecycle(
        owner="dispatch-scheduled-admission", registry_root=root / "native-custody",
        cleanup_results=cleanup,
    ) as native:
        environment.update(native)
        environment[REGISTRY_ENV] = str(root / "native-custody")
        children = []
        try:
            yield root, python, environment, children
        finally:
            (root / "release").touch()
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if not inert_business:
                    break
                from agent_dispatch.single_instance import is_locked, lock_path_for
                from agent_dispatch.supervisor_lane_status import profile_scope
                from agent_dispatch.supervisor_registration import supervisor_lease_scope
                if master:
                    from agent_dispatch import __main__ as cli
                    from agent_dispatch.registrar_discovery import _parse_env_file
                    from agent_dispatch.supervisor_lane_status import profile_arguments
                    args = cli.build_parser().parse_args(profile_arguments(_parse_env_file(root / "supervisor.env", strict=True)))
                    scope = supervisor_lease_scope(args.machine, args.env)
                else:
                    scope = profile_scope(root / "supervisor.env")
                if not is_locked(lock_path_for(root / "run", scope)):
                    break
                time.sleep(.02)
            else:
                raise AssertionError("contained fixture lane did not finish cooperative release")
            for child in children:
                try:
                    child.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    child.terminate()  # Exact fixture control, not a worker or daemon inventory.
                    child.wait(timeout=2)
                    raise AssertionError("fixture admission control did not finish:\n" + diagnostics(root))
    if not inert_business:
        assert cleanup and all(item.status in {"settled", "missing"} for item in cleanup)


def ensure_command(root, python):
    return [
        str(python), "-I", "-B", "-c",
        "import json,sys; from pathlib import Path; "
        "from agent_dispatch.supervisor_lane_status import ensure,profile_arguments; "
        "from agent_dispatch.registrar_discovery import _parse_env_file; "
        "r=Path(sys.argv[1]); p=r/'supervisor.env'; "
        "print(json.dumps(ensure(r,p,profile_arguments(_parse_env_file(p,strict=True)),timeout=30)))",
        str(root),
    ]


def launch(root, name, command, environment, children):
    # Admission completion must not depend on descendant-held pipe EOF.
    with (root / f"{name}.out").open("wb") as output, (root / f"{name}.err").open("wb") as error:
        child = subprocess.Popen(
            command, env=environment, stdout=output, stderr=error,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    children.append(child)
    return child


def diagnostics(root):
    from agent_procutil._daemon_instance_identity import identity_state
    from agent_procutil.daemon_instances import inspect_instances
    fields = ("instance_id", "state", "pid", "birth", "runtime_root")
    native = [
        {
            **{key: row.get(key) for key in fields},
            "native_state": identity_state(int(row["pid"]), row["birth"]) if row.get("pid") else "unbound",
        }
        for row in inspect_instances(root / "native-custody")
    ]
    return json.dumps(native) + "\n" + "\n".join(
        str(path.relative_to(root)) + ":\n" + path.read_text(errors="replace")
        for path in root.rglob("*")
        if path.is_file() and (path.suffix in {".log", ".out", ".err", ".jsonl"} or path.name.endswith(".status.json")
                               or path.parent.name == "supervisor-candidates")
    )


def assert_completed_generation(status):
    cycle = status["completed_cycle"]
    assert 0 < cycle["cycle_id"] <= status["cycle_id"]
    assert math.isfinite(cycle["completed_at"]) and math.isfinite(cycle["started_at"])
    assert cycle["completed_at"] >= cycle["started_at"]
    assert cycle["completed_at"] <= time.time()
    assert cycle["generation"] == status["generation"]
    assert cycle["runtime_version"] == status["runtime_version"]


@pytest.mark.timeout(150)
def test_unmocked_source_entrypoints_admit_real_coordinator_and_supervisor(tmp_path):
    from agent_dispatch.supervisor_registration import supervisor_lease_scope
    from agent_dispatch.supervisor_status_io import read_status, runtime_status_path
    from zdd import diagnostics as native_identity, routing

    with application(tmp_path, None, master=True, source=True, inert_business=False) as (
        root, python, environment, children,
    ):
        policy_path = root / "installation.json"
        policy = json.loads(policy_path.read_text())
        local = policy["installation"]["local"]
        local.update(start_coordinator=True, start_supervisor=False)
        policy_path.write_text(json.dumps(policy))
        (root / "supervisor.env").write_text(
            "AGENT_DISPATCH_SUPERVISE_MODE=serve\nAGENT_DISPATCH_SUPERVISE_MACHINE=fixture\n"
            "AGENT_DISPATCH_SUPERVISE_EXTRA_ARGS=--no-declared\n",
        )
        command = [str(python), "-I", "-B", "-m", "agent_dispatch", "__source-start"]
        # These are two real startup operations, each with its existing bound.
        coordinator = launch(root, "unmocked-coordinator", command, environment, children)
        try:
            coordinator.wait(timeout=60)
        except subprocess.TimeoutExpired:
            pytest.fail("unmocked coordinator startup did not complete:\n" + diagnostics(root))
        assert coordinator.returncode == 0, diagnostics(root)
        assert json.loads((root / "unmocked-coordinator.out").read_text())["ok"] is True
        endpoint = routing.Endpoint.from_dict(routing.read_table(root / "routes")["active"])
        assert endpoint and native_identity.process_start_time(endpoint.pid) == endpoint.process_start_time
        local.update(start_coordinator=False, start_supervisor=True)
        policy_path.write_text(json.dumps(policy))
        supervisor = launch(root, "unmocked-supervisor", command, environment, children)
        try:
            supervisor.wait(timeout=60)
        except subprocess.TimeoutExpired:
            pytest.fail("unmocked supervisor startup did not complete:\n" + diagnostics(root))
        assert supervisor.returncode == 0, diagnostics(root)
        scope = supervisor_lease_scope("fixture", "default")
        status, error = read_status(runtime_status_path(scope, directory=root / "run"))
        assert error is None, diagnostics(root)
        assert_completed_generation(status)
        assert status["generation"]["pid"] != endpoint.pid
        assert not (root / "cycles").exists(), "business methods must not be replaced"


@pytest.mark.parametrize("host", ["powershell.exe", "pwsh"])
@pytest.mark.parametrize("overlap", [False, True], ids=["scheduled-first", "overlapping-admission"])
def test_scheduled_activation_then_ensure_uses_one_native_lane(tmp_path, host, overlap):
    shell = shutil.which(host)
    if shell is None:
        pytest.skip(f"{host} unavailable")
    with application(tmp_path, shell) as (root, python, environment, children):
        command = [shell, "-NoProfile", "-NonInteractive", "-File", str(root / "supervise-service.ps1")]
        first = launch(root, "scheduled", command, environment, children)
        concurrent = launch(root, "overlap", ensure_command(root, python), environment, children) if overlap else None
        try:
            first.wait(timeout=20)
        except subprocess.TimeoutExpired:
            pytest.fail("scheduled admission did not finish:\n" + diagnostics(root))
        if concurrent:
            concurrent.wait(timeout=30)
        details = diagnostics(root)
        codes = [first.returncode] + ([concurrent.returncode] if concurrent else [])
        assert 0 in codes, details
        for code in codes:
            if code:
                # The shared finite ensure lock may reject an overlapping caller.
                assert "already in progress" in details
        result = launch(root, "after", ensure_command(root, python), environment, children)
        result.wait(timeout=30)
        assert result.returncode == 0, (root / "after.err").read_text()
        ready = json.loads((root / "after.out").read_text())
        assert ready["status"] == "ready"
        assert ready["native_observed"] is True
        assert set((root / "cycles").read_text().splitlines()) == {str(ready["pid"])}


def test_marked_master_same_version_profile_handoff_preserves_completed_readiness(tmp_path, capsys):
    # Keep the contained predecessor's cleanup Job open; this proves configuration,
    # not successor survival after caller cleanup.
    shell = shutil.which("powershell.exe")
    with application(tmp_path, shell, master=True) as (root, python, environment, children):
        command = [
            str(python), "-I", "-B", "-c",
            "import json,sys; from pathlib import Path; "
            "from agent_dispatch.supervisor_lane_status import ensure,profile_arguments; "
            "from agent_dispatch.registrar_discovery import _parse_env_file; "
            "r=Path(sys.argv[1]); p=r/'supervisor.env'; "
            "original=ensure(r,p,profile_arguments(_parse_env_file(p,strict=True)),timeout=25); "
            "(r/'fixture-phase.log').write_text(json.dumps({'initial_ready':original})); "
            "p.write_text(p.read_text().replace('INTERVAL=.05','INTERVAL=.08')"
            "+'AGENT_DISPATCH_SUPERVISE_EXTRA_ARGS=--no-declared\\n'); "
            "ready=ensure(r,p,profile_arguments(_parse_env_file(p,strict=True)),timeout=25); "
            "print(json.dumps({'original':original,'ready':ready}))",
            str(root),
        ]
        control = launch(root, "handoff", command, environment, children)
        try:
            control.wait(timeout=20)
        except subprocess.TimeoutExpired:
            with capsys.disabled():
                print(diagnostics(root)[-24000:], file=sys.stderr, flush=True)
            pytest.fail("marked master profile handoff timed out:\n" + diagnostics(root))
        assert control.returncode == 0, diagnostics(root)
        result = json.loads((root / "handoff.out").read_text())
        original, ready = result["original"], result["ready"]
        assert ready["status"] == "ready" and ready["pid"] != original["pid"]
        assert set((root / "cycles").read_text().splitlines()) == {str(original["pid"]), str(ready["pid"])}
        assert all(json.loads(line) == [None, None, None] for line in (root / "worker-markers").read_text().splitlines())


def test_source_admitted_master_profile_edit_replaces_itself_not_its_retained_preparation(tmp_path, capsys):
    # Source handoff also retains the contained predecessor after the real
    # singleton release, not after cleanup and not before candidate preparation.
    with application(tmp_path, None, master=True, source=True) as (root, python, environment, children):
        program = """
import hashlib,json,sys,time
from pathlib import Path
from agent_dispatch import __version__
from agent_dispatch.supervisor_lane_status import ensure,profile_arguments,PROFILE_ENV,GENERATION_ENV
from agent_dispatch.registrar_discovery import _parse_env_file
from agent_dispatch.supervisor_source import spawn_candidate,read_prepared
from agent_dispatch.supervisor_registration import supervisor_lease_scope
from agent_dispatch.single_instance import is_locked,lock_path_for
r=Path(sys.argv[1]);p=r/'supervisor.env'
arguments=lambda:profile_arguments(_parse_env_file(p,strict=True))
scope=supervisor_lease_scope('fixture','default')
child=spawn_candidate(sys.executable,arguments(),root=r,version=__version__,
    profile_binding={PROFILE_ENV:str(p),GENERATION_ENV:hashlib.sha256(p.read_bytes()).hexdigest()})
deadline=time.monotonic()+20
while not is_locked(lock_path_for(r/'run',scope)):
    if child.poll() is not None or time.monotonic()>=deadline: raise RuntimeError('Source predecessor did not admit')
    time.sleep(.02)
original=ensure(r,p,arguments(),timeout=20)
old=read_prepared(r,__version__,scope,pid=original['pid'])
assert old is not None
p.write_text(p.read_text().replace('INTERVAL=.05','INTERVAL=.08')+
    'AGENT_DISPATCH_SUPERVISE_EXTRA_ARGS=--no-declared\\n')
ready=ensure(r,p,arguments(),timeout=20)
new=read_prepared(r,__version__,scope,pid=ready['pid'])
assert new is not None
print(json.dumps(dict(original=original,ready=ready,old=old,new=new,version=__version__)))
"""
        control = launch(root, "source-handoff", [str(python), "-I", "-B", "-c", program, str(root)], environment, children)
        try:
            control.wait(timeout=20)
        except subprocess.TimeoutExpired:
            with capsys.disabled():
                print(diagnostics(root)[-24000:], file=sys.stderr, flush=True)
            pytest.fail("Source-admitted profile handoff timed out:\n" + diagnostics(root))
        assert control.returncode == 0, diagnostics(root)
        result = json.loads((root / "source-handoff.out").read_text())
        assert ".profile." in result["version"]
        assert result["ready"]["status"] == "ready"
        assert result["ready"]["pid"] != result["original"]["pid"]
        assert result["old"]["instance_id"] != result["new"]["instance_id"]
        assert result["old"]["settings"]["interval"] == .05
        assert result["new"]["settings"]["interval"] == .08
        assert result["new"]["settings"]["no_declared"] is True
        assert result["old"]["profile_binding"]["generation"] != result["new"]["profile_binding"]["generation"]
        assert set((root / "cycles").read_text().splitlines()) == {
            str(result["original"]["pid"]), str(result["ready"]["pid"]),
        }
        assert all(json.loads(line) == [None, None, None] for line in (root / "worker-markers").read_text().splitlines())


def installer_callback(root, profile):
    callback = root / "installer-callback.ps1"
    helper = INSTALLER.parent.parent / "src" / "agent_dispatch" / "windows-scheduling.ps1"
    callback.write_text(f"""
$ErrorActionPreference='Stop'
$InstallDir={quote(root)};$ScriptDir={quote(INSTALLER.parent)}
. {quote(INSTALLER.with_name('versioned_runtime_native.ps1'))}
. {quote(helper)}
function Get-BootstrapPython {{return {quote(sys.executable)}}}
function Get-ScheduledTask {{param($TaskName,$ErrorAction)}}
function Register-ScheduledTask {{throw 'host scheduling is excluded'}}
function Start-ScheduledTask {{throw 'host scheduling is excluded'}}
function Stop-ScheduledTask {{throw 'host scheduling is excluded'}}
function Unregister-ScheduledTask {{throw 'host scheduling is excluded'}}
function Start-DispatchDetachedLogonLauncher {{throw 'Source must retain its admitted application owner'}}
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile({quote(INSTALLER)},[ref]$tokens,[ref]$errors)
if($errors.Count){{throw 'source parse failed'}}
$mode=@($ast.FindAll({{param($node)
 $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Get-SupervisorMode'
}},$true))
if($mode.Count -ne 1){{throw 'installer mode owner is ambiguous'}}
. ([scriptblock]::Create($mode[0].Extent.Text))
Invoke-DispatchSupervisorEnsure -Name fixture-supervisor -EnvFile {quote(profile)} -Launcher fixture-unused
""")
    return callback


def test_actual_installer_source_supervisor_callback_preserves_supported_primary_flags(tmp_path, capsys):
    shell = shutil.which("powershell.exe")
    with application(tmp_path, shell, master=True, source=True) as (root, python, environment, children):
        profile = root / "supervisor.env"
        profile.write_text(profile.read_text()
                           + "AGENT_DISPATCH_SUPERVISE_EXTRA_ARGS=--no-declared --env primary-fixture\n")
        environment["AGENT_DISPATCH_SUPERVISE_MACHINE"] = "fixture"
        callback = installer_callback(root, profile)
        for host in (shell, shutil.which("pwsh")):
            if host is None:
                continue
            parse = (
                "$tokens=$null;$errors=$null;[void][Management.Automation.Language.Parser]::ParseFile("
                + quote(callback) + ",[ref]$tokens,[ref]$errors);if($errors.Count){throw ($errors|Out-String)}"
            )
            parsed = subprocess.run(
                [host, "-NoProfile", "-NonInteractive", "-Command", parse],
                capture_output=True, text=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW,
            )
            assert parsed.returncode == 0, parsed.stderr
        control = launch(root, "installer-callback", [shell, "-NoProfile", "-NonInteractive", "-File", str(callback)], environment, children)
        try:
            control.wait(timeout=20)
        except subprocess.TimeoutExpired:
            with capsys.disabled():
                print(diagnostics(root)[-24000:], file=sys.stderr, flush=True)
            pytest.fail("actual installer Source callback timed out:\n" + diagnostics(root))
        assert control.returncode == 0, diagnostics(root)
        from agent_dispatch.supervisor_source import read_prepared
        from agent_dispatch.supervisor_registration import supervisor_lease_scope
        from agent_dispatch.supervisor_status_io import runtime_status_path, read_status
        scope = supervisor_lease_scope("fixture", "primary-fixture")
        prepared = read_prepared(root, (root / "current-version").read_text(), scope)
        assert prepared is not None
        assert prepared["settings"] == {
            "machine": "fixture", "env": "primary-fixture", "interval": .05,
            "legacy_env": False, "no_declared": True,
        }
        status, error = read_status(runtime_status_path(scope, directory=root / "run"))
        assert error is None
        assert_completed_generation(status)
        assert status["generation"]["pid"] == prepared["generation"]["pid"]


def test_repeated_actual_installer_callbacks_reconfigure_bound_primary_without_default_drift(tmp_path, capsys):
    from agent_dispatch.supervisor_source import read_prepared
    from agent_dispatch.supervisor_registration import supervisor_lease_scope
    from agent_dispatch.supervisor_status_io import runtime_status_path, read_status

    shell = shutil.which("powershell.exe")
    with application(tmp_path, shell, master=True, source=True) as (root, python, environment, children):
        profile = root / "supervisor.env"
        profile.write_text(
            "AGENT_DISPATCH_SUPERVISE_MODE=serve\nAGENT_DISPATCH_SUPERVISE_MACHINE=fixture\n"
            "AGENT_DISPATCH_SUPERVISE_EXTRA_ARGS=--env primary-fixture\n",
        )
        environment["AGENT_DISPATCH_SUPERVISE_MACHINE"] = "fixture"
        callback = installer_callback(root, profile)
        scope = supervisor_lease_scope("fixture", "primary-fixture")
        version = (root / "current-version").read_text()

        def current():
            status, error = read_status(runtime_status_path(scope, directory=root / "run"))
            assert error is None, diagnostics(root)
            assert_completed_generation(status)
            prepared = read_prepared(root, version, scope, pid=status["generation"]["pid"])
            assert prepared is not None
            return prepared

        deadline = time.monotonic() + 20
        states = []
        for phase in ("initial", "edited"):
            if phase == "edited":
                profile.write_text(profile.read_text().replace("--env primary-fixture", "--no-declared --env primary-fixture"))
            control = launch(root, "callback-" + phase, [shell, "-NoProfile", "-NonInteractive", "-File", str(callback)], environment, children)
            try:
                control.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                with capsys.disabled():
                    print(phase + "\n" + diagnostics(root)[-24000:], file=sys.stderr, flush=True)
                pytest.fail("actual repeated callback timed out in " + phase)
            assert control.returncode == 0, diagnostics(root)
            states.append(current())
            with (root / "callback-phases.log").open("a") as log:
                log.write(json.dumps({"phase": phase, "prepared": states[-1]}) + "\n")
        old, new = states
        assert old["generation"]["pid"] != new["generation"]["pid"]
        assert old["instance_id"] != new["instance_id"]
        assert old["profile_binding"]["generation"] != new["profile_binding"]["generation"]
        for state in states:
            assert state["profile_binding"]["owner"] == "source-primary"
            assert state["settings"]["interval"] == 5
            assert state["settings"]["legacy_env"] is False
            assert state["settings"]["machine"] == "fixture"
            assert state["settings"]["env"] == "primary-fixture"
        assert old["owner"]["authority_target"] == new["owner"]["authority_target"]
        assert old["settings"]["no_declared"] is False and new["settings"]["no_declared"] is True
        assert all(json.loads(line) == [None, None, None] for line in (root / "worker-markers").read_text().splitlines())


def test_old_marker_free_source_producer_is_accepted_by_new_actual_primary_callback(tmp_path, capsys):
    from agent_dispatch.supervisor_source import read_prepared
    from agent_dispatch.supervisor_registration import supervisor_lease_scope
    from agent_dispatch.supervisor_status_io import runtime_status_path, read_status

    shell = shutil.which("powershell.exe")
    with application(tmp_path, shell, master=True, source=True) as (root, python, environment, children):
        primary = root / "supervisor.env"
        primary.write_text(
            "AGENT_DISPATCH_SUPERVISE_MODE=serve\nAGENT_DISPATCH_SUPERVISE_MACHINE=fixture\n"
            "AGENT_DISPATCH_SUPERVISE_EXTRA_ARGS=--no-declared --env primary-fixture\n",
        )
        environment["AGENT_DISPATCH_SUPERVISE_MACHINE"] = "fixture"
        program = """
import json,os,sys,time
from pathlib import Path
from agent_dispatch import __version__
from agent_dispatch.source_start import supervisor_plan
from agent_dispatch.supervisor_source import spawn_candidate
from agent_dispatch.supervisor_lane_status import PROFILE_ENV,GENERATION_ENV
from agent_dispatch.supervisor_profile import OWNER_ENV
from agent_dispatch.single_instance import is_locked,lock_path_for
r=Path(sys.argv[1]);plan=supervisor_plan(r)
assert all(key not in os.environ for key in (PROFILE_ENV,GENERATION_ENV,OWNER_ENV))
argv=['supervise','serve','--interval',str(plan.args.interval),'--env',plan.env]
if plan.machine: argv+=['--machine',plan.machine]
if plan.args.legacy_env: argv+=['--legacy-env']
if plan.args.no_declared: argv+=['--no-declared']
# Exact older producer shape: no profile_binding keyword or marker writes.
child=spawn_candidate(sys.executable,argv,root=r,version=__version__)
deadline=time.monotonic()+20
while not is_locked(lock_path_for(r/'run',plan.scope)):
    if child.poll() is not None or time.monotonic()>=deadline:
        raise RuntimeError('old producer candidate did not admit')
    time.sleep(.02)
(r/'old-producer-ready.log').write_text(json.dumps(dict(pid=child.pid,argv=argv,markers=False)))
# Retain this contained caller's Job until the fixture's explicit release.
while not (r/'release').exists(): time.sleep(.02)
"""
        deadline = time.monotonic() + 20
        producer = launch(root, "old-producer", [str(python), "-I", "-B", "-c", program, str(root)], environment, children)
        ready_file = root / "old-producer-ready.log"
        while not ready_file.exists():
            if producer.poll() is not None or time.monotonic() >= deadline:
                with capsys.disabled():
                    print(diagnostics(root)[-24000:], file=sys.stderr, flush=True)
                pytest.fail("marker-free producer did not reach actual Source admission")
            time.sleep(.02)
        produced = json.loads(ready_file.read_text())
        scope = supervisor_lease_scope("fixture", "primary-fixture")
        version = (root / "current-version").read_text()
        admitted = read_prepared(root, version, scope, pid=produced["pid"])
        assert admitted is not None, diagnostics(root)
        assert admitted.get("profile_binding", {}).get("owner") == "source-primary"
        with (root / "old-producer-admission.log").open("w") as log:
            log.write(json.dumps(admitted))
        callback = installer_callback(root, primary)
        control = launch(root, "new-reader", [shell, "-NoProfile", "-NonInteractive", "-File", str(callback)], environment, children)
        try:
            control.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            with capsys.disabled():
                details = diagnostics(root)
                print("new-reader\n" + ready_file.read_text() + "\n" + details[:1600] + details[-22000:],
                      file=sys.stderr, flush=True)
            pytest.fail("new primary reader did not accept the marker-free producer")
        assert control.returncode == 0, diagnostics(root)
        status_path = runtime_status_path(scope, directory=root / "run")
        status, error = read_status(status_path)
        from agent_dispatch.supervisor_status_io import completed_cycle_ready

        while time.monotonic() < deadline and (
            error is not None or not isinstance(status, dict)
            or not completed_cycle_ready(status)
        ):
            time.sleep(.02)
            status, error = read_status(status_path)
        assert error is None
        assert_completed_generation(status)
        assert status["generation"]["pid"] == produced["pid"]
        record = read_prepared(root, (root / "current-version").read_text(), scope, pid=produced["pid"])
        assert record is not None
        assert record["profile_binding"]["owner"] == "source-primary"
        assert Path(record["profile_binding"]["path"]) == primary
        assert record["settings"]["interval"] == 5 and record["settings"]["legacy_env"] is False
        assert all(json.loads(line) == [None, None, None] for line in (root / "worker-markers").read_text().splitlines())
