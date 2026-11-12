"""Joined source consumer, not native Copilot, mux, SSH or installed acceptance."""

from __future__ import annotations

import base64
import contextlib
import copy
import io
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
from types import SimpleNamespace

from agent_procutil import no_window_kwargs, spawn_sync_in_kill_on_close_job
from fastapi.testclient import TestClient
import pytest

from agent_bridge import __main__ as core
from agent_bridge import receiving_mux, session_targeting_cli
from agent_bridge import cli_mode_provider
from agent_bridge.app_routes import configure_serving_app
from agent_bridge.client import BridgeClient, BridgeClientError
from agent_bridge.loop_governance import LoopGovernance
from agent_bridge.models import ServiceConfig
from test_loop_governance_custody import installation as installation

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")
ROOT = Path(__file__).resolve().parents[3]
CHILD = Path(__file__).parent / "fixtures" / "portable-mux-sdk.mjs"


def _cli(argv, payload=None):
    output = io.StringIO()
    errors = io.StringIO()
    stream = io.TextIOWrapper(io.BytesIO((payload or "").encode()), encoding="utf-8")
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
        saved = sys.stdin
        try:
            sys.stdin = stream
            try:
                core.main(argv)
                code = 0
            except SystemExit as exc:
                code = exc.code
        finally:
            sys.stdin = saved
    return subprocess.CompletedProcess(argv, code, output.getvalue(), errors.getvalue())


class Consumer:
    def __init__(self, client, environment, cwd, *, duplicate, core_command):
        self.client = client
        self.calls, self.effects, self.errors, self.logs = [], [], [], []
        self.ready = threading.Event()
        self.consumed = threading.Event()
        self.refused = threading.Event()
        boundary = core_command.index("--")
        # A synthetic Copilot parent preserves the extension-worker parent PID.
        supervisor = (
            "const {spawn}=require('node:child_process');"
            "const child=spawn(process.execPath,[process.argv[1],process.argv[2]],"
            "{stdio:'inherit',windowsHide:true});"
            "child.on('error',()=>process.exit(1));"
            "child.on('exit',code=>process.exit(code??1));"
        )
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(10)
        command = [
            *core_command[:boundary + 1], NODE, "-e", supervisor,
            str(CHILD), json.dumps({
                "duplicateDriver": duplicate, "carrierPort": listener.getsockname()[1],
            }),
            *core_command[boundary + 2:],
        ]
        self.state_file = Path(core_command[core_command.index("--state-file") + 1])
        self.process = self.job = self.carrier = self.messages = self.reader = self.stderr = None
        try:
            self.process, self.job = spawn_sync_in_kill_on_close_job(
                command, require_job=os.name == "nt", allow_breakaway=False,
                start_new_session=os.name != "nt",
                cwd=cwd, env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE, text=True, encoding="utf-8", **no_window_kwargs(),
            )
            self.carrier, _ = listener.accept()
            self.messages = self.carrier.makefile("r", encoding="utf-8")
            self.writer = threading.Lock()
            self.reader = threading.Thread(target=self._read, daemon=True)
            self.stderr = threading.Thread(target=self._stderr, daemon=True)
            self.reader.start()
            self.stderr.start()
        except BaseException as error:
            try:
                self._settle()
            except BaseException as cleanup:
                cleanup.consumer = self
                raise cleanup from error
            raise
        finally:
            listener.close()

    def running(self):
        if self.job is not None:
            return self.process.poll() is None
        from agent_procutil.owned_process import _observe_unreaped

        return _observe_unreaped(self.process) is None

    def _wait_for_return(self, timeout):
        deadline = time.monotonic() + timeout
        while self.running():
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(self.process.args, timeout)
            time.sleep(.02)

    def _settle(self):
        if self.process is not None:
            if self.job is not None:
                self.job.terminate_and_wait(10)
            else:
                from agent_procutil._caller_owned import _confirm_group
                from agent_procutil.owned_process import _retire_posix_group

                _retire_posix_group(self.process)
                _confirm_group(self.process)
            self.process.wait(timeout=10)
        if self.carrier is not None:
            try:
                self.carrier.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.carrier.close()
        for thread in (self.reader, self.stderr):
            if thread is not None and thread.ident is not None:
                thread.join(timeout=10)
                assert not thread.is_alive()
        for stream in (
            self.messages,
            *(getattr(self.process, name, None) for name in ("stdin", "stdout", "stderr")),
        ):
            if stream is not None:
                stream.close()

    def broker_state(self):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if self.state_file.exists():
                state = json.loads(self.state_file.read_text(encoding="utf-8"))
                if state["state"] == "running":
                    assert state["host_pid"] == self.process.pid
                    assert state["child_pid"] != state["host_pid"]
                    assert state["extension_pid"] not in (state["host_pid"], state["child_pid"])
                    return state
            assert self.running(), "".join(self.logs)
            time.sleep(.02)
        pytest.fail("The selected common broker did not reach running")

    def _stderr(self):
        for line in self.process.stderr:
            self.logs.append(line)
            if "duplicated" in line:
                self.refused.set()

    def _write(self, value):
        with self.writer:
            self.carrier.sendall((json.dumps(value) + "\n").encode())

    def _read(self):
        try:
            for line in self.messages:
                message = json.loads(line)
                if message.get("ready"):
                    self.ready.set()
                elif message.get("stopped"):
                    self.carrier.shutdown(socket.SHUT_WR)
                elif "stage" in message:
                    self.logs.append(message["stage"])
                elif "effect" in message:
                    self.effects.append(message["effect"])
                elif "method" in message:
                    self.calls.append(message)
                    response = self.client.request(
                        message["method"], message["path"], json=message.get("body"),
                        headers=message["headers"],
                    )
                    if not response.is_success:
                        self.logs.append(f"{response.status_code}: {response.text}\n")
                    self._write({
                        "id": message["id"], "status": response.status_code,
                        "body": response.json(),
                    })
                    if message["path"].endswith("/outcome") and (
                        message.get("body", {}).get("outcome") == "consumed"
                        and response.is_success
                    ):
                        self.consumed.set()
        except BaseException:
            self.errors.append(traceback.format_exc())
            self.ready.set()

    def close(self, *, timeout=10):
        from agent_remote_driver.session_host.osutil import process_start_time

        state = json.loads(self.state_file.read_text(encoding="utf-8")) if self.state_file.exists() else {}
        extension_birth = process_start_time(state["extension_pid"]) if "extension_pid" in state else None
        try:
            if self.running():
                self._write({"stop": True})
                self._wait_for_return(timeout)
        finally:
            self._settle()
        assert not self.reader.is_alive() and not self.stderr.is_alive()
        assert self.process.returncode == 0, "".join(self.logs)
        if "child_pid" in state:
            assert process_start_time(state["child_pid"]) != state["child_process_start_time"]
        if extension_birth is not None:
            assert process_start_time(state["extension_pid"]) != extension_birth
        assert not self.errors


