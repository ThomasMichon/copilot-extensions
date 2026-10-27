"""Original-operation recovery, old writers and live-creator exclusion."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import sqlite3
import sys
import time
from threading import Event
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from agent_worktrees import creation_requests, execution_spaces, tracking, worktree_creation
from agent_worktrees import receiving_allocation as receiving
from agent_worktrees.state_root import resolve_state_root as real_resolve_state_root
from agent_worktrees.worktree_allocation_receiver import receive
from test_receiving_allocation import receiver as receiver
from test_worktree_allocation_receiver import request


@pytest.mark.parametrize("operation", ["create", "inspect"])
@pytest.mark.parametrize("no_pair", [True, False])
@pytest.mark.parametrize("expected_identity", [True, False])
def test_crash_after_first_save_recovers_same_child_and_nonce(
    receiver, monkeypatch, operation, no_pair, expected_identity,
):
    config, admission, records = receiver
    admission.target["options"]["no_pair"] = no_pair
    original = tracking.create_new_record
    saved = []

    def interrupted(**kwargs):
        record = original(**kwargs)
        saved.append(receiving.record_identity(record))
        raise OSError("interrupted after durable first record")

    monkeypatch.setattr(tracking, "create_new_record", interrupted)
    with pytest.raises(OSError, match="durable first"):
        receive("create", request(receiver), config)
    assert creation_requests.inspect(config, admission.key)["status"] == "pending"
    monkeypatch.setattr(tracking, "create_new_record", original)
    monkeypatch.setattr(
        worktree_creation.git_ops, "create_worktree",
        lambda *a, **k: pytest.fail("recovery must not create another primary child"),
    )
    recovered = receive(
        operation, request(receiver, saved[0].to_dict() if expected_identity else None), config,
    )
    assert recovered["status"] == "allocated" and recovered["allocation_finished"] is True
    assert recovered["identity"] == saved[0].to_dict()
    assert creation_requests.inspect(config, admission.key)["status"] == "complete"
    assert len(list(records.glob("*.yaml"))) == 1
    assert receive("create", request(receiver), config) == recovered


def test_crash_after_tail_checkpoint_does_not_repeat_pairing(receiver, monkeypatch):
    config, admission, _ = receiver
    original = creation_requests._owner_record
    monkeypatch.setattr(
        creation_requests, "_owner_record",
        lambda *a, **k: (_ for _ in ()).throw(OSError("interrupted before final journal commit")),
    )
    with pytest.raises(OSError, match="final journal"):
        receive("create", request(receiver), config)
    row = creation_requests.inspect(config, admission.key)
    assert row["status"] == "pending" and row["worktree"]["receiving_ready"]
    monkeypatch.setattr(creation_requests, "_owner_record", original)
    monkeypatch.setattr(
        worktree_creation, "_complete_created_pair",
        lambda *a, **k: pytest.fail("durably completed tail must not run again"),
    )
    assert receive("inspect", request(receiver), config)["status"] == "allocated"


def test_pairing_recovery_uses_configured_anchor_across_invocation_checkouts(receiver, monkeypatch, tmp_path):
    from agent_procutil.owned_process import run_caller_owned
    from agent_worktrees import state_root

    config, admission, records = receiver
    admission.target["options"]["no_pair"] = False
    other = tmp_path / "other-checkout"
    for checkout in (Path(config.default_repo.anchor), other):
        result = run_caller_owned(
            ["git", "init", "--quiet", str(checkout)],
            timeout=5, max_stdout_bytes=1024, max_stderr_bytes=4096,
        )
        assert result.returncode == 0, result.stderr
    monkeypatch.setattr(state_root, "resolve_state_root", real_resolve_state_root)
    monkeypatch.chdir(config.default_repo.anchor)
    original = tracking.create_new_record

    def first_save(**kwargs):
        original(**kwargs)
        raise OSError("interrupted before companion tail")

    monkeypatch.setattr(tracking, "create_new_record", first_save)
    with pytest.raises(OSError, match="before companion"):
        receive("create", request(receiver), config)
    row = creation_requests.inspect(config, admission.key)
    assert row["worktree"]["receiving_recovery"]["pairing"]["path"] == config.default_repo.anchor
    record = tracking.load_record(next(records.glob("*.yaml")))
    monkeypatch.setattr(tracking, "create_new_record", original)
    monkeypatch.chdir(other)
    assert real_resolve_state_root(config).path == str(other)
    recovered = receive("inspect", request(receiver), config)
    assert recovered["status"] == "allocated"
    assert recovered["identity"] == receiving.record_identity(record).to_dict()
    assert len(list(records.glob("*.yaml"))) == 1


@pytest.mark.parametrize("failure", ["lookup", "no-pair-env", "env-before-reserve"])
def test_required_companion_skip_stays_pending_and_recovers_original_child(receiver, monkeypatch, failure):
    from agent_worktrees import knowledge_plugins, state_root

    config, admission, records = receiver
    admission.target["options"]["no_pair"] = False
    original_root = state_root.StateRoot(
        path=Path(config.default_repo.anchor), source="knowledge_repo", repo="knowledge",
        stateless=True, requires_external=True, bound=True, error=None,
    )
    monkeypatch.setattr(state_root, "resolve_state_root", lambda *a, **k: original_root)
    original = tracking.create_new_record
    identities = []
    if failure == "env-before-reserve":
        original_create = creation_requests.create

        def delayed_create(config, key, facts, allocate, **kwargs):
            def after_facts(reserve):
                monkeypatch.setenv("AGENT_WORKTREES_NO_PAIR", "1")
                return allocate(reserve)
            return original_create(config, key, facts, after_facts, **kwargs)

        monkeypatch.setattr(creation_requests, "create", delayed_create)

    def first_save(**kwargs):
        record = original(**kwargs)
        identities.append(receiving.record_identity(record))
        if failure == "lookup":
            def unavailable(*a, **k):
                raise OSError("transient state-root lookup failure")
            monkeypatch.setattr(state_root, "resolve_state_root", unavailable)
        elif failure == "no-pair-env":
            monkeypatch.setenv("AGENT_WORKTREES_NO_PAIR", "1")
        return record

    monkeypatch.setattr(tracking, "create_new_record", first_save)
    with pytest.raises(worktree_creation.LaunchSeedStagingFailure, match="pairing remains incomplete"):
        receive("create", request(receiver), config)
    row = creation_requests.inspect(config, admission.key)
    assert row["status"] == "pending" and "receiving_ready" not in row["worktree"]
    assert row["worktree"]["receiving_recovery"]["pairing"]["requires_external"] is True
    assert row["facts"]["no_pair"] is False
    record = tracking.load_record(next(records.glob("*.yaml")))
    assert not record.pair_id and not record.pair_ref
    if failure != "lookup":
        with pytest.raises(RuntimeError, match="pairing remains incomplete"):
            receive("create", request(receiver), config)
        assert creation_requests.inspect(config, admission.key)["status"] == "pending"
    monkeypatch.setattr(state_root, "resolve_state_root", lambda *a, **k: original_root)
    monkeypatch.setattr(tracking, "create_new_record", original)
    monkeypatch.delenv("AGENT_WORKTREES_NO_PAIR", raising=False)
    monkeypatch.setattr(
        worktree_creation.git_ops, "create_worktree",
        lambda *a, **k: pytest.fail("companion recovery must not replace the primary child"),
    )
    carved = []

    def carve(*args, **kwargs):
        carved.append(kwargs["harness_id"])
        return {
            "pair_id": f"{kwargs['timestamp']}-{kwargs['suffix']}",
            "pair_role": "harness", "pair_kind": "anchor",
            "pair_ref": f"{admission.target['machine']}/knowledge/anchor",
        }

    monkeypatch.setattr(worktree_creation._core(), "_carve_paired_knowledge", carve)
    monkeypatch.setattr(knowledge_plugins, "compose_from_pair", lambda **kw: {"action": "unchanged"})
    recovered = receive("inspect", request(receiver, identities[0].to_dict()), config)
    assert recovered["status"] == "allocated" and recovered["identity"] == identities[0].to_dict()
    assert carved == [record.worktree_id] and len(list(records.glob("*.yaml"))) == 1
    assert creation_requests.inspect(config, admission.key)["status"] == "complete"


def test_original_no_pair_environment_remains_intentional_after_override_disappears(receiver, monkeypatch):
    config, admission, _ = receiver
    admission.target["options"]["no_pair"] = False
    monkeypatch.setenv("AGENT_WORKTREES_NO_PAIR", "1")
    created = receive("create", request(receiver), config)
    row = creation_requests.inspect(config, admission.key)
    assert row["facts"]["no_pair"] is True
    monkeypatch.delenv("AGENT_WORKTREES_NO_PAIR")
    assert receive("create", request(receiver), config) == created
    assert receive("inspect", request(receiver, created["identity"]), config) == created


@pytest.mark.parametrize("initial_override", [False, True])
@pytest.mark.parametrize("interrupted", [False, True])
def test_identical_create_retries_keep_original_effective_options_across_environment_change(
    receiver, monkeypatch, initial_override, interrupted,
):
    config, admission, records = receiver
    admission.target["options"]["no_pair"] = False
    if initial_override:
        monkeypatch.setenv("AGENT_WORKTREES_NO_PAIR", "1")
    else:
        monkeypatch.delenv("AGENT_WORKTREES_NO_PAIR", raising=False)
    original = tracking.create_new_record
    if interrupted:
        def first_save(**kwargs):
            original(**kwargs)
            raise OSError("response interrupted after first save")
        monkeypatch.setattr(tracking, "create_new_record", first_save)
        with pytest.raises(OSError, match="response interrupted"):
            receive("create", request(receiver), config)
    else:
        receive("create", request(receiver), config)
    record = tracking.load_record(next(records.glob("*.yaml")))
    identity = receiving.record_identity(record).to_dict()
    assert creation_requests.inspect(config, admission.key)["facts"]["no_pair"] is initial_override
    monkeypatch.setattr(tracking, "create_new_record", original)
    if initial_override:
        monkeypatch.delenv("AGENT_WORKTREES_NO_PAIR")
    else:
        monkeypatch.setenv("AGENT_WORKTREES_NO_PAIR", "1")
    monkeypatch.setattr(
        worktree_creation.git_ops, "create_worktree",
        lambda *a, **k: pytest.fail("identical retry must never allocate another primary child"),
    )
    recovered = receive("create", request(receiver), config)
    assert recovered["status"] == "allocated" and recovered["identity"] == identity
    assert creation_requests.inspect(config, admission.key)["facts"]["no_pair"] is initial_override
    assert len(list(records.glob("*.yaml"))) == 1


def test_live_creator_is_not_overtaken_after_its_first_save(receiver, monkeypatch):
    config, admission, records = receiver
    entered, finish = Event(), Event()
    original = tracking.create_new_record

    def paused(**kwargs):
        record = original(**kwargs)
        entered.set()
        assert finish.wait(5)
        return record

    monkeypatch.setattr(tracking, "create_new_record", paused)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(receive, "create", request(receiver), config)
        assert entered.wait(5)
        try:
            observed = receive("inspect", request(receiver), config)
            assert observed["status"] == "pending" and not observed["allocation_finished"]
            assert creation_requests.inspect(config, admission.key)["status"] == "pending"
            assert receive("create", request(receiver), config)["status"] == "pending"
        finally:
            finish.set()
        created = future.result(timeout=5)
    assert created["identity"] == observed["identity"] and created["status"] == "allocated"
    assert len(list(records.glob("*.yaml"))) == 1


@pytest.mark.parametrize("confirmed", [True, False])
@pytest.mark.parametrize("generic_save", [True, False])
def test_older_writer_omitted_receipt_is_recovered_from_exact_original_journal(
    receiver, confirmed, generic_save,
):
    config, _, records = receiver
    created = receive("create", request(receiver), config)
    if confirmed:
        created = receive("confirm", request(receiver, created["identity"]), config)
    path = next(records.glob("*.yaml"))
    raw = yaml.safe_load(path.read_text())
    del raw["receiving_allocation"]
    path.write_text(yaml.safe_dump(raw))
    record = tracking.load_record(path)
    assert not record.receiving_allocation_present
    if generic_save:
        record.title = "ordinary older-writer continuation"
        tracking.save_record(record, path)
        record = tracking.load_record(path)
    execution_spaces.require_record_mutation(record, config)
    assert record.receiving_allocation["phase"] == ("linked" if confirmed else "created")
    tracking.save_record(record, path)
    assert receive("inspect", request(receiver), config) == created


@pytest.mark.parametrize("field,value", [
    ("owner_ref", "remote/parent/replacement"), ("creation_nonce", "replaced"),
    ("branch", "worktree/replaced"), ("worktree_path", "/different/child"),
])
def test_absent_receipt_never_repairs_changed_identity(receiver, field, value):
    config, _, records = receiver
    receive("create", request(receiver), config)
    path = next(records.glob("*.yaml"))
    raw = yaml.safe_load(path.read_text())
    del raw["receiving_allocation"]
    # The YAML stores the checkout under worktree_path.
    raw[field] = value
    path.write_text(yaml.safe_dump(raw))
    record = tracking.load_record(path)
    with pytest.raises(execution_spaces.ExecutionSpaceError):
        execution_spaces.require_record_mutation(record, config)
    assert not record.receiving_allocation_present


@pytest.mark.parametrize("legacy", [False, True])
def test_reverse_receipt_recovery_is_indexed_and_bounded_with_retained_history(receiver, monkeypatch, legacy):
    config, admission, records = receiver
    created = receive("create", request(receiver), config)
    record = tracking.load_record(next(records.glob("*.yaml")))
    record.receiving_allocation = None
    record.receiving_allocation_present = False
    path = records.parent / "workspace-creation.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executemany(
            "INSERT INTO creations VALUES (?, 1, ?, 'complete', ?)",
            (
                (
                    f"unrelated-{index}", json.dumps({"receiving_allocation": {"origin": f"other/p/{index}"}}),
                    json.dumps({"creation_nonce": f"unrelated-{index}"}),
                )
                for index in range(10000)
            ),
        )
        if legacy:
            connection.execute(f"DROP INDEX {creation_requests.RECEIVING_IDENTITY_INDEX}")
    assert receiving.recover_missing(record, records)
    assert record.receiving_allocation["identity"] == created["identity"]
    with sqlite3.connect(path) as connection:
        plan = connection.execute(
            "EXPLAIN QUERY PLAN SELECT key_digest, version, facts, status, worktree FROM creations "
            "WHERE json_extract(facts, '$.receiving_allocation.origin')=? "
            "AND json_extract(worktree, '$.creation_nonce')=?",
            (admission.origin, record.creation_nonce),
        ).fetchall()
    assert any("SEARCH creations USING INDEX creations_receiving_identity" in row[3] for row in plan)
    assert not any("SCAN" in row[3] for row in plan)
    monkeypatch.setattr(
        creation_requests, "_connection",
        lambda *a, **k: pytest.fail("ordinary recovery must not reopen a journal schema writer"),
    )
    connect = sqlite3.connect
    instructions = []

    def measured_connect(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connection.set_progress_handler(lambda: instructions.append(100) or 0, 100)
        return connection

    monkeypatch.setattr(receiving.sqlite3, "connect", measured_connect)
    record.receiving_allocation = None
    record.receiving_allocation_present = False
    assert receiving.recover_missing(record, records)
    assert sum(instructions) < 1000
    assert record.receiving_allocation["identity"] == created["identity"]


@pytest.mark.parametrize("status", ["finalized", "archived"])
def test_old_writer_terminal_save_retains_actual_conclusion(receiver, status):
    config, _, records = receiver
    created = receive("create", request(receiver), config)
    path = next(records.glob("*.yaml"))
    raw = yaml.safe_load(path.read_text())
    del raw["receiving_allocation"]
    raw["status"] = status
    path.write_text(yaml.safe_dump(raw))
    result = receive("inspect", request(receiver), config)
    assert result["status"] == status and result["identity"] == created["identity"]
    path.unlink()
    assert receive("inspect", request(receiver), config) == result


@pytest.mark.parametrize("concluded,restored", [
    ("finalized", "active"), ("archived", "active"), ("archived", "finalized"),
])
@pytest.mark.parametrize("keep_receipt", [False, True])
def test_older_snapshot_cannot_reopen_retained_conclusion(receiver, concluded, restored, keep_receipt):
    config, _, records = receiver
    created = receive("create", request(receiver), config)
    path = next(records.glob("*.yaml"))
    raw = yaml.safe_load(path.read_text())
    record = tracking.load_record(path)
    record.status = concluded
    tracking.save_record(record, path)
    raw["status"] = restored
    if not keep_receipt:
        del raw["receiving_allocation"]
    path.write_text(yaml.safe_dump(raw))
    stale = tracking.load_record(path)
    with pytest.raises(execution_spaces.ExecutionSpaceError, match="concluded"):
        execution_spaces.require_record_mutation(stale, config)
    with pytest.raises(ValueError, match="concluded"):
        tracking.save_record(stale, path)
    evidence = receive("inspect", request(receiver), config)
    assert evidence["status"] == concluded and evidence["identity"] == created["identity"]


@pytest.mark.parametrize("pair_kind,sibling_fault", [
    ("anchor", None), ("worktree", None), ("worktree", "missing"),
    ("worktree", "owner"), ("worktree", "id"), ("worktree", "finalized"), ("worktree", "nonce"),
])
def test_existing_stamped_pair_retries_composition_without_recarving(
    receiver, monkeypatch, pair_kind, sibling_fault,
):
    from agent_worktrees import knowledge_plugins, paired_claims

    config, admission, records = receiver
    admission.target["options"]["no_pair"] = False
    companion = Path(config.default_repo.anchor)
    calls = []

    def carve(*args, **kwargs):
        calls.append(kwargs["harness_id"])
        return {
            "pair_id": f"{kwargs['timestamp']}-{kwargs['suffix']}",
            "pair_role": "harness",
            "pair_ref": f"{admission.target['machine']}/knowledge/{kwargs['harness_id']}-k",
            "pair_kind": pair_kind,
        }

    def interrupted(**kwargs):
        raise knowledge_plugins.KnowledgePluginError("transient composition interruption")

    monkeypatch.setattr(worktree_creation._core(), "_carve_paired_knowledge", carve)
    monkeypatch.setattr(knowledge_plugins, "compose_from_pair", interrupted)
    with pytest.raises(worktree_creation.LaunchSeedStagingFailure, match="pairing remains incomplete"):
        receive("create", request(receiver), config)
    record = tracking.load_record(next(records.glob("*.yaml")))
    assert record.pair_id
    monkeypatch.setattr(
        knowledge_plugins, "resolve_pair",
        lambda **kw: SimpleNamespace(
            pair_id=record.pair_id, pair_kind=pair_kind, harness_path=Path(record.worktree_path),
            knowledge_path=companion, knowledge_repo="",
        ),
    )
    sibling = SimpleNamespace(
        worktree_id=record.worktree_id + "-k", owner_ref=receiving.record_identity(record).ref,
        creation_nonce="original-companion", status="active",
    )
    monkeypatch.setattr(paired_claims, "reciprocal_sibling", lambda r: sibling)
    if sibling_fault:
        if sibling_fault == "missing":
            monkeypatch.setattr(paired_claims, "reciprocal_sibling", lambda r: None)
        else:
            name, value = {
                "owner": ("owner_ref", "wrong/owner/record"),
                "id": ("worktree_id", "replacement"),
                "finalized": ("status", "finalized"), "nonce": ("creation_nonce", None),
            }[sibling_fault]
            old = getattr(sibling, name)
            setattr(sibling, name, value)
        with pytest.raises(ValueError, match="reciprocal record"):
            receive("inspect", request(receiver), config)
        assert creation_requests.inspect(config, admission.key)["status"] == "pending"
        if sibling_fault == "missing":
            monkeypatch.setattr(paired_claims, "reciprocal_sibling", lambda r: sibling)
        else:
            setattr(sibling, name, old)
    # Typed composition failures propagate to the CLI's explicit RuntimeError
    # refusal handler; they must never publish an allocation success.
    with pytest.raises(knowledge_plugins.KnowledgePluginError, match="composition interruption"):
        receive("inspect", request(receiver), config)
    assert creation_requests.inspect(config, admission.key)["status"] == "pending"
    monkeypatch.setattr(knowledge_plugins, "compose_from_pair", lambda **kw: {"action": "unchanged"})
    recovered = receive("inspect", request(receiver), config)
    assert recovered["status"] == "allocated"
    assert recovered["identity"] == receiving.record_identity(record).to_dict()
    assert calls == [record.worktree_id]


def test_original_operation_exclusion_crosses_a_real_process_boundary(receiver, monkeypatch, tmp_path):
    from agent_procutil.owned_process import run_caller_owned

    config, admission, records = receiver
    original = tracking.create_new_record

    def interrupted(**kwargs):
        original(**kwargs)
        raise OSError("interrupted first save")

    monkeypatch.setattr(tracking, "create_new_record", interrupted)
    with pytest.raises(OSError, match="first save"):
        receive("create", request(receiver), config)
    monkeypatch.setattr(tracking, "create_new_record", original)
    digest = hashlib.sha256(admission.key.encode()).hexdigest()
    operation = records.parent / "creation-operations" / f"{digest}.yaml"
    ready, release = tmp_path / "ready", tmp_path / "release"
    script = """
import sys,time
from pathlib import Path
from agent_worktrees import tracking
operation,ready,release = map(Path, sys.argv[1:])
with tracking._RecordLock(operation, require_sidecar=True):
    ready.write_text("exclusive")
    deadline = time.monotonic() + 5
    while not release.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("test owner was not released")
        time.sleep(0.01)
"""
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            run_caller_owned,
            [sys.executable, "-c", script, str(operation), str(ready), str(release)],
            timeout=8, max_stdout_bytes=1024, max_stderr_bytes=4096,
        )
        try:
            deadline = time.monotonic() + 3
            while not ready.exists() and not future.done() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert ready.exists(), future.result(timeout=8) if future.done() else "owner not ready"
            result = receive("inspect", request(receiver), config)
            assert result["status"] == "pending" and not result["allocation_finished"]
            assert creation_requests.inspect(config, admission.key)["status"] == "pending"
        finally:
            release.write_text("release")
        completed = future.result(timeout=8)
    assert completed.returncode == 0, completed.stderr
    assert receive("inspect", request(receiver), config)["status"] == "allocated"
