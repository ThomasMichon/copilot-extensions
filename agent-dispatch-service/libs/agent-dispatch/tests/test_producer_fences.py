"""Durable, authenticated producer-generation fences for task creation."""

from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from agent_dispatch import telemetry
from agent_dispatch.coordinator import create_app
from agent_dispatch.queue import (
    ProducerFenceError,
    ProducerScopeValidationError,
)
from tests._helpers import OTHER_REPO, TEST_REPO
from tests._helpers import RepoDefaultingQueue as TaskQueue

SOURCE = "scheduled"
REQUIRED_LABEL = "nightly"
SCOPE = {"repo": TEST_REPO, "source": SOURCE}
CONTROL_TOKEN = "control-secret"


def _activate(
    queue: TaskQueue,
    *,
    producer_id: str = "producer-a",
    expected_generation: int = 0,
    required_label: str | None = REQUIRED_LABEL,
):
    return queue.handoff_producer_scope(
        TEST_REPO,
        SOURCE,
        producer_id=producer_id,
        expected_generation=expected_generation,
        required_label=required_label,
    )


def _create(
    queue: TaskQueue,
    capability: str,
    *,
    producer_id: str = "producer-a",
    generation: int = 1,
    request_id: str = "request-1",
    title: str = "bounded sweep",
    labels: list[str] | None = None,
    dedup_key: str | None = None,
    **kwargs,
):
    return queue.create(
        title,
        repo=TEST_REPO,
        source=SOURCE,
        labels=[REQUIRED_LABEL] if labels is None else labels,
        dedup_key=dedup_key,
        producer_scope=SCOPE,
        producer_id=producer_id,
        producer_generation=generation,
        producer_capability=capability,
        producer_request_id=request_id,
        **kwargs,
    )


def _complete(queue: TaskQueue, task_id: str) -> None:
    owner = "machine/worktree"
    assert queue.claim_one(owner, repo=TEST_REPO, task_id=task_id) is not None
    queue.start(task_id, owner)
    queue.complete(task_id, owner)


def test_unmanaged_scope_preserves_ordinary_create_behavior(tmp_path):
    queue = TaskQueue(tmp_path / "tasks.db")

    task = queue.create("ordinary", source=SOURCE, labels=[REQUIRED_LABEL])
    status = queue.producer_scope_status(TEST_REPO, SOURCE)

    assert task.producer_fence is None
    assert status.managed is False
    assert status.scope == SCOPE


def test_managed_source_cannot_be_bypassed_by_omitting_or_varying_label(tmp_path):
    queue = TaskQueue(tmp_path / "tasks.db")
    transition = _activate(queue)
    capability = transition.producer_capability
    assert capability

    with pytest.raises(ProducerFenceError) as omitted_source:
        queue.create(
            "unfenced label without source",
            repo=TEST_REPO,
            labels=[REQUIRED_LABEL],
        )
    assert omitted_source.value.reason == "missing_fence"
    assert omitted_source.value.source == SOURCE

    with pytest.raises(ProducerFenceError) as alternate_source:
        queue.create(
            "unfenced label with alternate source",
            repo=TEST_REPO,
            source="manual",
            labels=[REQUIRED_LABEL],
        )
    assert alternate_source.value.reason == "missing_fence"
    assert alternate_source.value.source == SOURCE

    with pytest.raises(ProducerFenceError) as alternate_scope:
        queue.create(
            "fenced label with alternate source",
            repo=TEST_REPO,
            source="manual",
            labels=[REQUIRED_LABEL],
            producer_scope={"repo": TEST_REPO, "source": "manual"},
            producer_id="producer-a",
            producer_generation=1,
            producer_capability=capability,
            producer_request_id="alternate-source",
        )
    assert alternate_scope.value.reason == "required_label_scope_mismatch"
    assert alternate_scope.value.source == SOURCE

    with pytest.raises(ProducerFenceError) as omitted_fence:
        queue.create("unfenced", repo=TEST_REPO, source=SOURCE, labels=[])
    assert omitted_fence.value.reason == "missing_fence"

    with pytest.raises(ProducerFenceError) as omitted_label:
        _create(queue, capability, labels=[])
    assert omitted_label.value.reason == "required_label_missing"

    accepted = _create(
        queue,
        capability,
        labels=["another", REQUIRED_LABEL],
    )
    assert accepted.producer_fence == {
        "scope": SCOPE,
        "producer_id": "producer-a",
        "generation": 1,
        "request_id": "request-1",
    }


def test_required_label_cannot_be_owned_by_multiple_sources(tmp_path):
    queue = TaskQueue(tmp_path / "tasks.db")
    _activate(queue)

    with pytest.raises(ProducerFenceError) as duplicate:
        queue.handoff_producer_scope(
            TEST_REPO,
            "reactive",
            producer_id="producer-b",
            expected_generation=0,
            required_label=REQUIRED_LABEL,
        )

    assert duplicate.value.reason == "required_label_conflict"


