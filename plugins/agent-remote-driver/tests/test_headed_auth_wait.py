"""Source-bound receiver controls and actual fixture-process stop/restart."""

import asyncio
import json
import os
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from agent_remote_driver.headed.auth_wait import AuthWaitReceiver
from agent_remote_driver.headed import launcher
from agent_remote_driver.headed.auth_child import AuthPaneChild
from agent_procutil import no_window_kwargs
from agent_procutil._interpreter import physical_executable

PYTHON = physical_executable(getattr(sys, "_base_executable", sys.executable))


@pytest.fixture(autouse=True)
def fixture_interpreter_arguments(monkeypatch):
    monkeypatch.setattr(launcher, "VALUE_OPTIONS", launcher.VALUE_OPTIONS | {"-c"})


def configuration(**changes):
    return {
        "source_owner": "fixture-owner", "source_revision": "source-revision-1",
        "lease_revision": 1, "budget_seconds": 10, "expires_at": time.time() + 10,
        **changes,
    }


async def request(receiver, operation, **changes):
    declaration = receiver.declaration() if hasattr(receiver, "declaration") else receiver
    reader, writer = await asyncio.open_connection("127.0.0.1", declaration["port"])
    fields = {
        key: declaration[key] for key in (
            "contract", "version", "nonce", "session_id", "incarnation",
            "source_owner", "source_revision",
        )
    }
    try:
        writer.write(json.dumps({**fields, "operation": operation, **changes}).encode() + b"\n")
        await writer.drain()
        return json.loads(await asyncio.wait_for(reader.readline(), 2))
    finally:
        writer.close()
        await writer.wait_closed()


IDLE = {
    "controlled": True, "closed": False, "pending": False,
    "sending": False, "outstanding": False, "waiting": True,
}


@pytest.mark.asyncio
async def test_expired_lease_requires_stop_then_new_revision_and_incarnation():
    receiver = AuthWaitReceiver("durable-session", configuration(budget_seconds=.03))
    await receiver.serve()
    initial = receiver.declaration()
    try:
        await receiver.expiry()
        assert not receiver.authorized()
        renewal = dict(lease_revision=2, budget_seconds=5, expires_at=time.time() + 5)
        assert not (await request(receiver, "renew", **renewal))["ok"]
        receiver.stopped(IDLE)
        result = await request(receiver, "renew", **renewal)
        assert result["ok"] and result["session_id"] == initial["session_id"]
        assert result["incarnation"] != initial["incarnation"]
        await asyncio.wait_for(receiver.next_grant(), 1)
        assert receiver.authorized()
        assert not (await request(initial, "revoke"))["ok"]
        assert not (await request(receiver, "renew", **renewal))["ok"]
    finally:
        await receiver.close()


@pytest.mark.parametrize("change", [
    {"budget_seconds": True}, {"budget_seconds": float("inf")},
    {"budget_seconds": 3601}, {"expires_at": 0},
    {"lease_revision": True}, {"source_revision": ""},
])
def test_missing_or_unbounded_source_authorization_refused(change):
    with pytest.raises(ValueError):
        AuthWaitReceiver("durable-session", configuration(**change))


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", ["posix", "other"])
async def test_unsupported_owner_death_custody_refuses_before_receiving_endpoint(
    tmp_path, monkeypatch, platform,
):
    from agent_remote_driver.headed import auth_child

    monkeypatch.setattr(auth_child, "os", SimpleNamespace(name=platform))

    async def forbidden_server(_receiver):
        raise AssertionError("Unsupported custody must refuse before opening the endpoint")

    monkeypatch.setattr(AuthWaitReceiver, "serve", forbidden_server)
    with pytest.raises(RuntimeError, match="owner-death custody"):
        await launcher.run_headed(
            ["native", "--session-id", "durable-session"], session_id="durable-session",
            attempt_directory=tmp_path / "attempt", state_file=tmp_path / "state.json",
            cwd=str(tmp_path), auth_wait=configuration(),
        )


