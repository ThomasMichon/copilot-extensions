"""Opt-in real Copilot receiver, history, source expiry and native-control proof."""

import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import inspect
import json
import os
from pathlib import Path
import re
import threading
import time
from uuid import uuid4

import pytest

from agent_procutil.daemon_instances import daemon_test_lifecycle
from agent_remote_driver.session_host.client import SessionHostClient
from agent_remote_driver.session_host.osutil import pid_alive, process_start_time


pytestmark = pytest.mark.portfolio_tier("T3")


async def _auth(binding, operation, **changes):
    reader, writer = await asyncio.open_connection("127.0.0.1", binding["port"])
    fields = {key: binding[key] for key in (
        "contract", "version", "nonce", "session_id", "incarnation",
        "source_owner", "source_revision",
    )}
    try:
        writer.write(json.dumps({**fields, "operation": operation, **changes}).encode() + b"\n")
        await writer.drain()
        return json.loads(await asyncio.wait_for(reader.readline(), 2))
    finally:
        writer.close()
        await writer.wait_closed()


async def _prompt(state, text, *, refused=False):
    client = await SessionHostClient.connect(port=state["port"])
    try:
        hello = await client.attach(nonce=state["nonce"].encode())
        assert hello.child_pid == state["child_pid"]
        for request_id, method, params in (
            (1, "initialize", {"protocolVersion": 1}),
            (2, "session/prompt", {
                "sessionId": state["session_id"], "prompt": [{"type": "text", "text": text}],
            }),
        ):
            await client.write((json.dumps({
                "jsonrpc": "2.0", "id": request_id, "method": method, "params": params,
            }) + "\n").encode())
            while True:
                cursor, frame = await asyncio.wait_for(anext(client.frames()), 30)
                reply = json.loads(frame)
                await client.ack(cursor)
                if reply.get("id") == request_id:
                    if request_id == 2 and refused:
                        assert reply.get("error", {}).get("code") == -32001, reply
                    else:
                        assert "result" in reply, reply
                    break
        await client.detach(reapable=True)
    finally:
        await client.close()


def _native_identities(state):
    identities = []
    for key in ("child_pid", "extension_pid"):
        pid = state[key]
        birth = process_start_time(pid)
        assert pid_alive(pid) and birth
        identities.append((pid, birth))
    assert identities[0][0] != identities[1][0]
    assert identities[0][1] == state["child_process_start_time"]
    return identities


def _retired(identities):
    return all(
        not pid_alive(pid) or (
            (current := process_start_time(pid)) is not None and current != birth
        )
        for pid, birth in identities
    )


@pytest.fixture
def native_provider():
    requests = []

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            body = {
                "id": "fixture", "object": "chat.completion.chunk", "model": "gpt-4.1",
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": "FIXTURE"},
                             "finish_reason": "stop"}],
            }
            if provider.pause_response.is_set():
                provider.response_started.set()
                if not provider.release_response.wait(30):
                    return
            try:
                self.wfile.write(("data: " + json.dumps(body) + "\n\ndata: [DONE]\n\n").encode())
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

    provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    provider.daemon_threads = True
    provider.pause_response = threading.Event()
    provider.response_started = threading.Event()
    provider.release_response = threading.Event()
    thread = threading.Thread(target=provider.serve_forever)
    thread.start()
    try:
        yield provider, requests
    finally:
        provider.release_response.set()
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def _diagnostic_publisher(error_file):
    import sys
    from agent_remote_driver.headed import launcher

    publish = launcher._write_host_state

    def write(path, state):
        kind, error, frame = sys.exc_info()
        if state.get("state") == "auth_held" and kind is not None:
            frames = []
            while frame is not None and len(frames) < 20:
                frames.append({
                    "file": Path(frame.tb_frame.f_code.co_filename).name[:80],
                    "function": frame.tb_frame.f_code.co_name[:80], "line": frame.tb_lineno,
                })
                frame = frame.tb_next
            diagnostic = {"exception": kind.__name__[:80], "frames": frames}
            for name in ("errno", "winerror"):
                value = getattr(error, name, None)
                diagnostic[name] = value if type(value) is int else None
            try:
                error_file.write_text(json.dumps(diagnostic), encoding="utf-8")
            except OSError:
                pass
        publish(path, state)

    return write