def test_required_label_ownership_is_global_across_repo_lanes(tmp_path):
    queue = TaskQueue(tmp_path / "tasks.db")
    _activate(queue)

    with pytest.raises(ProducerFenceError) as duplicate:
        queue.handoff_producer_scope(
            OTHER_REPO,
            "reactive",
            producer_id="producer-b",
            expected_generation=0,
            required_label=REQUIRED_LABEL,
        )

    assert duplicate.value.reason == "required_label_conflict"
    assert duplicate.value.detail(operation="transition")["owning_repo"] == TEST_REPO


def test_scope_activation_refuses_pre_fence_nonterminal_label_rows(tmp_path):
    queue = TaskQueue(tmp_path / "tasks.db")
    queued = [
        queue.create(f"legacy-{index}", labels=[REQUIRED_LABEL])
        for index in range(21)
    ]
    proposed = queue.propose("legacy-draft", labels=[REQUIRED_LABEL])

    with pytest.raises(ProducerFenceError) as blocked:
        _activate(queue)

    detail = blocked.value.detail(operation="transition")
    assert detail["reason"] == "scope_not_quiescent"
    assert detail["blocking_task_count"] == 22
    assert len(detail["blocking_task_ids"]) == 20
    assert detail["blocking_ids_truncated"] is True
    assert detail["blocking_status_counts"] == {"proposed": 1, "queued": 21}
    assert set(detail["blocking_task_ids"]).issubset(
        {task.id for task in [*queued, proposed]}
    )
    assert queue.producer_scope_status(TEST_REPO, SOURCE).managed is False


def test_scope_handoff_refuses_injected_nonterminal_label_row(tmp_path):
    db = tmp_path / "tasks.db"
    queue = TaskQueue(db)
    capability = _activate(queue).producer_capability
    assert capability
    valid = _create(queue, capability, request_id="valid")
    injected = queue.create("injected", source="manual")
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE tasks SET source = ?, labels = ? WHERE id = ?",
            (SOURCE, f'["{REQUIRED_LABEL}"]', injected.id),
        )

    with pytest.raises(ProducerFenceError) as blocked:
        _activate(queue, producer_id="producer-b", expected_generation=1)

    detail = blocked.value.detail(operation="transition")
    assert detail["reason"] == "scope_not_quiescent"
    assert detail["blocking_task_count"] == 1
    assert detail["blocking_task_ids"] == [injected.id]
    assert detail["blocking_status_counts"] == {"queued": 1}
    state = queue.producer_scope_status(TEST_REPO, SOURCE)
    assert state.current_generation == 1
    assert state.active_producer == "producer-a"
    assert queue.get(valid.id) is not None


def test_repo_lane_isolates_scopes_and_dedup_keys(tmp_path):
    queue = TaskQueue(tmp_path / "tasks.db")
    _activate(queue)

    other = queue.create(
        "same source elsewhere",
        repo=OTHER_REPO,
        source=SOURCE,
        labels=[],
        dedup_key="same-key",
    )
    local = queue.create(
        "ordinary local key",
        repo=TEST_REPO,
        source="manual",
        dedup_key="same-key",
    )

    assert other.repo == OTHER_REPO
    assert local.repo == TEST_REPO
    assert other.id != local.id
    assert queue.producer_scope_status(OTHER_REPO, SOURCE).managed is False


def test_globally_managed_label_rejects_create_in_another_repo(tmp_path):
    queue = TaskQueue(tmp_path / "tasks.db")
    _activate(queue)

    with pytest.raises(ProducerFenceError) as rejected:
        queue.create(
            "cross-lane bypass",
            repo=OTHER_REPO,
            source=SOURCE,
            labels=[REQUIRED_LABEL],
        )

    detail = rejected.value.detail(operation="create")
    assert detail["reason"] == "required_label_scope_mismatch"
    assert detail["repo"] == OTHER_REPO
    assert detail["owning_repo"] == TEST_REPO


def test_repo_selectors_and_existing_rows_are_canonicalized(tmp_path):
    db = tmp_path / "tasks.db"
    queue = TaskQueue(db)
    raw_repo = "https://Example.com/acme/widget.git"
    task = queue.create(
        "canonical",
        repo=raw_repo,
        requires=[f"repo:{raw_repo}"],
    )

    assert task.repo == TEST_REPO
    assert task.requires == [f"repo:{TEST_REPO}"]
    assert [row.id for row in queue.list(repo=raw_repo)] == [task.id]
    assert queue.claim_one(
        "machine/worktree", repo=raw_repo, task_id=task.id
    ) is not None

    legacy_db = tmp_path / "legacy.db"
    legacy = TaskQueue(legacy_db)
    old = legacy.create("legacy spelling", repo=TEST_REPO)
    with sqlite3.connect(legacy_db) as conn:
        conn.execute(
            "UPDATE tasks SET repo = ?, requires = ?, excludes = ? WHERE id = ?",
            (
                raw_repo,
                f'["repo:{raw_repo}"]',
                "[]",
                old.id,
            ),
        )
    restarted = TaskQueue(legacy_db)
    assert restarted.get(old.id).repo == TEST_REPO
    assert restarted.get(old.id).requires == [f"repo:{raw_repo}"]
    assert restarted.list(repo=raw_repo)[0].id == old.id
    assert restarted.claim_one(
        "machine/legacy", repo=raw_repo, task_id=old.id
    ) is not None


