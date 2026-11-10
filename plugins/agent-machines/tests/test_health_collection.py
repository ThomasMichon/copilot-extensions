"""Local candidate/transaction controls; native producers have a separate lane."""

from __future__ import annotations

import copy
import json
import time
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from agent_machines import health_collect as collector, health_hourly as hourly, health_slots as slots
from agent_machines import health_observe, service_reports
from agent_machines import health_run as runner
from agent_machines.__main__ import main

NOW = datetime(2026, 11, 9, 12, 30, tzinfo=timezone.utc)
STAMP = int(NOW.timestamp() * 1000)
HOUR = STAMP // slots.HOUR_MS


@pytest.fixture
def store(tmp_path, monkeypatch):
    root = tmp_path / "store"
    root.mkdir()
    (root / "policy.json").write_text(json.dumps({
        "schema": slots.POLICY_SCHEMA, "version": 2,
        "publisher": "example-worker", "continuity": "example-generation",
        "collection": {"bridge_port": 1234, "services": ["queue=agent-dispatch"]},
    }), encoding="utf-8")
    (root / "collection.lock").write_bytes(b"\0")
    monkeypatch.setattr(collector, "time", SimpleNamespace(
        monotonic=lambda: 10.0, time_ns=lambda: STAMP * 1_000_000,
    ))
    monkeypatch.setattr(runner, "time", collector.time)
    monkeypatch.setattr(slots, "time", collector.time)
    monkeypatch.setattr(health_observe, "observe_bridge", lambda port, publisher, continuity: {
        "schema": slots.SCHEMA, "version": 1, "publisher": publisher, "continuity": continuity,
        "hour": HOUR, "revision": 1, "parent": None, "observed_at_ms": STAMP,
        "domains": {"local_listener": {
            "state": "ok", "evidence": "listener-check", "reason": "none", "observed_at_ms": STAMP,
        }},
    })
    return root


def stage(store, tmp_path, candidate):
    source = tmp_path / "candidate.json"
    source.write_text(json.dumps(candidate), encoding="utf-8")
    preview = slots.record(store, source, now_ms=STAMP)
    return slots.record(store, source, now_ms=STAMP, apply=True, expected=preview["preview_token"])


def test_current_collection_never_exports_or_stages_and_confirmed_record_reuses_owner(
    store, tmp_path, monkeypatch, capsys,
):
    monkeypatch.setattr(service_reports, "collect", lambda *a, **k: pytest.fail("current-hour service export"))
    before = {path.name: path.read_bytes() for path in store.iterdir()}
    assert main(["health", "collect", "--store-dir", str(store)]) == 0
    candidate = json.loads(capsys.readouterr().out)
    assert candidate["hour"] == HOUR and candidate["services"] == []
    assert {path.name: path.read_bytes() for path in store.iterdir()} == before
    assert stage(store, tmp_path, candidate)["applied"]
    view = slots.inspect(store, now_ms=STAMP)
    assert view["hours"][-1]["sample"]["domains"]["local_listener"]["state"] == "ok"
    assert view["hours"][-1]["sample"]["domains"]["logical_agents"]["count"] is None
    assert view["observation_qualification"] == "input-supplied-not-verified"


def test_historical_collection_does_not_probe_and_cannot_relabel_current_health(
    store, tmp_path, monkeypatch,
):
    monkeypatch.setattr(health_observe, "observe_bridge", lambda *a: pytest.fail("historical probe"))
    calls = []

    def services(names, hour, **fields):
        calls.append((names, hour, fields))
        return [{"instance": "queue", "service": "agent-dispatch", "hour": hour, "state": "unsupported"}]

    monkeypatch.setattr(service_reports, "collect", services)
    candidate = collector.collect(store, "2026-11-09T11:00:00Z")
    assert candidate["hour"] == HOUR - 1 and candidate["observation"] is None
    assert calls == [(["queue=agent-dispatch"], "2026-11-09T11:00:00Z", {
        "now_ms": STAMP, "deadline": 54.75,
    })]
    assert stage(store, tmp_path, candidate)["applied"]
    sample = slots.inspect(store, now_ms=STAMP)["hours"][-2]["sample"]
    assert sample["domains"]["local_listener"]["state"] == "unknown"
    assert sample["services"][0]["state"] == "unsupported"
    forged = copy.deepcopy(candidate)
    forged["observation"] = {
        "schema": slots.SCHEMA, "version": 1, "publisher": forged["publisher"],
        "continuity": forged["continuity"], "hour": HOUR, "observed_at_ms": STAMP,
        "revision": 1, "parent": None, "domains": {},
    }
    with pytest.raises(slots.HealthError, match="hourly-observation-mismatch"):
        hourly.validate_record(forged)