def _observation_loss_backend(fault_file, receipt_file):
    from agent_remote_driver.headed.backend import HeadedBackend

    class ObservationLossBackend(HeadedBackend):
        async def serve(self):
            self._fixture_fault = None
            await super().serve()
            self._fixture_fault = asyncio.create_task(self._lose_observation())

        async def _lose_observation(self):
            await self.bound.wait()
            while True:
                try:
                    request = json.loads(fault_file.read_text(encoding="utf-8"))
                    break
                except (FileNotFoundError, json.JSONDecodeError):
                    await asyncio.sleep(.01)
            try:
                assert request == {
                    "session_id": self._session_id,
                    "incarnation": self._auth_wait.incarnation,
                    "native_pid": self.native_pid,
                    "extension_pid": self.extension_pid,
                }
                assert self.bound.is_set() and not self.auth_stopping
                assert self._auth_wait.authorized() and self._auth_wait.hold is None
                snapshot = dict(self.auth_snapshot)
                assert snapshot["controlled"] and not snapshot["closed"]
                assert any(snapshot[key] for key in ("pending", "sending", "outstanding"))
                writer = self._writer
                assert writer is not None and not writer.is_closing()
                # Drop the authenticated SDK/control stream, not the public broker client.
                writer.close()
                await asyncio.wait_for(writer.wait_closed(), 1)
                await asyncio.wait_for(self.lost.wait(), 1)
                receipt = {
                    **request, "snapshot": snapshot, "status": self._auth_wait.status(),
                    "writer_closed": writer.is_closing(), "bound": self.bound.is_set(),
                }
            except Exception as error:
                receipt = {"error": type(error).__name__}
            receipt_file.write_text(json.dumps(receipt), encoding="utf-8")

        async def close(self):
            if self._fixture_fault is not None:
                self._fixture_fault.cancel()
                await asyncio.gather(self._fixture_fault, return_exceptions=True)
            await super().close()

    return ObservationLossBackend


