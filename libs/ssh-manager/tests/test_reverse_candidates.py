from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError

import pytest

from ssh_manager.config_sources import SSHConfig
import ssh_manager.reverse_candidates as candidate_mod
from ssh_manager.reverse_candidates import (
    FiniteReverseForward, ReverseEndpoint, ReverseEvidence, ReverseForwardError,
)
from ssh_manager.relay_channel import SupervisedRelayForward, _SettleResult


class Process:
    def __init__(self, pid):
        self.pid = pid
        self.returncode = None
        self.stderr = None


def fixture_owner(monkeypatch, states, *, observer=None, timeout=1):
    spawned, retired, attempts = [], [], []
    endpoints = (ReverseEndpoint("127.0.0.1", 41001), ReverseEndpoint("127.0.0.1", 41002))

    async def spawn(*args, **kwargs):
        proc = Process(len(spawned) + 100)
        proc.args = args
        proc.env = kwargs["env"]
        spawned.append(proc)
        return proc

    async def settle(self, proc):
        state = states[len(spawned) - 1]
        if state == "occupied":
            proc.returncode = 255
            return _SettleResult(False, "remote port forwarding failed", True)
        return _SettleResult(True, "")

    async def terminate(self, proc):
        retired.append(proc)
        proc.returncode = -9

    async def observe(attempt):
        attempts.append(attempt)
        if observer is not None:
            return await observer(attempt)
        return ReverseEvidence(attempt.attempt_id, states[len(spawned) - 1])

    monkeypatch.setattr(candidate_mod, "create_ssh_subprocess", spawn)
    monkeypatch.setattr(FiniteReverseForward, "_wait_settled", settle)
    monkeypatch.setattr(FiniteReverseForward, "_terminate", terminate)
    relay = FiniteReverseForward(
        SSHConfig(host_alias="fixture-host"), endpoints, request_id="operation-a",
        observe=observe, ready_timeout=timeout,
    )
    return relay, spawned, retired, attempts


async def test_collision_selects_and_retains_actual_candidate_process(monkeypatch):
    relay, spawned, retired, attempts = fixture_owner(monkeypatch, ["occupied", "ready"])
    await relay.establish()
    status = relay.reverse_status
    assert status.state == "ready"
    assert status.attempt.endpoint.port == 41002
    assert status.attempt.request_id == "operation-a"
    assert attempts[0].attempt_id != attempts[1].attempt_id
    assert retired == [spawned[0]]
    assert relay._proc is spawned[1] and relay.is_alive
    assert "127.0.0.1:41002:127.0.0.1:41001" in spawned[1].args
    assert "ExitOnForwardFailure=yes" in spawned[1].args
    assert spawned[0].env == spawned[1].env
    with pytest.raises(FrozenInstanceError):
        status.state = "unknown"
    await relay.stop()
    assert relay.reverse_status.state != "ready"
    assert retired == spawned


async def test_exhaustion_is_explicit_and_does_not_widen_the_candidate_set(monkeypatch):
    relay, spawned, retired, _ = fixture_owner(monkeypatch, ["occupied", "occupied"])
    with pytest.raises(ReverseForwardError) as error:
        await relay.establish()
    assert error.value.status.reason == "candidates-exhausted"
    assert len(spawned) == 2 and retired == spawned
    assert relay.reverse_status.state == "unavailable"


@pytest.mark.parametrize("answer", ["unknown", "refused", "occupied"])
async def test_live_process_or_foreign_listener_is_not_verified_readiness(monkeypatch, answer):
    relay, spawned, retired, _ = fixture_owner(monkeypatch, [answer])
    # Occupied evidence without an SSH-refused attempt is not a collision.
    async def live_settle(self, proc):
        return _SettleResult(True, "")
    monkeypatch.setattr(FiniteReverseForward, "_wait_settled", live_settle)
    with pytest.raises(ReverseForwardError):
        await relay.establish()
    assert len(spawned) == 1 and retired == spawned
    assert relay.reverse_status.state in ("unknown", "refused")
    if answer != "refused":
        with pytest.raises(ReverseForwardError):
            await relay.establish()
        assert len(spawned) == 1, "unknown outcome must not allocate again"


