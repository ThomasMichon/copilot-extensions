"""Receipt caller -> current registered HTTP receiver, with synthetic backing state."""

from __future__ import annotations

import json
import subprocess
import sys
from types import SimpleNamespace

import httpx
import pytest

from agent_dispatch.client import DispatchClient, DispatchUpgradeRequired
from agent_dispatch.overrides import set_override
from agent_dispatch.producers import emitter, emitter_relevance
from agent_dispatch.queue import Status
from agent_dispatch.supervisor_registration import build_command
from tests.test_task_relevance_registration import SPEC, emitted, q as q
from tests.test_task_relevance_effective_scope import declaration, venue as venue
from tests.test_task_relevance_transport import api as api, transport


@pytest.fixture
def spec(q, tmp_path, monkeypatch):
    monkeypatch.setattr(
        emitter, "receipts_path", lambda name: tmp_path / emitter._digest_emitter_id(name) / "receipts.jsonl",
    )
    value = {**SPEC, "relevance": {"command": ["source-reader", "relevance"], "batch_size": 2}}
    q.register_registration("emitter", value, reg_id="source-a", machine="host-a")
    return value


def _retain(q, spec, **changes):
    task = emitted(q, **changes)
    emitter._append_receipts(spec["id"], [{"id": task.id, "dedup_key": f"object:{task.id}"}])
    return task


def _runner(verdict="obsolete", *, before_return=None):
    def run(command, **kwargs):
        assert command == ["source-reader", "relevance"]
        payload = json.loads(kwargs["input"])
        assert payload["version"] == 1
        if before_return:
            before_return()
        return SimpleNamespace(returncode=0, stdout=json.dumps({
            "version": 1, "observations": [
                {"task_id": task["snapshot"]["task_id"], "verdict": verdict,
                 "reason": "Authoritative object observation", "observation_ref": "provider:revision-2"}
                for task in payload["tasks"]
            ],
        }))
    return run


def _reconcile(client, spec, runner=None):
    return emitter_relevance.reconcile_receipts(
        client, spec, registration_id="source-a", runner=runner or _runner(),
    )


def test_tick_reconciles_closed_object_even_when_producer_open_listing_is_empty(q, api, spec):
    task = _retain(q, spec)
    calls = []

    def runner(command, **kwargs):
        if command == spec["command"]:
            return SimpleNamespace(returncode=0, stdout="[]")
        return _runner()(command, **kwargs)

    with DispatchClient(
        "http://testserver", token="data", control_token="control", transport=transport(api, calls),
    ) as client:
        result = emitter.run_tick(client, spec, holder="host-a", registration_id="source-a", runner=runner)
    assert result["returncode"] == 0 and result["error"] is None
    assert result["created"] == []
    assert result["relevance"]["accepted"][0]["decision"] == "abandoned"
    assert q.get(task.id).status == Status.ABANDONED
    assert q.list_wakes(task.id) == []


@pytest.mark.parametrize("verdict", ["needed", "unknown"])
def test_keep_and_unknown_never_infer_obsolescence_from_age_or_empty_listing(q, api, spec, verdict):
    task = _retain(q, spec)
    with DispatchClient(
        "http://testserver", token="data", control_token="control", transport=transport(api, []),
    ) as client:
        result = _reconcile(client, spec, _runner(verdict))
    assert result["accepted"][0]["decision"] == "unchanged"
    assert q.get(task.id).status == Status.QUEUED


def test_bound_receipt_routes_to_original_owner_closeout_not_task_abandon(q, api, spec):
    task = _retain(q, spec)
    q.claim_one("worker-a", task_id=task.id)
    with DispatchClient(
        "http://testserver", token="data", control_token="control", transport=transport(api, []),
    ) as client:
        result = _reconcile(client, spec)
        again = _reconcile(client, spec)
    assert result["accepted"][0]["decision"] == "closeout_pending"
    assert q.get(task.id).owner == "worker-a" and q.get(task.id).status == Status.CLAIMED
    assert len(q.steer_log(task.id)) == 1
    assert again["accepted"] == []