@pytest.mark.parametrize("phase", ["construction", "stop"])
def test_carrier_failures_settle_owned_tree_and_streams(tmp_path, monkeypatch, phase):
    from agent_procutil._daemon_instance_identity import birth_identity

    marker = tmp_path / "child.json"
    program = """
const fs=require('node:fs'), net=require('node:net');
const {spawn}=require('node:child_process');
const child=spawn(process.execPath,['-e',"process.send('ready');setTimeout(()=>{},60000)"],
    {stdio:['ignore','inherit','inherit','ipc'],windowsHide:true});
child.on('error',()=>process.exit(2));
child.once('message',message=>{
    if(message!=='ready')process.exit(2);
    const marker=process.argv[1], pending=marker+'.pending';
    fs.writeFileSync(pending,JSON.stringify({child:child.pid}));
    fs.renameSync(pending,marker);
    if(process.argv[2]==='stop'){
        const config=JSON.parse(process.argv.at(-1));
        const carrier=net.createConnection({host:'127.0.0.1',port:config.carrierPort});
        carrier.on('error',()=>process.exit(2));
    }
});
setTimeout(()=>{},60000);
"""
    spawned, births, listeners, pinned = [], [], [], []
    spawn = spawn_sync_in_kill_on_close_job
    accept = socket.socket.accept

    def capture(*args, **kwargs):
        process, job = spawn(*args, **kwargs)
        spawned.append(process)
        return process, job

    def timeout(listener):
        listeners.append(listener)
        if phase == "stop":
            connected = accept(listener)
        deadline = time.monotonic() + 10
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        child = json.loads(marker.read_text(encoding="utf-8"))["child"]
        births.append((child, birth_identity(child)))
        if os.name == "nt":
            from agent_procutil.process_retirement import PinnedProcess

            pinned.append(pins.enter_context(PinnedProcess(child, births[-1][1])))
        if phase == "construction":
            raise TimeoutError("synthetic carrier timeout")
        return connected

    monkeypatch.setattr(sys.modules[__name__], "spawn_sync_in_kill_on_close_job", capture)
    monkeypatch.setattr(socket.socket, "accept", timeout)
    def construct():
        return Consumer(
            None, dict(os.environ), str(tmp_path), duplicate=False,
            core_command=[
                NODE, "-e", program, str(marker), phase,
                "--state-file", str(tmp_path / "host.json"), "--",
            ],
        )
    with contextlib.ExitStack() as pins:
        if phase == "construction":
            with pytest.raises(TimeoutError, match="synthetic carrier timeout"):
                construct()
        else:
            consumer = construct()
            with pytest.raises(subprocess.TimeoutExpired):
                consumer.close(timeout=.1)
            assert not consumer.reader.is_alive() and not consumer.stderr.is_alive()
            assert consumer.messages.closed and consumer.carrier.fileno() == -1
        assert len(spawned) == len(births) == len(listeners) == 1
        assert spawned[0].returncode is not None and spawned[0].stderr.closed
        assert listeners[0].fileno() == -1
        if pinned:
            assert pinned[0].exited(timeout=10)
        else:
            child, original = births[0]
            try:
                current = birth_identity(child)
            except (OSError, ValueError, RuntimeError):
                current = None
            assert current != original


