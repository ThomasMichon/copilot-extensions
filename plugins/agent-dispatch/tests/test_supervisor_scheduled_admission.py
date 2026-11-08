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
def application(tmp_path, shell):
    root = tmp_path / "application"
    slot = root / "versions" / agent_dispatch.__version__
    scripts = slot / "Scripts"
    site = slot / "Lib" / "site-packages"
    scripts.mkdir(parents=True)
    site.mkdir(parents=True)
    python = scripts / "python.exe"
    shutil.copyfile(sys.executable, python)
    (slot / "pyvenv.cfg").write_text(
        f"home = {sys.base_prefix}\ninclude-system-site-packages = false\nversion = {sys.version.split()[0]}\n",
    )
    shutil.copytree(Path(agent_dispatch.__file__).parent, site / "agent_dispatch",
                    ignore=shutil.ignore_patterns("__pycache__"))
    (site / "fixture_business.py").write_text("""
import os,time
from pathlib import Path
from agent_dispatch import supervisor, diagnostic_logs
from agent_procutil import owned_process
root=Path(os.environ['AGENT_DISPATCH_INSTALL_DIR'])
original=owned_process.run_caller_owned
def scheduling_boundary(command, **options):
    if '-Command' in command and 'Read-DispatchInstallationPolicy' in command[-1]:
        prefix="function Get-ScheduledTask {param($TaskName,$ErrorAction)}; "
        return original([*command[:-1],prefix+command[-1]],**options)
    return original(command,**options)
owned_process.run_caller_owned=scheduling_boundary
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
""")
    (site / "fixture.pth").write_text(
        "import site; site.addsitedir(" + repr(sysconfig.get_paths()["purelib"]) + "); import fixture_business\n",
    )
    (root / "current-version").write_text(agent_dispatch.__version__)
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
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-File", str(publisher)],
        env=environment, capture_output=True, text=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stderr
    children = []
    try:
        yield root, python, environment, children
    finally:
        (root / "release").touch()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            from agent_dispatch.single_instance import is_locked, lock_path_for
            from agent_dispatch.supervisor_lane_status import profile_scope
            if not is_locked(lock_path_for(root / "run", profile_scope(root / "supervisor.env"))):
                break
            time.sleep(.02)
        else:
            raise AssertionError("contained fixture lane did not finish cooperative release")
        for child in children:
            child.wait(timeout=30)


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
    return "\n".join(
        str(path.relative_to(root)) + ":\n" + path.read_text(errors="replace")
        for path in root.rglob("*")
        if path.is_file() and (path.suffix in {".log", ".out", ".err"} or path.name.endswith(".status.json"))
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