@pytest.mark.parametrize("failure", ["timeout", "exit", "malformed", "missing", "foreign", "duplicate"])
def test_failed_or_incomplete_source_read_has_no_report_effect(q, api, spec, failure):
    task = _retain(q, spec)
    calls = []

    def runner(*_args, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired("source-reader", 30)
        if failure == "exit":
            return SimpleNamespace(returncode=1, stdout="[]")
        if failure == "malformed":
            return SimpleNamespace(returncode=0, stdout="not json")
        row = {"task_id": task.id, "verdict": "obsolete", "reason": "Closed", "observation_ref": "revision"}
        rows = [] if failure == "missing" else [row, row] if failure == "duplicate" else [{**row, "task_id": "foreign"}]
        return SimpleNamespace(returncode=0, stdout=json.dumps({"version": 1, "observations": rows}))

    with DispatchClient(
        "http://testserver", token="data", control_token="control", transport=transport(api, calls),
    ) as client:
        result = _reconcile(client, spec, runner)
    assert result["errors"] and result["pending"] == 0
    assert q.get(task.id).status == Status.QUEUED
    assert not any(method == "POST" for method, _ in calls)


def test_lost_post_response_recovers_original_receipt_after_restart_and_hold(q, api, spec):
    task = _retain(q, spec)
    calls = []
    delegate = transport(api, calls)

    def send(request):
        response = delegate.handle_request(request)
        if request.method == "POST":
            raise httpx.ReadError("response lost", request=request)
        return response

    with DispatchClient(
        "http://testserver", token="data", control_token="control", transport=httpx.MockTransport(send),
    ) as client:
        first = _reconcile(client, spec)
    assert first["pending"] == 1 and q.get(task.id).status == Status.ABANDONED
    pending_path = emitter.receipts_path(spec["id"]).with_name("relevance.json")
    original = json.loads(pending_path.read_text())["pending"][task.id]
    q.set_registration_status("source-a", "paused")
    with DispatchClient(
        "http://testserver", token="data", control_token="control", transport=transport(api, calls),
    ) as client:
        second = _reconcile(client, spec)
    assert second["pending"] == 0
    assert second["recovered"][0]["request_id"] == original["request_id"]
    assert len([1 for method, _ in calls if method == "POST"]) == 1


def test_unknown_send_with_absent_receipt_is_not_replayed_or_replaced(q, api, spec):
    task = _retain(q, spec)
    calls = []
    delegate = transport(api, calls)
    attempts = []

    def send(request):
        if request.method == "POST":
            attempts.append(json.loads(request.content))
            raise httpx.ReadError("unknown delivery", request=request)
        return delegate.handle_request(request)

    with DispatchClient(
        "http://testserver", token="data", control_token="control", transport=httpx.MockTransport(send),
    ) as client:
        first = _reconcile(client, spec)
        second = _reconcile(client, spec)
    assert first["pending"] == second["pending"] == len(attempts) == 1
    assert q.get(task.id).status == Status.QUEUED
    assert attempts[0]["reports"][0]["request_id"]


@pytest.mark.parametrize("change", ["claim", "registration", "newer_source"])
def test_movement_during_source_check_fences_old_report(q, api, spec, change):
    task = _retain(q, spec)

    def move():
        if change == "claim":
            q.claim_one("worker-a", task_id=task.id)
        elif change == "registration":
            q.register_registration("emitter", {**spec, "command": ["changed"]}, reg_id="source-a", machine="host-a")
        else:
            from tests.test_task_relevance_registration import report
            q.reconcile_task_relevance(task.id, **report(
                q, task.id, verdict="needed", request_id="reopened", observation_ref="provider:revision-3",
            ))

    with DispatchClient(
        "http://testserver", token="data", control_token="control", transport=transport(api, []),
    ) as client:
        result = _reconcile(client, spec, _runner(before_return=move))
    assert result["pending"] == 0 and result["errors"]
    assert q.get(task.id).status != Status.ABANDONED
    with DispatchClient(
        "http://testserver", token="data", control_token="control", transport=transport(api, []),
    ) as client:
        retry = _reconcile(client, spec, _runner("needed"))
    if change != "registration":
        assert retry["accepted"][0]["decision"] == "unchanged"
    else:
        assert retry["errors"] and not retry["accepted"]


@pytest.mark.parametrize("change", ["hold", "foreign", "changed_spec", "prior"])
def test_scope_and_peer_refusals_do_not_run_source_command(q, api, spec, change):
    task = _retain(q, spec, **({"source": "other"} if change == "foreign" else {}))
    if change == "hold":
        from agent_dispatch.config import overrides_path
        set_override(overrides_path(), "source-a", disabled=True, reason="Hold")
    if change == "changed_spec":
        q.register_registration("emitter", {**spec, "relevance": {"command": ["new-reader"]}}, reg_id="source-a", machine="host-a")
    calls = []

    def forbidden(*_args, **_kwargs):
        pytest.fail("unqualified source command ran")

    wire = transport(api, calls, contract=None) if change == "prior" else transport(api, calls)
    with DispatchClient("http://testserver", token="data", control_token="control", transport=wire) as client:
        result = _reconcile(client, spec, forbidden)
    assert result["errors"] and not result["accepted"]
    assert q.get(task.id).status == Status.QUEUED
    assert not any(method == "POST" for method, _ in calls)


def test_receipt_trim_is_visible_and_batch_cursor_reconsiders_kept_tasks(q, api, spec):
    tasks = [_retain(q, spec) for _ in range(3)]
    with DispatchClient(
        "http://testserver", token="data", control_token="control", transport=transport(api, []),
    ) as client:
        first = _reconcile(client, spec, _runner("needed"))
        second = _reconcile(client, spec, _runner("needed"))
        emitter._append_receipts(spec["id"], [{"id": tasks[-1].id}], max_receipts=1)
        trimmed = _reconcile(client, spec, _runner("needed"))
    assert len(first["accepted"]) == 2 and len(second["accepted"]) == 1
    assert second["accepted"][0]["task_id"] == tasks[-1].id
    assert trimmed["receipt_history_trimmed"] is True


def test_supervisor_and_serve_keep_actual_registration_not_emitter_spec_id(spec, tmp_path):
    reg = {"id": "declared:project:review-source", "kind": "emitter", "spec": spec, "machine": "host-a"}
    argv = build_command(reg, python="PY", materialize=lambda *_args: str(tmp_path / "source.json"))
    assert argv[-2:] == ["--registration-id", reg["id"]]
    path = tmp_path / "source.json"
    path.write_text(json.dumps(spec))
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout='{"held":false}')

    def stop(_interval):
        raise KeyboardInterrupt

    emitter.serve(path, holder="host-a", registration_id=reg["id"], runner=run, sleep=stop)
    assert calls[0][-2:] == ["--registration-id", reg["id"]]