async def test_observer_exception_is_redacted_and_latched(monkeypatch):
    async def fail(_attempt):
        raise RuntimeError("private credential-shaped diagnostic")
    relay, spawned, retired, _ = fixture_owner(monkeypatch, ["ready"], observer=fail)
    with pytest.raises(ReverseForwardError, match="observer-outcome") as error:
        await relay.establish()
    assert "private" not in str(error.value)
    assert error.value.__suppress_context__
    assert retired == spawned
    assert await relay._restart_with_backoff("interruption") is False
    assert len(spawned) == 1


async def test_wrong_correlation_cannot_publish_success(monkeypatch):
    async def wrong(_attempt):
        return ReverseEvidence("different-attempt", "ready")
    relay, spawned, retired, _ = fixture_owner(monkeypatch, ["ready"], observer=wrong)
    with pytest.raises(ReverseForwardError, match="uncorrelated-evidence"):
        await relay.establish()
    assert retired == spawned


async def test_concurrent_establish_reports_busy_without_replacing_the_owned_process(monkeypatch):
    relay, spawned, _, _ = fixture_owner(monkeypatch, ["ready"])
    results = await asyncio.gather(
        relay.establish(), relay.establish(), relay.establish(), return_exceptions=True,
    )
    assert len(spawned) == 1
    assert sum(result is None for result in results) == 1
    assert all(result is None or result.status.reason == "lifecycle-busy" for result in results)
    spawned[0].returncode = 255
    assert relay.reverse_status.state == "unavailable"
    await relay.stop()


async def test_stop_during_observation_does_not_resurrect_readiness(monkeypatch):
    entered, finish = asyncio.Event(), asyncio.Event()
    async def observe(attempt):
        entered.set()
        await finish.wait()
        return ReverseEvidence(attempt.attempt_id, "ready")
    relay, spawned, _, _ = fixture_owner(monkeypatch, ["ready"], observer=observe)
    task = asyncio.create_task(relay.establish())
    await entered.wait()
    await relay.stop()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not relay.is_alive and relay.reverse_status.state == "unknown"
    assert len(spawned) == 1


async def test_cancelled_observer_reaps_owned_process_and_preserves_unknown(monkeypatch):
    entered = asyncio.Event()
    async def observe(_attempt):
        entered.set()
        await asyncio.Event().wait()
    relay, spawned, retired, _ = fixture_owner(monkeypatch, ["ready"], observer=observe)
    task = asyncio.create_task(relay.establish())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert retired == spawned
    assert relay.reverse_status.state == "unknown"
    await relay.stop()
    assert relay.reverse_status.state == "unknown"


async def test_observer_deadline_is_bounded_and_does_not_try_another_candidate(monkeypatch):
    async def observe(_attempt):
        await asyncio.Event().wait()
    relay, spawned, retired, _ = fixture_owner(
        monkeypatch, ["ready"], observer=observe, timeout=0.02,
    )
    with pytest.raises(ReverseForwardError, match="observer-outcome"):
        await relay.establish()
    assert retired == spawned and len(spawned) == 1


async def test_cancel_during_spawn_reaps_the_late_returned_owned_process(monkeypatch):
    relay, spawned, retired, _ = fixture_owner(monkeypatch, ["ready"])
    entered = asyncio.Event()
    release = asyncio.Event()
    async def slow_spawn(*_args, **_kwargs):
        proc = Process(123)
        spawned.append(proc)
        entered.set()
        await release.wait()
        return proc
    monkeypatch.setattr(candidate_mod, "create_ssh_subprocess", slow_spawn)
    task = asyncio.create_task(relay.establish())
    await entered.wait()
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert retired == spawned
    assert relay.reverse_status.state == "unknown"
    with pytest.raises(ReverseForwardError):
        await relay.establish()
    assert len(spawned) == 1


