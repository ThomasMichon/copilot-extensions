"""Both orderings of terminal custody and a subsequent native attachment."""

from pathlib import Path

import pytest

from agent_dispatch.ordinary_native_bindings import reservation_native_identity
from agent_dispatch.ordinary_agent_storage import record_created_agent
from agent_dispatch.native_attachment_custody import require_not_reassigned
from agent_dispatch.queue import TaskQueue
from agent_dispatch.queue_common import Task
from agent_dispatch.queue_records import TaskError


@pytest.mark.parametrize("effect_first", [False, True])
@pytest.mark.parametrize("complete", [False, True], ids=["yield", "complete"])
@pytest.mark.parametrize(("host", "handle", "driver", "incoming", "hold"), [
    (" HOST-A ", "native", "cli", "native", True),
    ("desktop-alias", "native", "cli", "native", True),
    ("host-a", "local-body:body", None, "native", True),
    ("desktop-alias", "local-body:body", None, "native", True),
    ("host-a", "native", "cli", "different", False),
    ("host-a", "local-body:body", None, "different", True),
    ("host-a", "local-body:body", None, "unrelated", False),
])
def test_candidate_overlap_uses_attachment_and_binding(
    tmp_path: Path, effect_first: bool, complete: bool, host: str, handle: str, driver: str | None,
    incoming: str, hold: bool,
) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    old = queue.create("previous task", repo="example")
    assert isinstance(old, Task)
    res, _ = queue.reserve_spawn(old.id, driver=driver)
    queue.record_spawn(res.key, session_handle=handle, worktree="tree", startup_complete=True,
                       startup_revision=res.startup_revision)
    with queue._connect() as conn:
        row = conn.execute("SELECT * FROM spawn_reservations WHERE key=?", (res.key,)).fetchone()
        assert reservation_native_identity(conn, row) == ("native" if driver else None)
    queue.claim_one("host-a/tree", task_id=old.id)
    queue.start(old.id, "host-a/tree", owner_session_id="native")
    if complete:
        queue.complete(old.id, "host-a/tree")
    else:
        queue.yield_task(old.id, "host-a/tree")
        queue.abandon(old.id, permitted=True)
    new = queue.create("new task", repo="example")
    assert isinstance(new, Task)
    owner = f"{host}/other" if incoming == "unrelated" else f"{host}/tree"
    queue.claim_one(owner, task_id=new.id)

    def effect() -> None:
        queue.fence_spawn_startup(res.key, startup_revision=res.startup_revision)

    def attach() -> None:
        queue.start(new.id, owner, owner_session_id=incoming)

    first, second = (effect, attach) if effect_first else (attach, effect)
    first()
    before = queue.get(new.id), queue.get_reservation(res.key)
    if hold:
        with pytest.raises(TaskError, match="another task|remains admitted"):
            second()
        assert before == (queue.get(new.id), queue.get_reservation(res.key))
    else:
        second()


@pytest.mark.parametrize("release_first", [False, True])
@pytest.mark.parametrize("driver", ["cli", None], ids=["native", "legacy-body"])
def test_release_and_native_attachment_cannot_overtake_each_other(
    tmp_path: Path, release_first: bool, driver: str | None,
) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    old = queue.create("previous task", repo="example")
    new = queue.create("new task", repo="example")
    assert isinstance(old, Task) and isinstance(new, Task)
    res, _ = queue.reserve_spawn(old.id, driver=driver)
    queue.record_spawn(
        res.key, session_handle="native" if driver else "local-body:body",
        worktree="tree", startup_complete=True, startup_revision=res.startup_revision,
    )
    queue.claim_one("host-a/tree", task_id=old.id)
    queue.start(old.id, "host-a/tree", owner_session_id="native")
    queue.yield_task(old.id, "host-a/tree")
    queue.claim_one("host-a/tree", task_id=new.id)

    def release() -> None:
        queue.request_spawn_release(res.key)

    def attach() -> None:
        queue.start(new.id, "host-a/tree", owner_session_id="native")

    first, second = (release, attach) if release_first else (attach, release)
    first()
    before = queue.get(new.id), queue.get_reservation(res.key)
    with pytest.raises(TaskError, match="another task|remains admitted"):
        second()
    assert before == (queue.get(new.id), queue.get_reservation(res.key))


