"""One actual resident admission spans setup, listeners and shutdown."""

import json
import os
import subprocess
import sys

import pytest


HARNESS = r"""
import asyncio, io, json, os, sys, types
from pathlib import Path
from agent_procutil import daemon_instances as api
from agent_procutil import spawn_daemon as real_spawn
from agent_vault import config, cutover, launch, service

mode, registry, state = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
state.mkdir(parents=True, exist_ok=True)
events = []
children = []

def rows():
    return api.inspect_instances(registry)

def admitted(stage):
    current = [r for r in rows() if r.get("pid") == str(os.getpid())]
    assert len(current) == 1, (stage, current)
    assert current[0]["state"] == "running", (stage, current)
    assert current[0]["lifecycle"] == "resident", current
    events.append(stage)

class Vault:
    def __init__(self):
        assert mode != "windows", "launcher initialized an application"
        admitted("construct")
        self._shutdown = False
        self.is_expired = False
        self.cli = types.SimpleNamespace(status=lambda _: "fixture")
    def initialize(self):
        admitted("initialize")

def startup(vault, context):
    admitted("ready")
    vault._shutdown = True

def handoff(vault, payload):
    admitted("handoff")
    assert payload == {"fixture": "handoff"}
    return 0

async def no_pipe(*args):
    return []

service.VaultService = Vault
service.send_command = lambda *_args: None
service.resolve_kpdb = lambda **kwargs: None
service.resolve_context = lambda: types.SimpleNamespace(kpdb=None, group=None, vault_name="")
service.get_registry = lambda: types.SimpleNamespace(run_startup=startup)
service.configured_tcp_port = lambda: 0
service.start_pipe_server = no_pipe
service.run_dir = lambda: state / "run"
service.LOG_FILE = str(state / "vault.log")
service.PID_FILE = str(state / "vault.pid")
service.SOCKET_PATH = str(state / "vault.sock")
cutover.apply_handoff_payload = handoff

def invoke():
    if mode == "resident":
        sys.argv = ["agent_vault.service", *sys.argv[4:]]
        service.main()
        assert len(rows()) == 1, rows()
        assert rows()[0]["state"] in {"running", "finished"}, rows()
        assert events == ["construct", "initialize", "handoff", "ready"], events
        return
    if mode == "foreground":
        sys.argv = ["agent_vault.service", "--foreground", "--handoff-stdin"]
        service.main()
        assert len(rows()) == 1, rows()
        assert rows()[0]["state"] == "finished", rows()
        assert events == ["construct", "initialize", "handoff", "ready"], events
    elif mode == "windows":
        service.IS_WINDOWS = True
        def spawn(argv, **options):
            assert not rows(), rows()
            assert not events, events
            assert argv[4:] == ["--handoff-stdin", "--foreground"]
            command = [
                sys.executable, "-c", os.environ["VAULT_STARTUP_HARNESS"],
                "resident", str(registry), str(state), *argv[4:],
            ]
            process = real_spawn(command, **options)
            children.append(process)
            return process
        launch.spawn_daemon = spawn
        sys.argv = ["agent_vault.service", "--handoff-stdin"]
        service.main()
        assert "AGENT_VAULT_HANDOFF_JSON" not in os.environ
        assert len(children) == 1
        assert children[0].wait(timeout=30) == 0, (state / "service-stdio.log").read_text()
        assert len(rows()) == 1, rows()
        assert rows()[0]["state"] == "finished", rows()
        assert rows()[0]["pid"] == str(children[0].pid), rows()
        assert not events
    elif mode == "posix-launcher":
        service.IS_WINDOWS = False
        def detach():
            assert rows() == [], rows()
            assert not events
            return False
        service.daemonize_unix = detach
        sys.argv = ["agent_vault.service", "--handoff-stdin"]
        service.main()
        assert rows() == [], rows()
        assert not events
    elif mode == "already-running":
        service.send_command = lambda *_args: {
            "ok": True, "pid": os.getpid(), "cli": "fixture",
        }
        sys.argv = ["agent_vault.service"]
        service.main()
        assert rows() == [], rows()
        assert not events
    else:
        sys.argv = ["agent_vault.service", "--ping"]
        try:
            service.main()
        except SystemExit as error:
            assert error.code == 1
        assert rows() == [], rows()
        assert not events

if mode == "resident":
    invoke()
else:
    os.environ[config.HOME_ENV] = str(state)
    sys.stdin = io.StringIO(json.dumps({"fixture": "handoff"}))
    with api.daemon_test_lifecycle(owner="vault-startup-fixture", registry_root=registry) as overlay:
        os.environ.update(overlay)
        invoke()
        print(json.dumps({"mode": mode, "records": len(rows()), "events": events}))
"""


@pytest.mark.parametrize(
    "mode", ["foreground", "windows", "setup-only", "posix-launcher", "already-running"],
)
def test_actual_main_has_one_resident_and_no_finished_setup_record(tmp_path, mode):
    environment = dict(os.environ)
    for name in (
        "COPILOT_EXTENSIONS_DAEMON_TEST_CONTEXT",
        "COPILOT_EXTENSIONS_DAEMON_INSTANCE",
        "COPILOT_EXTENSIONS_DAEMON_REGISTRY",
    ):
        environment.pop(name, None)
    environment["COPILOT_EXTENSIONS_TEST_CONTAINED"] = "1"
    environment["VAULT_STARTUP_HARNESS"] = HARNESS
    environment["KPDB"] = str(tmp_path / "unconfigured.kdbx")
    result = subprocess.run(
        [
            sys.executable, "-B", "-c", HARNESS, mode,
            str(tmp_path / "registry"), str(tmp_path / "state"),
        ],
        env=environment, capture_output=True, text=True, timeout=45,
        **(
            {"creationflags": subprocess.CREATE_NO_WINDOW}
            if os.name == "nt" else {}
        ),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    summary = json.loads(result.stdout.splitlines()[-1])
    assert summary["records"] == (1 if mode in {"foreground", "windows"} else 0)