async def test_stop_during_spawn_prevents_late_observer_effect(monkeypatch):
    relay, spawned, retired, attempts = fixture_owner(monkeypatch, ["ready"])
    entered, release = asyncio.Event(), asyncio.Event()
    async def slow_spawn(*_args, **_kwargs):
        proc = Process(123)
        spawned.append(proc)
        entered.set()
        await release.wait()
        return proc
    monkeypatch.setattr(candidate_mod, "create_ssh_subprocess", slow_spawn)
    task = asyncio.create_task(relay.establish())
    await entered.wait()
    stopping = asyncio.create_task(relay.stop())
    await asyncio.sleep(0)
    release.set()
    await stopping
    with pytest.raises(asyncio.CancelledError):
        await task
    assert retired == spawned and not attempts
    assert not relay.is_alive


async def test_no_rotation_from_generic_ssh_exit_without_owner_collision_evidence(monkeypatch):
    relay, spawned, retired, _ = fixture_owner(monkeypatch, ["unknown"])
    async def fail(self, proc):
        proc.returncode = 255
        return _SettleResult(False, "remote port forwarding failed", True)
    monkeypatch.setattr(FiniteReverseForward, "_wait_settled", fail)
    with pytest.raises(ReverseForwardError):
        await relay.establish()
    assert relay.reverse_status.state == "unknown"
    assert len(spawned) == 1 and retired == spawned


async def test_target_port_is_stable_across_changed_reverse_endpoint_and_reconnect(monkeypatch):
    relay, spawned, _, attempts = fixture_owner(
        monkeypatch, ["occupied", "ready", "occupied", "ready"],
    )
    await relay.establish()
    assert await relay._restart_reason() is None
    spawned[-1].returncode = 255
    assert await relay._restart_with_backoff("interruption")
    assert relay.reverse_status.attempt.endpoint.port == 41002
    assert {attempt.target_port for attempt in attempts} == {41001}
    assert "127.0.0.1:41002:127.0.0.1:41001" in spawned[-1].args
    await relay.stop()


async def test_monitor_retirement_and_public_establish_cannot_lose_replacement(monkeypatch):
    relay, spawned, retired, _ = fixture_owner(monkeypatch, ["ready", "ready"])
    await relay.establish()
    old = spawned[0]
    old.returncode = 255
    entered, release = asyncio.Event(), asyncio.Event()
    async def retire(self, proc):
        if proc is old:
            entered.set()
            await release.wait()
        retired.append(proc)
        proc.returncode = -9
    monkeypatch.setattr(FiniteReverseForward, "_terminate", retire)
    restart = asyncio.create_task(relay._restart_with_backoff("exited"))
    await entered.wait()
    with pytest.raises(ReverseForwardError, match="lifecycle-busy"):
        await relay.establish()
    assert spawned == [old]
    release.set()
    assert await restart is True
    assert relay._proc is spawned[1] and spawned[1].returncode is None
    assert relay.reverse_status.attempt.process_pid == spawned[1].pid
    assert retired == [old]
    await relay.stop()
    assert retired == spawned


async def test_new_live_process_never_exposes_the_previous_ready_attempt(monkeypatch):
    relay, spawned, _, _ = fixture_owner(monkeypatch, ["ready", "ready"])
    await relay.establish()
    old_attempt = relay.reverse_status.attempt
    spawned[0].returncode = 255
    entered, release = asyncio.Event(), asyncio.Event()
    async def observe(attempt):
        entered.set()
        await release.wait()
        return ReverseEvidence(attempt.attempt_id, "ready")
    relay._observe_candidate = observe
    replacement = asyncio.create_task(relay.establish())
    await entered.wait()
    assert relay.is_alive
    assert relay.reverse_status.state != "ready"
    release.set()
    await replacement
    assert relay.reverse_status.attempt.attempt_id != old_attempt.attempt_id
    await relay.stop()