def test_malformed_legacy_repo_selector_does_not_block_migration(tmp_path):
    db = tmp_path / "tasks.db"
    queue = TaskQueue(db)
    task = queue.create("legacy malformed selector")
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE tasks SET requires = ? WHERE id = ?",
            ('["repo:"]', task.id),
        )

    restarted = TaskQueue(db)
    assert restarted.get(task.id).requires == ["repo:"]


def test_legacy_raw_selector_request_hash_replays_after_migration(tmp_path):
    db = tmp_path / "tasks.db"
    queue = TaskQueue(db)
    capability = _activate(queue).producer_capability
    assert capability
    raw_repo = "https://Example.com/acme/widget.git"
    raw_selector = f"repo:{raw_repo}"
    accepted = _create(
        queue,
        capability,
        request_id="legacy-hash",
        requires=[raw_selector],
    )
    legacy_hash = queue._producer_request_hash(
        {
            "title": "bounded sweep",
            "repo": TEST_REPO,
            "prompt": "",
            "status": "queued",
            "requires": [raw_selector],
            "excludes": [],
            "affinity": {},
            "labels": [REQUIRED_LABEL],
            "payload_ref": None,
            "payload_inline": None,
            "target_machine": None,
            "target_worktree": None,
            "target_repo": None,
            "source": SOURCE,
            "origin_ref": None,
            "evaluator_ref": None,
            "dedup_key": None,
            "producer_scope": SCOPE,
            "producer_id": "producer-a",
            "producer_generation": 1,
            "goal": None,
            "done_criteria": None,
        }
    )
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE tasks SET requires = ?, producer_request_hash = ? WHERE id = ?",
            (f'["{raw_selector}"]', legacy_hash, accepted.id),
        )
        conn.execute(
            "UPDATE producer_create_requests SET request_hash = ? "
            "WHERE request_id = ?",
            (legacy_hash, "legacy-hash"),
        )
    _activate(queue, producer_id="producer-b", expected_generation=1)

    replay = _create(
        TaskQueue(db),
        capability,
        request_id="legacy-hash",
        requires=[raw_selector],
    )
    assert replay.id == accepted.id


def test_managed_create_requires_complete_capability_request_tuple(tmp_path):
    queue = TaskQueue(tmp_path / "tasks.db")
    capability = _activate(queue).producer_capability
    assert capability

    with pytest.raises(ProducerScopeValidationError):
        queue.create(
            "partial",
            repo=TEST_REPO,
            source=SOURCE,
            producer_scope=SCOPE,
            producer_id="producer-a",
            producer_generation=1,
        )

    with pytest.raises(ProducerScopeValidationError):
        queue.create(
            "missing capability only",
            repo=TEST_REPO,
            source=SOURCE,
            labels=[REQUIRED_LABEL],
            producer_scope=SCOPE,
            producer_id="producer-a",
            producer_generation=1,
            producer_request_id="missing-capability",
        )

    with pytest.raises(ProducerFenceError) as spoofed:
        _create(queue, capability, producer_id="producer-b")
    assert spoofed.value.reason == "wrong_producer"

    with pytest.raises(ProducerFenceError) as wrong_capability:
        _create(queue, "wrong-capability")
    assert wrong_capability.value.reason == "invalid_capability"


def test_capabilities_are_one_time_hash_only_and_generations_are_monotonic(tmp_path):
    db = tmp_path / "tasks.db"
    queue = TaskQueue(db)

    first = _activate(queue, producer_id="producer-a", expected_generation=0)
    assert first.producer_capability
    first_capability = first.producer_capability
    first_status = queue.producer_scope_status(TEST_REPO, SOURCE)
    assert "producer_capability" not in first_status.__dict__

    second = _activate(queue, producer_id="producer-b", expected_generation=1)
    assert second.producer_capability
    assert second.producer_capability != first_capability
    selected = _create(
        queue,
        second.producer_capability,
        producer_id="producer-b",
        generation=2,
        request_id="generation-2-request",
    )
    assert selected.producer_fence["generation"] == 2

    with pytest.raises(ProducerFenceError) as stale:
        _create(
            queue,
            first_capability,
            producer_id="producer-a",
            generation=1,
            request_id="late",
        )
    assert stale.value.reason == "stale_generation"

    rollback = _activate(queue, producer_id="producer-a", expected_generation=2)
    assert rollback.state.current_generation == 3
    assert rollback.state.active_producer == "producer-a"

    restarted = TaskQueue(db).producer_scope_status(TEST_REPO, SOURCE)
    assert restarted == rollback.state
    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            "SELECT capability_hash FROM producer_scope_generations "
            "WHERE repo = ? AND source = ?",
            (TEST_REPO, SOURCE),
        ).fetchall()
    assert rows
    assert all(row[0] not in {first_capability, second.producer_capability} for row in rows)