@pytest.mark.parametrize("release_first", [False, True])
@pytest.mark.parametrize("incoming", ["native", "other"])
def test_published_reservation_without_attachment_still_guards_native(
    tmp_path: Path, release_first: bool, incoming: str,
) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    old = queue.create("never started", repo="example")
    new = queue.create("new assignment", repo="example")
    assert isinstance(old, Task) and isinstance(new, Task)
    res, _ = queue.reserve_spawn(old.id, driver="cli")
    queue.record_spawn(res.key, session_handle="native", worktree="tree", startup_complete=True,
                       startup_revision=res.startup_revision)
    assert not queue.attachment_history(old.id)
    queue.claim_one("host/tree", task_id=new.id)

    def release() -> None:
        queue.request_spawn_release(res.key)

    def attach() -> None:
        queue.start(new.id, "host/tree", owner_session_id=incoming)

    first, second = (release, attach) if release_first else (attach, release)
    first()
    if incoming != "native":
        second()
        return
    before = queue.get(new.id), queue.get_reservation(res.key)
    with pytest.raises(TaskError):
        second()
    assert before == (queue.get(new.id), queue.get_reservation(res.key))


@pytest.mark.parametrize("publish_first", [False, True])
def test_ordinary_first_delivery_excludes_competing_native_attachment(tmp_path: Path, publish_first: bool) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    original = queue.create("initial delivery", repo="example")
    other = queue.create("other goal", repo="example")
    assert isinstance(original, Task) and isinstance(other, Task)
    res, _ = queue.reserve_spawn(original.id, driver="ordinary-bridge")
    queue.claim_one("host/tree", task_id=other.id)

    def publish() -> None:
        record_created_agent(queue, res.key, task_generation=original.generation, agent_info={
            "session_id": "bridge-body", "ready": True, "status": "awaiting_first_prompt",
            "connection": {"native_session_id": "native", "worktree_id": "tree",
                           "repo": "example", "target_dir": "/fixture/tree"},
        })

    def attach() -> None:
        queue.start(other.id, "host/tree", owner_session_id="native")

    first, second = (publish, attach) if publish_first else (attach, publish)
    first()
    before = queue.get(other.id), queue.get_reservation(res.key)
    with pytest.raises(TaskError, match="another task|remains admitted"):
        second()
    assert (queue.get(other.id), queue.get_reservation(res.key)) == before


def test_repeated_returns_do_not_rescan_attachment_suffixes(tmp_path: Path) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    task = queue.create("original goal", repo="example")
    other = queue.create("other goal", repo="example")
    assert isinstance(task, Task) and isinstance(other, Task)
    res, _ = queue.reserve_spawn(task.id, driver="cli")
    queue.record_spawn(res.key, session_handle="native", worktree="tree", startup_complete=True,
                       startup_revision=res.startup_revision)
    with queue._connect() as conn:
        conn.execute("BEGIN")
        # Alternate completed assignments; the newest return belongs to the original.
        conn.executemany("INSERT INTO task_attachments(task_id,session_id,worktree_id,machine,attached_at,detached_at) "
                         "VALUES (?,'native','tree','host',1,2)",
                         ((owner,) for _ in range(500) for owner in (other.id, task.id)))
        conn.executemany("INSERT INTO task_attachments(task_id,session_id,machine,attached_at) "
                         "VALUES (?,'unrelated','remote',1)", ((other.id,) for _ in range(5000)))
        row = conn.execute("SELECT * FROM spawn_reservations WHERE key=?", (res.key,)).fetchone()
        statements: list[str] = []
        conn.set_trace_callback(statements.append)
        try:
            require_not_reassigned(conn, row)
        finally:
            conn.set_trace_callback(None)
        assert len(statements) < 10
        plans = [entry["detail"] for sql in statements for entry in conn.execute("EXPLAIN QUERY PLAN " + sql)]
        assert not any("SCAN task_attachments" in plan or "SCAN tasks" in plan for plan in plans), plans


@pytest.mark.parametrize("adopt", [False, True])
def test_same_native_resume_waits_for_foreign_release(tmp_path: Path, adopt: bool) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    old = queue.create("previous task", repo="example")
    active = queue.create("suspended assignment", repo="example")
    assert isinstance(old, Task) and isinstance(active, Task)
    res, _ = queue.reserve_spawn(old.id, driver="cli")
    queue.record_spawn(res.key, session_handle="native", worktree="tree", startup_complete=True,
                       startup_revision=res.startup_revision)
    queue.claim_one("host/tree", task_id=active.id)
    queue.start(active.id, "host/tree", owner_session_id="native")
    held = queue.suspend(active.id, "host/tree", reason="wait", cooldown_seconds=None)
    # A previous coordinator admitted release without the original attachment.
    with queue._connect() as conn:
        conn.execute("UPDATE spawn_reservations SET state='releasing',release_requested=1 WHERE key=?",
                     (res.key,))
        conn.commit()
    before = queue.attachment_history(active.id)
    with pytest.raises(TaskError, match="remains admitted"):
        queue.resume(active.id, "host/tree", wake_requested=True, adopt_owner_session_id="native" if adopt else None)
    assert queue.get(active.id) == held and queue.attachment_history(active.id) == before
    assert not queue.list_wakes(active.id)