@pytest.mark.asyncio
async def test_missing_native_job_retirement_api_refuses_before_endpoint(tmp_path, monkeypatch):
    from agent_remote_driver.headed import auth_child

    monkeypatch.setattr(auth_child, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(auth_child, "JobHandle", SimpleNamespace())

    async def forbidden_server(_receiver):
        raise AssertionError("Missing native Job custody must refuse before endpoint creation")

    monkeypatch.setattr(AuthWaitReceiver, "serve", forbidden_server)
    with pytest.raises(RuntimeError, match="Job retirement custody"):
        await launcher.run_headed(
            ["native", "--session-id", "durable-session"], session_id="durable-session",
            attempt_directory=tmp_path / "attempt", state_file=tmp_path / "state.json",
            cwd=str(tmp_path), auth_wait=configuration(),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("shortened", ["budget", "wall"])
async def test_active_shortening_stops_before_endpoint_acceptance_can_extend_authority(shortened):
    receiver = AuthWaitReceiver("durable-session", configuration())
    await receiver.serve()
    receiver.phase = "running"
    watcher = asyncio.create_task(receiver.expiry())
    try:
        await asyncio.sleep(.01)
        renewal = dict(lease_revision=2, budget_seconds=5, expires_at=time.time() + 5)
        renewal["budget_seconds" if shortened == "budget" else "expires_at"] = (
            .01 if shortened == "budget" else time.time() + .5
        )
        result = await request(receiver, "renew", **renewal)
        assert result["ok"] and result["lease_revision"] == 2
        assert receiver._stop.is_set()
        await asyncio.wait_for(watcher, .05)
        assert receiver.phase == "stopping" and not receiver.authorized()
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)
        await receiver.close()


@pytest.mark.asyncio
async def test_sub_reserve_renewal_cannot_restart_a_stopped_native_incarnation():
    receiver = AuthWaitReceiver("durable-session", configuration())
    await receiver.serve()
    original = receiver.incarnation
    try:
        await request(receiver, "revoke")
        await receiver.expiry()
        receiver.stopped(IDLE)
        result = await request(
            receiver, "renew", lease_revision=2, budget_seconds=.01,
            expires_at=time.time() + 5,
        )
        assert result["ok"] and result["phase"] == "auth_wait"
        assert result["incarnation"] == original and not receiver.authorized()
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(receiver.next_grant(), .05)
    finally:
        await receiver.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [
    {"controlled": False}, {"closed": True}, {"pending": True},
    {"sending": True}, {"outstanding": True}, {"waiting": False}, None,
])
async def test_new_source_lease_never_clears_native_control_holds(changed):
    receiver = AuthWaitReceiver("durable-session", configuration())
    await receiver.serve()
    try:
        await request(receiver, "revoke")
        await receiver.expiry()
        receiver.stopped(None if changed is None else {**IDLE, **changed})
        assert not (await request(
            receiver, "renew", lease_revision=2, budget_seconds=5, expires_at=time.time() + 5,
        ))["ok"]
        assert receiver.phase == "held" and not receiver.authorized()
    finally:
        await receiver.close()


@pytest.mark.asyncio
async def test_wall_expiry_and_binding_refusals_do_not_become_positive_support(monkeypatch):
    receiver = AuthWaitReceiver("durable-session", configuration())
    await receiver.serve()
    try:
        for change in (
            {"version": True}, {"version": 2}, {"source_owner": "foreign-owner"},
            {"source_revision": "obsolete-source"}, {"incarnation": "obsolete"},
            {"nonce": "wrong"}, {"session_id": "foreign-session"},
        ):
            assert not (await request(receiver, "status", **change))["ok"]
        monkeypatch.setattr(time, "time", lambda: receiver._wall_deadline + 1)
        assert not receiver.authorized()
    finally:
        await receiver.close()