def test_lost_transition_response_replay_never_reveals_capability(tmp_path):
    queue = TaskQueue(tmp_path / "tasks.db")

    first = _activate(queue, producer_id="producer-a", expected_generation=0)
    assert first.producer_capability
    replay = _activate(queue, producer_id="producer-a", expected_generation=0)
    assert replay.replayed is True
    assert replay.producer_capability is None
    assert replay.state.current_generation == 1

    with pytest.raises(ProducerFenceError) as wrong_replay:
        _activate(queue, producer_id="producer-b", expected_generation=0)
    assert wrong_replay.value.reason == "generation_mismatch"

    recovery = _activate(queue, producer_id="producer-a", expected_generation=1)
    assert recovery.state.current_generation == 2
    assert recovery.producer_capability


def test_accepted_request_retry_survives_completion_and_retirement(tmp_path):
    queue = TaskQueue(tmp_path / "tasks.db")
    first = _activate(queue)
    capability = first.producer_capability
    assert capability
    accepted = _create(
        queue,
        capability,
        dedup_key="scheduled:nightly:1",
        not_before=10,
    )
    _complete(queue, accepted.id)
    _activate(queue, producer_id="producer-b", expected_generation=1)

    with pytest.raises(ProducerFenceError) as invalid_replay:
        _create(
            queue,
            "wrong-capability",
            dedup_key="scheduled:nightly:1",
            not_before=999,
            claim_as="different/scheduler",
        )
    assert invalid_replay.value.reason == "invalid_capability"

    retry = _create(
        queue,
        capability,
        dedup_key="scheduled:nightly:1",
        not_before=999,
        claim_as="different/scheduler",
    )

    assert retry.id == accepted.id
    assert retry.status == "completed"


def test_claim_rejects_injected_protected_label_without_accepted_fence(tmp_path):
    db = tmp_path / "tasks.db"
    queue = TaskQueue(db)
    capability = _activate(queue).producer_capability
    assert capability
    valid = _create(queue, capability, request_id="valid-before-handoff")
    injected = queue.create(
        "legacy injected row",
        repo=TEST_REPO,
        source="manual",
    )
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE tasks SET source = ?, labels = ?, producer_fence = ?, "
            "producer_request_hash = NULL WHERE id = ?",
            (
                SOURCE,
                f'["{REQUIRED_LABEL}"]',
                (
                    '{"generation":1,"producer_id":"producer-a",'
                    '"request_id":"injected","scope":'
                    f'{{"repo":"{TEST_REPO}","source":"{SOURCE}"}}}}'
                ),
                injected.id,
            ),
        )
    assert queue.claim_one(
        "machine/injected",
        repo=TEST_REPO,
        task_id=injected.id,
    ) is None
    claimed = queue.claim_one(
        "machine/valid",
        repo=TEST_REPO,
        task_id=valid.id,
    )
    assert claimed is not None
    assert claimed.id == valid.id


def test_request_id_mismatch_rejects_even_after_generation_retirement(tmp_path):
    queue = TaskQueue(tmp_path / "tasks.db")
    capability = _activate(queue).producer_capability
    assert capability
    _create(queue, capability, request_id="stable-request")
    _activate(queue, producer_id="producer-b", expected_generation=1)

    with pytest.raises(ProducerFenceError) as mismatch:
        _create(
            queue,
            capability,
            request_id="stable-request",
            title="different semantic request",
        )
    assert mismatch.value.reason == "request_mismatch"


def test_new_request_id_preserves_terminal_release_dedup_semantics(tmp_path):
    queue = TaskQueue(tmp_path / "tasks.db")
    capability = _activate(queue).producer_capability
    assert capability
    first = _create(
        queue,
        capability,
        request_id="request-1",
        dedup_key="scheduled:nightly",
    )
    _complete(queue, first.id)

    second = _create(
        queue,
        capability,
        request_id="request-2",
        dedup_key="scheduled:nightly",
    )

    assert second.id != first.id