def test_new_candidate_preserves_unselected_components_and_obeys_revision_conflicts(
    store, tmp_path, monkeypatch,
):
    original = collector.collect(store)
    original["services"] = [{
        "instance": "queue", "service": "agent-dispatch",
        "hour": "2026-11-09T12:00:00Z", "state": "unsupported",
    }]
    original["coverage"]["services"] = "collected"
    assert stage(store, tmp_path, original)["applied"]
    candidate = collector.collect(store)
    assert candidate["services"] == original["services"]
    assert (candidate["revision"], candidate["parent"]) == (2, 1)
    assert stage(store, tmp_path, candidate)["applied"]
    source = tmp_path / "stale.json"
    source.write_text(json.dumps(original), encoding="utf-8")
    assert slots.record(store, source, now_ms=STAMP)["status"] == "revision-conflict"


def test_record_boundary_refuses_component_erasure_even_for_a_forged_candidate(store, tmp_path):
    original = collector.collect(store)
    assert stage(store, tmp_path, original)["applied"]
    forged = collector.collect(store)
    forged["observation"] = None
    source = tmp_path / "forged.json"
    source.write_text(json.dumps(forged), encoding="utf-8")
    with pytest.raises(slots.HealthError, match="observation-replacement-refused"):
        slots.record(store, source, now_ms=STAMP)
    assert slots.inspect(store, now_ms=STAMP)["hours"][-1]["sample"]["domains"]["local_listener"]["state"] == "ok"


def test_listener_recollection_preserves_unselected_domain_evidence_and_source_times(
    store, tmp_path, monkeypatch,
):
    original = collector.collect(store)
    retained = {
        "ssh_authenticated": {
            "state": "ok", "evidence": "authenticated-session", "reason": "none",
            "observed_at_ms": STAMP - 20_000, "direction": "outbound",
        },
        "logical_agents": {
            "state": "ok", "evidence": "session-owner", "reason": "none",
            "observed_at_ms": STAMP - 10_000, "count": 3,
        },
    }
    original["observation"]["domains"].update(retained)
    target = store / f"{HOUR % slots.SLOTS:03d}.json"
    target.write_text(json.dumps(hourly.validate_record(original)), encoding="utf-8")
    fresh = copy.deepcopy(original["observation"])
    fresh["domains"] = {
        "local_listener": {
            "state": "unknown", "evidence": "not-observed",
            "reason": "unavailable", "observed_at_ms": None,
        },
        "ssh_authenticated": {
            "state": "unknown", "evidence": "not-observed", "reason": "not-observed",
            "observed_at_ms": None, "direction": None,
        },
        "logical_agents": {
            "state": "unknown", "evidence": "not-observed", "reason": "not-observed",
            "observed_at_ms": None, "count": None,
        },
    }
    monkeypatch.setattr(health_observe, "observe_bridge", lambda *a: fresh)
    candidate = collector.collect(store)
    for domain, evidence in retained.items():
        assert candidate["observation"]["domains"][domain] == evidence
    assert candidate["observation"]["domains"]["local_listener"] == fresh["domains"]["local_listener"]
    assert stage(store, tmp_path, candidate)["applied"]
    sample = slots.inspect(store, now_ms=STAMP)["hours"][-1]["sample"]
    assert sample["domains"]["ssh_authenticated"]["observed_at_ms"] == STAMP - 20_000
    assert sample["domains"]["logical_agents"]["count"] == 3
    assert sample["domains"]["logical_agents"]["observed_at_ms"] == STAMP - 10_000
    for domain in retained:
        for change in ("erase", "refresh"):
            forged = collector.collect(store)
            if change == "erase":
                del forged["observation"]["domains"][domain]
            else:
                forged["observation"]["domains"][domain]["observed_at_ms"] = STAMP
            with pytest.raises(slots.HealthError, match="observation-replacement-refused"):
                stage(store, tmp_path, forged)
    assert json.loads(target.read_text()) == candidate


