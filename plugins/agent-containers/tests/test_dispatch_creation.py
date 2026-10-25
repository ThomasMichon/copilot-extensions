"""Provider binding storage, not qualified guest/native execution."""

import copy
import importlib
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from agent_containers import dispatch_creation as creation
from agent_containers import dispatch_projection as projection, lease, lifecycle
from . import test_dispatch_projection as fixtures

target = fixtures.target
PHYSICAL_ID = fixtures.PHYSICAL_ID
FACTS = fixtures.FACTS


@pytest.fixture
def prepared(target, monkeypatch, tmp_path):
    monkeypatch.setattr(projection, "_STATE", tmp_path / "fresh-bindings")
    projection.register_birth(PHYSICAL_ID, "c" * 32, creation_custody=True)
    binding = projection.prepare("pool-1", PHYSICAL_ID, FACTS)
    association = {
        "admission": {"installation": "installation-1", "epoch": 3, "producer": "producer-1"},
        "handoff_id": "e" * 32, "key_digest": "f" * 64, "facts_digest": "1" * 64,
        "session_id": "session-1", "consumer_incarnation": "consumer-1",
        "runtime": {
            "root": str(tmp_path / "runtime"), "version": "1.0.0+provider-exec",
            "payload_hash": "2" * 64, "incarnation": "provider-1",
        },
    }
    return binding["binding_id"], association


def _retain(prepared):
    binding, association = prepared
    return creation.retain("pool-1", PHYSICAL_ID, binding, association)


def _read(prepared):
    binding, association = prepared
    return creation.read(PHYSICAL_ID, binding, association)


def _bytes():
    path = projection._path(PHYSICAL_ID)
    return path.read_bytes(), path.stat().st_mtime_ns


def test_explicit_birth_is_required_and_legacy_binding_is_not_upgraded(target, tmp_path):
    binding = projection.prepare("pool-1", PHYSICAL_ID, FACTS)
    before = _bytes()
    with pytest.raises(RuntimeError, match="explicit provider birth"):
        creation.retain("pool-1", PHYSICAL_ID, binding["binding_id"], {
            "admission": {"installation": "installation-1", "epoch": 1, "producer": "producer-1"},
            "handoff_id": "e" * 32, "key_digest": "f" * 64, "facts_digest": "1" * 64,
            "session_id": "session-1", "consumer_incarnation": "consumer-1",
            "runtime": {
                "root": str(tmp_path / "runtime"), "version": "1.0.0+provider-exec",
                "payload_hash": "2" * 64, "incarnation": "provider-1",
            },
        })
    assert _bytes() == before
    assert binding["version"] == 1
    assert "creation" not in binding


def test_retention_is_original_idempotent_and_cannot_replace_birth(prepared):
    assert _retain(prepared) == {
        "association": prepared[1], "closed": False, "started_at": None,
    }
    before = _bytes()
    assert _retain(prepared) == _read(prepared)
    assert _bytes() == before
    with pytest.raises(FileExistsError):
        projection.register_birth(PHYSICAL_ID, "d" * 32, creation_custody=True)
    assert _bytes() == before


@pytest.mark.parametrize("field", [
    "handoff_id", "key_digest", "facts_digest", "session_id", "consumer_incarnation",
    "admission.installation", "admission.epoch", "admission.producer",
    "runtime.root", "runtime.version", "runtime.payload_hash", "runtime.incarnation",
])
def test_changed_owner_or_original_cannot_replace_association(prepared, field, tmp_path):
    _retain(prepared)
    changed = copy.deepcopy(prepared[1])
    parent, _, key = field.rpartition(".")
    document = changed[parent] if parent else changed
    document[key] = (
        4 if key == "epoch" else str(tmp_path / "other-runtime") if key == "root"
        else "b" * len(document[key]) if key.endswith("digest") or key in ("handoff_id", "payload_hash")
        else "other"
    )
    before = _bytes()
    with pytest.raises(RuntimeError, match="original association"):
        creation.retain("pool-1", PHYSICAL_ID, prepared[0], changed)
    assert _bytes() == before