async def test_monitor_rechecks_gate_after_collision_and_never_restarts_stopped_venue(monkeypatch):
    relay, spawned, _, _ = fixture_owner(monkeypatch, ["ready", "occupied", "ready"])
    answers = [True, True, False]
    async def gate():
        return answers.pop(0)
    relay._reconnect_gate = gate
    await relay.establish()
    spawned[0].returncode = 255
    assert await relay._restart_with_backoff("exited") is False
    assert not answers and len(spawned) == 2
    assert relay.retired and not relay.is_alive
    await relay.stop()


async def test_inconclusive_candidate_gate_defers_before_any_further_spawn(monkeypatch):
    relay, spawned, _, _ = fixture_owner(monkeypatch, ["ready", "occupied", "ready"])
    answers = [True, True, RuntimeError("private gate diagnostic"), True, True]
    async def gate():
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer
    backoffs = []
    async def sleep(self, delay):
        backoffs.append((len(spawned), delay))
    relay._reconnect_gate = gate
    monkeypatch.setattr(FiniteReverseForward, "_sleep", sleep)
    await relay.establish()
    spawned[0].returncode = 255
    assert await relay._restart_with_backoff("exited") is True
    assert backoffs and backoffs[0][0] == 2
    assert len(spawned) == 3 and not answers
    await relay.stop()


async def test_failed_target_resolver_never_silently_forwards_to_a_candidate_port(monkeypatch):
    relay, spawned, _, _ = fixture_owner(monkeypatch, ["ready"])
    def fail():
        raise RuntimeError("private resolver diagnostic")
    relay._host_port_resolver = fail
    with pytest.raises(ReverseForwardError, match="target-port-unavailable"):
        await relay.establish()
    assert not spawned and relay.reverse_status.state == "refused"


async def test_cleanup_failure_is_redacted_and_prevents_another_allocation(monkeypatch):
    relay, spawned, _, _ = fixture_owner(monkeypatch, ["unknown"])
    async def fail(*_args):
        raise RuntimeError("private cleanup diagnostic")
    monkeypatch.setattr(FiniteReverseForward, "_terminate", fail)
    with pytest.raises(ReverseForwardError, match="cleanup-failed") as error:
        await relay.establish()
    assert "private" not in str(error.value)
    assert relay.reverse_status.state == "unknown"
    with pytest.raises(ReverseForwardError):
        await relay.establish()
    assert len(spawned) == 1


async def test_late_attempt_release_cannot_stop_reconnected_successor(monkeypatch):
    relay, spawned, _, _ = fixture_owner(monkeypatch, ["ready", "ready"])
    await relay.establish()
    old_attempt = relay.reverse_status.attempt
    spawned[0].returncode = 255
    assert await relay._restart_with_backoff("interruption")
    current = relay.reverse_status.attempt
    assert current.attempt_id != old_attempt.attempt_id
    assert await relay.stop_attempt(old_attempt.attempt_id) is None
    assert relay.is_alive and relay.reverse_status.attempt == current
    result = await relay.stop_attempt(current.attempt_id)
    assert result.state == "stopped" and not relay.is_alive


async def test_concurrent_start_cannot_reopen_owner_while_stop_retires_transport(monkeypatch):
    relay, spawned, _, _ = fixture_owner(monkeypatch, ["ready"])
    await relay.establish()
    entered, release = asyncio.Event(), asyncio.Event()
    async def retire(self, proc):
        entered.set()
        await release.wait()
        proc.returncode = -9
    monkeypatch.setattr(FiniteReverseForward, "_terminate", retire)
    stopping = asyncio.create_task(relay.stop())
    await entered.wait()
    with pytest.raises(ReverseForwardError):
        await relay.start()
    assert relay._stopped
    release.set()
    await stopping
    assert len(spawned) == 1 and relay._stopped and not relay.is_alive


async def test_attempt_cleanup_does_not_settle_an_unknown_observation(monkeypatch):
    relay, _, _, _ = fixture_owner(monkeypatch, ["unknown"])
    with pytest.raises(ReverseForwardError):
        await relay.establish()
    attempt = relay.reverse_status.attempt
    result = await relay.stop_attempt(attempt.attempt_id)
    assert result.state == "unknown"