def test_real_source_command_reads_retained_objects_from_stdin(q, api, spec):
    task = _retain(q, spec)
    command = [sys.executable, "-c", (
        "import json,sys; p=json.load(sys.stdin); "
        "print(json.dumps({'version':1,'observations':["
        "{'task_id':x['snapshot']['task_id'],'verdict':'obsolete',"
        "'reason':'Explicit provider closed state','observation_ref':'provider:closed:4'} "
        "for x in p['tasks']]}))"
    )]
    spec["relevance"]["command"] = command
    q.register_registration("emitter", spec, reg_id="source-a", machine="host-a")
    with DispatchClient(
        "http://testserver", token="data", control_token="control", transport=transport(api, []),
    ) as client:
        result = _reconcile(client, spec, subprocess.run)
    assert result["accepted"][0]["decision"] == "abandoned"
    assert q.get(task.id).status == Status.ABANDONED


@pytest.mark.parametrize("config", [
    {"command": []}, {"command": "source-reader"},
    {"command": ["source-reader"], "batch_size": True},
    {"command": ["source-reader"], "batch_size": 101},
    {"command": ["source-reader"], "timeout_seconds": float("inf")},
    {"command": ["source-reader"], "timeout_seconds": 301},
    {"command": ["source-reader"], "allow_abandon": True},
])
def test_source_config_is_bounded_and_opt_in(config):
    with pytest.raises(emitter.EmitterError):
        emitter.validate_spec({**SPEC, "relevance": config})


def test_missing_registration_does_not_guess_from_emitter_spec(q, spec):
    with pytest.raises(emitter.EmitterError, match="registration-id"):
        emitter_relevance.reconcile_receipts(None, spec, registration_id=None)