@pytest.mark.parametrize("closed_first", [False, True])
def test_one_shot_and_closure_retain_unknown_without_launching(prepared, monkeypatch, closed_first):
    _retain(prepared)
    binding, association = prepared
    monkeypatch.setattr(
        projection.subprocess, "run",
        lambda *a, **kw: pytest.fail("storage attempted provider or guest execution"),
    )
    if closed_first:
        creation.close(PHYSICAL_ID, binding, association)
        with pytest.raises(RuntimeError, match="admission is closed"):
            creation.begin(PHYSICAL_ID, binding, association, now=10.0)
        assert _read(prepared)["started_at"] is None
    else:
        assert creation.begin(PHYSICAL_ID, binding, association, now=10.0) is True
        assert creation.begin(PHYSICAL_ID, binding, association, now=20.0) is False
        creation.close(PHYSICAL_ID, binding, association)
        assert _read(prepared)["started_at"] == 10.0
    before = _bytes()
    assert creation.close(PHYSICAL_ID, binding, association)["closed"] is True
    assert _bytes() == before
    importlib.reload(creation)
    assert _read(prepared)["closed"] is True
    assert _retain(prepared) == _read(prepared)
    assert _bytes() == before


def test_wrapper_admission_cleanup_cannot_remove_original_creation(prepared):
    _retain(prepared)
    binding, association = prepared
    assert creation.begin(PHYSICAL_ID, binding, association, now=10.0)
    before = _bytes()
    with lease.session_admission("pool-1"):
        assert _read(prepared)["started_at"] == 10.0
    assert _read(prepared)["started_at"] == 10.0
    assert _bytes() == before


@pytest.mark.parametrize("second_action", ["begin", "close"])
def test_separate_processes_serialize_original_attempt_and_closure(prepared, second_action):
    _retain(prepared)
    binding, association = prepared
    code = """
import json, sys
from pathlib import Path
from agent_containers import dispatch_creation as creation, dispatch_projection as projection
projection._STATE = Path(sys.argv[1])
association = json.loads(sys.argv[4])
if sys.argv[5] == 'close':
    creation.close(sys.argv[2], sys.argv[3], association)
    result = 'closed'
else:
    try:
        result = 'began' if creation.begin(sys.argv[2], sys.argv[3], association, now=10.0) else 'already'
    except RuntimeError as exc:
        if str(exc) != 'Retained provider creation admission is closed':
            raise
        result = 'refused'
print(json.dumps(result))
"""
    env = {
        **os.environ,
        "AGENT_CONTAINERS_HOME": str(projection._STATE.parent),
        "AGENT_CONTAINERS_STATE_DIR": str(projection._STATE.parent),
    }

    def operate(action):
        result = subprocess.run(
            [sys.executable, "-I", "-c", code, str(projection._STATE),
             PHYSICAL_ID, binding, json.dumps(association), action],
            env=env, capture_output=True, text=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(operate, ["begin", second_action]))
    observed = _read(prepared)
    if second_action == "begin":
        assert sorted(results) == ["already", "began"]
        assert observed["started_at"] == 10.0 and observed["closed"] is False
    else:
        assert results in (["began", "closed"], ["refused", "closed"])
        assert observed["closed"] is True
        assert observed["started_at"] == (10.0 if results[0] == "began" else None)


@pytest.mark.parametrize("change", ["dev", "epoch", "extra", "native-owner", "runtime-root"])
def test_invalid_original_association_never_writes_or_queries_provider(
    prepared, monkeypatch, change,
):
    from agent_containers import resolver

    association = copy.deepcopy(prepared[1])
    if change == "dev":
        association["runtime"]["version"] = "dev"
    elif change == "epoch":
        association["admission"]["epoch"] = True
    elif change == "extra":
        association["native_session_id"] = "not-an-accepted-native-owner"
    elif change == "native-owner":
        association["consumer_incarnation"] = ""
    else:
        association["runtime"]["root"] = "relative-root"
    before = _bytes()
    monkeypatch.setattr(
        resolver, "resolve_live_exec_target", lambda *a, **kw: pytest.fail("invalid owner queried provider"),
    )
    with pytest.raises(RuntimeError):
        creation.retain("pool-1", PHYSICAL_ID, prepared[0], association)
    assert _bytes() == before


def test_retained_liveness_probe_reads_one_binding_without_guest_work(prepared, monkeypatch):
    _retain(prepared)
    read = projection._read
    reads = []

    def counted(container_id):
        reads.append(container_id)
        return read(container_id)

    monkeypatch.setattr(projection, "_read", counted)
    monkeypatch.setattr(
        projection.subprocess, "run", lambda *a, **kw: pytest.fail("probe reached guest"),
    )
    inspected = lifecycle.inspect_container(PHYSICAL_ID)
    assert projection.probe_worker_liveness(PHYSICAL_ID, inspected).state == "unknown"
    assert reads == [PHYSICAL_ID]