def test_managed_dedup_rejects_unfenced_row_before_request_ledger_insert(tmp_path):
    db = tmp_path / "tasks.db"
    queue = TaskQueue(db)
    existing = queue.create(
        "ordinary existing work",
        source="manual",
        dedup_key="scheduled:nightly",
    )
    capability = _activate(queue).producer_capability
    assert capability

    with pytest.raises(ProducerFenceError) as conflict:
        _create(
            queue,
            capability,
            request_id="new-managed-request",
            dedup_key="scheduled:nightly",
        )

    detail = conflict.value.detail(operation="create")
    assert detail["reason"] == "unfenced_dedup_conflict"
    assert detail["conflicting_task_id"] == existing.id
    with sqlite3.connect(db) as conn:
        recorded = conn.execute(
            "SELECT COUNT(*) FROM producer_create_requests "
            "WHERE request_id = 'new-managed-request'"
        ).fetchone()[0]
    assert recorded == 0


def test_managed_dedup_rejects_prior_generation_row(tmp_path):
    queue = TaskQueue(tmp_path / "tasks.db")
    first = _activate(queue)
    assert first.producer_capability
    existing = _create(
        queue,
        first.producer_capability,
        request_id="generation-one",
        dedup_key="scheduled:nightly",
    )
    second = _activate(queue, producer_id="producer-b", expected_generation=1)
    assert second.producer_capability

    with pytest.raises(ProducerFenceError) as conflict:
        _create(
            queue,
            second.producer_capability,
            producer_id="producer-b",
            generation=2,
            request_id="generation-two",
            dedup_key="scheduled:nightly",
        )

    assert conflict.value.reason == "unfenced_dedup_conflict"
    assert conflict.value.detail(operation="create")["conflicting_task_id"] == existing.id


def test_late_uncommitted_request_is_rejected_after_handoff(tmp_path):
    queue = TaskQueue(tmp_path / "tasks.db")
    capability = _activate(queue).producer_capability
    assert capability
    release_late_request = threading.Event()

    def late_request():
        release_late_request.wait(timeout=5)
        return _create(queue, capability, request_id="late-request")

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(late_request)
        _activate(queue, producer_id="producer-b", expected_generation=1)
        release_late_request.set()
        with pytest.raises(ProducerFenceError) as stale:
            pending.result(timeout=5)

    assert stale.value.reason == "stale_generation"


def test_stale_generation_rejects_before_uncommitted_capability_validation(tmp_path):
    queue = TaskQueue(tmp_path / "tasks.db")
    _activate(queue)
    _activate(queue, producer_id="producer-b", expected_generation=1)

    with pytest.raises(ProducerFenceError) as stale:
        _create(
            queue,
            "wrong-capability",
            producer_id="producer-a",
            generation=1,
            request_id="never-accepted",
        )

    assert stale.value.reason == "stale_generation"