def test_scheduled_resume_rechecks_native_before_sending(tmp_path: Path) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    old = queue.create("previous task", repo="example")
    active = queue.create("resuming assignment", repo="example")
    assert isinstance(old, Task) and isinstance(active, Task)
    res, _ = queue.reserve_spawn(old.id, driver="cli")
    queue.record_spawn(res.key, session_handle="native", worktree="tree", startup_complete=True,
                       startup_revision=res.startup_revision)
    queue.claim_one("host/tree", task_id=active.id)
    queue.start(active.id, "host/tree", owner_session_id="native")
    queue.suspend(active.id, "host/tree", reason="wait", cooldown_seconds=None)
    queue.resume(active.id, "host/tree", wake_requested=True)
    wake = queue.claim_due_wake()
    assert wake is not None
    with queue._connect() as conn:
        conn.execute("UPDATE spawn_reservations SET state='releasing',release_requested=1 WHERE key=?",
                     (res.key,))
        conn.commit()
    with pytest.raises(TaskError, match="remains admitted"):
        queue.admit_execution_wake(wake, provenance=None)
    assert queue.list_wakes(active.id)[0].execution_send_state is None


@pytest.mark.parametrize("cleanup_first", [False, True])
@pytest.mark.parametrize("retire", ["fail_spawn", "settle_spawn"])
@pytest.mark.parametrize(("incoming", "tree"), [("native", "tree"), ("different", "tree"), ("different", "other")])
def test_retired_cleanup_and_native_attachment_cannot_overtake_each_other(
    tmp_path: Path, cleanup_first: bool, retire: str, incoming: str, tree: str,
) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    old = queue.create("previous task", repo="example")
    new = queue.create("new task", repo="example")
    assert isinstance(old, Task) and isinstance(new, Task)
    res, _ = queue.reserve_spawn(old.id, driver="cli")
    queue.record_spawn(res.key, session_handle="native", worktree="tree", startup_complete=True,
                       startup_revision=res.startup_revision)
    queue.claim_one("host-a/tree", task_id=old.id)
    queue.start(old.id, "host-a/tree", owner_session_id="native")
    queue.complete(old.id, "host-a/tree")
    getattr(queue, retire)(res.key)
    worker = f"host-a/{tree}"
    queue.claim_one(worker, task_id=new.id)

    def cleanup() -> None:
        queue.record_spawn_conclusion(res.key, conclusion_state="pending", conclusion_detail="{}")
        _, claimed, token = queue.claim_spawn_conclusion_retry(res.key)
        assert claimed and token
        queue.validate_spawn_conclusion_claim(res.key, token)

    def attach() -> None:
        queue.start(new.id, worker, owner_session_id=incoming)

    first, second = (cleanup, attach) if cleanup_first else (attach, cleanup)
    first()
    if tree != "tree":
        second()
        return
    before = queue.get(new.id)
    with pytest.raises(TaskError, match="another task|remains admitted"):
        second()
    assert queue.get(new.id) == before
    if not cleanup_first:
        retained = queue.get_reservation(res.key)
        assert retained is not None and retained.cleanup_claim_token is None


@pytest.mark.parametrize("incoming", ["native", "different"])
def test_cleanup_claim_revalidation_refuses_legacy_later_attachment(tmp_path: Path, incoming: str) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    old = queue.create("previous task", repo="example")
    new = queue.create("new task", repo="example")
    assert isinstance(old, Task) and isinstance(new, Task)
    res, _ = queue.reserve_spawn(old.id, driver="cli")
    queue.record_spawn(res.key, session_handle="native", worktree="tree", startup_complete=True,
                       startup_revision=res.startup_revision)
    queue.claim_one("host-a/tree", task_id=old.id)
    queue.start(old.id, "host-a/tree", owner_session_id="native")
    queue.complete(old.id, "host-a/tree")
    queue.fail_spawn(res.key, conclusion_state="pending", conclusion_detail="{}")
    _, claimed, token = queue.claim_spawn_conclusion_retry(res.key)
    assert claimed and token
    # A legacy writer could publish this attachment before the admission fence existed.
    with queue._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO task_attachments "
            "(task_id,session_id,worktree_id,machine,attached_at,detached_at) "
            "VALUES (?,?,?,?,?,NULL)",
            (new.id, incoming, "tree", "host-a", queue._now(None)),
        )
        conn.commit()
    with pytest.raises(TaskError, match="another task"):
        queue.validate_spawn_conclusion_claim(res.key, token)