@pytest.mark.parametrize("boundary", ["verify", "prepare", "spawn", "reserve", "probe", "close"])
def test_ordinary_owners_cannot_downgrade_retained_creation(prepared, target, monkeypatch, boundary):
    _retain(prepared)
    binding, _ = prepared
    before = _bytes()
    monkeypatch.setattr(
        projection.subprocess, "run",
        lambda *a, **kw: pytest.fail("ordinary path attempted guest preparation"),
    )
    inspected = lifecycle.inspect_container(PHYSICAL_ID)
    if boundary == "verify":
        with pytest.raises(RuntimeError, match="cannot use ordinary"):
            projection.verify_binding(PHYSICAL_ID, binding)
    elif boundary == "prepare":
        with pytest.raises(RuntimeError, match="cannot use ordinary"):
            projection.prepare("pool-1", PHYSICAL_ID, FACTS)
    elif boundary == "spawn":
        with pytest.raises(RuntimeError, match="cannot use ordinary"):
            projection.build_spawn_command(
                target, projection._read(PHYSICAL_ID), "copilot --acp",
                worker_admission=(1, "e" * 32),
            )
    elif boundary == "reserve":
        with lease.deploy_hold("pool-1", "stop") as hold:
            with pytest.raises(RuntimeError, match="cannot use ordinary"):
                projection._reserve_worker_admission(
                    "pool-1", PHYSICAL_ID, inspected, hold_token=hold.token,
                )
    elif boundary == "probe":
        assert projection.probe_worker_liveness(PHYSICAL_ID, inspected).state == "unknown"
    else:
        with lease.deploy_hold("pool-1", "stop") as hold:
            assert projection.close_worker_admission(
                "pool-1", PHYSICAL_ID, inspected, hold_token=hold.token,
            ).state == "unknown"
    assert _bytes() == before


@pytest.mark.parametrize("change", ["physical", "birth", "hold", "admission"])
def test_new_association_refuses_wrong_or_busy_provider_before_retention(prepared, target, change):
    before = _bytes()
    if change == "physical":
        target.container_id = fixtures.OTHER_ID
    elif change == "birth":
        prepared = ("d" * 32, prepared[1])
    if change == "hold":
        with lease.deploy_hold("pool-1", "remove"):
            with pytest.raises(RuntimeError, match="busy"):
                _retain(prepared)
    elif change == "admission":
        with lease.session_admission("pool-1"):
            with pytest.raises(RuntimeError, match="busy"):
                _retain(prepared)
    else:
        with pytest.raises(RuntimeError):
            _retain(prepared)
    assert _bytes() == before


@pytest.mark.parametrize("change", [
    "closed-type", "time-type", "time-negative", "admission-epoch", "missing-creation",
    "missing-generation", "ordinary-owner", "ordinary-generation", "duplicate", "linked",
])
def test_malformed_retained_binding_is_not_empty_or_available(prepared, change):
    _retain(prepared)
    raw = projection._read(PHYSICAL_ID)
    if change == "closed-type":
        raw["creation"]["closed"] = 1
    elif change == "time-type":
        raw["creation"]["started_at"] = True
    elif change == "time-negative":
        raw["creation"]["started_at"] = -1
    elif change == "admission-epoch":
        raw["creation"]["association"]["admission"]["epoch"] = True
    elif change == "missing-creation":
        del raw["creation"]
    elif change == "missing-generation":
        del raw["worker_admission_generation"]
    elif change == "ordinary-owner":
        raw["owner_session_id"] = "ordinary-native"
    elif change == "ordinary-generation":
        raw["worker_admission_generation"] = 1
    if change == "linked":
        path = projection._path(PHYSICAL_ID)
        destination = path.with_name("linked-binding.json")
        path.rename(destination)
        path.symlink_to(destination)
    elif change == "duplicate":
        projection._path(PHYSICAL_ID).write_text(
            json.dumps(raw).replace('"closed": false', '"closed": false, "closed": true'),
            encoding="utf-8",
        )
    else:
        projection.atomic_write_json(projection._path(PHYSICAL_ID), raw)
    before = _bytes()
    with pytest.raises(RuntimeError, match="binding is (invalid|unreadable)"):
        _read(prepared)
    assert _bytes() == before