async def test_interrupted_refusal_cleanup_holds_admission_and_retains_exact_process(monkeypatch):
    relay, spawned, retired, _ = fixture_owner(monkeypatch, ["refused", "ready"])
    entered, release = asyncio.Event(), asyncio.Event()
    async def retire(self, proc):
        entered.set()
        await release.wait()
        retired.append(proc)
        proc.returncode = -9
    monkeypatch.setattr(FiniteReverseForward, "_terminate", retire)
    establishing = asyncio.create_task(relay.establish())
    await entered.wait()
    stopping = asyncio.create_task(relay.stop())
    await asyncio.sleep(0)
    with pytest.raises(ReverseForwardError):
        await relay.start()
    assert len(spawned) == 1
    release.set()
    await stopping
    with pytest.raises(asyncio.CancelledError):
        await establishing
    assert retired == spawned and relay._proc is None
    assert relay.reverse_status.state == "unknown"
    with pytest.raises(ReverseForwardError):
        await relay.start()
    assert len(spawned) == 1


async def test_nowait_cleanup_cannot_sweep_a_replacement_before_it_finishes(monkeypatch):
    relay, spawned, retired, _ = fixture_owner(monkeypatch, ["ready", "ready"])
    await relay.establish()
    old = spawned[0]
    entered, release = asyncio.Event(), asyncio.Event()
    async def retire(self, proc):
        if proc is old:
            entered.set()
            await release.wait()
        retired.append(proc)
        proc.returncode = -9
    def kill(self, proc):
        proc.returncode = -9
    monkeypatch.setattr(FiniteReverseForward, "_terminate", retire)
    monkeypatch.setattr(FiniteReverseForward, "_kill_tree_nowait", kill)
    relay.stop_nowait()
    await entered.wait()
    with pytest.raises(ReverseForwardError, match="cleanup-pending"):
        await relay.start()
    assert len(spawned) == 1
    release.set()
    await asyncio.gather(*tuple(relay._cleanup_tasks))
    await relay.start()
    assert len(spawned) == 2 and relay._proc is spawned[1]
    assert retired == [old] and spawned[1].returncode is None
    await relay.stop()


async def test_failed_late_spawn_cleanup_keeps_process_for_explicit_retry(monkeypatch):
    relay, spawned, retired, _ = fixture_owner(monkeypatch, ["ready"])
    entered, release = asyncio.Event(), asyncio.Event()
    async def late(*_args, **_kwargs):
        proc = Process(123)
        spawned.append(proc)
        entered.set()
        await release.wait()
        return proc
    calls = []
    async def retire(self, proc):
        calls.append(proc)
        if len(calls) == 1:
            raise OSError("private cleanup detail")
        proc.returncode = -9
        retired.append(proc)
    monkeypatch.setattr(candidate_mod, "create_ssh_subprocess", late)
    monkeypatch.setattr(FiniteReverseForward, "_terminate", retire)
    task = asyncio.create_task(relay.establish())
    await entered.wait()
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert relay._proc is spawned[0] and spawned[0].returncode is None
    await relay.stop()
    assert calls == [spawned[0], spawned[0]]
    assert retired == spawned and relay._proc is None
    assert relay.reverse_status.state == "unknown"


@pytest.mark.parametrize("conditional", [False, True])
async def test_concurrent_stops_join_shutdown_without_cancelling_the_first_caller(
    monkeypatch, conditional,
):
    relay, spawned, retired, _ = fixture_owner(monkeypatch, ["ready", "ready"])
    await relay.establish()
    attempt_id = relay.reverse_status.attempt.attempt_id
    entered, release = asyncio.Event(), asyncio.Event()
    async def retire(self, proc):
        entered.set()
        await release.wait()
        proc.returncode = -9
        retired.append(proc)
    monkeypatch.setattr(FiniteReverseForward, "_terminate", retire)
    first = asyncio.create_task(
        relay.stop_attempt(attempt_id) if conditional else relay.stop(),
    )
    await entered.wait()
    second = asyncio.create_task(relay.stop())
    await asyncio.sleep(0)
    relay.stop_nowait()  # cooperates with the same in-flight shutdown
    assert not first.done() and relay.reverse_status.state != "unknown"
    release.set()
    result, other = await asyncio.gather(first, second)
    assert result is None or result.state == "stopped"
    assert other is None and retired == spawned
    assert relay.reverse_status.state == "stopped"
    await relay.start()
    assert len(spawned) == 2 and relay.is_alive
    await relay.stop()