def test_pending_request_survives_receipt_trim_and_duplicate_task_receipts(q, api, spec):
    task = _retain(q, spec)
    emitter._append_receipts(spec["id"], [{"id": task.id}])
    delegate = transport(api, [])
    attempts = []

    def send(request):
        if request.method == "POST":
            attempts.append(json.loads(request.content))
            raise httpx.ReadError("outcome unknown", request=request)
        return delegate.handle_request(request)

    with DispatchClient(
        "http://testserver", token="data", control_token="control", transport=httpx.MockTransport(send),
    ) as client:
        first = _reconcile(client, spec)
        other = _retain(q, spec)
        emitter._append_receipts(spec["id"], [{"id": other.id}], max_receipts=1)
        second = _reconcile(client, spec, _runner("needed"))
    assert first["pending"] == 1 and second["pending"] == 2
    assert [row["reports"][0]["verdict"] for row in attempts] == ["obsolete", "needed"]
    retained = json.loads(emitter.receipts_path(spec["id"]).with_name("relevance.json").read_text())
    assert retained["pending"][task.id]["request_id"] == attempts[0]["reports"][0]["request_id"]


def test_cli_keeps_registration_argument_through_tick_and_serve(tmp_path, monkeypatch):
    from contextlib import nullcontext
    from agent_dispatch import __main__ as cli, producers_cli

    path = tmp_path / "source.json"
    path.write_text(json.dumps(SPEC))
    client = object()
    monkeypatch.setattr(producers_cli, "_client", lambda _args: nullcontext(client))
    monkeypatch.setattr(producers_cli, "_resolve_client_target", lambda _args: None)
    monkeypatch.setattr(producers_cli, "_emit", lambda _value: 0)
    calls = []

    def record(*args, **kwargs):
        calls.append(kwargs)
        return {}

    monkeypatch.setattr(emitter, "run_tick", record)
    monkeypatch.setattr(emitter, "serve", record)
    for subcommand in ("tick", "serve"):
        args = cli.build_parser().parse_args([
            "emitter", subcommand, str(path), "--holder", "host-a", "--registration-id", "source-a",
        ])
        assert producers_cli._cmd_emitter(args) == 0
    assert all(call["registration_id"] == "source-a" for call in calls)


def test_post_commit_failure_is_unknown_not_a_safe_rejection(q, api, spec, monkeypatch):
    task = _retain(q, spec)
    original = q.reconcile_task_relevance
    calls = []

    def fail_after_commit(*args, **kwargs):
        original(*args, **kwargs)
        raise ValueError("publication callback failed after commit")

    monkeypatch.setattr(q, "reconcile_task_relevance", fail_after_commit)
    with DispatchClient(
        "http://testserver", token="data", control_token="control", transport=transport(api, calls),
    ) as client:
        first = _reconcile(client, spec)
        second = _reconcile(client, spec)
    assert first["pending"] == 1 and second["pending"] == 0
    assert second["recovered"][0]["decision"] == "abandoned"
    assert q.get(task.id).status == Status.ABANDONED
    assert len([1 for method, _ in calls if method == "POST"]) == 1


def test_crash_after_request_persistence_never_implies_it_was_not_sent(q, api, spec, monkeypatch):
    task = _retain(q, spec)

    def crash(*_args, **_kwargs):
        raise SystemExit("lost process")

    with DispatchClient(
        "http://testserver", token="data", control_token="control", transport=transport(api, []),
    ) as client:
        with monkeypatch.context() as patch:
            patch.setattr(client, "report_task_relevance_batch", crash)
            with pytest.raises(SystemExit):
                _reconcile(client, spec)
        recovered = _reconcile(client, spec)
    assert recovered["pending"] == 1 and not recovered["accepted"]
    assert q.get(task.id).status == Status.QUEUED


def test_supplier_qualification_keeps_relevance_command_at_its_registered_root(tmp_path):
    from agent_dispatch.registrar import ProfileDeclaration
    from agent_dispatch.registrar_document import qualify_supplier_context

    root = tmp_path / "source"
    root.mkdir()
    spec = {**SPEC, "relevance": {"command": ["{python}", str(root / "checks.py")]}}
    declaration = ProfileDeclaration(
        name="reviews", kind="emitter", spec=spec,
    )
    qualified = qualify_supplier_context(declaration, "source-repo", root)
    assert qualified.spec["relevance"]["command"] == ["{python}", "repo:source-repo:checks.py"]