def test_unconfirmed_exporter_cleanup_holds_admission_until_native_settlement(
    store, monkeypatch, capsys,
):
    ready = False

    def cleanup(deadline):
        if not ready:
            raise TimeoutError("fixture cleanup still unconfirmed")

    error = service_reports.OwnedCleanupError(None, cleanup_retry=cleanup)

    def unsettled(*args, **kwargs):
        assert collector._RUN_LOCK.locked()
        raise error

    monkeypatch.setattr(service_reports, "collect", unsettled)
    try:
        assert main([
            "health", "collect", "--store-dir", str(store), "--hour", "2026-11-09T11:00:00Z",
        ]) == 2
        captured = capsys.readouterr()
        assert not captured.out
        assert json.loads(captured.err)["error"] == "collection-cleanup-unconfirmed"
        with pytest.raises(slots.HealthError, match="collection-overlap"):
            collector.collect(store)
        with pytest.raises(TimeoutError):
            error.retry_cleanup(deadline=time.monotonic() + 1)
        with pytest.raises(slots.HealthError, match="collection-overlap"):
            collector.collect(store)
        assert collector._RUN_LOCK.locked()
    finally:
        ready = True
        error.retry_cleanup(deadline=time.monotonic() + 1)
    assert (store / "collection.lock").read_bytes() == b"\0"
    assert collector.collect(store)["hour"] == HOUR


def test_successful_fence_cleanup_cannot_return_a_candidate_after_the_parent_deadline(
    store, monkeypatch,
):
    tick, calls = 10.0, 0
    fsync = collector.os.fsync
    monkeypatch.setattr(collector.time, "monotonic", lambda: tick)

    def delayed_cleanup(descriptor):
        nonlocal tick, calls
        fsync(descriptor)
        calls += 1
        if calls == 2:
            tick = 60.0

    monkeypatch.setattr(collector.os, "fsync", delayed_cleanup)
    monkeypatch.setattr(service_reports, "collect", lambda *a, **k: [])
    with pytest.raises(slots.HealthError, match="collection-timeout"):
        collector.collect(store, "2026-11-09T11:00:00Z")
    assert calls == 2
    assert (store / "collection.lock").read_bytes() == b"\0"
    assert not list(store.glob("[0-9][0-9][0-9].json"))
    with collector.admission(store):
        pass


def test_collection_overlap_refuses_before_native_owners_and_releases_on_exit(store, monkeypatch):
    monkeypatch.setattr(health_observe, "observe_bridge", lambda *a: pytest.fail("overlapping probe"))
    with collector.admission(store):
        with pytest.raises(slots.HealthError, match="collection-overlap"):
            collector.collect(store)
    with collector.admission(store):
        pass


def test_old_store_refuses_collection_without_creating_a_lock(store):
    policy = json.loads((store / "policy.json").read_text())
    policy.pop("collection")
    policy["version"] = 1
    (store / "policy.json").write_text(json.dumps(policy), encoding="utf-8")
    (store / "collection.lock").unlink()
    with pytest.raises(slots.HealthError, match="collection-store-unsupported"):
        collector.collect(store)
    assert {path.name for path in store.iterdir()} == {"policy.json"}


@pytest.mark.parametrize("clock", ["regression", "boundary", "deadline"])
def test_changed_hour_or_budget_does_not_produce_a_candidate(store, monkeypatch, clock):
    times = iter([STAMP, STAMP - 1 if clock == "regression" else STAMP + slots.HOUR_MS])
    ticks = iter([10, 10, 60]) if clock == "deadline" else iter([10, 10])
    monkeypatch.setattr(collector, "time", SimpleNamespace(
        monotonic=lambda: next(ticks, 60), time_ns=lambda: next(times, STAMP) * 1_000_000,
    ))
    if clock == "deadline":
        monkeypatch.setattr(collector.time, "time_ns", lambda: STAMP * 1_000_000)
    with pytest.raises(slots.HealthError, match="collection-(hour-changed|timeout)"):
        collector.collect(store)
    assert not list(store.glob("[0-9][0-9][0-9].json"))


def test_lock_and_policy_failures_are_bounded_cli_errors(store, capsys):
    (store / "collection.lock").write_bytes(b"")
    assert main(["health", "collect", "--store-dir", str(store)]) == 2
    result = json.loads(capsys.readouterr().err)
    assert result["error"] == "invalid-collection-lock" and result["applied"] is False
    assert str(store) not in json.dumps(result)