@pytest.mark.parametrize("host,port", [
    ("0.0.0.0", 1234), ("example.com", 1234), ("127.0.0.1", 0),
    ("localhost", True), ("::1", 65536),
])
def test_invalid_candidate_refuses_before_spawn(host, port):
    with pytest.raises(ValueError):
        ReverseEndpoint(host, port)


def test_candidates_and_observer_must_be_explicit_and_bounded():
    candidate = ReverseEndpoint("127.0.0.1", 41001)
    oversized = tuple(ReverseEndpoint("localhost", 41000 + i) for i in range(17))
    for candidates in ((), (candidate, candidate), [candidate], oversized):
        with pytest.raises(ValueError):
            FiniteReverseForward(
                SSHConfig(host_alias="fixture"), candidates, request_id="op", observe=lambda _: None,
            )
    with pytest.raises(ValueError):
        FiniteReverseForward(
            SSHConfig(host_alias="fixture"), (candidate,), request_id="op", observe=None,
        )


def test_nowait_stop_without_an_event_loop_is_explicit_and_inert_before_spawn():
    relay = FiniteReverseForward(
        SSHConfig(host_alias="fixture"), (ReverseEndpoint("localhost", 41001),),
        request_id="op", observe=lambda _: None,
    )
    relay.stop_nowait()
    assert relay._stopped and relay._proc is None


@pytest.mark.parametrize("reason", ["authentication", "host-key", "permission", "transport"])
async def test_refusal_keeps_bounded_owner_failure_class_without_rotating(monkeypatch, reason):
    async def refuse(attempt):
        return ReverseEvidence(attempt.attempt_id, "refused", reason)
    relay, spawned, retired, _ = fixture_owner(monkeypatch, ["ready"], observer=refuse)
    with pytest.raises(ReverseForwardError) as error:
        await relay.establish()
    assert error.value.status.reason == reason
    assert error.value.status.state == "refused"
    assert len(spawned) == 1 and retired == spawned


@pytest.mark.parametrize("request_id,timeout", [
    ("", 1), ("bad\nid", 1), ("a" * 129, 1), ("op", 0), ("op", float("nan")),
    ("op", True), ("op", 301),
])
def test_operation_bounds_are_not_silently_defaulted(request_id, timeout):
    with pytest.raises(ValueError):
        FiniteReverseForward(
            SSHConfig(host_alias="fixture"), (ReverseEndpoint("localhost", 41001),),
            request_id=request_id, observe=lambda _: None, ready_timeout=timeout,
        )


async def test_fixed_forward_baseline_retries_the_occupied_tuple_only(monkeypatch):
    import ssh_manager.relay_channel as legacy
    calls = []
    async def spawn(*args, **_kwargs):
        calls.append(args)
        proc = Process(100 + len(calls))
        proc.returncode = 255
        return proc
    async def settle(self, proc):
        return _SettleResult(False, "remote port forwarding failed", True)
    async def nothing(*_args):
        return None
    monkeypatch.setattr(legacy, "create_ssh_subprocess", spawn)
    monkeypatch.setattr(SupervisedRelayForward, "_wait_settled", settle)
    monkeypatch.setattr(SupervisedRelayForward, "_terminate", nothing)
    monkeypatch.setattr(SupervisedRelayForward, "_sleep", nothing)
    relay = SupervisedRelayForward(SSHConfig(host_alias="fixture"), 41001)
    with pytest.raises(ConnectionError):
        await relay.establish()
    assert len(calls) == 4
    assert all("41001:127.0.0.1:41001" in args for args in calls)