def _qualify_native_receiver(
    tmp_path, monkeypatch, native_provider, *, inflight=False, observation_loss=False,
):
    import sys

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    from headed_mux_fixture import FixtureMux

    binary = Path(os.environ["COPILOT_NATIVE_TEST_EXECUTABLE"])
    mux_binary = Path(os.environ["PSMUX_TEST_BIN"])
    assert binary.is_file() and mux_binary.is_file()
    home, checkout = tmp_path / "home", tmp_path / "checkout"
    home.mkdir()
    checkout.mkdir()
    (home / "config.json").write_text(
        json.dumps({"trustedFolders": [str(checkout)]}), encoding="utf-8",
    )
    (home / ".psmux.conf").write_text("set -g warm off\n", encoding="utf-8")
    session_id = str(uuid4())
    prompts = [
        "Receiver original\nsingle ' double \" & $literal",
        "controlled before source expiry", "controlled after same-session resume",
        "ISOLATED native keyboard input",
    ]
    provider, requests = native_provider
    environment = {
        key: value for key, value in os.environ.items()
        if (key.startswith("COPILOT_EXTENSIONS_") or not key.startswith(("COPILOT_", "OTEL_")))
        and key not in {"GH_TOKEN", "GITHUB_TOKEN", "GITHUB_API_TOKEN"}
    }
    environment.update(
        COPILOT_EXTENSIONS_TEST_CONTAINED="1", COPILOT_HOME=str(home),
        COPILOT_OFFLINE="true", COPILOT_PROVIDER_TYPE="openai",
        COPILOT_PROVIDER_BASE_URL=f"http://127.0.0.1:{provider.server_port}",
        COPILOT_PROVIDER_MODEL_ID="gpt-4.1", COPILOT_MODEL="gpt-4.1", USE_TGREP="false",
        HOME=str(home), USERPROFILE=str(home), AGENT_HOME=str(home),
        PSMUX_DATA_DIR=str(tmp_path / "mux-data"), PSMUX_CONFIG_FILE=os.devnull,
        PSMUX_NO_WARM="1", LOCALAPPDATA=str(tmp_path / "local-app-data"),
    )
    Path(environment["PSMUX_DATA_DIR"]).mkdir()
    Path(environment["LOCALAPPDATA"]).mkdir()
    state_file = tmp_path / "headed.json"
    history_file = home / "session-state" / session_id / "events.jsonl"
    configuration = tmp_path / "launch.json"
    configuration.write_text(json.dumps({
        "argv": [
            str(binary), "--session-id", session_id, "-i", prompts[0], "--screen-reader",
            "--no-auto-update", "--disable-builtin-mcps", "--no-custom-instructions",
            "--no-color", "--no-ask-user", "--log-dir", str(tmp_path / "native-logs"),
        ],
        "session_id": session_id, "attempt_directory": str(tmp_path / "attempt"),
        "state_file": str(state_file), "cwd": str(checkout),
    }), encoding="utf-8")
    driver = tmp_path / "receiver.py"
    fault_file, receipt_file = tmp_path / "lose-sdk.json", tmp_path / "lost-sdk.json"
    error_file = tmp_path / "coordinator-error.json"
    diagnostic_hook = (
        inspect.getsource(_diagnostic_publisher)
        + "\nimport agent_remote_driver.headed.launcher as launcher\n"
        + "launcher._write_host_state=_diagnostic_publisher("
        + f"Path({str(error_file)!r}))\n"
    )
    fault_hook = ""
    if observation_loss:
        fault_hook = (
            inspect.getsource(_observation_loss_backend)
            + "\nimport agent_remote_driver.headed.launcher as launcher\n"
            + "launcher.HeadedBackend=_observation_loss_backend("
            + f"Path({str(fault_file)!r}),Path({str(receipt_file)!r}))\n"
        )
    driver.write_text(
        "import asyncio,json,sys,time\n"
        "from pathlib import Path\n"
        "from agent_remote_driver.headed.launcher import run_headed\n"
        + diagnostic_hook
        + fault_hook
        + "value=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))\n"
        "value['auth_wait']={'source_owner':'native-fixture',"
        "'source_revision':'native-fixture-revision','lease_revision':1,"
        "'budget_seconds':90,'expires_at':time.time()+90}\n"
        "raise SystemExit(asyncio.run(run_headed(value.pop('argv'),**value)))\n",
        encoding="utf-8",
    )
    compile(driver.read_text(encoding="utf-8"), str(driver), "exec")
    deadline = time.monotonic() + 120
    cleanup, identities = [], []
    mux, incarnation = None, None

    def state():
        return json.loads(state_file.read_text(encoding="utf-8"))

    async def until(read, label, timeout=30):
        end = min(deadline, time.monotonic() + timeout)
        while time.monotonic() < end:
            try:
                value = read()
                if inspect.isawaitable(value):
                    value = await value
                if value:
                    return value
            except (FileNotFoundError, json.JSONDecodeError):
                pass
            await asyncio.sleep(.05)
        observed = state() if state_file.exists() else {}
        diagnostic = json.dumps({
            key: observed.get(key) for key in
            ("state", "session_id", "pid", "child_pid", "extension_pid", "child_exit_code")
        })
        diagnostic += "\n" + json.dumps({
            key: observed.get("auth_wait", {}).get(key)
            for key in ("phase", "hold", "lease_revision", "native_qualification")
        })
        if error_file.exists():
            diagnostic += "\n" + error_file.read_text(encoding="utf-8")[:4096]
        if mux is not None:
            diagnostic += "\n" + mux.query("capture-pane", "-p", "-t", mux.target).stdout[-8192:]
        raise AssertionError(label + "\n" + diagnostic)

    def events():
        return [json.loads(line) for line in history_file.read_text(encoding="utf-8").splitlines()]

    def history():
        return [value["data"]["content"] for value in events() if value["type"] == "user.message"]

    async def flow():
        first = await until(
            lambda: value if (value := state())["state"] == "running" else None,
            "The opted-in receiver did not reach running after native startup",
        )
        assert first["session_id"] == session_id and first["mode"] == "headed"
        assert first["host_process_start_time"] == process_start_time(first["pid"])
        original = _native_identities(first)
        identities.extend(original)
        await until(
            lambda: history() == prompts[:1]
            and any(value["type"] == "assistant.turn_end" for value in events()),
            "The original literal launch turn did not finish before controlled input",
        )
        binding = first["auth_wait"]
        if inflight or observation_loss:
            provider.pause_response.set()
            operation = asyncio.create_task(_prompt(first, prompts[1]))
            try:
                await until(
                    provider.response_started.is_set,
                    "The actual native model request did not remain in flight",
                )
                expected_hold = "native-operation-unsettled"
                if observation_loss:
                    fault = {
                        "session_id": session_id, "incarnation": binding["incarnation"],
                        "native_pid": first["child_pid"], "extension_pid": first["extension_pid"],
                    }
                    fault_file.write_text(json.dumps(fault), encoding="utf-8")
                    receipt = await until(
                        lambda: json.loads(receipt_file.read_text(encoding="utf-8")),
                        "The fixture did not close the actual authenticated SDK stream",
                    )
                    assert "error" not in receipt, receipt
                    assert all(receipt[key] == value for key, value in fault.items())
                    assert receipt["writer_closed"] and not receipt["bound"]
                    expected_hold = "native-connection-lost"
                    assert receipt["status"]["hold"] == expected_hold
                    assert receipt["status"]["phase"] == "running"
                    unknown = await _auth(binding, "status")
                    assert unknown["ok"] and unknown["hold"] == expected_hold
                    assert unknown["incarnation"] == binding["incarnation"]
                    assert pid_alive(original[0][0])
                    assert process_start_time(original[0][0]) == original[0][1]
                shortened_at = time.monotonic()
                shortened = await _auth(
                    binding, "renew", lease_revision=2,
                    budget_seconds=3, expires_at=time.time() + 30,
                )
                assert shortened["ok"]
                if observation_loss:
                    assert shortened["hold"] == expected_hold
                    assert shortened["incarnation"] == binding["incarnation"]
                stopped = await until(
                    lambda: value if (value := state())["state"] == "auth_held" else None,
                    "In-flight native work did not preserve a hold after source expiry", timeout=3,
                )
                assert time.monotonic() - shortened_at < 3 and _retired(original)
                assert stopped["auth_wait"]["hold"] == expected_hold
                assert stopped["auth_wait"]["incarnation"] == binding["incarnation"]
                assert not (await _auth(
                    binding, "renew", lease_revision=3,
                    budget_seconds=30, expires_at=time.time() + 30,
                ))["ok"]
                outcome, = await asyncio.wait_for(
                    asyncio.gather(operation, return_exceptions=True), 2,
                )
                assert isinstance(outcome, Exception), "An interrupted native turn cannot succeed"
                await asyncio.sleep(.15)
                unchanged = state()
                assert unchanged["auth_wait"]["incarnation"] == binding["incarnation"]
                assert unchanged["auth_wait"]["hold"] == expected_hold
                assert unchanged["child_pid"] == first["child_pid"] and _retired(original)
                assert history() == prompts[:2] and len(requests) == 2
                assert list((home / "session-state").glob("*/events.jsonl")) == [history_file]
            finally:
                provider.release_response.set()
                if not operation.done():
                    operation.cancel()
                await asyncio.gather(operation, return_exceptions=True)
            return
        await _prompt(first, prompts[1])
        await until(lambda: history() == prompts[:2], "Original literal seed/history changed")
        active = await _auth(
            binding, "renew", lease_revision=2, budget_seconds=30, expires_at=time.time() + 30,
        )
        assert active["ok"] and active["incarnation"] == binding["incarnation"]
        assert _native_identities(first) == original
        shortened_at = time.monotonic()
        shortened = await _auth(
            binding, "renew", lease_revision=3, budget_seconds=3, expires_at=time.time() + 30,
        )
        assert shortened["ok"]
        stopped = await until(
            lambda: value if (value := state())["state"] == "auth_wait" else None,
            "Shortened source grant did not settle into auth wait", timeout=3,
        )
        assert time.monotonic() - shortened_at < 3 and _retired(original)
        assert stopped["auth_wait"]["hold"] is None
        renewed = await _auth(
            binding, "renew", lease_revision=4, budget_seconds=60, expires_at=time.time() + 60,
        )
        assert renewed["ok"] and renewed["incarnation"] != binding["incarnation"]
        assert not (await _auth(binding, "status"))["ok"]
        resumed = await until(
            lambda: value if (value := state())["state"] == "running"
            and value["auth_wait"]["incarnation"] == renewed["incarnation"] else None,
            "A new authorized native incarnation did not resume the same session",
        )
        current = _native_identities(resumed)
        identities.extend(current)
        assert current[0] != original[0] and _retired(original)
        assert resumed["session_id"] == session_id and resumed["pid"] == first["pid"]
        await _prompt(resumed, prompts[2])
        await until(lambda: history() == prompts[:3], "Resume replayed input or lost history")
        assert mux.query("send-keys", "-t", mux.target, "-l", "--", prompts[3]).returncode == 0
        assert mux.query("send-keys", "-t", mux.target, "Enter").returncode == 0
        await until(lambda: history() == prompts, "Actual native keyboard input was not observed")
        binding = resumed["auth_wait"]

        async def held():
            value = await _auth(binding, "status")
            return value if value.get("hold") == "native-control-lost" else None

        hold = await until(held, "Native input did not preserve a broker-control hold")
        renewed = await _auth(
            binding, "renew", lease_revision=5, budget_seconds=30, expires_at=time.time() + 30,
        )
        assert renewed["ok"] and renewed["hold"] == hold["hold"]
        assert renewed["incarnation"] == binding["incarnation"]
        await _prompt(resumed, "must remain refused", refused=True)
        await asyncio.sleep(.15)
        assert _native_identities(resumed) == current
        assert (await _auth(binding, "revoke"))["ok"]
        final = await until(
            lambda: value if (value := state())["state"] == "auth_held" else None,
            "Revoked held incarnation did not retire",
        )
        assert _retired(current) and final["auth_wait"]["hold"] == hold["hold"]
        assert not (await _auth(
            binding, "renew", lease_revision=6, budget_seconds=30, expires_at=time.time() + 30,
        ))["ok"]
        assert history() == prompts and len(requests) == len(prompts)
        assert list((home / "session-state").glob("*/events.jsonl")) == [history_file]

    with daemon_test_lifecycle(
        owner="native-auth-wait-fixture", registry_root=tmp_path / "daemon-instances",
        deadline=deadline + 10, cleanup_results=cleanup,
    ) as lifecycle:
        environment.update(lifecycle)
        mux = FixtureMux(mux_binary, tmp_path, environment)
        try:
            launched = mux.query(
                "new-session", "-d", "-s", mux.target, "-P", "-F",
                "#{session_id}:#{session_created}|#{pane_id}", "--",
                sys.executable, "-I", "-B", str(driver), str(configuration),
            )
            assert launched.returncode == 0, launched.stderr
            incarnation = launched.stdout.strip()
            assert re.fullmatch(r"\$[0-9]+:[0-9]+\|%[0-9]+", incarnation), incarnation
            try:
                asyncio.run(flow())
            except Exception as error:
                if error_file.exists():
                    raise AssertionError(
                        "Fixture coordinator diagnostic:\n"
                        + error_file.read_text(encoding="utf-8")[:4096]
                    ) from error
                raise
        finally:
            present = mux.query("has-session", "-t", mux.target)
            assert present.returncode in (0, 1), present.stderr
            if present.returncode == 0:
                observed = mux.query(
                    "display-message", "-p", "-t", mux.target,
                    "#{session_id}:#{session_created}|#{pane_id}",
                )
                assert observed.returncode == 0 and observed.stdout.strip() == incarnation
                assert mux.query("kill-session", "-t", mux.target).returncode == 0
    assert identities and _retired(identities)
    assert all(result.status == "settled" for result in cleanup), cleanup


