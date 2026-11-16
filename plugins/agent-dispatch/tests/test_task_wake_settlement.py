"""Explicit provider settlement preserves the original episode and passive default."""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from agent_dispatch.bridge_command_delivery import NativePromptNotSent
from agent_dispatch.bridge_native_settlement import (
    native_prompt_settlement,
    original_request_digest,
)
from agent_dispatch.coordinator import create_app
from agent_dispatch.procutil import no_window_kwargs
from agent_dispatch.queue_execution_wakes import wake_table
from agent_dispatch.queue_records import TaskError
from agent_dispatch.task_wake_recovery import reconcile_wake
from tests import test_task_wake_recovery as recovery
from tests.test_native_owner_wake import BRIDGE, NATIVE
from tests.test_native_owner_wake import hosted as hosted
from tests.test_task_scope_auth import CONTROL_TOKEN, TOKEN, _auth
from tests.test_task_wake_recovery import retained as retained

current = cast(Callable[..., Any], recovery.current)
receipt = cast(Callable[..., dict[str, Any]], recovery.receipt)


def negative(wake: Any) -> dict[str, Any]:
    return {
        "version": 1, "session_id": BRIDGE, "native_session_id": NATIVE,
        "invocation_key": wake.id, "queued": False, "admission": "not_admitted",
        "outcome": "not_admitted", "fenced": True,
        "request_digest": original_request_digest(
            BRIDGE, NATIVE, "original prompt", idempotency_key=wake.id,
        ),
    }


def settle(queue: Any, task_id: str, wake: Any, observe: Any) -> dict[str, Any]:
    return reconcile_wake(
        queue, task_id, outbox=wake_table(wake), wake_id=wake.id,
        expected_revision=queue.task_diagnostics(task_id)["revision"],
        actor="operator", reason="settle original key", settle_native_send=True, observe=observe,
    )