def test_seedless_resume_preserves_option_values_but_not_launch_input():
    argv = [
        "native", "--session-id", "durable-session", "-i", "never replay",
        "--model", "--interactive=literal", "--log-dir=fixture",
    ]
    assert launcher._seedless_resume(argv, "durable-session") == [
        "native", "--model", "--interactive=literal", "--log-dir=fixture",
        "--resume", "durable-session",
    ]
    with pytest.raises(ValueError, match="positional"):
        launcher._seedless_resume([*argv, "--", "never replay"], "durable-session")
    for extra in (["bare launch input"], ["-iattached-seed"]):
        with pytest.raises(ValueError, match="positional"):
            launcher._seedless_resume([*argv, *extra], "durable-session")


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [["bare launch input"], ["-iattached-seed"]])
async def test_unreplayable_input_refuses_before_receiving_endpoint(tmp_path, monkeypatch, extra):
    from agent_remote_driver.headed import auth_child

    monkeypatch.setattr(auth_child, "require_auth_custody", lambda: None)

    async def forbidden_server(_receiver):
        raise AssertionError("Invalid launch input must refuse before endpoint creation")

    monkeypatch.setattr(AuthWaitReceiver, "serve", forbidden_server)
    with pytest.raises(ValueError, match="positional"):
        await launcher.run_headed(
            ["native", "--session-id", "durable-session", *extra],
            session_id="durable-session", attempt_directory=tmp_path / "attempt",
            state_file=tmp_path / "state.json", cwd=str(tmp_path), auth_wait=configuration(),
        )


_NATIVE = r"""
import json, os, socket, subprocess, sys, time
binding = json.loads(os.environ["COPILOT_DRIVER_HEADED_BINDING"])
descendant = subprocess.Popen(
    [sys.executable, "-c", "import time;time.sleep(60)"],
    creationflags=0x08000000 if os.name == "nt" else 0,
)
with open(os.environ["AUTH_FIXTURE_HISTORY"], "a", encoding="utf-8") as history:
    history.write(json.dumps({"pid": os.getpid(), "descendant": descendant.pid,
                             "argv": sys.argv[1:]}) + "\n")
peer = socket.create_connection(("127.0.0.1", binding["port"]))
peer.sendall((json.dumps({
    "contract": "copilot-driver.headed-sdk", "version": 1,
    "sessionId": binding["sessionId"], "nonce": binding["nonce"],
    "nativePid": os.getpid(), "extensionPid": os.getpid(),
}) + "\n").encode())
if os.environ.get("AUTH_FIXTURE_NATIVE_INPUT"):
    peer.sendall((json.dumps({
        "contract": "copilot-driver.headed-auth-state", "version": 1,
        "incarnation": binding["authWait"]["incarnation"],
        "state": {"controlled": False, "closed": False, "pending": False,
                  "sending": False, "outstanding": False, "waiting": False},
    }) + "\n").encode())
if os.environ.get("AUTH_FIXTURE_DROP_AFTER_BIND"):
    peer.close()
    time.sleep(60)
    sys.exit(0)
stream = peer.makefile("rb")
for line in stream:
    request = json.loads(line)
    if request.get("contract") == "copilot-driver.headed-auth-freeze":
        state = {"controlled": not bool(os.environ.get("AUTH_FIXTURE_NATIVE_INPUT")),
                 "closed": False, "pending": False,
                 "sending": False, "outstanding": False, "waiting": True}
        if os.environ.get("AUTH_FIXTURE_PENDING"):
            state["pending"] = True
        peer.sendall((json.dumps({
            "contract": "copilot-driver.headed-auth-state", "version": 1,
            "incarnation": binding["authWait"]["incarnation"], "state": state,
        }) + "\n").encode())
"""


async def eventually(read, predicate):
    async def poll():
        while True:
            try:
                value = read()
                if predicate(value):
                    return value
            except (FileNotFoundError, json.JSONDecodeError):
                pass
            await asyncio.sleep(.01)
    return await asyncio.wait_for(poll(), 10)


