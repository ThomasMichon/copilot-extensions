"""Scheduled entry and ensure share real native custody; business work is inert."""
from __future__ import annotations

from contextlib import contextmanager
import json
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
def application(tmp_path, shell, *, master=False, source=False):
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
    (site / "fixture_business.py").write_text("""
import os,time,sys
from pathlib import Path
root=Path(os.environ['AGENT_DISPATCH_INSTALL_DIR'])
from agent_procutil import owned_process
original=owned_process.run_caller_owned
def scheduling_boundary(command, **options):
    if '-Command' in command and 'Read-DispatchInstallationPolicy' in command[-1]:
        prefix="function Get-ScheduledTask {param($TaskName,$ErrorAction)}; "
        return original([*command[:-1],prefix+command[-1]],**options)
    return original(command,**options)
owned_process.run_caller_owned=scheduling_boundary
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
                time.sleep(interval)
            options['sleep']=sleep
            original_init(self,*args,**options)
            logging.getLogger('agent-dispatch').setLevel(logging.DEBUG)
        def reconcile(self):
            poll(self)
            with (root/'worker-markers').open('a') as stream:
                stream.write(json.dumps([os.environ.get('AGENT_DISPATCH_SUPERVISOR_PROFILE'),
                                         os.environ.get('AGENT_DISPATCH_SUPERVISOR_PROFILE_GENERATION')])+'\\n')
            return supervisor_daemon.ReconcileSummary()
        supervisor_daemon.SupervisorDaemon.__init__=initialize
        supervisor_daemon.SupervisorDaemon.reconcile_once=reconcile
        supervisor_daemon.SupervisorDaemon._recheck_governance=lambda *args: None
        supervise_cli._client=lambda *args,**options: contextlib.nullcontext(SimpleNamespace(close=lambda:None))
        procutil.spawn_dispatch_daemon=spawn
        if os.environ.get('FIXTURE_SOURCE')=='1':
            from agent_dispatch import source_start,config,pool_source_snapshot,registrar_discovery
            from zdd.diagnostics import process_start_time
            class Client:
                def close(self): pass
                def health(self):
                    return dict(status='ok',runtime_config_identity=source_start.config_identity(config.load_config(),base_dir=root),
                                slot=dict(pid=os.getpid(),process_start_time=process_start_time(os.getpid())))
            supervise_cli._client=lambda *args,**options: contextlib.nullcontext(Client())
            cohort=lambda *args,**options: pool_source_snapshot.DeclarationRead((),True)
            pool_source_snapshot.read_supervisor_declarations=cohort
            registrar_discovery.read_legacy_env_profiles=cohort
            def release(self):
                original_release(self)
                state=self._source_handoff
                if state is not None and state['phase']=='withdrawing':
                    while not (root/'release').exists(): time.sleep(.02)
            supervisor_daemon.SupervisorDaemon.release_singleton=release
""")
    (site / "fixture.pth").write_text(
        "import site; site.addsitedir(" + repr(sysconfig.get_paths()["purelib"]) + "); import fixture_business\n",
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
$InstallDir={quote(root)};$LinkDir={quote(root / '.venv')};$LinkPython={quote(python)}
$utf8NoBom=[Text.UTF8Encoding]::new($false)
$tokens=$null;$errors=$null
$tree=[Management.Automation.Language.Parser]::ParseFile({quote(INSTALLER)},[ref]$tokens,[ref]$errors)
if($errors.Count){{throw 'source parse failed'}}
$definition=@($tree.FindAll({{param($node)
 $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Write-SupervisorLauncher'
}},$true))
if($definition.Count -ne 1){{throw 'writer boundary is ambiguous'}}
. ([scriptblock]::Create($definition[0].Extent.Text))
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
    with daemon_test_lifecycle(
        owner="dispatch-scheduled-admission", registry_root=root / "native-custody",
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
        if path.is_file() and (path.suffix in {".log", ".out", ".err", ".jsonl"} or path.name.endswith(".status.json"))
    )


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
        assert all(json.loads(line) == [None, None] for line in (root / "worker-markers").read_text().splitlines())


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
        assert all(json.loads(line) == [None, None] for line in (root / "worker-markers").read_text().splitlines())


def test_actual_installer_source_supervisor_callback_preserves_supported_primary_flags(tmp_path, capsys):
    shell = shutil.which("powershell.exe")
    with application(tmp_path, shell, master=True, source=True) as (root, python, environment, children):
        profile = root / "supervisor.env"
        profile.write_text(profile.read_text()
                           + "AGENT_DISPATCH_SUPERVISE_EXTRA_ARGS=--no-declared --env primary-fixture\n")
        environment["AGENT_DISPATCH_SUPERVISE_MACHINE"] = "fixture"
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
        assert error is None and status["cycle_id"] > 0
        assert status["updated_at"] >= status["cycle_started_at"]
        assert status["generation"]["pid"] == prepared["generation"]["pid"]
