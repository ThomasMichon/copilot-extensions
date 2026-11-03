"""Fenced ready intent; no ACP/network/provider is mocked into success."""

from dataclasses import asdict
import json
import subprocess
from types import SimpleNamespace

import pytest

from agent_dispatch.ordinary_agent_storage import record_created_agent
from agent_dispatch.ordinary_invocation import prepare
from agent_dispatch.queue import TaskQueue, TaskError
from tests._hosting_owner import admission_owner as admission_owner


@pytest.fixture
def intent(tmp_path):
    queue = TaskQueue(tmp_path / "queue.db")
    task = queue.create(title="fixed work", repo="fixture", require_verification=True)
    reservation, _ = queue.reserve_spawn(task.id, driver="ordinary-bridge")
    info = {
        "session_id": "bridge-a", "ready": True, "status": "awaiting_first_prompt",
        "connection": {"worktree_id": "worktree-a", "repo": "fixture",
                       "native_session_id": "native-a", "target_dir": "/fixture/worktree-a"},
        "creation": {"worktree": {"creation_nonce": "original-nonce"}},
    }
    prompt = 'Unicode café "quoted"\n第二行'
    invocation = prepare(asdict(task), asdict(reservation), info, prompt)
    return queue, task, reservation, info, prompt, invocation


def test_ready_and_frozen_invocation_commit_and_read_back_together(intent):
    queue, task, reservation, info, _, invocation = intent
    written = record_created_agent(queue, reservation.key, task_generation=task.generation,
                                   agent_info=info, startup_invocation=invocation)
    assert written.agent_info["startup_invocation"] == invocation
    assert queue.get_reservation(reservation.key).agent_info == written.agent_info


def test_trace_distinguishes_local_bytes_from_sorted_retained_values(intent, tmp_path, monkeypatch):
    import hashlib
    from agent_dispatch import launch_debug
    from agent_dispatch.ordinary_invocation import native_payload

    queue, task, reservation, info, _, invocation = intent
    record_created_agent(queue, reservation.key, task_generation=task.generation,
                         agent_info=info, startup_invocation=invocation)
    retained = queue.get_reservation(reservation.key).agent_info["startup_invocation"]
    initial = json.dumps(native_payload(invocation), ensure_ascii=False)
    reconstructed = json.dumps(native_payload(retained), ensure_ascii=False)
    assert json.loads(initial) == json.loads(reconstructed)
    assert len(initial.encode("utf-8")) == len(reconstructed.encode("utf-8"))
    assert hashlib.sha256(initial.encode("utf-8")).hexdigest() != hashlib.sha256(
        reconstructed.encode("utf-8"),
    ).hexdigest()

    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path / "trace-install"))
    monkeypatch.setenv("AGENT_DISPATCH_BRIDGE_CLI_TRACE", "1")
    trace = launch_debug.begin(["bridge"], ["--json", "send", "bridge-a"],
                               prompt_input=initial, input_reference=reservation.key)
    assert trace
    stdin = json.loads(trace.path.read_text().splitlines()[0])["stdin"]
    assert stdin["sha256"] == hashlib.sha256(initial.encode("utf-8")).hexdigest()
    canonical = json.dumps(json.loads(reconstructed), sort_keys=True,
                           separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    assert stdin["json_sha256"] == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert stdin["json_status"] == "valid"
    assert invocation["prompt"] not in trace.path.read_text()


def _launch_debug_recipe(call, reservation):
    import re
    from pathlib import Path

    document = Path(__file__).resolve().parents[1] / "docs" / "launch-debugging.md"
    recipe, = re.findall(r"```python\n(.*?)```", document.read_text(encoding="utf-8"), re.S)
    namespace = {"call": call, "reservation": reservation}
    exec(compile(recipe, str(document), "exec"), namespace)
    return namespace


@pytest.mark.parametrize("retained_readback", [False, True], ids=["old-byte-exact", "sorted-value-exact"])
def test_documented_stdin_recipe_with_real_child_and_retained_readback(
    intent, tmp_path, monkeypatch, capsys, retained_readback,
):
    import sys
    from agent_dispatch import launch_debug
    from agent_dispatch.ordinary_invocation import native_payload
    from agent_dispatch.procutil import no_window_kwargs

    queue, task, reservation, info, _, invocation = intent
    record_created_agent(queue, reservation.key, task_generation=task.generation,
                         agent_info=info, startup_invocation=invocation)
    retained = asdict(queue.get_reservation(reservation.key))
    if not retained_readback:
        retained["agent_info"]["startup_invocation"] = invocation
    original = json.dumps(native_payload(invocation), ensure_ascii=False)
    child = tmp_path / "bounded_json_consumer.py"
    child.write_text(
        "import json,sys\nvalue=json.load(sys.stdin)\nprint(json.dumps({'ok':value['version']==1}))\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path / "trace-install"))
    monkeypatch.setenv("AGENT_DISPATCH_BRIDGE_CLI_TRACE", "1")
    prefix, argv = [sys.executable], [str(child)]
    trace = launch_debug.begin(prefix, argv, prompt_input=original, timeout=5)
    assert trace
    result = subprocess.run(
        [*prefix, *argv], input=original, capture_output=True, text=True,
        encoding="utf-8", timeout=5, check=False, **no_window_kwargs(),
    )
    trace.finish(result=result)
    call = json.loads(trace.path.read_text().splitlines()[0])
    if not retained_readback:
        # Old traces can still prove byte identity without an additive field.
        call["stdin"].pop("json_status")
        call["stdin"].pop("json_sha256")
    consumed = _launch_debug_recipe(call, retained)
    assert consumed["result"].returncode == result.returncode == 0
    assert json.loads(consumed["result"].stdout) == {"ok": True}
    assert json.loads(consumed["payload"]) == json.loads(original)
    expected = "JSON-value-exact (not byte-exact)" if retained_readback else "byte-exact"
    assert capsys.readouterr().out.strip() == expected
    assert (consumed["payload"] != original) is retained_readback
    assert "Unicode caf" not in trace.path.read_text()


@pytest.mark.parametrize("change", ["old-trace", "changed-value"])
def test_documented_stdin_recipe_never_waives_missing_or_mismatched_evidence(
    intent, tmp_path, monkeypatch, change,
):
    from agent_dispatch import launch_debug
    from agent_dispatch.ordinary_invocation import native_payload

    queue, task, reservation, info, _, invocation = intent
    record_created_agent(queue, reservation.key, task_generation=task.generation,
                         agent_info=info, startup_invocation=invocation)
    retained = asdict(queue.get_reservation(reservation.key))
    original = json.dumps(native_payload(invocation), ensure_ascii=False)
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path / "trace-install"))
    monkeypatch.setenv("AGENT_DISPATCH_BRIDGE_CLI_TRACE", "1")
    trace = launch_debug.begin(["must-not-run"], [], prompt_input=original)
    assert trace
    call = json.loads(trace.path.read_text().splitlines()[0])
    if change == "old-trace":
        call["stdin"].pop("json_status")
        call["stdin"].pop("json_sha256")
        message = "old/invalid trace"
    else:
        retained["agent_info"]["startup_invocation"]["prompt"] += " changed"
        message = "JSON values differ"
    with pytest.raises(AssertionError, match=message):
        _launch_debug_recipe(call, retained)