@pytest.mark.asyncio
async def test_native_input_hold_preserves_native_lifetime_until_actual_source_revocation(
    tmp_path, monkeypatch,
):
    if os.name != "nt":
        pytest.skip("POSIX auth wait refuses until independent owner-death custody is supported")
    from agent_remote_driver.session_host.osutil import pid_alive

    monkeypatch.setattr(launcher, "_require_receiving_policy", lambda *_args: None)
    state_file, history_file = tmp_path / "host.json", tmp_path / "history.jsonl"
    task = asyncio.create_task(launcher.run_headed(
        [PYTHON, "-c", _NATIVE, "--session-id", "durable-session"],
        session_id="durable-session", attempt_directory=tmp_path / "attempt",
        state_file=state_file, cwd=str(tmp_path), auth_wait=configuration(),
        env={"AUTH_FIXTURE_HISTORY": str(history_file), "AUTH_FIXTURE_NATIVE_INPUT": "1"},
    ))
    read_state = lambda: json.loads(state_file.read_text(encoding="utf-8"))
    read_history = lambda: [json.loads(line) for line in history_file.read_text().splitlines()]
    try:
        running = await eventually(read_state, lambda value: value.get("state") == "running")
        child = (await eventually(read_history, lambda value: len(value) == 1))[0]
        endpoint = running["auth_wait"]

        async def held_status():
            while True:
                status = await request(endpoint, "status")
                if status["hold"] == "native-control-lost":
                    return status
                await asyncio.sleep(.01)

        assert (await asyncio.wait_for(held_status(), 5))["phase"] == "running"
        renewed = await request(
            endpoint, "renew", lease_revision=2, budget_seconds=10, expires_at=time.time() + 10,
        )
        assert renewed["ok"] and renewed["hold"] == "native-control-lost"
        assert renewed["incarnation"] == endpoint["incarnation"]
        await asyncio.sleep(.15)
        assert not task.done() and pid_alive(child["pid"]) and pid_alive(child["descendant"])
        assert len(read_history()) == 1
        assert (await request(endpoint, "revoke"))["ok"]
        stopped = await eventually(read_state, lambda value: value.get("state") == "auth_held")
        assert stopped["auth_wait"]["hold"] == "native-control-lost"
        assert not pid_alive(child["pid"]) and not pid_alive(child["descendant"])
        assert not (await request(
            endpoint, "renew", lease_revision=3, budget_seconds=10, expires_at=time.time() + 10,
        ))["ok"]
        assert not task.done() and len(read_history()) == 1
    finally:
        task.cancel()
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 5)


@pytest.mark.asyncio
async def test_cancelled_retirement_waiter_retains_one_exact_cleanup(tmp_path, monkeypatch):
    if os.name != "nt":
        pytest.skip("POSIX auth wait refuses until independent owner-death custody is supported")
    owner = AuthPaneChild()
    owner.launch([PYTHON, "-c", "import time;time.sleep(60)"], **no_window_kwargs())
    started, release = threading.Event(), threading.Event()
    retire = owner._retire
    calls = []

    def blocked_retire():
        calls.append(owner.pid)
        started.set()
        assert release.wait(5)
        retire()

    monkeypatch.setattr(owner, "_retire", blocked_retire)
    first = asyncio.create_task(owner.terminate())
    second = None
    try:
        assert await asyncio.to_thread(started.wait, 2)
        assert owner.retirement_started
        await eventually(lambda: owner.returncode, lambda value: value is not None)
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        second = asyncio.create_task(owner.terminate())
        await asyncio.sleep(.025)
        assert calls == [owner.pid] and not second.done()
    finally:
        release.set()
        await owner.terminate()
        if second is not None:
            await second
    assert owner.returncode is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("pending,check_job_setup", [(False, False), (True, False), (False, True)])