def test_concurrent_create_and_handoff_serialize(tmp_path):
    db = tmp_path / "tasks.db"
    setup = TaskQueue(db)
    capability = _activate(setup).producer_capability
    assert capability
    barrier = threading.Barrier(2)

    def create_old():
        barrier.wait(timeout=5)
        try:
            return _create(
                TaskQueue(db),
                capability,
                request_id="racing-request",
            )
        except ProducerFenceError as exc:
            return exc

    def transition():
        barrier.wait(timeout=5)
        return _activate(
            TaskQueue(db),
            producer_id="producer-b",
            expected_generation=1,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        create_result = pool.submit(create_old)
        transition_result = pool.submit(transition)
        created_or_rejected = create_result.result(timeout=10)
        state = transition_result.result(timeout=10)

    assert state.state.current_generation == 2
    if isinstance(created_or_rejected, ProducerFenceError):
        assert created_or_rejected.reason == "stale_generation"
    else:
        assert created_or_rejected.producer_fence["generation"] == 1


def test_insert_failure_does_not_leave_orphan_spill_blob(tmp_path, monkeypatch):
    payloads = tmp_path / "payloads"
    queue = TaskQueue(
        tmp_path / "tasks.db",
        payload_dir=payloads,
        blob_threshold=8,
    )
    capability = _activate(queue).producer_capability
    assert capability

    def fail_audit(*_args, **_kwargs):
        raise sqlite3.IntegrityError("forced insert rollback")

    monkeypatch.setattr(TaskQueue, "_audit", staticmethod(fail_audit))
    with pytest.raises(sqlite3.IntegrityError):
        _create(
            queue,
            capability,
            payload_inline="large managed payload",
        )

    assert not payloads.exists() or not list(payloads.iterdir())


def test_concurrent_creates_share_committed_blob_without_broken_refs(tmp_path):
    payloads = tmp_path / "payloads"
    db = tmp_path / "tasks.db"
    queue = TaskQueue(db, payload_dir=payloads, blob_threshold=8)
    capability = _activate(queue).producer_capability
    assert capability
    barrier = threading.Barrier(2)

    def create(request_id: str):
        barrier.wait(timeout=5)
        return _create(
            TaskQueue(db, payload_dir=payloads, blob_threshold=8),
            capability,
            request_id=request_id,
            title=request_id,
            payload_inline="shared large payload",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(create, "request-a")
        second = pool.submit(create, "request-b")
        tasks = [first.result(timeout=10), second.result(timeout=10)]

    assert len({task.id for task in tasks}) == 2
    assert len({task.payload_ref for task in tasks}) == 1
    assert all(queue.read_payload(task) == "shared large payload" for task in tasks)
    assert len(list(payloads.glob("*.md"))) == 1


def test_post_commit_spill_failure_returns_readable_accepted_task(
    tmp_path, monkeypatch, caplog
):
    queue = TaskQueue(tmp_path / "tasks.db", blob_threshold=8)
    capability = _activate(queue).producer_capability
    assert capability
    monkeypatch.setattr(
        queue,
        "_spill_committed_payload",
        lambda *_args: (_ for _ in ()).throw(OSError("read-only blob store")),
    )

    accepted = _create(
        queue,
        capability,
        request_id="spill-failure",
        payload_inline="large inline fallback",
    )
    retry = _create(
        queue,
        capability,
        request_id="spill-failure",
        payload_inline="large inline fallback",
    )

    assert accepted.payload_ref is None
    assert accepted.payload_inline == "large inline fallback"
    assert queue.read_payload(accepted) == "large inline fallback"
    assert retry.id == accepted.id
    assert "payload spill compaction failed" in caplog.text


def test_schema_migration_and_restart_include_repo_in_every_scope_table(tmp_path):
    db = tmp_path / "tasks.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, status TEXT)")
        conn.execute("INSERT INTO tasks (id, status) VALUES ('legacy', 'queued')")

    queue = TaskQueue(db)
    transition = _activate(queue)
    restarted = TaskQueue(db)
    assert restarted.producer_scope_status(TEST_REPO, SOURCE) == transition.state
    with sqlite3.connect(db) as conn:
        for table in (
            "producer_scopes",
            "producer_scope_generations",
            "producer_create_requests",
        ):
            columns = {
                row[1] for row in conn.execute(f"PRAGMA table_info({table})")
            }
            assert "repo" in columns


def test_existing_duplicate_label_ownership_is_quarantined_without_startup_failure(
    tmp_path,
):
    db = tmp_path / "tasks.db"
    queue = TaskQueue(db)
    _activate(queue)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO producer_scopes "
            "(repo, source, required_label, current_generation, active_producer, "
            "capability_hash, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                TEST_REPO,
                "reactive",
                REQUIRED_LABEL,
                1,
                "producer-b",
                "legacy-hash",
                1.0,
                1.0,
            ),
        )
        conn.execute(
            "INSERT INTO producer_scope_generations "
            "(repo, source, generation, producer_id, capability_hash, "
            "required_label, state, activated_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                TEST_REPO,
                "reactive",
                1,
                "producer-b",
                "legacy-hash",
                REQUIRED_LABEL,
                "active",
                1.0,
            ),
        )

    restarted = TaskQueue(db)
    with pytest.raises(ProducerFenceError) as create_error:
        restarted.create(
            "ambiguous protected work",
            repo=TEST_REPO,
            labels=[REQUIRED_LABEL],
        )
    assert create_error.value.reason == "ambiguous_required_label"

    with pytest.raises(ProducerFenceError) as handoff_error:
        restarted.handoff_producer_scope(
            TEST_REPO,
            SOURCE,
            producer_id="producer-c",
            expected_generation=1,
        )
    assert handoff_error.value.reason == "required_label_conflict"


def test_pre_release_label_scope_schema_is_quarantined_on_migration(tmp_path):
    db = tmp_path / "tasks.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY)")
        conn.execute(
            "CREATE TABLE producer_scopes ("
            "source TEXT NOT NULL, label TEXT NOT NULL, "
            "current_generation INTEGER NOT NULL, active_producer TEXT NOT NULL, "
            "created_at REAL NOT NULL, updated_at REAL NOT NULL, "
            "PRIMARY KEY(source, label))"
        )
        conn.execute(
            "CREATE TABLE producer_scope_generations ("
            "source TEXT NOT NULL, label TEXT NOT NULL, generation INTEGER NOT NULL, "
            "producer_id TEXT NOT NULL, state TEXT NOT NULL, activated_at REAL NOT NULL, "
            "retired_at REAL, PRIMARY KEY(source, label, generation))"
        )

    TaskQueue(db)
    with sqlite3.connect(db) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(producer_scopes)")
        }
    assert "producer_scopes_label_v1" in tables
    assert "producer_scope_generations_label_v1" in tables
    assert "repo" in columns