@pytest.mark.timeout(180)
@pytest.mark.skipif(
    os.name != "nt" or os.environ.get("COPILOT_DRIVER_HEADED_MUX_E2E") != "1",
    reason="Explicitly select the disposable pinned Windows receiver qualification lane",
)
def test_opt_in_receiver_auth_wait_and_same_session_resume(tmp_path, monkeypatch, native_provider):
    _qualify_native_receiver(tmp_path, monkeypatch, native_provider)


@pytest.mark.timeout(180)
@pytest.mark.skipif(
    os.name != "nt" or os.environ.get("COPILOT_DRIVER_HEADED_MUX_E2E") != "1",
    reason="Explicitly select the disposable pinned Windows receiver qualification lane",
)
def test_opt_in_receiver_inflight_expiry_preserves_hold(tmp_path, monkeypatch, native_provider):
    _qualify_native_receiver(tmp_path, monkeypatch, native_provider, inflight=True)


@pytest.mark.timeout(180)
@pytest.mark.skipif(
    os.name != "nt" or os.environ.get("COPILOT_DRIVER_HEADED_MUX_E2E") != "1",
    reason="Explicitly select the disposable pinned Windows receiver qualification lane",
)
def test_opt_in_receiver_sdk_observation_loss_preserves_hold(
    tmp_path, monkeypatch, native_provider,
):
    _qualify_native_receiver(tmp_path, monkeypatch, native_provider, observation_loss=True)