async def test_real_native_scope_stops_preserves_and_resumes_without_seed(
    tmp_path, monkeypatch, pending, check_job_setup,
):
    if os.name != "nt":
        pytest.skip("POSIX auth wait refuses until independent owner-death custody is supported")
    from agent_remote_driver.session_host.osutil import pid_alive

    monkeypatch.setattr(launcher, "_require_receiving_policy", lambda *_args: None)
    job_setups = []
    if check_job_setup:
        from agent_remote_driver import winjob

        def setup_job(*, allow_breakaway):
            job_setups.append(allow_breakaway)
            return len(job_setups) == 1

        monkeypatch.setattr(launcher, "contained_test_mode", lambda: False)
        monkeypatch.setattr(winjob, "setup_kill_on_close_job", setup_job)
    state_file, history_file = tmp_path / "host.json", tmp_path / "history.jsonl"
    unrelated = AuthPaneChild()
    unrelated.launch(
        [PYTHON, "-c", "import time;time.sleep(60)"], **no_window_kwargs(),
    )
    native_argv = [
        PYTHON, "-c", _NATIVE, "--session-id", "durable-session", "-i", "original-seed",
    ]
    task = asyncio.create_task(launcher.run_headed(
        native_argv, session_id="durable-session", attempt_directory=tmp_path / "attempt",
        state_file=state_file, cwd=str(tmp_path), auth_wait=configuration(budget_seconds=3),
        env={
            "AUTH_FIXTURE_HISTORY": str(history_file),
            **({"AUTH_FIXTURE_PENDING": "1"} if pending else {}),
        },
    ))
    read_state = lambda: json.loads(state_file.read_text(encoding="utf-8"))
    read_history = lambda: [json.loads(line) for line in history_file.read_text().splitlines()]
    try:
        first = await eventually(read_state, lambda value: value.get("state") == "running")
        first_child = (await eventually(read_history, lambda value: len(value) == 1))[0]
        assert first_child["pid"] == first["child_pid"] and unrelated.returncode is None
        endpoint = first["auth_wait"]
        if pending:
            assert (await request(endpoint, "revoke"))["ok"]

        def stopped_state(value):
            if not pending and value.get("state") == "auth_held":
                raise AssertionError("Idle auth stop held: " + str(value["auth_wait"]["hold"]))
            return value.get("state") == ("auth_held" if pending else "auth_wait")

        stopped = await eventually(read_state, stopped_state)
        assert not pid_alive(first_child["pid"])
        assert not pid_alive(first_child["descendant"])
        assert unrelated.returncode is None and not task.done()
        renewed = await request(
            endpoint, "renew", lease_revision=2, budget_seconds=10, expires_at=time.time() + 10,
        )
        if pending:
            assert not renewed["ok"] and stopped["auth_wait"]["hold"]
            assert len(read_history()) == 1
        else:
            assert renewed["ok"]
            second = await eventually(
                read_state, lambda value: value.get("state") == "running"
                and value["child_pid"] != first_child["pid"],
            )
            histories = await eventually(read_history, lambda value: len(value) == 2)
            assert second["session_id"] == first["session_id"] == "durable-session"
            assert second["auth_wait"]["incarnation"] != endpoint["incarnation"]
            assert histories[0]["argv"].count("original-seed") == 1
            assert "original-seed" not in histories[1]["argv"]
            resume_index = histories[1]["argv"].index("--resume")
            assert histories[1]["argv"][resume_index + 1] == "durable-session"
            assert not (await request(endpoint, "revoke"))["ok"]
            assert unrelated.returncode is None
            if check_job_setup:
                assert job_setups == [False]
    finally:
        task.cancel()
        done, _ = await asyncio.wait((task,), timeout=5)
        if not done:
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await unrelated.terminate()
        assert done, "Receiver cancellation did not settle on its first request"


