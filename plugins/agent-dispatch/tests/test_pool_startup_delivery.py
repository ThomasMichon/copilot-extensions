"""Recorded subprocess boundaries never substitute ACP, queue or blind replay."""

import json
import subprocess

import pytest

from agent_dispatch import pool_startup_delivery as delivery
from agent_dispatch.pool_startup_retry_contract import retry_invocation


def invocation():
    return retry_invocation({
        "version": 1, "task_id": "task", "reservation_key": "key", "generation": 1,
        "startup_revision": 1, "native_id": "native", "worktree": "workspace",
        "machine": "host-a", "reserved_by": "allocator",
    }, 2)


def envelope(facts, receipt):
    return {
        "protocol": "live-keyed-receipt/v1", "version": 1,
        "binding": {
            "version": 1, "expected_session_id": facts["native_id"],
            "idempotency_key": facts["idempotency_key"],
            "sender": f"agent-dispatch:{facts['idempotency_key']}", "body": facts["prompt"],
        },
        "receipt": receipt,
    }


def receipt(**changes):
    return {"session_id": "native", "message_id": 1, "stage": "admitted", "admitted_at": 100, **changes}


@pytest.mark.parametrize("observe_only", [True, False])
def test_receipt_only_recovers_original_admission_without_a_send(monkeypatch, observe_only):
    facts, calls = invocation(), []
    monkeypatch.setattr(delivery, "_command_result", lambda argv, *args, **kwargs:
                        calls.append((argv, kwargs)) or subprocess.CompletedProcess(
                            argv, 0, json.dumps(envelope(facts, receipt())), ""))
    assert delivery.deliver_retry(facts, observe_only=observe_only)[0] == "startup_succeeded"
    assert len(calls) == 1 and "--live-receipt-only" in calls[0][0]
    assert calls[0][1]["machine"] == "host-a"


@pytest.mark.parametrize("observed", [None, {}, receipt(session_id="other"), receipt(message_id=True)])
def test_unproven_pending_receipt_never_authorizes_send_or_native_replacement(monkeypatch, observed):
    facts, calls = invocation(), []
    monkeypatch.setattr(delivery, "_command_result", lambda argv, *args, **kwargs:
                        calls.append(argv) or subprocess.CompletedProcess(
                            argv, 0, json.dumps(envelope(facts, observed)), ""))
    assert delivery.deliver_retry(facts, observe_only=True)[0] == "startup_unknown"
    assert len(calls) == 1


def test_confirmed_new_invocation_uses_distinct_exact_cli_send_not_legacy_or_queue(monkeypatch):
    facts, calls = invocation(), []

    def run(argv, *args, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(
            argv, 0, json.dumps(envelope(facts, None if len(calls) == 1 else receipt())), "",
        )

    monkeypatch.setattr(delivery, "_command_result", run)
    assert delivery.deliver_retry(facts, observe_only=False)[0] == "startup_succeeded"
    assert len(calls) == 2 and "--live-exact" in calls[1]
    for argv in calls:
        assert "--queue" not in argv and "--force" not in argv and "--expected-native-session-id" not in argv
        assert argv[argv.index("send") + 1] == "native"
        assert argv[argv.index("--expected-session-id") + 1] == "native"