@pytest.mark.parametrize("accepted", [False, True])
def test_settlement_preserves_episode_without_replay(retained: Any, accepted: bool) -> None:
    queue, task_id, wake = retained
    original_task = asdict(queue.get(task_id))
    original_reservation = asdict(queue.latest_reservation(task_id))
    original_wake = asdict(wake)
    outcome = {**receipt(wake), "outcome": "accepted"} if accepted else negative(wake)
    calls = []

    def observe(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append((args, kwargs))
        return outcome

    result = settle(queue, task_id, wake, observe)
    assert result["outcome"] == ("sent" if accepted else "not_sent")
    assert calls == [((BRIDGE, NATIVE, "original prompt"), {
        "idempotency_key": wake.id, "machine": "host-a",
    })]
    after = asdict(current(queue, task_id, wake))
    for field in ("execution_send_state", "execution_send_detail"):
        after.pop(field)
        original_wake.pop(field)
    if not accepted:
        original_wake["status"] = "failed"
        if wake_table(wake) == "wake_outbox":
            original_task["wake_status"] = "failed"
    assert after == original_wake
    assert asdict(queue.get(task_id)) == original_task
    assert asdict(queue.latest_reservation(task_id)) == original_reservation
    assert settle(queue, task_id, wake, observe)["replayed"]
    assert queue.claim_due_wake() is None
    assert queue.claim_due_run_waiter_wake() is None
    # A provider fence is not the retryable busy/not-attempted receipt.
    with queue._connect() as conn:
        from agent_dispatch.queue_execution_wakes import _known_native_retry
        row = conn.execute(
            f"SELECT * FROM {wake_table(wake)} WHERE id=?", (wake.id,),  # noqa: S608
        ).fetchone()
        assert not _known_native_retry(row, wake, queue.get(task_id))


def test_fenced_negative_stays_terminal_after_lease_expiry(retained: Any) -> None:
        queue, task_id, wake = retained
        settle(queue, task_id, wake, lambda *args, **kwargs: negative(wake))
        after_settlement = asdict(current(queue, task_id, wake))
        future = wake.delivery_expires_at + 1
        assert queue.recover_inflight_wakes(now=future) == 0
        assert queue.recover_inflight_run_waiter_wakes(now=future) == 0
        assert queue.claim_due_wake(now=future) is None
        assert queue.claim_due_run_waiter_wake(now=future) is None
        finish = (
            queue.finish_wake if wake_table(wake) == "wake_outbox"
            else queue.finish_run_waiter_wake
        )
        with pytest.raises(TaskError, match="not held by this delivery"):
            finish(wake.id, wake.delivery_token, delivered=False, now=future)
        assert asdict(current(queue, task_id, wake)) == after_settlement
        assert settle(queue, task_id, wake, lambda *args, **kwargs: negative(wake))["replayed"]


@pytest.mark.parametrize("reply_lost", [False, True])
def test_retryable_not_sent_refuses_before_any_provider_fence(
        retained: Any, reply_lost: bool,
) -> None:
        queue, task_id, wake = retained
        detail = json.dumps(NativePromptNotSent(BRIDGE, NATIVE, wake.id).receipt)
        with queue._connect() as conn:
            conn.execute(
                f"UPDATE {wake_table(wake)} SET execution_send_state='not_sent',"  # noqa: S608
                " execution_send_detail=? WHERE id=?",
                (detail, wake.id),
            )
        before = asdict(current(queue, task_id, wake))

        def provider(*args: Any, **kwargs: Any) -> dict[str, Any]:
            if reply_lost:
                raise TaskError("synthetic acknowledgement lost after fence")
            pytest.fail("retryable refusal must not reach provider settlement")

        with pytest.raises(TaskError, match="lacks retained fenced-settlement"):
            settle(queue, task_id, wake, provider)
        assert asdict(current(queue, task_id, wake)) == before


@pytest.mark.parametrize("change", [
    {"fenced": False}, {"request_digest": "other-request"}, {"turn_index": 0},
    {"native_session_id": "other-native"}, {"session_id": "other-bridge"},
    {"invocation_key": "other-key"}, {"queued": True}, {"version": True},
    {"outcome": "accepted"}, {"admission": "unknown"}, {"admission": None, "outcome": None},
])
def test_unqualified_negative_preserves_unknown(retained: Any, change: dict[str, Any]) -> None:
    queue, task_id, wake = retained
    with pytest.raises(TaskError, match="does not prove"):
        settle(queue, task_id, wake, lambda *a, **k: {**negative(wake), **change})
    assert current(queue, task_id, wake).execution_send_state == "unknown"


def test_passive_observation_cannot_consume_a_negative(retained: Any) -> None:
    queue, task_id, wake = retained
    with pytest.raises(TaskError, match="does not prove"):
        reconcile_wake(
            queue, task_id, outbox=wake_table(wake), wake_id=wake.id,
            expected_revision=queue.task_diagnostics(task_id)["revision"],
            actor="operator", reason="observe", observe=lambda *a, **k: negative(wake),
        )
    assert current(queue, task_id, wake).execution_send_state == "unknown"


@pytest.mark.parametrize("mutation", ["task", "outbox"])
def test_provider_effect_does_not_override_concurrent_queue_change(
    retained: Any, mutation: str,
) -> None:
    queue, task_id, wake = retained

    def observe(*args: Any, **kwargs: Any) -> dict[str, Any]:
        if mutation == "task":
            queue.detach(task_id)
        else:
            subprocess.run([  # noqa: S603 - fixed fixture program and isolated queue database
                sys.executable, "-c",
                "import sqlite3,sys; "
                "c=sqlite3.connect(sys.argv[1]); "
                "c.execute('UPDATE '+sys.argv[2]+' SET attempts=attempts+1 WHERE id=?',"
                "(sys.argv[3],)); "
                "c.commit()",
                queue.db_path, wake_table(wake), wake.id,
            ], check=True, capture_output=True, timeout=10, **no_window_kwargs())
        return negative(wake)

    with pytest.raises(TaskError, match="changed during"):
        settle(queue, task_id, wake, observe)
    assert current(queue, task_id, wake).execution_send_state == "unknown"


def test_control_endpoint_is_explicit_and_never_accepts_an_outcome(
    retained: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue, task_id, wake = retained
    calls = []
    def observe(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append((args, kwargs))
        return negative(wake)

    monkeypatch.setattr("agent_dispatch.task_wake_recovery.native_prompt_settlement", observe)
    body = {
        "outbox": wake_table(wake), "wake_id": wake.id,
        "expected_revision": queue.task_diagnostics(task_id)["revision"],
        "actor": "operator", "reason": "explicit recovery", "settle_native_send": True,
    }
    with TestClient(create_app(queue, token=TOKEN, control_token=CONTROL_TOKEN)) as api:
        path = f"/tasks/{task_id}/wakes/reconcile"
        assert api.post(path, json=body, headers=_auth(TOKEN)).status_code == 403
        assert api.post(
            path, json={**body, "outcome": "not_sent"}, headers=_auth(CONTROL_TOKEN),
        ).status_code == 422
        assert api.post(
            path, json={**body, "settle_native_send": "true"}, headers=_auth(CONTROL_TOKEN),
        ).status_code == 422
        assert not calls
        result = api.post(path, json=body, headers=_auth(CONTROL_TOKEN))
        assert result.status_code == 200, result.text
        assert result.json()["outcome"] == "not_sent"
    assert len(calls) == 1


@pytest.mark.parametrize("support", [None, {}, {"version": True}, {
    "schema": "agent-bridge.cli-contracts", "version": 1, "contracts": {
        "agent-bridge.send": {"version": 1, "capabilities": []},
    },
}])
def test_old_cli_refuses_before_new_syntax_or_private_prompt(
    hosted: Any, monkeypatch: pytest.MonkeyPatch, support: Any,
) -> None:
    calls = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, json.dumps(support), "")

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(TaskError, match="lacks native-send-settlement"):
        native_prompt_settlement(
            BRIDGE, NATIVE, "private", idempotency_key="key", machine="host-a",
        )
    assert len(calls) == 1
    assert calls[0][0][-2:] == ["version", "--contracts-json"]
    assert calls[0][1]["input"] == ""


@pytest.mark.parametrize("fault", ["lost", "malformed", "negative", "accepted"])
def test_selected_transport_has_no_fallback_or_automatic_retry(
    retained: Any, monkeypatch: pytest.MonkeyPatch, fault: str,
) -> None:
    queue, task_id, wake = retained
    calls = []
    support = {"schema": "agent-bridge.cli-contracts", "version": 1, "contracts": {
        "agent-bridge.send": {"version": 1, "capabilities": ["native-send-settlement"]},
    }}

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs))
        if argv[-1] == "--contracts-json":
            return subprocess.CompletedProcess(argv, 0, json.dumps(support), "")
        result = (
            {**receipt(wake), "outcome": "accepted"} if fault == "accepted" else negative(wake)
        )
        return subprocess.CompletedProcess(
            argv, 70 if fault == "lost" else 0,
            "not json" if fault == "malformed" else json.dumps(result), "",
        )

    monkeypatch.setattr(subprocess, "run", run)
    if fault in {"lost", "malformed"}:
        with pytest.raises(TaskError, match="remains unknown"):
            settle(queue, task_id, wake, None)
        assert current(queue, task_id, wake).execution_send_state == "unknown"
    else:
        assert settle(queue, task_id, wake, None)["outcome"] == (
            "sent" if fault == "accepted" else "not_sent"
        )
    assert len(calls) == 2
    argv, options = calls[-1]
    assert "--settle-native-send" in argv and "--receipt-only" not in argv
    assert options["input"] == "original prompt"
    assert "original prompt" not in json.dumps(argv)


