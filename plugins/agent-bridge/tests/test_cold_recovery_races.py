"""Independent processes, kernel custody, crash reconciliation and delivery proof."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from agent_procutil import no_window_kwargs
from zdd import diagnostics, process_exit, routing

from agent_bridge import cold_outages
from agent_bridge.session_cold_drain import cold_drain, owned_execution
from agent_bridge.session_host.host_index import HostIndex
from test_cold_drain import NATIVE, bind_peer, cleanup, live_session, messages, until

pytestmark = pytest.mark.skipif(
    sys.platform not in {"linux", "win32"},
    reason="native process identity requires Linux or Windows",
)


class Workers:
    def __init__(self, database: Path, shared: Path):
        self.database, self.shared = database, shared
        self.processes: list[tuple[subprocess.Popen, Path, object]] = []

    def start(self, role: str, mode: str = "recover") -> tuple[subprocess.Popen, Path]:
        control = self.shared / role
        control.mkdir()
        output = (control / "worker.log").open("wb")
        process = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).with_name("cold_recovery_worker.py")),
                str(self.database),
                str(self.shared),
                str(control),
                mode,
            ],
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            **no_window_kwargs(),
        )
        self.processes.append((process, control, output))
        return process, control

    async def ready(self, control: Path) -> None:
        await until((control / "ready").exists)

    async def result(self, process, control) -> dict:
        await until(lambda: (control / "result.json").exists() or process.poll() is not None)
        assert (control / "result.json").exists(), (control / "worker.log").read_text(
            errors="replace"
        )
        return json.loads((control / "result.json").read_text())

    def close(self) -> None:
        for process, control, output in self.processes:
            (control / "done").touch()
            (control / "start").touch()
            (control / "continue").touch()
            (control / "allow-exit").touch()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            output.close()
        # Fixture-only native children may intentionally outlive a crashed worker.
        index = HostIndex(self.database.parent / "hosts/index.json")
        for record in index.all():
            state = json.loads(Path(record.state_file).read_text())
            for name in ("child", "host"):
                pid, birth = state[name + "_pid"], state[name + "_process_start_time"]
                if not process_exit.observe_original_exit(pid, birth):
                    result = diagnostics.terminate_pid_if_identity(pid, birth, exit_timeout=5)
                    assert result.get("exit_confirmed") or process_exit.observe_original_exit(
                        pid, birth
                    ), result


@pytest.fixture
def workers(tmp_path, tmp_db):
    pool = Workers(tmp_db.db_path, tmp_path)
    try:
        yield pool
    finally:
        pool.close()


async def interrupted(manager, tmp_path, monkeypatch) -> Path:
    _session, transcript = await live_session(manager, tmp_path, monkeypatch)
    result = await cold_drain(
        manager, timeout=10, poll=0.05, source="fixture", reason="update", exclude_session_id=None
    )
    assert result["drained"], result
    return transcript


def prompts(transcript: Path) -> list[dict]:
    return [m for m in messages(transcript) if m.get("method") == "session/prompt"]


@pytest.mark.asyncio
async def test_cold_recovery_two_successors_exclude_before_native_spawn(
    session_manager,
    tmp_path,
    monkeypatch,
    workers,
) -> None:
    transcript = await interrupted(session_manager, tmp_path, monkeypatch)
    first, first_control = workers.start("first", "blocked-launch")
    second, second_control = workers.start("second")
    await asyncio.gather(workers.ready(first_control), workers.ready(second_control))
    (first_control / "start").touch()
    await until((first_control / "connect-entered").exists)
    held = cold_outages.latest(session_manager.db, "cold-session")
    assert held["recovery_pid"] == first.pid and held["recovery_phase"] == "launching"
    assert diagnostics.process_start_time(first.pid) == held["recovery_start_time"]
    (second_control / "start").touch()
    refused = await workers.result(second, second_control)
    assert refused["result"]["reattached"] == 0
    assert not (second_control / "connect-entered").exists()
    assert cold_outages.latest(session_manager.db, "cold-session")["recovery_pid"] == first.pid
    (first_control / "continue").touch()
    recovered = await workers.result(first, first_control)
    assert recovered["result"]["reattached"] == 1
    assert recovered["outage"]["state"] == "reconnected"
    assert len([m for m in messages(transcript) if "boot" in m]) == 2
    assert len(prompts(transcript)) == 1
    assert prompts(transcript)[0]["params"]["sessionId"] == NATIVE
    assert len(session_manager.db.get_turns("cold-session")) == 1
    # The kernel descriptor has now been released, but a live recorded owner
    # still fences a different generation. Age is never takeover authority.
    third, third_control = workers.start("third")
    await workers.ready(third_control)
    (third_control / "start").touch()
    third_result = await workers.result(third, third_control)
    assert third_result["result"]["reattached"] == 0
    assert not (third_control / "connect-entered").exists()
    assert cold_outages.latest(session_manager.db, "cold-session")["recovery_pid"] == first.pid
    assert len(prompts(transcript)) == 1


@pytest.mark.asyncio
async def test_cold_recovery_registration_crash_adopts_existing_host(
    session_manager,
    tmp_path,
    monkeypatch,
    workers,
) -> None:
    transcript = await interrupted(session_manager, tmp_path, monkeypatch)
    crashed, control = workers.start("registered-crash", "crash-registered")
    await workers.ready(control)
    (control / "start").touch()
    await until(lambda: crashed.poll() is not None)
    assert crashed.returncode == 23, (control / "worker.log").read_text()
    pending = cold_outages.latest(session_manager.db, "cold-session")
    assert pending["recovery_phase"] == "launching"
    assert pending["replacement_json"] is None
    old = HostIndex(session_manager.db.db_path.parent / "hosts/index.json").get("cold-session")
    claimant, claim_control = workers.start("claim-crash", "crash-claim")
    await workers.ready(claim_control)
    (claim_control / "start").touch()
    await until(lambda: claimant.poll() is not None)
    assert claimant.returncode == 27, (claim_control / "worker.log").read_text()
    claimed = cold_outages.latest(session_manager.db, "cold-session")
    assert claimed["recovery_pid"] == claimant.pid
    assert claimed["replacement_json"] is not None
    assert (
        HostIndex(session_manager.db.db_path.parent / "hosts/index.json")
        .get("cold-session")
        .owner_pid
        == crashed.pid
    )
    recovering, next_control = workers.start("registered-successor")
    await workers.ready(next_control)
    (next_control / "start").touch()
    result = await workers.result(recovering, next_control)
    assert result["result"]["reattached"] == 1, result
    assert result["outage"]["state"] == "reconnected"
    new = HostIndex(session_manager.db.db_path.parent / "hosts/index.json").get("cold-session")
    assert (old.host_pid, old.child_pid) == (new.host_pid, new.child_pid)
    assert new.owner_pid == recovering.pid
    assert not (next_control / "connect-entered").exists()
    assert len([m for m in messages(transcript) if "boot" in m]) == 2
    assert len(prompts(transcript)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["recover", "resync"])
@pytest.mark.parametrize("mode,exit_code", [("drain-crash", 24), ("drain-settlement-error", None)])
async def test_cold_recovery_prepared_and_failed_reconcile_only_exact_exit(
    session_manager,
    tmp_path,
    workers,
    mode,
    exit_code,
    entrypoint,
) -> None:
    original, control = workers.start("drain-original", mode)
    if exit_code is not None:
        await until(lambda: original.poll() is not None)
        assert original.returncode == exit_code, (control / "worker.log").read_text()
    else:
        await workers.result(original, control)
    retained = cold_outages.latest(session_manager.db, "cold-session")
    assert retained["state"] == ("prepared" if exit_code else "failed")
    execution = json.loads(retained["execution_json"])
    assert all(
        process_exit.observe_original_exit(v["pid"], v["start_time"]) for v in execution.values()
    )
    recovered, next_control = workers.start("drain-successor", entrypoint)
    await workers.ready(next_control)
    (next_control / "start").touch()
    result = await workers.result(recovered, next_control)
    if entrypoint == "recover":
        assert result["result"]["reattached"] == 1, result
    else:
        assert "events" in result["result"], result
    assert result["outage"]["state"] == "reconnected"
    assert result["outage"]["native_session_id"] == NATIVE
    assert session_manager.db.get_turns("cold-session")[0]["stop_reason"] == "interrupted"
    assert len(prompts(tmp_path / "peer.jsonl")) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("child_exited", [False, True])
async def test_cold_recovery_live_original_resync_refuses_before_teardown(
    session_manager,
    tmp_path,
    monkeypatch,
    workers,
    child_exited,
) -> None:
    session, transcript = await live_session(session_manager, tmp_path, monkeypatch)
    record = session_manager._host_index.get(session.session_id)
    execution = owned_execution(session_manager, session, record)
    with cold_outages.operation(session_manager, session.session_id):
        cold_outages.prepare(session_manager, session, execution)
        if child_exited:
            await session.client.session_host_client.terminate()
            await until(
                lambda: process_exit.observe_original_exit(
                    execution["child"]["pid"],
                    execution["child"]["start_time"],
                )
            )
    worker, control = workers.start("live-resync", "resync")
    await workers.ready(control)
    (control / "start").touch()
    result = await workers.result(worker, control)
    assert "uncertain" in result["result"]["refused"]
    assert result["outage"]["state"] == "prepared"
    assert not (control / "connect-entered").exists()
    assert not process_exit.observe_original_exit(
        execution["host"]["pid"],
        execution["host"]["start_time"],
    )
    assert (
        process_exit.observe_original_exit(
            execution["child"]["pid"],
            execution["child"]["start_time"],
        )
        is child_exited
    )
    assert len([m for m in messages(transcript) if "boot" in m]) == 1
    await cleanup(session_manager)


@pytest.mark.asyncio
async def test_cold_recovery_resync_routes_same_native_without_rebuild(
    session_manager,
    tmp_path,
    monkeypatch,
    workers,
) -> None:
    transcript = await interrupted(session_manager, tmp_path, monkeypatch)
    worker, control = workers.start("cold-resync", "resync")
    await workers.ready(control)
    (control / "start").touch()
    result = await workers.result(worker, control)
    assert "events" in result["result"], result
    assert result["outage"]["state"] == "reconnected"
    assert {p["params"]["sessionId"] for p in prompts(transcript)} == {NATIVE}
    assert len(prompts(transcript)) == 1
    assert not [m for m in messages(transcript) if m.get("method") == "session/new"]


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["recover", "resync"])
@pytest.mark.parametrize(
    "mode,exit_code",
    [
        ("crash-admitted", 25),
        ("crash-before-connect", 26),
        ("crash-provider-proof", 28),
        ("crash-false-confirmed", 29),
    ],
)
async def test_cold_recovery_unknown_admission_or_launch_is_never_replayed(
    session_manager,
    tmp_path,
    monkeypatch,
    workers,
    mode,
    exit_code,
    entrypoint,
) -> None:
    transcript = await interrupted(session_manager, tmp_path, monkeypatch)
    crashed, control = workers.start("unknown-first", mode)
    await workers.ready(control)
    (control / "start").touch()
    await until(lambda: crashed.poll() is not None)
    assert crashed.returncode == exit_code, (control / "worker.log").read_text()
    retained = cold_outages.latest(session_manager.db, "cold-session")
    expected_state = (
        "interrupted"
        if exit_code == 26
        else ("reconnected" if exit_code == 29 else "reconnect_admitted")
    )
    assert retained["state"] == expected_state
    assert retained["reconnect_evidence_json"] is None
    if exit_code == 28:
        assert session_manager.db.get_turns("cold-session")[0]["stop_reason"] == "end_turn"
        assert session_manager.db.get_turns("cold-session")[0]["completed_at"] is not None
    successor, next_control = workers.start("unknown-successor", entrypoint)
    await workers.ready(next_control)
    (next_control / "start").touch()
    result = await workers.result(successor, next_control)
    if entrypoint == "recover":
        assert result["result"]["reattached"] == 0, result
    else:
        assert "unknown" in result["result"]["refused"], result
    assert result["outage"]["state"] != "reconnected"
    assert result["outage"]["reconnect_evidence_json"] is None
    assert not (next_control / "connect-entered").exists()
    assert len(prompts(transcript)) == (1 if exit_code == 28 else 0)
    assert len([m for m in messages(transcript) if "boot" in m]) == (1 if exit_code == 26 else 2)
    assert len(session_manager.db.get_turns("cold-session")) == (0 if exit_code == 26 else 1)


@pytest.mark.asyncio
async def test_cold_recovery_provider_failure_is_not_confirmation(
    session_manager,
    tmp_path,
    monkeypatch,
    workers,
) -> None:
    transcript = await interrupted(session_manager, tmp_path, monkeypatch)
    worker, control = workers.start("provider-refusal", "provider-refusal")
    await workers.ready(control)
    (control / "start").touch()
    result = await workers.result(worker, control)
    assert result["outage"]["state"] == "reconnect_unknown", result
    assert result["outage"]["reconnect_evidence_json"] is None
    assert "unknown" in result["result"]["resync_refused"]
    assert session_manager.db.get_turns("cold-session")[0]["stop_reason"].startswith("error:")
    assert len(prompts(transcript)) == 1


def active_fixture_app(tmp_path, monkeypatch):
    import agent_bridge.app as app_module
    from agent_bridge.models import ServiceConfig

    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("AGENT_BRIDGE_SELF_RETIRE", "0")
    monkeypatch.setenv("AGENT_WORKTREES_PROJECTS_YAML", str(tmp_path / "no-projects.yaml"))
    monkeypatch.setattr(app_module, "_HEARTBEAT_INTERVAL_S", 0.03)
    bind_peer(tmp_path, monkeypatch)
    app = app_module.create_app(
        config=ServiceConfig(
            bind="127.0.0.1",
            port=0,
            db_path=str(tmp_path / "test.db"),
            enable_credential_relay=False,
        ),
        token="test-token",
    )
    app.state.bound_port = 44002
    app.state.publish_on_ready = True
    return app


@pytest.mark.asyncio
async def test_startup_cold_outage_after_initial_scan_waits_for_predecessor_exit(
    session_manager,
    tmp_path,
    monkeypatch,
    workers,
) -> None:
    from agent_bridge.session_manager import SessionManager

    old, control = workers.start("startup-old", "startup-predecessor")
    await workers.ready(control)
    previous = routing.publish_active(
        tmp_path,
        bind="127.0.0.1",
        port=44001,
        pid=old.pid,
        version="fixture-old",
    )
    assert previous.process_start_time == diagnostics.process_start_time(old.pid)
    initial_done = threading.Event()
    scans = []
    scan = SessionManager.reattach_session_hosts

    async def tracked_scan(manager, *args, **kwargs):
        result = await scan(manager, *args, **kwargs)
        scans.append((result, kwargs))
        initial_done.set()
        return result

    monkeypatch.setattr(SessionManager, "reattach_session_hosts", tracked_scan)
    drain_replies = []

    class Client:
        def drain(self, **kwargs):
            assert initial_done.wait(5)
            assert cold_outages.latest(session_manager.db, "cold-session") is None
            (control / "request-drain").write_text(json.dumps(kwargs))
            deadline = time.monotonic() + 10
            while not (control / "drain-result.json").exists():
                assert time.monotonic() < deadline
                time.sleep(0.01)
            reply = json.loads((control / "drain-result.json").read_text())
            drain_replies.append(reply)
            return reply

        def shutdown(self):
            (control / "request-shutdown").touch()
            return {"shutting_down": True}

        def undrain(self):
            return {"draining": False}

    app = active_fixture_app(tmp_path, monkeypatch)
    app.state.supersession_client_factory = lambda _endpoint: Client()
    waiting = threading.Event()
    native_wait = process_exit.ProcessExitReference.wait

    def tracked_wait(reference, timeout):
        if reference.pid == old.pid and timeout > 0:
            waiting.set()
        return native_wait(reference, timeout)

    monkeypatch.setattr(process_exit.ProcessExitReference, "wait", tracked_wait)
    recover = cold_outages.recover
    productive = []

    async def tracked_recover(manager, **kwargs):
        if not kwargs.get("confirmed_only", False):
            assert initial_done.is_set() and old.poll() is not None
            productive.append(kwargs)
        return await recover(manager, **kwargs)

    monkeypatch.setattr(cold_outages, "recover", tracked_recover)
    lifecycle = app.router.lifespan_context(app)
    entered = False
    try:
        await lifecycle.__aenter__()
        entered = True
        await until(
            lambda: (
                waiting.is_set()
                or bool(
                    drain_replies and not drain_replies[-1].get("drained"),
                )
            )
        )
        assert waiting.is_set(), (drain_replies, (control / "worker.log").read_text())
        assert initial_done.is_set() and scans[0][1]["recover_cold"] is False
        assert old.poll() is None
        assert cold_outages.latest(session_manager.db, "cold-session")["state"] == "interrupted"
        assert productive == []
        assert len([m for m in messages(tmp_path / "peer.jsonl") if "boot" in m]) == 1
        (control / "allow-exit").touch()
        await until(lambda: old.poll() is not None)
        await until(
            lambda: (
                cold_outages.latest(session_manager.db, "cold-session")["state"] == "reconnected"
            )
        )
        assert productive
        assert len([m for m in messages(tmp_path / "peer.jsonl") if "boot" in m]) == 2
        # One original interrupted request and one new reconnect, never replay.
        assert len(prompts(tmp_path / "peer.jsonl")) == 2
        assert {m["params"]["sessionId"] for m in prompts(tmp_path / "peer.jsonl")} == {NATIVE}
    finally:
        (control / "allow-exit").touch()
        if entered:
            await lifecycle.__aexit__(None, None, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("confirmed", [True, False])
async def test_heartbeat_partial_cold_replacement_exit_preserves_delivery_fence(
    session_manager,
    tmp_path,
    monkeypatch,
    workers,
    confirmed,
) -> None:
    from agent_bridge.session_manager import SessionManager

    transcript = await interrupted(session_manager, tmp_path, monkeypatch)
    producer, control = workers.start(
        "partial-producer",
        "exit-confirmed" if confirmed else "crash-admitted",
    )
    await workers.ready(control)
    (control / "start").touch()
    if confirmed:
        await workers.result(producer, control)
    await until(lambda: producer.poll() is not None)
    assert producer.returncode == (0 if confirmed else 25)
    initial_done = asyncio.Event()
    scan = SessionManager.reattach_session_hosts

    async def tracked_scan(manager, *args, **kwargs):
        result = await scan(manager, *args, **kwargs)
        initial_done.set()
        return result

    monkeypatch.setattr(SessionManager, "reattach_session_hosts", tracked_scan)
    app = active_fixture_app(tmp_path, monkeypatch)
    app.state.supersession_client_factory = lambda _endpoint: pytest.fail(
        "no predecessor route exists"
    )
    lifecycle = app.router.lifespan_context(app)
    await lifecycle.__aenter__()
    try:
        await until(initial_done.is_set)
        record = HostIndex(session_manager.db.db_path.parent / "hosts/index.json").get(
            "cold-session"
        )
        state = json.loads(Path(record.state_file).read_text())
        old_host, old_child = record.host_pid, record.child_pid
        assert app.state.session_manager.get_session("cold-session").acp_session_id == NATIVE
        result = await asyncio.to_thread(
            diagnostics.terminate_pid_if_identity,
            old_child,
            state["child_process_start_time"],
            exit_timeout=5,
        )
        assert result.get("exit_confirmed")
        assert not process_exit.observe_original_exit(old_host, state["host_process_start_time"])
        ticks = []
        recover = cold_outages.recover

        async def observe_tick(manager, **kwargs):
            if kwargs.get("automatic"):
                ticks.append(True)
            return await recover(manager, **kwargs)

        monkeypatch.setattr(cold_outages, "recover", observe_tick)
        if confirmed:
            await until(
                lambda: (
                    (
                        current := HostIndex(
                            session_manager.db.db_path.parent / "hosts/index.json"
                        ).get("cold-session")
                    )
                    is not None
                    and current.child_pid != old_child
                )
            )
            assert process_exit.observe_original_exit(old_host, state["host_process_start_time"])
            assert len([m for m in messages(transcript) if "boot" in m]) == 3
            assert len(prompts(transcript)) == 1
            assert (
                cold_outages.latest(session_manager.db, "cold-session")["state"] == "reconnected"
            )
        else:
            await until(lambda: len(ticks) >= 2)
            assert not process_exit.observe_original_exit(
                old_host, state["host_process_start_time"]
            )
            retained = cold_outages.latest(session_manager.db, "cold-session")
            assert (
                retained["state"] == "reconnect_unknown"
                and retained["reconnect_evidence_json"] is None
            )
            assert len([m for m in messages(transcript) if "boot" in m]) == 2
            assert len(prompts(transcript)) == 0
        assert ticks
        assert app.state.session_manager.get_session("cold-session").acp_session_id == NATIVE
    finally:
        await lifecycle.__aexit__(None, None, None)
