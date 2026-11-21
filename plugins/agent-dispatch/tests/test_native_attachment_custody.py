"""Both orderings of terminal custody and a subsequent native attachment."""

from pathlib import Path

import pytest

from agent_dispatch.ordinary_native_bindings import reservation_native_identity
from agent_dispatch.queue import TaskQueue
from agent_dispatch.queue_common import Task
from agent_dispatch.queue_records import TaskError


@pytest.mark.parametrize("effect_first", [False, True])
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
    tmp_path: Path, effect_first: bool, host: str, handle: str, driver: str | None,
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