def test_partial_label_scope_migration_resumes_atomically(tmp_path):
    db = tmp_path / "tasks.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY)")
        conn.execute(
            "CREATE TABLE producer_scopes_label_v1 ("
            "source TEXT NOT NULL, label TEXT NOT NULL, "
            "current_generation INTEGER NOT NULL, active_producer TEXT NOT NULL, "
            "created_at REAL NOT NULL, updated_at REAL NOT NULL, "
            "PRIMARY KEY(source, label))"
        )
        conn.execute(
            "CREATE TABLE producer_scope_generations ("
            "source TEXT NOT NULL, label TEXT NOT NULL, generation INTEGER NOT NULL, "
            "producer_id TEXT NOT NULL, state TEXT NOT NULL, activated_at REAL NOT NULL, "
            "retired_at REAL, PRIMARY KEY(source, label, generation))"
        )
        conn.execute(
            "CREATE INDEX idx_producer_scope_history "
            "ON producer_scope_generations(source, label, generation DESC)"
        )

    TaskQueue(db)
    restarted = TaskQueue(db)
    with sqlite3.connect(db) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        index_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' "
            "AND name = 'idx_producer_scope_history'"
        ).fetchone()[0]
    assert "producer_scope_generations_label_v1" in tables
    assert restarted.producer_scope_status(TEST_REPO, SOURCE).managed is False
    assert "repo, source, generation DESC" in index_sql


def test_concurrent_label_scope_migration_serializes(tmp_path):
    db = tmp_path / "tasks.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY)")
        conn.execute(
            "CREATE TABLE producer_scopes ("
            "source TEXT NOT NULL, label TEXT NOT NULL, "
            "current_generation INTEGER NOT NULL, active_producer TEXT NOT NULL, "
            "created_at REAL NOT NULL, updated_at REAL NOT NULL, "
            "PRIMARY KEY(source, label))"
        )
        conn.execute(
            "CREATE TABLE producer_scope_generations ("
            "source TEXT NOT NULL, label TEXT NOT NULL, generation INTEGER NOT NULL, "
            "producer_id TEXT NOT NULL, state TEXT NOT NULL, activated_at REAL NOT NULL, "
            "retired_at REAL, PRIMARY KEY(source, label, generation))"
        )
        conn.execute(
            "CREATE INDEX idx_producer_scope_history "
            "ON producer_scope_generations(source, label, generation DESC)"
        )
    barrier = threading.Barrier(4)

    def open_queue():
        barrier.wait(timeout=5)
        return TaskQueue(db).producer_scope_status(TEST_REPO, SOURCE)

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = [pool.submit(open_queue) for _ in range(4)]
        states = [result.result(timeout=10) for result in results]

    assert all(state.managed is False for state in states)


@pytest.mark.parametrize("bad_not_before", [float("nan"), float("inf")])
def test_queue_rejects_non_finite_scheduling_values(tmp_path, bad_not_before):
    queue = TaskQueue(tmp_path / "tasks.db")
    with pytest.raises(ProducerScopeValidationError) as invalid:
        queue.create("invalid", not_before=bad_not_before)
    assert invalid.value.reason == "invalid_not_before"


def test_http_control_authority_structured_errors_and_bounded_events(
    tmp_path, monkeypatch
):
    queue = TaskQueue(tmp_path / "tasks.db")
    app = create_app(
        queue,
        token="client-secret",
        control_token=CONTROL_TOKEN,
    )
    published: list[dict] = []
    emitted: list[dict] = []
    monkeypatch.setattr(app.state.bus, "publish", published.append)
    telemetry.set_telemetry_sink(emitted.append)
    api = TestClient(app)
    client_headers = {"Authorization": "Bearer client-secret"}
    control_headers = {"Authorization": f"Bearer {CONTROL_TOKEN}"}
    try:
        denied = api.post(
            "/producer-scopes/handoff",
            headers=client_headers,
            json={
                "repo": TEST_REPO,
                "source": SOURCE,
                "producer_id": "producer-a",
                "expected_generation": 0,
                "required_label": REQUIRED_LABEL,
            },
        )
        assert denied.status_code == 403
        assert denied.json()["detail"]["reason"] == "invalid_control_authority"
        assert published[-1]["type"] == "producer_scope.transition_rejected"

        managed = api.post(
            "/producer-scopes/handoff",
            headers=control_headers,
            json={
                "repo": TEST_REPO,
                "source": SOURCE,
                "producer_id": "producer-a",
                "expected_generation": 0,
                "required_label": REQUIRED_LABEL,
            },
        )
        assert managed.status_code == 200
        capability = managed.json()["producer_capability"]
        status = api.get(
            "/producer-scopes/status",
            headers=client_headers,
            params={"repo": TEST_REPO, "source": SOURCE},
        )
        assert status.status_code == 200
        assert "producer_capability" not in status.json()

        create_body = {
            "title": "accepted",
            "repo": TEST_REPO,
            "source": SOURCE,
            "labels": [REQUIRED_LABEL],
            "producer_scope": SCOPE,
            "producer_id": "producer-a",
            "producer_generation": 1,
            "producer_capability": capability,
            "producer_request_id": "http-request",
        }
        accepted = api.post(
            "/tasks",
            headers=client_headers,
            json=create_body,
        )
        assert accepted.status_code == 200
        created_count = sum(
            event["type"] == "task.created" for event in published
        )
        replay = api.post(
            "/tasks",
            headers=client_headers,
            json=create_body,
        )
        assert replay.status_code == 200
        assert replay.json()["id"] == accepted.json()["id"]
        assert sum(
            event["type"] == "task.created" for event in published
        ) == created_count

        rejected = api.post(
            "/tasks",
            headers=client_headers,
            json={
                "title": "secret title",
                "prompt": "secret prompt",
                "payload_inline": "secret payload",
                "repo": TEST_REPO,
                "source": SOURCE,
                "labels": [REQUIRED_LABEL],
                "producer_scope": SCOPE,
                "producer_id": "producer-a",
                "producer_generation": 1,
                "producer_capability": "wrong",
                "producer_request_id": "rejected-request",
            },
        )
        assert rejected.status_code == 403
        detail = rejected.json()["detail"]
        assert detail["code"] == "producer_fence_rejected"
        assert detail["reason"] == "invalid_capability"
        event = published[-1]
        assert event["type"] == "task.create_rejected"
        serialized = repr(event) + repr(emitted[-1])
        assert "secret title" not in serialized
        assert "secret prompt" not in serialized
        assert "secret payload" not in serialized
        assert capability not in serialized
    finally:
        telemetry.clear_telemetry_sink()