def test_actual_declared_emitter_receipts_use_fresh_registration_not_mirror(q, venue, spec):
    api, trusted, _ = venue
    rid = declaration(trusted, "review-source", "emitter", spec)
    task = _retain(q, spec)
    assert q.get_registration(rid) is None
    with DispatchClient(
        "http://testserver", token="data", control_token="control", transport=transport(api, []),
    ) as client:
        result = emitter_relevance.reconcile_receipts(
            client, spec, registration_id=rid, runner=_runner(),
        )
    assert result["errors"] == []
    assert result["accepted"][0]["decision"] == "abandoned"
    assert q.get(task.id).status == Status.ABANDONED


def test_prior_receiver_without_spec_correlation_gets_no_source_command_or_post(q, api, spec):
    _retain(q, spec)
    delegate = transport(api, [])
    attempts = []

    def send(request):
        response = delegate.handle_request(request)
        if request.method == "POST":
            attempts.append(request)
        if request.url.path == "/task-relevance/snapshots":
            value = json.loads(response.content)
            for item in value["result"]["items"]:
                item["result"].pop("emitter_spec_revision")
            return httpx.Response(200, json=value)
        return response

    def forbidden(*_args, **_kwargs):
        pytest.fail("prior receiver admitted a source check")

    with DispatchClient(
        "http://testserver", token="data", control_token="control", transport=httpx.MockTransport(send),
    ) as client:
        result = _reconcile(client, spec, forbidden)
    assert result["errors"] and not attempts


def test_overlapping_callers_share_receipt_lock_and_do_not_repeat_abandon(q, api, spec):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    task = _retain(q, spec)
    entered, release = Event(), Event()
    calls = []

    def source(command, **kwargs):
        entered.set()
        assert release.wait(5)
        return _runner()(command, **kwargs)

    def reconcile(runner):
        with DispatchClient(
            "http://testserver", token="data", control_token="control", transport=transport(api, calls),
        ) as client:
            return _reconcile(client, spec, runner)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(reconcile, source)
        assert entered.wait(5)
        second = executor.submit(reconcile, _runner())
        release.set()
        results = [first.result(timeout=5), second.result(timeout=5)]
    assert sum(len(result["accepted"]) for result in results) == 1
    assert len([1 for method, _ in calls if method == "POST"]) == 1
    assert q.get(task.id).status == Status.ABANDONED


def test_unknown_request_backpressure_preserves_custody_without_unbounded_growth(q, api, spec, monkeypatch):
    _retain(q, spec)
    monkeypatch.setattr(emitter, "DEFAULT_MAX_RECEIPTS", 1)
    delegate = transport(api, [])
    attempts = []

    def send(request):
        if request.method == "POST":
            attempts.append(request)
            raise httpx.ReadError("outcome unknown", request=request)
        return delegate.handle_request(request)

    with DispatchClient(
        "http://testserver", token="data", control_token="control", transport=httpx.MockTransport(send),
    ) as client:
        first = _reconcile(client, spec)
        _retain(q, spec)
        second = _reconcile(client, spec)
    assert first["pending"] == second["pending"] == len(attempts) == 1
    assert second["capacity_held"] is True


def test_duplicate_rediscovery_cannot_starve_an_older_retained_task(q, api, spec):
    spec["relevance"]["batch_size"] = 1
    q.register_registration("emitter", spec, reg_id="source-a", machine="host-a")
    first, repeated = _retain(q, spec), _retain(q, spec)
    with DispatchClient(
        "http://testserver", token="data", control_token="control", transport=transport(api, []),
    ) as client:
        assert _reconcile(client, spec, _runner("needed"))["accepted"][0]["task_id"] == first.id
        emitter._append_receipts(spec["id"], [{"id": repeated.id}])
        assert _reconcile(client, spec, _runner("needed"))["accepted"][0]["task_id"] == repeated.id
        emitter._append_receipts(spec["id"], [{"id": repeated.id}])
        revisit = _reconcile(client, spec)
    assert revisit["accepted"][0]["task_id"] == first.id
    assert q.get(first.id).status == Status.ABANDONED
    assert q.get(repeated.id).status == Status.QUEUED


