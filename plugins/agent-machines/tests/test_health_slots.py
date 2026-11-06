"""Explicit local consumer contracts, not native probe or carrier qualification."""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_machines import __main__ as cli, health_slots as health

NOW = datetime(2026, 11, 6, 12, 30, tzinfo=timezone.utc)
STAMP = int(NOW.timestamp() * 1000)


def sample(*, stamp=STAMP, revision=1, parent=None):
    return {
        "schema": health.SCHEMA, "version": 1, "publisher": "example-worker",
        "continuity": "example-generation", "hour": stamp // health.HOUR_MS,
        "observed_at_ms": stamp, "revision": revision, "parent": parent,
        "domains": {
            "local_listener": {
                "state": "ok", "evidence": "listener-check", "reason": "none", "observed_at_ms": stamp,
            },
            "logical_agents": {
                "state": "unknown", "evidence": "not-observed", "reason": "unavailable",
                "observed_at_ms": None, "count": None,
            },
        },
    }


@pytest.fixture
def local(tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    (store / "policy.json").write_text(json.dumps({
        "schema": health.POLICY_SCHEMA, "version": 1,
        "publisher": "example-worker", "continuity": "example-generation",
    }), encoding="utf-8")
    source = tmp_path / "observation.json"
    source.write_text(json.dumps(sample()), encoding="utf-8")
    return store, source


def apply(store, source, *, now_ms=STAMP):
    preview = health.record(store, source, now_ms=now_ms)
    return health.record(store, source, now_ms=now_ms, apply=True, expected=preview["preview_token"])


def test_normal_cli_preview_record_and_age_qualified_reader(local, capsys):
    store, source = local
    arguments = [
        "health", "record", "--input", str(source), "--store-dir", str(store),
        "--now", NOW.isoformat(),
    ]
    original = source.read_bytes()
    assert cli.main(arguments) == 0
    preview = json.loads(capsys.readouterr().out)
    assert set(path.name for path in store.iterdir()) == {"policy.json"}
    assert cli.main(arguments + ["--apply", "--expect", preview["preview_token"]]) == 0
    recorded = json.loads(capsys.readouterr().out)
    assert recorded["applied"] and recorded["changed"]
    assert source.read_bytes() == original
    assert cli.main(["health", "inspect", "--store-dir", str(store), "--now", NOW.isoformat()]) == 0
    view = json.loads(capsys.readouterr().out)
    assert len(view["hours"]) == 168 and view["observed_hours"] == 1
    assert all(item["state"] == "missing" for item in view["hours"][:-1])
    domains = view["hours"][-1]["sample"]["domains"]
    assert domains["local_listener"]["state"] == "ok"
    assert domains["ssh_authenticated"]["state"] == "unknown"
    assert domains["logical_agents"]["count"] is None
    assert view["probe_execution"] == "not-performed" and view["carrier_receipt"] == "not-observed"
    assert view["observation_qualification"] == "input-supplied-not-verified"
    unchanged = apply(store, source)
    assert unchanged["applied"] and not unchanged["changed"]


def test_weekly_slot_reuse_cannot_promote_delayed_old_input(local):
    store, source = local
    apply(store, source)
    later = STAMP + health.SLOTS * health.HOUR_MS
    source.write_text(json.dumps(sample(stamp=later)), encoding="utf-8")
    assert apply(store, source, now_ms=later)["changed"]
    target = store / f"{sample()['hour'] % health.SLOTS:03d}.json"
    current = target.read_bytes()
    source.write_text(json.dumps(sample()), encoding="utf-8")
    preview = health.record(store, source, now_ms=later)
    assert preview["status"] == "expired" and not preview["eligible"]
    with pytest.raises(health.HealthError, match="expired"):
        health.record(store, source, now_ms=later, apply=True, expected=preview["preview_token"])
    assert target.read_bytes() == current
    view = health.inspect(store, now_ms=later)
    assert view["observed_hours"] == 1 and view["hours"][-1]["sample"]["sample_age_ms"] == 0


def test_same_hour_revision_parent_and_observation_time_are_not_last_write_wins(local):
    store, source = local
    apply(store, source)
    revised = sample(stamp=STAMP + 1000, revision=2, parent=1)
    source.write_text(json.dumps(revised), encoding="utf-8")
    apply(store, source, now_ms=STAMP + 2000)
    target = store / f"{revised['hour'] % health.SLOTS:03d}.json"
    before = target.read_bytes()
    cases = [
        (sample(), "revision-conflict"),
        (sample(stamp=STAMP + 2000, revision=2, parent=1), "revision-conflict"),
        (sample(stamp=STAMP + 2000, revision=3, parent=1), "parent-gap"),
        (sample(stamp=STAMP, revision=3, parent=2), "time-regression"),
    ]
    for candidate, expected in cases:
        source.write_text(json.dumps(candidate), encoding="utf-8")
        result = health.record(store, source, now_ms=STAMP + 2000)
        assert result["status"] == expected and not result["eligible"]
        assert target.read_bytes() == before


@pytest.mark.parametrize("changed", ["input", "policy", "slot"])
def test_exact_preview_inputs_are_rechecked_without_replaying_a_write(local, changed):
    store, source = local
    apply(store, source)
    preview = health.record(store, source, now_ms=STAMP)
    target = store / f"{sample()['hour'] % health.SLOTS:03d}.json"
    selected = {"input": source, "policy": store / "policy.json", "slot": target}[changed]
    selected.write_bytes(selected.read_bytes() + b"\n")
    current = target.read_bytes()
    with pytest.raises(health.HealthError, match="preview-changed"):
        health.record(store, source, now_ms=STAMP, apply=True, expected=preview["preview_token"])
    assert target.read_bytes() == current


def test_writer_lock_rechecks_a_concurrent_record(local, monkeypatch):
    store, source = local
    preview = health.record(store, source, now_ms=STAMP)
    target = store / f"{sample()['hour'] % health.SLOTS:03d}.json"
    concurrent = sample()
    concurrent["domains"] = {}

    @contextmanager
    def changed_lock(_path):
        target.write_text(json.dumps(concurrent), encoding="utf-8")
        yield

    monkeypatch.setattr(health, "mutation_lock", changed_lock)
    with pytest.raises(health.HealthError, match="preview-changed"):
        health.record(store, source, now_ms=STAMP, apply=True, expected=preview["preview_token"])
    assert json.loads(target.read_bytes()) == concurrent


def test_reader_keeps_old_proof_and_absent_domains_unknown(local):
    store, source = local
    old = sample()
    old["domains"]["local_listener"]["observed_at_ms"] -= 2 * health.HOUR_MS
    source.write_text(json.dumps(old), encoding="utf-8")
    apply(store, source)
    view = health.inspect(store, now_ms=STAMP)
    domains = view["hours"][-1]["sample"]["domains"]
    assert domains["local_listener"]["state"] == "stale"
    assert domains["local_listener"]["observed_state"] == "ok"
    assert domains["ssh_authenticated"]["state"] == "unknown"
    assert domains["logical_agents"]["state"] == "unknown" and domains["logical_agents"]["count"] is None
    target = store / f"{old['hour'] % health.SLOTS:03d}.json"
    target.write_text('{"raw":"must-not-be-output"}', encoding="utf-8")
    view = health.inspect(store, now_ms=STAMP)
    assert view["hours"][-1]["state"] == "invalid"
    assert "must-not-be-output" not in json.dumps(view)


def test_oversized_integer_slot_is_invalid_evidence_not_a_reader_failure(local):
    store, _source = local
    target = store / f"{sample()['hour'] % health.SLOTS:03d}.json"
    target.write_bytes(b'{"value":' + b"9" * 5000 + b"}")
    view = health.inspect(store, now_ms=STAMP)
    assert view["hours"][-1]["state"] == "invalid" and view["observed_hours"] == 0


@pytest.mark.parametrize("change", ["publisher", "continuity", "future"])
def test_foreign_or_future_observations_do_not_create_slots_or_locks(local, change):
    store, source = local
    candidate = sample()
    if change == "future":
        candidate = sample(stamp=STAMP + 1)
    else:
        candidate[change] = "different-owner"
    source.write_text(json.dumps(candidate), encoding="utf-8")
    preview = health.record(store, source, now_ms=STAMP)
    assert preview["status"] == ("future" if change == "future" else "owner-mismatch")
    with pytest.raises(health.HealthError):
        health.record(store, source, now_ms=STAMP, apply=True, expected=preview["preview_token"])
    assert set(path.name for path in store.iterdir()) == {"policy.json"}


@pytest.mark.parametrize("raw", [
    b'{"version":1,"version":2}', b'{"value":NaN}', b"\xff",
    b" " * (health.MAX_BYTES + 1),
], ids=["duplicate-key", "nonfinite", "invalid-utf8", "oversize"])
def test_invalid_input_is_bounded_and_not_echoed(local, capsys, raw):
    store, source = local
    source.write_bytes(raw)
    assert cli.main([
        "health", "record", "--input", str(source), "--store-dir", str(store), "--now", NOW.isoformat(),
    ]) == 2
    error = capsys.readouterr().err
    assert str(source) not in error and source.read_bytes() == raw
    assert set(path.name for path in store.iterdir()) == {"policy.json"}


@pytest.mark.parametrize("invalid", ["transport-proof", "unknown-count", "timestamp-unit", "extra-field"])
def test_closed_domain_evidence_does_not_promote_transport_to_authentication(local, invalid):
    store, source = local
    candidate = sample()
    if invalid == "transport-proof":
        candidate["domains"]["ssh_authenticated"] = {
            "state": "ok", "evidence": "ssh-banner", "observed_at_ms": STAMP,
            "reason": "none", "direction": "outbound",
        }
    elif invalid == "unknown-count":
        candidate["domains"]["logical_agents"]["count"] = 0
    elif invalid == "timestamp-unit":
        candidate["observed_at_ms"] *= 1_000_000
    else:
        candidate["domains"]["local_listener"]["raw_error"] = "not-permitted"
    source.write_text(json.dumps(candidate), encoding="utf-8")
    with pytest.raises(health.HealthError):
        health.record(store, source, now_ms=STAMP)
    assert set(path.name for path in store.iterdir()) == {"policy.json"}


def test_input_output_overlap_is_refused_before_writer_lock(local):
    store, source = local
    target = store / f"{sample()['hour'] % health.SLOTS:03d}.json"
    target.write_bytes(source.read_bytes())
    original = target.read_bytes()
    with pytest.raises(health.HealthError, match="input-overlaps-output"):
        health.record(store, target, now_ms=STAMP)
    assert target.read_bytes() == original and not (store / "health.lock").exists()


@pytest.mark.parametrize("kind", ["hardlink", "parent-traversal", "relative"])
def test_unsafe_input_paths_never_enter_the_local_writer(local, kind):
    store, source = local
    if kind == "hardlink":
        alias = source.with_name("alias.json")
        try:
            alias.hardlink_to(source)
        except OSError as error:
            pytest.skip(f"native hardlinks unavailable: {error}")
        source = alias
    elif kind == "parent-traversal":
        source = source.parent / "unused" / ".." / source.name
    else:
        source = Path(source.name)
    with pytest.raises(health.HealthError):
        health.record(store, source, now_ms=STAMP)
    assert set(path.name for path in store.iterdir()) == {"policy.json"}


@pytest.mark.parametrize("state", ["expired", "future", "owner-mismatch"])
def test_reader_does_not_display_old_week_foreign_or_future_slots_as_health(local, state):
    store, source = local
    candidate = sample()
    if state == "owner-mismatch":
        candidate["continuity"] = "different-generation"
    else:
        offset = (-1 if state == "expired" else 1) * health.SLOTS * health.HOUR_MS
        candidate = sample(stamp=STAMP + offset)
    (store / f"{candidate['hour'] % health.SLOTS:03d}.json").write_text(
        json.dumps(candidate), encoding="utf-8",
    )
    result = health.inspect(store, now_ms=STAMP)
    assert result["observed_hours"] == 0 and result["hours"][-1]["state"] == state
    assert "sample" not in result["hours"][-1]


def test_reader_refuses_a_policy_change_during_its_scan(local, monkeypatch):
    store, _source = local
    original = health._read
    policy = store / "policy.json"

    def read(path, validator, **fields):
        result = original(path, validator, **fields)
        if path.name == f"{sample()['hour'] % health.SLOTS:03d}.json":
            policy.write_bytes(policy.read_bytes() + b"\n")
        return result

    monkeypatch.setattr(health, "_read", read)
    with pytest.raises(health.HealthError, match="store-policy-changed"):
        health.inspect(store, now_ms=STAMP)


def test_unknown_write_acknowledgement_is_not_retried_or_reported_as_untouched(local, monkeypatch):
    store, source = local
    original = health._write
    writes = []

    def failed_ack(path, value):
        writes.append(path)
        original(path, value)
        raise OSError("private diagnostic, not output")

    monkeypatch.setattr(health, "_write", failed_ack)
    result = apply(store, source)
    assert result["status"] == "unknown" and result["applied"] is None
    assert len(writes) == 1 and health.inspect(store, now_ms=STAMP)["observed_hours"] == 1
    assert "private diagnostic" not in json.dumps(result)


def test_lock_release_failure_after_publication_keeps_an_unknown_outcome(local, monkeypatch):
    store, source = local
    original = health.mutation_lock

    @contextmanager
    def failed_release(path):
        with original(path):
            yield
        raise OSError("synthetic lock acknowledgement failure")

    monkeypatch.setattr(health, "mutation_lock", failed_release)
    result = apply(store, source)
    assert result["status"] == "unknown" and result["applied"] is None and not result["eligible"]
    assert health.inspect(store, now_ms=STAMP)["observed_hours"] == 1