@pytest.mark.parametrize("duplicate", [False, True], ids=["one-admitted-send", "duplicate-loadout-holds"])
@pytest.mark.parametrize("installation", ["namespaced"], indirect=True)
def test_bridge_provider_receiver_and_extensions_share_one_conversation(
    installation, tmp_db, tmp_path, tmp_path_factory, monkeypatch, duplicate,
):
    # Cross-plugin composition lives in this opt-in packet, not either runtime's imports.
    for source in (
        ROOT / "plugins/agent-codespaces/src",
        ROOT / "libs/venue-copilot/src",
        ROOT / "libs/session-liveness-probe/src",
    ):
        monkeypatch.syspath_prepend(str(source))
    from agent_codespaces import bridge_receiving, config, copilot_detach as detach
    from agent_codespaces import portable_mux_launch as launch, session_forwards, worktrees
    from agent_codespaces import copilot_venue
    from repository_policy.receiving import build_receiving_policy_check
    from repository_policy import validate_hosting_result

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    root = tmp_path_factory.mktemp("mux")
    state = SimpleNamespace(pending=None, meta=None, consumer=None, native=[], observations=[], sends=[])
    app = configure_serving_app(
        ServiceConfig(db_path=str(tmp_db.db_path), bind="127.0.0.1"),
        "synthetic-secret", lifespan=None,
    )
    app.state.db = tmp_db
    app.state.ready = True
    app.state.loop_governance = LoopGovernance()
    app.state.session_manager = SimpleNamespace(is_draining=False)
    scheme = "Bear" + "er "
    headers = {"Authorization": scheme + "synthetic-secret"}
    assert headers["Authorization"].endswith("synthetic-secret")

    with TestClient(app, headers=headers, client=("127.0.0.1", 12345)) as client:
        def selected(*, ensure=True):
            bridge = BridgeClient("http://127.0.0.1:12001", "synthetic-secret")

            def request(method, path, body=None, **kwargs):
                response = client.request(method, path, json=body, params=kwargs.get("params"))
                if not response.is_success:
                    raise BridgeClientError(response.status_code, response.json().get("detail"))
                return response.json()

            bridge._request = request
            return bridge

        monkeypatch.setattr(core, "_get_client", selected)
        monkeypatch.setattr(worktrees, "validate_context", lambda: None)
        def bridge_run(self, argv, **kwargs):
            result = _cli(argv[1:], kwargs.get("input"))
            if "send" in argv:
                state.sends.append(result.stdout + result.stderr)
            return result

        monkeypatch.setattr(bridge_receiving.BridgeReceiving, "run", bridge_run)
        monkeypatch.setattr(session_targeting_cli.shutil, "which", lambda name: "fixture-provider")
        monkeypatch.setattr("agent_bridge._peer_launch.launch_prefix", lambda *a: ["fixture-provider"])
        monkeypatch.setattr("agent_bridge.related_plugins.control_plane_anchors", lambda: [])
        monkeypatch.setattr("agent_bridge.local_cache_refresh._checkout_root", lambda path: workspace)
        monkeypatch.setattr(config, "load_merged_config", lambda: SimpleNamespace(
            resolved_workspace_folder_for=lambda repo: str(workspace),
        ))
        monkeypatch.setattr(detach, "_codespace", lambda name: SimpleNamespace(
            name=name, state="Available", repository="example/project",
        ))
        monkeypatch.setattr(launch, "model_copilot_args", lambda args: [])
        monkeypatch.setattr(launch, "require_owner", lambda: None)
        monkeypatch.setattr(copilot_venue, "claim_or_exit_code", lambda *a, **k: None)
        monkeypatch.setattr(launch.owner, "ensure_owner_running", lambda config: True)
        monkeypatch.setattr(launch.owner, "get_pending_delivery", lambda *a: copy.deepcopy(state.pending))
        monkeypatch.setattr(launch.owner, "record_pending_delivery", lambda name, tenant, value: setattr(
            state, "pending", copy.deepcopy(value),
        ))
        monkeypatch.setattr(launch.owner, "get_hold", lambda *a: SimpleNamespace(
            sessions={state.meta["tenant"]: state.meta},
        ) if state.meta else None)

        def hold(name, tenant, **kwargs):
            state.meta = {"tenant": tenant, **kwargs}

        monkeypatch.setattr(launch.owner, "hold", hold)
        monkeypatch.setattr(launch.owner, "release", lambda *a, **k: pytest.fail("native custody released"))

        async def forward(*args, **kwargs):
            return True

        monkeypatch.setattr(session_forwards, "await_owner_bridge_forward", forward)

        def policy(request, support):
            checked = subprocess.run(
                [
                    sys.executable, "-I", "-S", "-c",
                    "import json,runpy,sys; from pathlib import Path; "
                    "runpy.run_path(sys.argv[1])['_policy'](json.load(sys.stdin), Path(sys.argv[2]))",
                    str(support / "receiver.py"), str(support),
                ],
                input=json.dumps(request).encode(), capture_output=True, timeout=15,
                **no_window_kwargs(),
            )
            assert checked.returncode == 0, checked.stderr.decode()

        monkeypatch.setattr(receiving_mux, "_policy", policy)
        monkeypatch.setattr(receiving_mux, "_mux_binary", lambda: "fixture-tmux")
        monkeypatch.setattr(receiving_mux, "_socket_identity", lambda path: {"device": 1, "inode": 2})

        def native(argv, *, cwd):
            state.native.append(argv)
            assert argv[3] == "new-session"
            assert state.consumer is None
            attempt = Path(argv[-1])
            captured = []
            with monkeypatch.context() as child_patch:
                child_patch.chdir(cwd)
                child_patch.setattr(receiving_mux.os, "execvpe", lambda *args: captured.append(args))
                receiving_mux.child(attempt)
            executable, arguments, environment = captured[0]
            assert executable == "bash" and arguments[2:4] == [
                "--aw-manager-root", str(attempt / "support/mux"),
            ]
            assert arguments[4:8] == receiving_mux._core_executable(attempt / "support")
            assert arguments[arguments.index("--mode") + 1] == "headed"
            child = arguments[arguments.index("--") + 1:]
            assert child[:3] == ["copilot", "--session-id", json.loads(
                (attempt / "launch.json").read_text(),
            )["session_id"]]
            state.consumer = Consumer(
                client, environment, cwd, duplicate=duplicate, core_command=arguments[4:],
            )
            assert state.consumer.ready.wait(10), (
                state.consumer.logs,
                state.consumer.state_file.read_text() if state.consumer.state_file.exists() else "no core state",
            )
            assert not state.consumer.errors
            return subprocess.CompletedProcess(argv, 0, "$1:1234|%2\n", "")

        monkeypatch.setattr(receiving_mux, "_run_tmux", native)

        def probe(name, directory, context, remote):
            command, payload = build_receiving_policy_check("/portable-fixture/workspace", context)
            words = shlex.split(command)
            argv = words[words.index("python3"):]
            argv[0] = sys.executable
            argv[5] = directory
            checked = subprocess.run(
                argv, input=payload, capture_output=True, timeout=15, **no_window_kwargs(),
            )
            assert checked.returncode == 0, checked.stderr.decode()
            validate_hosting_result(json.loads(checked.stdout))
            # The synthetic carrier maps its POSIX state namespace into private scratch.
            return "/portable-fixture"

        monkeypatch.setattr(launch, "_probe", probe)

        def ssh(args, *, remote_cmd_builder, remote_input_builder, receiving_context, result_sink, **kwargs):
            args.plugin_prepared_context["plugin_context"] = {
                "version": 1, "cwd": str(workspace), "plugins": [],
            }
            command = remote_cmd_builder([])
            wire = remote_input_builder()
            if wire:
                envelope = json.loads(wire)
                assert envelope["request"]["policy_context"] == receiving_context
                assert state.pending["phase"] == "submitted"
                request = {**envelope["request"], "state_root": str(root)}
                receipt = receiving_mux.receive(
                    request, base64.b64decode(envelope["support"], validate=True),
                )
                receipt["mux_socket"] = str(
                    PurePosixPath("/portable-fixture") / request["attempt"] / "tmux.sock",
                )
                output = json.dumps(receipt)
            else:
                state.observations.append(command)
                assert state.meta["mux_endpoint"]["native"] == {"incarnation": "$1:1234", "pane": "%2"}
                output = "present\n"
            return result_sink(SimpleNamespace(exit_code=0, stdout=output))

        provider_calls = []

        def provider(argv, **kwargs):
            provider_calls.append(argv)
            assert argv[1:3] == ["copilot", "fixture-venue"]
            import argparse

            parser = argparse.ArgumentParser()
            parser.add_argument("--receiving-support")
            parser.add_argument("--detach", action="store_true")
            parser.add_argument("--seed-file")
            parser.add_argument("--driver")
            args = parser.parse_args(argv[3:])
            args.name = argv[2]
            args.worktree_id = args.seed = None
            args.ensure_mux = args.no_relay = False
            args.copilot_args = []
            args.register_timeout = 10
            with contextlib.redirect_stdout(io.StringIO()) as output:
                saved = sys.stdin
                try:
                    sys.stdin = io.StringIO(kwargs.get("input") or "")
                    code = detach.cmd_detach(args, ssh_session=ssh)
                finally:
                    sys.stdin = saved
            print(output.getvalue(), end="")
            return subprocess.CompletedProcess(argv, code)

        monkeypatch.setattr(cli_mode_provider, "_portable_provider_run", provider)
        try:
            initial = _cli([
                "--json", "create", "codespace:fixture-venue", "one task\nsecond line",
                "--cli", "--detach", "--receiving-support", "portable",
            ])
            assert initial.returncode == 0, (
                initial.stdout + initial.stderr
                + ("\n" + "\n".join(state.consumer.logs + state.consumer.errors) if state.consumer else "")
                + "\n" + "\n".join(state.sends)
            )
            first = json.loads(initial.stdout)
            assert first["seeded"] is True
            assert state.pending is None
            consumer = state.consumer
            broker = consumer.broker_state()
            assert broker["session_id"] == first["session_id"]
            assert broker["lifetime_policy"] == "cli"
            if duplicate:
                assert consumer.refused.wait(12), "\n".join(consumer.logs)
                assert not consumer.effects
            else:
                assert consumer.consumed.wait(12), "\n".join(consumer.logs)
                assert len(consumer.effects) == 1
                effect = consumer.effects[0]
                assert effect["sessionId"] == first["session_id"]
                assert "one task\nsecond line" in effect["options"]["prompt"]
            second = _cli([
                "--json", "create", "codespace:fixture-venue", "--cli", "--detach",
                "--receiving-support", "portable",
            ])
            assert second.returncode == 0, second.stdout
            assert json.loads(second.stdout)["session_id"] == first["session_id"]
            assert consumer.broker_state()["child_pid"] == broker["child_pid"]
            assert len(state.native) == len(state.observations) == 1
            assert len(provider_calls) == 2
            messages = tmp_db.execute_read("SELECT * FROM live_messages WHERE kind NOT LIKE 'control:%'")
            assert len(messages) == 1
            assert messages[0]["body"].endswith("one task\nsecond line")
            assert not any(
                "/messages/ack" in call["path"] or call["path"].endswith("/controls")
                for call in consumer.calls
            )
            assert consumer.running()
        finally:
            if state.consumer:
                state.consumer.close()