def test_empty_explicit_hour_refuses_before_native_admission(store, monkeypatch, capsys):
    monkeypatch.setattr(collector, "admission", lambda *a: pytest.fail("invalid hour admitted"))
    assert main(["health", "collect", "--store-dir", str(store), "--hour", ""]) == 2
    captured = capsys.readouterr()
    assert not captured.out and json.loads(captured.err)["applied"] is False
    assert not list(store.glob("[0-9][0-9][0-9].json"))


def test_read_only_storage_failure_does_not_prune_or_activate(store, tmp_path, monkeypatch):
    candidate = collector.collect(store)
    source = tmp_path / "candidate.json"
    source.write_text(json.dumps(candidate), encoding="utf-8")
    preview = slots.record(store, source, now_ms=STAMP)
    monkeypatch.setattr(slots, "mutation_lock", lambda *a, **k: nullcontext())

    def unavailable(*args):
        raise OSError("private-storage-detail")

    monkeypatch.setattr(slots, "_write", unavailable)
    result = slots.record(store, source, now_ms=STAMP, apply=True, expected=preview["preview_token"])
    assert result["status"] == "unknown" and result["applied"] is None
    assert not list(store.glob("[0-9][0-9][0-9].json"))


def approve(store):
    return runner.run(store)["policy_token"]


def apply_run(store):
    return runner.run(store, apply=True, expected=approve(store))