@pytest.mark.parametrize("payload,status", [
    (None, "absent"), ("", "invalid"), ("not JSON", "invalid"), ("{", "invalid"),
    ('{"value": NaN}', "invalid"), ('{"value": "\\ud800"}', "invalid"),
    ("null", "valid"), ('{"value": false}', "valid"),
])
def test_stdin_json_digest_availability_is_explicit_and_nonblocking(tmp_path, monkeypatch, payload, status):
    from agent_dispatch import launch_debug

    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path / "trace-install"))
    monkeypatch.setenv("AGENT_DISPATCH_BRIDGE_CLI_TRACE", "1")
    trace = launch_debug.begin(["bridge"], ["status"], prompt_input=payload)
    assert trace
    stdin = json.loads(trace.path.read_text().splitlines()[0])["stdin"]
    assert stdin["json_status"] == status
    assert (stdin["json_sha256"] is not None) is (status == "valid")
    result = subprocess.CompletedProcess([], 19, "unchanged", "")
    trace.finish(result=result)
    assert result.returncode == 19 and result.stdout == "unchanged"


def test_json_digest_does_not_log_sensitive_input_and_default_off_does_not_parse(tmp_path, monkeypatch):
    from agent_dispatch import launch_debug

    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path / "trace-install"))
    monkeypatch.setenv("AGENT_DISPATCH_BRIDGE_CLI_TRACE", "1")
    payload = '{"prompt":"private-synthetic-assignment","token":"synthetic-auth-value"}'
    trace = launch_debug.begin(["bridge"], ["send", "actor"], prompt_input=payload)
    assert trace
    text = trace.path.read_text()
    assert json.loads(text)["stdin"]["json_status"] == "valid"
    assert "private-synthetic-assignment" not in text and "synthetic-auth-value" not in text
    monkeypatch.setenv("AGENT_DISPATCH_BRIDGE_CLI_TRACE", "0")

    def no_parse(*args, **kwargs):
        raise AssertionError("default-off tracing parsed stdin")

    monkeypatch.setattr(launch_debug.json, "loads", no_parse)
    assert launch_debug.begin(["bridge"], ["send", "actor"], prompt_input=payload) is None


def test_deferral_keeps_original_key_label_generation_and_prompt(intent):
    queue, task, reservation, info, prompt, invocation = intent
    record_created_agent(queue, reservation.key, task_generation=0,
                         agent_info=info, startup_invocation=invocation)
    queue.defer_spawn(reservation.key)
    retry, admitted = queue.reserve_spawn(task.id, driver="ordinary-bridge")
    assert admitted and retry.key != reservation.key
    retained = retry.agent_info
    assert prepare(asdict(task), asdict(retry), retained, prompt) == invocation
    assert retry.allocation_revision == reservation.allocation_revision