def test_claim_rejection_is_audited_and_emitted_once_per_fingerprint(
    tmp_path, monkeypatch
):
    db = tmp_path / "tasks.db"
    queue = TaskQueue(db)
    injected = queue.create(
        "cross-lane injected",
        repo=OTHER_REPO,
        source="manual",
    )
    _activate(queue)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE tasks SET labels = ? WHERE id = ?",
            (f'["{REQUIRED_LABEL}"]', injected.id),
        )
    app = create_app(queue)
    published: list[dict] = []
    emitted: list[dict] = []
    monkeypatch.setattr(app.state.bus, "publish", published.append)
    telemetry.set_telemetry_sink(emitted.append)
    api = TestClient(app)
    try:
        body = {"worker_id": "machine/worktree", "repo": OTHER_REPO}
        assert api.post("/claim", json=body).json() is None
        rejection_events = [
            event
            for event in published
            if event["type"] == "producer.claim_rejected"
        ]
        assert len(rejection_events) == 1
        detail = rejection_events[0]["producer_fence"]
        assert detail["task_id"] == injected.id
        assert detail["reason"] == "required_label_repo_mismatch"
        assert detail["owning_repo"] == TEST_REPO
        assert api.post("/claim", json=body).json() is None
        assert (
            sum(
                event["type"] == "producer.claim_rejected"
                for event in published
            )
            == 1
        )
        notes = [event["note"] for event in queue.events(injected.id)]
        assert notes.count(
            "producer.claim_rejected:required_label_repo_mismatch"
        ) == 1
        assert emitted[-1]["event"] == "producer.claim_rejected"
    finally:
        telemetry.clear_telemetry_sink()


def test_http_requires_configured_control_token_and_rejects_extra_fields(tmp_path):
    app = create_app(TaskQueue(tmp_path / "tasks.db"))
    published: list[dict] = []
    app.state.bus.publish = published.append
    api = TestClient(app)

    unavailable = api.post(
        "/producer-scopes/handoff",
        json={
            "repo": TEST_REPO,
            "source": SOURCE,
            "producer_id": "producer-a",
            "expected_generation": 0,
        },
    )
    extra = api.post(
        "/tasks",
        json={
            "title": "invalid",
            "repo": TEST_REPO,
            "unexpected": True,
        },
    )

    assert unavailable.status_code == 503
    assert unavailable.json()["detail"]["reason"] == "control_authority_not_configured"
    assert published[-1]["type"] == "producer_scope.transition_rejected"
    assert extra.status_code == 422


def test_control_token_must_be_distinct_from_ordinary_token(tmp_path):
    with pytest.raises(ValueError, match="must differ"):
        create_app(
            TaskQueue(tmp_path / "tasks.db"),
            token="same-token",
            control_token="same-token",
        )


def test_http_nan_is_explicit_bad_request(tmp_path):
    api = TestClient(create_app(TaskQueue(tmp_path / "tasks.db")))
    response = api.post(
        "/tasks",
        content=(
            '{"title":"invalid","repo":"example.com/acme/widget",'
            '"not_before":NaN}'
        ),
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 400