def test_one_shot_preview_is_pure_and_token_is_policy_not_hour_or_revision(
    store, monkeypatch, capsys,
):
    monkeypatch.setattr(collector, "admission", lambda *a, **k: pytest.fail("preview admission"))
    monkeypatch.setattr(health_observe, "observe_bridge", lambda *a: pytest.fail("preview observation"))
    monkeypatch.setattr(service_reports, "collect", lambda *a, **k: pytest.fail("preview export"))
    before = {path.name: path.read_bytes() for path in store.iterdir()}
    assert main(["health", "run", "--store-dir", str(store)]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["status"] == "preview"
    assert preview["qualification"] == "not-executed"
    assert all(row["applied"] is False for row in preview["targets"])
    assert {path.name: path.read_bytes() for path in store.iterdir()} == before
    (store / "000.json").write_text("unrelated revision", encoding="utf-8")
    monkeypatch.setattr(runner.time, "time_ns", lambda: (STAMP + slots.HOUR_MS) * 1_000_000)
    assert approve(store) == preview["policy_token"]


@pytest.mark.parametrize("scenario", ["missing", "wrong", "without-apply", "policy-drift", "store-drift"])
def test_one_shot_requires_exact_approval_before_any_effect(store, tmp_path, monkeypatch, scenario):
    token = approve(store)
    if scenario == "policy-drift":
        path = store / "policy.json"
        path.write_text(path.read_text() + "\n", encoding="utf-8")
    if scenario == "store-drift":
        replacement = tmp_path / "replacement"
        replacement.mkdir()
        (replacement / "policy.json").write_bytes((store / "policy.json").read_bytes())
        store = replacement
    monkeypatch.setattr(collector, "admission", lambda *a, **k: pytest.fail("unapproved admission"))
    with pytest.raises(slots.HealthError):
        runner.run(
            store, apply=scenario != "without-apply",
            expected="" if scenario == "missing" else "0" * 64 if scenario == "wrong" else token,
        )
    assert not list(store.glob("[0-9][0-9][0-9].json"))


def test_one_shot_two_targets_share_admission_deadline_and_normal_writer(store, monkeypatch, capsys):
    admissions, deadlines = [], []
    real_admission = collector.admission

    @contextmanager
    def admitted(*args, **kwargs):
        admissions.append(True)
        with real_admission(*args, **kwargs) as begin:
            yield begin

    def services(names, hour, **fields):
        assert collector._RUN_LOCK.locked()
        deadlines.append(fields["deadline"])
        return [{"instance": "queue", "service": "agent-dispatch", "hour": hour, "state": "unsupported"}]

    monkeypatch.setattr(collector, "admission", admitted)
    monkeypatch.setattr(service_reports, "collect", services)
    token = approve(store)
    assert main(["health", "run", "--store-dir", str(store), "--apply", "--expect-policy", token]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result.keys() == {
        "schema", "version", "status", "policy_token", "current_hour", "targets", "qualification",
        "carrier_publication",
    }
    assert len(admissions) == 1 and deadlines == [54.75]
    assert result["status"] == "completed" and result["qualification"] == "within-budget"
    assert [row["hour"] for row in result["targets"]] == [HOUR, HOUR - 1]
    for row in result["targets"]:
        assert row.keys() == {
            "hour", "component", "requested", "collection", "transaction", "applied", "changed", "reason",
        }
        assert row["transaction"] == "recorded" and row["applied"] and row["changed"]
    view = slots.inspect(store, now_ms=STAMP)
    assert view["observed_hours"] == 2
    assert view["hours"][-1]["sample"]["domains"]["local_listener"]["state"] == "ok"
    assert view["hours"][-2]["sample"]["domains"]["local_listener"]["state"] == "unknown"
    assert (store / "collection.lock").read_bytes() == b"\0"
    assert approve(store) == token


@pytest.mark.parametrize("component", ["observation", "services", "both"])
def test_one_shot_empty_selections_are_not_requested_and_never_enumerated(store, monkeypatch, component):
    path = store / "policy.json"
    policy = json.loads(path.read_text())
    if component in {"observation", "both"}:
        policy["collection"]["bridge_port"] = None
        monkeypatch.setattr(health_observe, "observe_bridge", lambda *a: pytest.fail("unselected probe"))
    if component in {"services", "both"}:
        policy["collection"]["services"] = []
        monkeypatch.setattr(service_reports, "collect", lambda *a, **k: pytest.fail("unselected export"))
    else:
        monkeypatch.setattr(service_reports, "collect", lambda *a, **k: [])
    path.write_text(json.dumps(policy), encoding="utf-8")
    result = apply_run(store)
    assert result["status"] == "completed"
    for row in result["targets"]:
        if component in {row["component"], "both"}:
            assert row["collection"] == row["transaction"] == "not-requested"
            assert not row["applied"] and not row["changed"]
    assert slots.inspect(store, now_ms=STAMP)["observed_hours"] == (0 if component == "both" else 1)


def test_one_shot_revalidates_approval_inside_mutation_lock(store, monkeypatch):
    token = approve(store)

    @contextmanager
    def drift(*a, **k):
        path = store / "policy.json"
        path.write_text(path.read_text() + "\n", encoding="utf-8")
        yield

    monkeypatch.setattr(slots, "mutation_lock", drift)
    monkeypatch.setattr(service_reports, "collect", lambda *a, **k: pytest.fail("drifted export"))
    result = runner.run(store, apply=True, expected=token)
    assert result["status"] == "failed"
    assert result["qualification"] == "store-policy-changed"
    assert result["targets"][0]["transaction"] == "refused"
    assert not list(store.glob("[0-9][0-9][0-9].json"))


def test_one_shot_revision_drift_is_not_overwritten_or_retried(store, monkeypatch):
    candidate = collector.collect(store)
    target = store / f"{HOUR % slots.SLOTS:03d}.json"
    candidate["observed_at_ms"] -= 1
    candidate["observation"]["observed_at_ms"] -= 1
    candidate["observation"]["domains"]["local_listener"]["observed_at_ms"] -= 1
    calls = 0

    @contextmanager
    def conflict(*a, **k):
        nonlocal calls
        calls += 1
        if calls == 1:
            target.write_text(json.dumps(candidate), encoding="utf-8")
        yield

    monkeypatch.setattr(slots, "mutation_lock", conflict)
    monkeypatch.setattr(service_reports, "collect", lambda *a, **k: [])
    result = apply_run(store)
    assert result["status"] == "partial"
    assert result["targets"][0]["reason"] == "preview-changed"
    assert result["targets"][1]["applied"]
    assert json.loads(target.read_text()) == candidate


@pytest.mark.parametrize("failure", ["export", "unknown-write"])
def test_second_target_failure_preserves_first_and_unknown_write_fences_replay(store, monkeypatch, failure):
    original_write = slots._write

    def export(*a, **k):
        if failure == "export":
            raise OSError("private-export-detail")
        return []

    def write(path, value):
        if value["hour"] == HOUR - 1:
            raise OSError("private-write-detail")
        original_write(path, value)

    monkeypatch.setattr(service_reports, "collect", export)
    monkeypatch.setattr(slots, "_write", write)
    result = apply_run(store)
    assert result["status"] == "partial"
    assert result["targets"][0]["transaction"] == "recorded"
    assert result["targets"][0]["applied"] and result["targets"][0]["changed"]
    assert slots.inspect(store, now_ms=STAMP)["observed_hours"] == 1
    if failure == "export":
        assert result["targets"][1]["collection"] == "failed"
        assert (store / "collection.lock").read_bytes() == b"\0"
    else:
        assert result["targets"][1]["transaction"] == "unknown"
        assert result["targets"][1]["applied"] is None
        assert result["qualification"] == "write-unconfirmed"
        assert (store / "collection.lock").read_bytes() == b"\1"
        again = apply_run(store)
        assert again["qualification"] == "collection-cleanup-unconfirmed"
        assert all(not row["applied"] for row in again["targets"])
    assert "private-" not in json.dumps(result)


@pytest.mark.parametrize("late", ["deadline", "hour"])
def test_late_confirmed_first_commit_retains_fact_and_stops_new_collectors(store, monkeypatch, late):
    original_write = slots._write
    tick, stamp = 10.0, STAMP
    monkeypatch.setattr(runner.time, "monotonic", lambda: tick)
    monkeypatch.setattr(runner.time, "time_ns", lambda: stamp * 1_000_000)

    def write(*args):
        nonlocal tick, stamp
        original_write(*args)
        if late == "deadline":
            tick = 60.0
        else:
            stamp += slots.HOUR_MS

    monkeypatch.setattr(slots, "_write", write)
    monkeypatch.setattr(service_reports, "collect", lambda *a, **k: pytest.fail("late export"))
    result = apply_run(store)
    assert result["status"] == "partial"
    assert result["qualification"] == ("collection-timeout" if late == "deadline" else "collection-hour-changed")
    assert result["targets"][0]["transaction"] == "recorded"
    assert result["targets"][0]["applied"] and result["targets"][0]["changed"]
    assert not result["targets"][1]["applied"]
    assert slots.inspect(store, now_ms=stamp)["observed_hours"] == 1


def test_one_shot_unknown_cleanup_keeps_first_commit_and_original_admission_custody(store, monkeypatch):
    ready = False

    def settle(deadline):
        if not ready:
            raise TimeoutError("fixture not settled")

    error = service_reports.OwnedCleanupError(None, cleanup_retry=settle)

    def export(*args, **kwargs):
        raise error

    monkeypatch.setattr(service_reports, "collect", export)
    try:
        result = apply_run(store)
        assert result["status"] == "partial"
        assert result["qualification"] == "collection-cleanup-unconfirmed"
        assert result["targets"][0]["applied"]
        assert not result["targets"][1]["applied"]
        assert collector._RUN_LOCK.locked()
        with pytest.raises(TimeoutError):
            error.retry_cleanup(deadline=time.monotonic() + 1)
        assert apply_run(store)["qualification"] == "collection-overlap"
    finally:
        ready = True
        error.retry_cleanup(deadline=time.monotonic() + 1)
    assert (store / "collection.lock").read_bytes() == b"\0"
    assert slots.inspect(store, now_ms=STAMP)["observed_hours"] == 1


def test_one_shot_lock_budget_is_recomputed_after_planning(store, monkeypatch):
    original_plan = slots._plan
    tick = 10.0
    timeouts = []
    monkeypatch.setattr(runner.time, "monotonic", lambda: tick)

    def slow_plan(*a, **k):
        nonlocal tick
        result = original_plan(*a, **k)
        tick = 53.0
        return result

    @contextmanager
    def lock(*a, **k):
        timeouts.append(k["timeout"])
        yield

    monkeypatch.setattr(slots, "_plan", slow_plan)
    monkeypatch.setattr(slots, "mutation_lock", lock)
    monkeypatch.setattr(service_reports, "collect", lambda *a, **k: [])
    assert apply_run(store)["status"] == "completed"
    assert timeouts == [1.75, 1.75]


def test_one_shot_final_admission_settlement_cannot_hide_a_late_success(store, monkeypatch):
    tick = 10.0
    real_admission = collector.admission
    monkeypatch.setattr(runner.time, "monotonic", lambda: tick)
    monkeypatch.setattr(service_reports, "collect", lambda *a, **k: [])

    @contextmanager
    def delayed_release(*args, **kwargs):
        nonlocal tick
        with real_admission(*args, **kwargs) as begin:
            yield begin
        tick = 60.0

    monkeypatch.setattr(collector, "admission", delayed_release)
    result = apply_run(store)
    assert result["status"] == "partial" and result["qualification"] == "collection-timeout"
    assert all(row["transaction"] == "recorded" and row["applied"] for row in result["targets"])
    assert slots.inspect(store, now_ms=STAMP)["observed_hours"] == 2
    assert (store / "collection.lock").read_bytes() == b"\0"