def test_prompt_is_not_identity_but_overwrite_is_conflict(intent):
    queue, task, reservation, info, prompt, invocation = intent
    changed = prepare(asdict(task), asdict(reservation), info, prompt + " changed")
    assert changed["invocation_key"] == invocation["invocation_key"]
    record_created_agent(queue, reservation.key, task_generation=0,
                         agent_info=info, startup_invocation=invocation)
    with pytest.raises(TaskError, match="already bound"):
        record_created_agent(queue, reservation.key, task_generation=0,
                             agent_info=info, startup_invocation=changed)


def test_actual_claim_increments_generation_without_rewriting_admission(intent):
    queue, task, reservation, info, _, invocation = intent
    record_created_agent(queue, reservation.key, task_generation=0,
                         agent_info=info, startup_invocation=invocation)
    claimed = queue.claim_one("worker", task_id=task.id)
    assert claimed.generation == 1 and invocation["binding"]["admission_generation"] == 0
    assert queue.get_reservation(reservation.key).agent_info["startup_invocation"] == invocation
    with pytest.raises(TaskError, match="fence"):
        record_created_agent(queue, reservation.key, task_generation=0,
                             agent_info=info, startup_invocation=invocation)


@pytest.mark.parametrize("change", ["omitted", "unknown-history"])
def test_created_storage_preserves_frozen_intent_transactionally(intent, change):
    queue, _, reservation, info, _, invocation = intent
    record_created_agent(queue, reservation.key, task_generation=0,
                         agent_info=info, startup_invocation=invocation)
    if change == "unknown-history":
        previous = queue.get_reservation(reservation.key).agent_info
        previous["startup_invocation"]["version"] = 2
        with queue._connect() as conn:
            conn.execute("UPDATE spawn_reservations SET agent_info=? WHERE key=?",
                         (json.dumps(previous), reservation.key))
    before = asdict(queue.get_reservation(reservation.key))
    with pytest.raises(TaskError):
        record_created_agent(queue, reservation.key, task_generation=0, agent_info=info,
                             startup_invocation=None if change == "omitted" else invocation)
    assert asdict(queue.get_reservation(reservation.key)) == before


@pytest.mark.parametrize("field,value", [
    ("version", 2), ("invocation_key", "different"),
])
def test_unknown_or_changed_identity_cannot_be_stored(intent, field, value):
    queue, _, reservation, info, _, invocation = intent
    with pytest.raises(TaskError):
        record_created_agent(queue, reservation.key, task_generation=0,
                             agent_info=info, startup_invocation={**invocation, field: value})
    assert queue.get_reservation(reservation.key).agent_info is None


@pytest.mark.parametrize("version", [None, 2, True])
def test_unknown_historical_intent_never_gets_a_new_key(intent, version):
    _, task, reservation, info, prompt, invocation = intent
    previous = {**invocation, "version": version}
    with pytest.raises(TaskError):
        prepare(asdict(task), asdict(reservation), {**info, "startup_invocation": previous}, prompt)


def test_old_storage_ignoring_intent_refuses_before_send(intent, monkeypatch, admission_owner):
    from agent_dispatch import bridge, ordinary_bridge
    queue, task, reservation, info, prompt, _ = intent
    calls = []

    class Client:
        def get_reservation(self, key):
            return asdict(queue.get_reservation(key))

        def record_spawn_allocation(self, key, project):
            return asdict(queue.record_spawn_allocation(key, project))

        def record_created_agent(self, key, **facts):
            facts.pop("startup_invocation")
            return asdict(record_created_agent(queue, key, **facts))

    def run(argv, **kwargs):
        calls.append(argv)
        assert argv[2] == "create"
        assert argv[argv.index("--min-daemon-protocol") + 1] == "45"
        return subprocess.CompletedProcess(argv, 0, json.dumps(info), "")

    monkeypatch.setattr(ordinary_bridge, "agent_bridge_launch_prefix", lambda: ["test-bridge"])
    monkeypatch.setattr(ordinary_bridge, "subprocess", SimpleNamespace(
        run=run, TimeoutExpired=subprocess.TimeoutExpired,
    ))
    monkeypatch.setattr(bridge, "registered_agent_project", lambda *_a, **_kw: "fixture")
    with pytest.raises(ordinary_bridge.CreatedAgentUnrecorded, match="read-back"):
        ordinary_bridge.start(
            {**asdict(task), "spawn_reservation_key": reservation.key},
            client=Client(), agent="mock", project="fixture", prompt=prompt,
        )
    assert len(calls) == 1


@pytest.mark.parametrize("change", ["held", "released", "retired", "superseded"])
def test_existing_fresh_admission_fences_still_refuse(intent, change):
    queue, task, reservation, info, _, invocation = intent
    if change == "held":
        queue.set_hold(task.id, reason="test hold", actor="test")
    elif change == "released":
        queue.request_spawn_release(reservation.key, detail="test release")
    elif change == "retired":
        queue.fail_spawn(reservation.key, detail="test failure")
    else:
        queue.defer_spawn(reservation.key)
        queue.reserve_spawn(task.id, driver="ordinary-bridge")
    with pytest.raises(TaskError):
        record_created_agent(queue, reservation.key, task_generation=0,
                             agent_info=info, startup_invocation=invocation)