@pytest.mark.asyncio
async def test_cancellation_retires_native_scope_before_connected_websocket_drain(
    tmp_path, monkeypatch,
):
    if os.name != "nt":
        pytest.skip("POSIX auth wait refuses until independent owner-death custody is supported")
    from websockets.asyncio.client import connect

    from agent_remote_driver.session_host.osutil import pid_alive
    from agent_remote_driver.session_host.ws_transport import WebSocketTransport

    monkeypatch.setattr(launcher, "_require_receiving_policy", lambda *_args: None)
    draining, release = asyncio.Event(), asyncio.Event()
    close = WebSocketTransport.close

    async def gated_close(transport, *, drain=False):
        assert drain and transport._server.connections
        draining.set()
        await release.wait()
        await close(transport, drain=drain)

    monkeypatch.setattr(WebSocketTransport, "close", gated_close)
    state_file, history_file = tmp_path / "host.json", tmp_path / "history.jsonl"
    grant = configuration(budget_seconds=3, expires_at=time.time() + 3)
    task = asyncio.create_task(launcher.run_headed(
        [PYTHON, "-c", _NATIVE, "--session-id", "durable-session"],
        session_id="durable-session", attempt_directory=tmp_path / "attempt",
        state_file=state_file, cwd=str(tmp_path), auth_wait=grant, ws_port=0,
        env={"AUTH_FIXTURE_HISTORY": str(history_file)},
    ))
    websocket = None
    try:
        running = await eventually(
            lambda: json.loads(state_file.read_text(encoding="utf-8")),
            lambda value: value.get("state") == "running",
        )
        child = (await eventually(
            lambda: [json.loads(line) for line in history_file.read_text().splitlines()],
            lambda value: len(value) == 1,
        ))[0]
        scheme = "bear" + "er."
        websocket = await connect(
            running["websocket"]["url"],
            subprotocols=["acp.v1", scheme + running["websocket"]["nonce"]],
        )
        assert pid_alive(child["pid"]) and pid_alive(child["descendant"])
        task.cancel()
        await asyncio.wait_for(draining.wait(), 2)
        assert not pid_alive(child["pid"]) and not pid_alive(child["descendant"])
        await asyncio.sleep(max(0, grant["expires_at"] - time.time()) + .1)
        assert not task.done()
        assert not pid_alive(child["pid"]) and not pid_alive(child["descendant"])
    finally:
        release.set()
        if websocket is not None:
            await websocket.close()
        if not draining.is_set():
            task.cancel()
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 5)


@pytest.mark.asyncio
async def test_sdk_loss_still_monitors_expiry_publication_failure(tmp_path, monkeypatch):
    if os.name != "nt":
        pytest.skip("POSIX auth wait refuses until independent owner-death custody is supported")
    from agent_remote_driver.session_host.osutil import pid_alive

    monkeypatch.setattr(launcher, "_require_receiving_policy", lambda *_args: None)
    publish = launcher._write_host_state

    def failed_expiry_publication(path, state):
        if state.get("state") == "auth_stopping":
            raise OSError("fixture-state-publication-failure")
        publish(path, state)

    monkeypatch.setattr(launcher, "_write_host_state", failed_expiry_publication)
    state_file, history_file = tmp_path / "state.json", tmp_path / "history.jsonl"
    task = asyncio.create_task(launcher.run_headed(
        [PYTHON, "-c", _NATIVE, "--session-id", "durable-session"],
        session_id="durable-session", attempt_directory=tmp_path / "attempt",
        state_file=state_file, cwd=str(tmp_path), auth_wait=configuration(budget_seconds=3),
        env={"AUTH_FIXTURE_HISTORY": str(history_file), "AUTH_FIXTURE_DROP_AFTER_BIND": "1"},
    ))
    read_state = lambda: json.loads(state_file.read_text(encoding="utf-8"))
    read_history = lambda: [json.loads(line) for line in history_file.read_text().splitlines()]
    try:
        child = (await eventually(read_history, lambda value: len(value) == 1))[0]
        await eventually(read_state, lambda value: value.get("state") == "control_unavailable")
        assert pid_alive(child["pid"])
        await asyncio.wait_for(
            eventually(read_state, lambda value: value.get("state") == "auth_held"), 5,
        )
        assert not pid_alive(child["pid"]) and not pid_alive(child["descendant"])
        assert not task.done() and len(read_history()) == 1
    finally:
        task.cancel()
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 5)
