"""One actual resident admission spans setup, listeners and shutdown."""

import asyncio
import json
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

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
    def _check_password_ttl(self):
        pass

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
service.resolve_context = lambda: types.SimpleNamespace(kpdb=None, group=None, vault_name="", port=0, sources={"port": "default"})
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
            bootstrap = argv.index("-c")
            assert argv[:bootstrap] == [
                launch.windowless_python(sys.executable), "-X", argv[2],
            ]
            assert argv[2].startswith("pycache_prefix=")
            cache = Path(argv[2].split("=", 1)[1])
            assert cache.parent.name == "python"
            assert cache.parent.parent.parent.samefile(state)
            assert argv[bootstrap + 1] == launch._SERVICE_BOOTSTRAP
            assert argv[bootstrap + 2] == str(Path(launch.__file__).resolve().parent.parent)
            service_args = argv[bootstrap + 3:]
            assert service_args == [
                "--handoff-stdin", "--foreground", "--configuration-root", os.getcwd(),
            ]
            command = [
                sys.executable, "-c", os.environ["VAULT_STARTUP_HARNESS"],
                "resident", str(registry), str(state), *service_args,
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
        from agent_vault.service_generation import generation_lease
        lease = generation_lease(config.home_dir())
        lease.acquire()
        def unexpected(*args):
            raise AssertionError("Startup must not depend on predecessor IPC")
        service.send_command = unexpected
        sys.argv = ["agent_vault.service"]
        try:
            service.main()
        finally:
            lease.release()
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
    activation = tmp_path / "activation.json"
    activation.write_text('{"enabled":true}', encoding="utf-8")
    environment["AGENT_VAULT_CONFIG"] = str(activation)
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


@pytest.mark.parametrize("phase", ["tick", "cancel"])
def test_expiry_progress_and_cancellation_ignore_saturated_request_executor(
    monkeypatch: pytest.MonkeyPatch, phase: str,
) -> None:
    from agent_vault import service

    checked = threading.Event()

    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        started, progressed = asyncio.Event(), asyncio.Event()
        release = threading.Event()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
        vault = SimpleNamespace(
            _shutdown=False, is_expired=False, _check_password_ttl=checked.set,
        )

        class Generation:
            def __init__(self, *_args: object) -> None:
                pass

            async def start(self, _port: int) -> None:
                pass

            async def tick(self) -> None:
                assert checked.is_set()
                progressed.set()

        def block_request_worker() -> None:
            loop.call_soon_threadsafe(started.set)
            assert release.wait(5)

        monkeypatch.setattr(service, "ServiceGeneration", Generation)
        blocker = loop.run_in_executor(None, block_request_worker)
        await asyncio.wait_for(started.wait(), 2)
        pending = asyncio.create_task(service._serve(vault, 0, [], [], None))
        try:
            if phase == "tick":
                await asyncio.wait_for(progressed.wait(), 1)
            else:
                await asyncio.sleep(0)
            pending.cancel()
            done, _ = await asyncio.wait({pending}, timeout=1)
            assert pending in done, "expiry cancellation waited for a request worker"
            with pytest.raises(asyncio.CancelledError):
                pending.result()
            assert not release.is_set()
        finally:
            release.set()
            if not pending.done():
                pending.cancel()
            await asyncio.gather(pending, blocker, return_exceptions=True)

    asyncio.run(scenario())