@pytest.mark.parametrize("old_cli", [False, True])
def test_real_cli_process_boundary_and_lost_settlement_response(
    retained: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, old_cli: bool,
) -> None:
    """Exercise literal stdin/argv and independent result custody, not native execution."""
    from agent_dispatch import bridge

    queue, task_id, wake = retained
    script = tmp_path / "bridge_fixture.py"
    response_file = tmp_path / "response.json"
    response_file.write_text(json.dumps(negative(wake)), encoding="utf-8")
    contract_source = (
        Path(__file__).resolve().parents[2]
        / "agent-bridge/src/agent_bridge/resume_cli_contract.py"
    )
    script.write_text(
        "import argparse,json,pathlib,runpy,sys\n"
        "root=pathlib.Path(__file__).parent\n"
        f"old={old_cli!r}\n"
        "if sys.argv[1:] == ['version','--contracts-json']:\n"
        f"    value=runpy.run_path({str(contract_source)!r})['cli_contracts']()\n"
        "    if old: value['contracts']['agent-bridge.send']['capabilities']=[]\n"
        "    print(json.dumps(value)); sys.exit(0)\n"
        "parser=argparse.ArgumentParser(allow_abbrev=False)\n"
        "parser.add_argument('--json',action='store_true',required=True)\n"
        "parser.add_argument('command',choices=['send'])\n"
        "parser.add_argument('target')\n"
        "for key in ['expected-native-session-id','idempotency-key','caller','prompt-file']:\n"
        "    parser.add_argument('--'+key,required=True)\n"
        "parser.add_argument('--settle-native-send',action='store_true',required=True)\n"
        "parser.add_argument('--no-wait',action='store_true',required=True)\n"
        "args=parser.parse_args()\n"
        "prompt=sys.stdin.read()\n"
        "assert args.prompt_file == '-'\n"
        "request={**vars(args),'prompt':prompt}\n"
        "with (root/'calls.jsonl').open('a',encoding='utf-8') as stream:\n"
        "    stream.write(json.dumps(request)+'\\n')\n"
        "saved=root/'settled.json'\n"
        "if not saved.exists():\n"
        "    saved.write_text((root/'response.json').read_text(encoding='utf-8'),"
        "encoding='utf-8')\n"
        "    sys.exit(70)\n"
        "print(saved.read_text(encoding='utf-8'))\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        bridge, "_agent_bridge_launch_prefix", lambda: [sys.executable, str(script)],
    )
    if old_cli:
        with pytest.raises(TaskError, match="lacks native-send-settlement"):
            settle(queue, task_id, wake, None)
        assert not (tmp_path / "calls.jsonl").exists()
        return
    with pytest.raises(TaskError, match="remains unknown"):
        settle(queue, task_id, wake, None)
    assert current(queue, task_id, wake).execution_send_state == "unknown"
    assert len((tmp_path / "calls.jsonl").read_text(encoding="utf-8").splitlines()) == 1
    assert settle(queue, task_id, wake, None)["outcome"] == "not_sent"
    calls = [
        json.loads(line)
        for line in (tmp_path / "calls.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(calls) == 2 and calls[0] == calls[1]
    assert calls[0]["prompt"] == "original prompt"
    assert calls[0]["target"] == BRIDGE and calls[0]["expected_native_session_id"] == NATIVE
    assert calls[0]["idempotency_key"] == wake.id
    assert calls[0]["caller"] == f"agent-dispatch:{wake.id}"


def test_original_request_digest_matches_the_bridge_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = ModuleType("settlement_owner_fixture")
    package.__path__ = [str(
        Path(__file__).resolve().parents[2] / "agent-bridge/src/agent_bridge"
    )]
    monkeypatch.setitem(sys.modules, package.__name__, package)
    owner = importlib.import_module(f"{package.__name__}.native_send_settlement")
    try:
        request = owner.NativeSendPrompt(
            invocation_key="original", expected_native_session_id="native",
            prompt="original \u03bb prompt", caller_id="agent-dispatch:original",
        )
        assert original_request_digest(
            "bridge", "native", request.prompt, idempotency_key="original",
        ) == owner.request_digest("bridge", request)
    finally:
        for name in (f"{package.__name__}.native_send_settlement",
                     f"{package.__name__}.native_send_admission"):
            sys.modules.pop(name, None)