@pytest.mark.parametrize("incoming", ["old-native", "different"])
def test_process_only_retry_keeps_native_but_not_completed_checkout(tmp_path: Path, incoming: str) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    old = queue.create("previous task", repo="example")
    new = queue.create("different goal", repo="example")
    assert isinstance(old, Task) and isinstance(new, Task)
    res, _ = queue.reserve_spawn(old.id, driver="cli")
    queue.record_spawn(res.key, session_handle="old-native", worktree="tree", startup_complete=True,
                       startup_revision=res.startup_revision)
    queue.fail_spawn(res.key, conclusion_state="pending", conclusion_detail=(
        '{"worktree_cleanup":{"state":"complete","outcome":{"action":"preserved","reason":"targeted-worktree"}},'
        '"session_end":{"state":"pending","session_id":"old-native"}}'
    ))
    _, claimed, token = queue.claim_spawn_conclusion_retry(res.key)
    assert claimed and token
    queue.claim_one("host/tree", task_id=new.id)
    if incoming == "old-native":
        with pytest.raises(TaskError, match="remains admitted"):
            queue.start(new.id, "host/tree", owner_session_id=incoming)
    else:
        queue.start(new.id, "host/tree", owner_session_id=incoming)
    queue.validate_spawn_conclusion_claim(res.key, token)


@pytest.mark.parametrize("effect_first", [False, True])
@pytest.mark.parametrize("effect", ["cooling", "reopen-without-handle", "implicit-cold-restart"])
def test_dormant_cooling_or_startup_reopen_cannot_overtake_native_transfer(
    tmp_path: Path, effect_first: bool, effect: str,
) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    old = queue.create("previous task", repo="example")
    new = queue.create("new task", repo="example")
    assert isinstance(old, Task) and isinstance(new, Task)
    res, _ = queue.reserve_spawn(old.id, driver="cli")
    queue.record_spawn(res.key, session_handle="native", worktree="tree", startup_complete=True,
                       startup_revision=res.startup_revision)
    queue.claim_one("host-a/tree", task_id=old.id)
    queue.start(old.id, "host-a/tree", owner_session_id="native")
    queue.yield_task(old.id, "host-a/tree")
    queue.record_cold(res.key)
    queue.claim_one("host-a/tree", task_id=new.id)

    def admit_effect() -> None:
        if effect == "cooling":
            queue.claim_spawn_cooling(
                res.key, startup_revision=res.startup_revision, session_handle="native",
                state="cold",
            )
        elif effect == "implicit-cold-restart":
            queue.record_spawn(res.key)
        else:
            queue.record_spawn(
                res.key, startup_complete=False, startup_revision=res.startup_revision,
            )

    def attach() -> None:
        queue.start(new.id, "host-a/tree", owner_session_id="native")

    first, second = (admit_effect, attach) if effect_first else (attach, admit_effect)
    first()
    before = queue.get(new.id), queue.get_reservation(res.key)
    with pytest.raises(TaskError, match="another task|remains admitted"):
        second()
    assert before == (queue.get(new.id), queue.get_reservation(res.key))


@pytest.mark.parametrize("cold", [True, False], ids=["completed-cold-restart", "replace-handle"])
def test_spawn_publication_preserves_a_native_now_owned_by_another_task(tmp_path: Path, cold: bool) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    old = queue.create("previous task", repo="example")
    new = queue.create("new task", repo="example")
    assert isinstance(old, Task) and isinstance(new, Task)
    res, _ = queue.reserve_spawn(old.id, driver="cli")
    queue.record_spawn(res.key, session_handle="native", worktree="tree", startup_complete=True,
                       startup_revision=res.startup_revision)
    queue.claim_one("host/tree", task_id=old.id)
    queue.start(old.id, "host/tree", owner_session_id="native")
    queue.yield_task(old.id, "host/tree")
    if cold:
        queue.record_cold(res.key)
    queue.claim_one("host/tree", task_id=new.id)
    queue.start(new.id, "host/tree", owner_session_id="native")

    before = queue.get_reservation(res.key)
    with pytest.raises(TaskError, match="another task"):
        queue.record_spawn(res.key, session_handle="native" if cold else "replacement", startup_complete=True,
                           startup_revision=res.startup_revision)
    assert queue.get_reservation(res.key) == before


def test_return_to_original_native_uses_normalized_host(tmp_path: Path) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    old = queue.create("original goal", repo="example")
    new = queue.create("intervening goal", repo="example")
    assert isinstance(old, Task) and isinstance(new, Task)
    res, _ = queue.reserve_spawn(old.id, driver="cli")
    queue.record_spawn(res.key, session_handle="native", worktree="tree", startup_complete=True,
                       startup_revision=res.startup_revision)
    for task, owner in ((old, "host-a/tree"), (new, "HOST-A/tree"), (old, " Host-A /tree")):
        assert queue.claim_one(owner, task_id=task.id)
        queue.start(task.id, owner, owner_session_id="native")
        queue.yield_task(task.id, owner)
    queue.abandon(old.id, permitted=True)
    assert queue.fence_spawn_startup(res.key, startup_revision=res.startup_revision).startup_fenced