@pytest.mark.parametrize("failure", ["timeout", "unsupported"])
def test_preflight_failure_is_definitely_unsent_and_allows_later_fresh_check(q, api, spec, monkeypatch, failure):
    task = _retain(q, spec)
    calls = []

    def fail(**_kwargs):
        if failure == "unsupported":
            raise DispatchUpgradeRequired("receiver rolled back before POST")
        raise httpx.ReadTimeout("health preflight timed out")

    with DispatchClient(
        "http://testserver", token="data", control_token="control", transport=transport(api, calls),
    ) as client:
        with monkeypatch.context() as patch:
            result = _reconcile(client, spec, _runner(
                before_return=lambda: patch.setattr(client, "_require_relevance", fail),
            ))
        assert result["pending"] == 0 and result["errors"]
        assert not any(method == "POST" for method, _ in calls)
        retry = _reconcile(client, spec, _runner("needed"))
    assert retry["accepted"][0]["task_id"] == task.id
    assert q.get(task.id).status == Status.QUEUED


def test_invalid_post_receipt_is_not_misclassified_as_unsent_upgrade(q, api, spec):
    task = _retain(q, spec)
    delegate = transport(api, [])

    def send(request):
        response = delegate.handle_request(request)
        if request.method == "POST":
            return httpx.Response(200, json={"result": None})
        return response

    with DispatchClient(
        "http://testserver", token="data", control_token="control", transport=httpx.MockTransport(send),
    ) as client:
        first = _reconcile(client, spec)
        second = _reconcile(client, spec)
    assert first["pending"] == 1 and second["pending"] == 0
    assert second["recovered"][0]["decision"] == "abandoned"
    assert q.get(task.id).status == Status.ABANDONED


@pytest.mark.parametrize("contents", ["not json", '{"version": 99}'])
def test_invalid_sidecar_preserves_custody_and_does_not_block_normal_production(q, api, spec, contents):
    task = _retain(q, spec)
    path = emitter.receipts_path(spec["id"]).with_name("relevance.json")
    path.write_text(contents)
    commands = []
    calls = []

    def produce(command, **_kwargs):
        commands.append(command)
        assert command == spec["command"]
        return SimpleNamespace(returncode=0, stdout="[]")

    with DispatchClient(
        "http://testserver", token="data", control_token="control", transport=transport(api, calls),
    ) as client:
        for _tick in range(2):
            result = emitter.run_tick(
                client, spec, holder="host-a", registration_id="source-a", runner=produce,
            )
            assert result["returncode"] == 0
            assert result["relevance"]["errors"] and result["relevance"]["pending"] is None
            assert "relevance" in result["error"]
            assert path.read_text() == contents
    assert len(commands) == 2 and q.get(task.id).status == Status.QUEUED
    assert not any(path == "/task-relevance/reports" for _method, path in calls)


def test_pre_send_sidecar_write_failure_keeps_normal_production_available(q, api, spec, monkeypatch):
    task = _retain(q, spec)
    original = emitter_relevance._save
    commands = []
    calls = []

    def fail_before_send(path, state):
        if state["pending"]:
            raise OSError("sidecar write unavailable")
        original(path, state)

    monkeypatch.setattr(emitter_relevance, "_save", fail_before_send)

    def produce(command, **kwargs):
        commands.append(command)
        if command == spec["command"]:
            return SimpleNamespace(returncode=0, stdout="[]")
        return _runner()(command, **kwargs)

    with DispatchClient(
        "http://testserver", token="data", control_token="control", transport=transport(api, calls),
    ) as client:
        result = emitter.run_tick(
            client, spec, holder="host-a", registration_id="source-a", runner=produce,
        )
    assert result["returncode"] == 0 and result["relevance"]["errors"]
    assert commands[-1] == spec["command"]
    assert q.get(task.id).status == Status.QUEUED
    assert not any(path == "/task-relevance/reports" for _method, path in calls)
