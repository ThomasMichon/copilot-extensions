import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

import agent_chat_service.store as store_module
from agent_chat_service import ChatError, ChatStore, __version__, json_bytes, strict_json


@pytest.fixture
def store(tmp_path):
    instance = ChatStore(tmp_path / "data", "example-installation")
    try:
        yield instance
    finally:
        instance.close()


def rpc(store, op, **fields):
    return store.dispatch({"op": op, **fields})


def join(store, codename="agent-a", channel="room", **metadata):
    return rpc(store, "join", channel=channel, actor={"codename": codename, **metadata})


def post(store, request_id="request-1", text="hello", channel="room", codename="agent-a"):
    return rpc(store, "post", channel=channel, codename=codename,
               text=text, request_id=request_id)


def read(store, codename="agent-a", channel="room", **fields):
    return rpc(store, "read", channel=channel, codename=codename, **fields)


def scan(store, after=None, epoch=None, **fields):
    return rpc(store, "scan", codename="agent-a", epoch=epoch, timeout=0,
               after=after or [{"channel": "room", "event_id": 0}], **fields)


def expect(code, function, *args, **kwargs):
    with pytest.raises(ChatError) as caught:
        function(*args, **kwargs)
    assert caught.value.code == code
    assert caught.value.as_dict() == {"error": code, "message": str(caught.value)}


def test_independent_nonconsuming_pages_and_forward_cas(store):
    join(store)
    join(store, "agent-b")
    events = [post(store, str(number))["event"] for number in range(3)]
    first = read(store, limit=2)
    assert first["messages"] == events[:2] and first["more"]
    assert read(store, limit=2) == first
    rpc(store, "cursor", channel="room", codename="agent-a",
        expected_cursor=0, next_cursor=first["next_cursor"])
    assert read(store)["messages"] == events[2:]
    assert read(store, "agent-b")["messages"] == events
    expect("conflict", rpc, store, "cursor", channel="room", codename="agent-a",
           expected_cursor=0, next_cursor=events[-1]["id"])
    expect("invalid", rpc, store, "cursor", channel="room", codename="agent-a",
           expected_cursor=events[1]["id"], next_cursor=events[0]["id"])


def test_restart_dedup_snapshot_identity_and_monotonic_events(store, tmp_path):
    join(store, machine="example-host", session_id="session-1")
    original = post(store)["event"]
    join(store, session_id="session-2")
    assert post(store) == {"event": original, "duplicate": True}
    expect("conflict", post, store, text="changed")
    store.close()
    expect("conflict", ChatStore, tmp_path / "data", "different-installation")
    reopened = ChatStore(tmp_path / "data", "example-installation")
    try:
        assert post(reopened)["event"] == original
        later = post(reopened, "request-2")["event"]
        assert later["id"] > original["id"]
        assert original["author"]["session_id"] == "session-1"
        assert later["author"]["session_id"] == "session-2"
        assert reopened.health()["version"] == __version__
    finally:
        reopened.close()


def test_expected_owner_leave_succession_and_inactive_cursor(store):
    actor = join(store, machine="example-host", worktree_id="example-worktree",
                 session_id="session-1")["actor"]
    event = post(store)["event"]
    expect("chat_owner_mismatch", rpc, store, "leave", channel="room",
           codename="agent-a", actor={**actor, "machine": "different-host"})
    assert rpc(store, "resolve", codename="agent-a")["actor"] == actor
    join(store, session_id="session-2")
    assert not rpc(store, "leave", channel="room", codename="agent-a", actor=actor)["active"]
    expect("notjoined", read, store)
    rpc(store, "cursor", channel="room", codename="agent-a",
        expected_cursor=0, next_cursor=event["id"])
    assert join(store)["cursor"] == event["id"]
    assert read(store)["messages"] == []
    assert not rpc(store, "leave", channel="absent", codename="absent")["active"]


def test_committed_post_retry_after_leave_is_reconciled_without_rejoining(store, tmp_path):
    join(store, session_id="session-1")
    original = post(store)["event"]
    rpc(store, "leave", channel="room", codename="agent-a")
    store.close()
    reopened = ChatStore(tmp_path / "data", "example-installation")
    try:
        assert post(reopened) == {"event": original, "duplicate": True}
        expect("conflict", post, reopened, text="changed")
        expect("conflict", post, reopened, channel="other")
        expect("notjoined", post, reopened, request_id="new-request")
        assert reopened._db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
        assert reopened._db.execute("SELECT active FROM memberships").fetchone()[0] == 0
    finally:
        reopened.close()


def test_post_failure_rolls_back_counter_event_and_request_identity(store):
    join(store)
    retained = post(store)["event"]
    before = dict(store._db.execute("SELECT key, value FROM settings"))
    store._db.execute(
        "CREATE TEMP TRIGGER fail_counter BEFORE UPDATE ON settings "
        "WHEN OLD.key='message_bytes' "
        "BEGIN SELECT RAISE(ABORT, 'synthetic counter failure'); END"
    )
    try:
        with pytest.raises(sqlite3.IntegrityError, match="synthetic counter failure"):
            post(store, "failed-request")
    finally:
        store._db.execute("DROP TRIGGER fail_counter")
    assert dict(store._db.execute("SELECT key, value FROM settings")) == before
    assert read(store)["messages"] == [retained]
    assert not post(store, "failed-request")["duplicate"]
    assert post(store, "failed-request")["duplicate"]


def test_full_quota_keeps_dedup_leave_and_cursor_available(store):
    store.max_events = 1
    join(store)
    original = post(store)["event"]
    expect("capacity", post, store, "request-2")
    assert post(store)["duplicate"]
    rpc(store, "leave", channel="room", codename="agent-a")
    rpc(store, "cursor", channel="room", codename="agent-a",
        expected_cursor=0, next_cursor=original["id"])
    assert join(store)["cursor"] == original["id"]
    assert read(store)["messages"] == []


def test_concurrent_retries_commit_one_effect(store):
    join(store)
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(post, store) for _ in range(8)]
        results = [future.result(timeout=3) for future in futures]
    assert sum(not result["duplicate"] for result in results) == 1
    assert len(read(store)["messages"]) == 1


def test_scan_paging_contains_no_bodies_or_semantic_progress(store):
    join(store)
    join(store, channel="other")
    events = [post(store, "one")["event"],
              post(store, "two", channel="other")["event"], post(store, "three")["event"]]
    positions = [{"channel": "room", "event_id": 0}, {"channel": "other", "event_id": 0}]
    changes = store._db.total_changes
    first = scan(store, positions, limit=1)
    assert store._db.total_changes == changes
    assert first["schema"] == "agent-chat.scan" and first["version"] == 1
    assert first["events"] == [{key: events[0][key] for key in (
        "id", "channel", "timestamp", "author",
    )}]
    assert first["more"]
    assert first["channels"][1]["scan_cursor"] == 0
    assert read(store)["cursor"] == 0
    positions[0]["event_id"] = events[0]["id"]
    assert scan(store, positions, first["epoch"], limit=1)["events"][0]["id"] == events[1]["id"]
    expect("scan_epoch_mismatch", scan, store, epoch="0" * 32)
    expect("scan_gap", scan, store, [{"channel": "room", "event_id": events[1]["id"]}],
           first["epoch"])


def test_pending_hint_crash_keeps_exact_body_unread(store, tmp_path):
    join(store)
    original = post(store, text="recover this exact body")["event"]
    hint = scan(store)
    assert hint["events"][0]["id"] == original["id"]
    assert read(store)["cursor"] == 0
    epoch = hint["epoch"]
    store.close()
    reopened = ChatStore(tmp_path / "data", "example-installation")
    try:
        assert scan(reopened)["epoch"] == epoch
        assert read(reopened)["cursor"] == 0
        assert read(reopened)["messages"] == [original]
    finally:
        reopened.close()


def test_unicode_and_escaped_pages_never_advance_omitted_events(store):
    join(store, machine="\U00010000" * 250, repo="\U00010001" * 250,
         worktree_id="\U00010002" * 250, session_id="\U00010003" * 250)
    for number in range(100):
        post(store, str(number), text="\x01" * 8192)
    page = read(store, limit=100)
    metadata = scan(store)
    for result in (page, metadata):
        assert len(json_bytes(result)) <= 1024 * 1024
        assert len(json.dumps(result).encode()) + 1 <= 1024 * 1024
        assert result["more"]
    assert page["next_cursor"] == page["messages"][-1]["id"]
    assert metadata["channels"][0]["scan_cursor"] == metadata["events"][-1]["id"]
    assert read(store)["cursor"] == 0


def test_shared_waiter_bound_is_released(store):
    join(store)
    held = [store._waiters.acquire(False) for _ in range(store.max_waiters)]
    try:
        assert all(held)
        expect("busy", scan, store)
        expect("busy", rpc, store, "wait", channel="room", codename="agent-a", timeout=0)
    finally:
        for acquired in held:
            if acquired:
                store._waiters.release()
    assert not rpc(store, "wait", channel="room", codename="agent-a", timeout=0)["ready"]


@pytest.mark.parametrize("end", ["leave-rejoin", "close"])
def test_held_scan_observes_membership_or_store_end(store, monkeypatch, end):
    join(store)
    started = threading.Event()
    result = []
    original = store._scan_page

    def capture(request):
        page = original(request)
        started.set()
        return page

    def wait():
        try:
            rpc(store, "scan", codename="agent-a", epoch=None,
                after=[{"channel": "room", "event_id": 0}], timeout=2)
        except ChatError as error:
            result.append(error.code)

    monkeypatch.setattr(store, "_scan_page", capture)
    thread = threading.Thread(target=wait)
    thread.start()
    try:
        assert started.wait(1)
        if end == "close":
            store.close()
            expect("busy", store.health)
        else:
            with store._changed:
                rpc(store, "leave", channel="room", codename="agent-a")
                join(store)
        thread.join(1)
        assert not thread.is_alive()
        assert result == ["busy" if end == "close" else "notjoined"]
    finally:
        store.close()
        thread.join(3)


@pytest.mark.parametrize("op", ["wait", "scan"])
@pytest.mark.parametrize("transition", [
    "leave-rejoin", "active-rejoin", "other-leave-rejoin", "failed-leave",
])
def test_held_membership_generation_is_independent_of_clock(store, monkeypatch, op, transition):
    monkeypatch.setattr(store_module, "_utc", lambda: "2000-01-01T00:00:00Z")
    joined_at = join(store)["membership"]["joined_at"]
    join(store, channel="other")
    started = threading.Event()
    errors, pages = [], []
    original_wait = store._changed.wait

    def held_wait(timeout):
        started.set()
        return original_wait(timeout)

    def wait():
        try:
            fields = {"channel": "room"} if op == "wait" else {
                "epoch": None, "after": [{"channel": "room", "event_id": 0}],
            }
            pages.append(rpc(store, op, codename="agent-a", timeout=2, batch_ms=0, **fields))
        except ChatError as error:
            errors.append(error.code)

    monkeypatch.setattr(store._changed, "wait", held_wait)
    thread = threading.Thread(target=wait)
    thread.start()
    try:
        assert started.wait(1)
        with store._changed:
            if transition == "leave-rejoin":
                rpc(store, "leave", channel="room", codename="agent-a")
            elif transition == "other-leave-rejoin":
                rpc(store, "leave", channel="other", codename="agent-a")
                join(store, channel="other")
            elif transition == "failed-leave":
                store._db.execute(
                    "CREATE TEMP TRIGGER fail_leave BEFORE UPDATE OF active ON memberships "
                    "BEGIN SELECT RAISE(ABORT, 'synthetic leave failure'); END"
                )
                try:
                    with pytest.raises(sqlite3.IntegrityError, match="synthetic leave failure"):
                        rpc(store, "leave", channel="room", codename="agent-a")
                finally:
                    store._db.execute("DROP TRIGGER fail_leave")
            assert join(store)["membership"]["joined_at"] == joined_at
            post(store, text="new membership event")
        thread.join(1)
        assert not thread.is_alive()
        assert errors == (["notjoined"] if transition == "leave-rejoin" else [])
        assert bool(pages) is (transition != "leave-rejoin")
        assert read(store)["cursor"] == 0
    finally:
        store.close()
        thread.join(3)


def test_database_busy_is_explicit_without_partial_post(store, tmp_path):
    join(store)
    blocker = sqlite3.connect(tmp_path / "data" / "chat.sqlite3", isolation_level=None)
    try:
        blocker.execute("BEGIN IMMEDIATE")
        expect("busy", post, store)
    finally:
        blocker.close()
    assert read(store)["messages"] == []
    assert not post(store)["duplicate"]


@pytest.mark.parametrize("payload", [
    {"op": "join", "channel": "other", "actor": {"codename": "agent-b"}},
    {"op": "leave", "channel": "room", "codename": "agent-a"},
    {"op": "post", "channel": "room", "codename": "agent-a",
     "text": "not committed", "request_id": "new-request"},
    {"op": "read", "channel": "room", "codename": "agent-a"},
    {"op": "cursor", "channel": "room", "codename": "agent-a",
     "expected_cursor": 0, "next_cursor": 1},
    {"op": "members", "channel": "room"},
    {"op": "resolve", "codename": "agent-a"},
    {"op": "wait", "channel": "room", "codename": "agent-a", "timeout": 0},
    {"op": "scan", "codename": "agent-a", "epoch": None,
     "after": [{"channel": "room", "event_id": 0}], "timeout": 0},
])
def test_drain_freezes_all_rpc_without_mutating_conversation(store, payload):
    join(store)
    event = post(store)["event"]
    before = list(store._db.iterdump())
    health = store.health()
    expected = {
        "state": "drained", "active_waiters": 0,
        "storage_schema": 1, "installation_id": "example-installation",
    }
    assert store.drain() == expected
    assert store.drain() == expected
    with pytest.raises(ChatError) as caught:
        store.dispatch(payload)
    assert caught.value.code == "draining" and caught.value.status == 503
    assert list(store._db.iterdump()) == before
    assert store.health() == health
    assert store.undrain() == {**expected, "state": "active"}
    assert read(store)["messages"] == [event]
    assert read(store)["cursor"] == 0
    assert post(store) == {"event": event, "duplicate": True}


@pytest.mark.parametrize("op", ["wait", "scan"])
@pytest.mark.parametrize("rollback", [False, True])
def test_drain_fences_held_waits_even_after_immediate_undrain(tmp_path, monkeypatch, op, rollback):
    store = ChatStore(tmp_path / "data", "example-installation", max_waiters=1)
    join(store)
    entered = threading.Event()
    original_wait = store._changed.wait
    errors, pages = [], []

    def waiting(timeout):
        entered.set()
        return original_wait(timeout)

    def request():
        try:
            fields = {"channel": "room"} if op == "wait" else {
                "epoch": None, "after": [{"channel": "room", "event_id": 0}],
            }
            pages.append(rpc(store, op, codename="agent-a", timeout=3, **fields))
        except ChatError as error:
            errors.append(error.code)

    monkeypatch.setattr(store._changed, "wait", waiting)
    thread = threading.Thread(target=request)
    thread.start()
    try:
        assert entered.wait(1)
        with store._changed:
            state = store.drain()
            assert state["state"] == "draining" and state["active_waiters"] == 1
            fields = {"channel": "room"} if op == "wait" else {
                "epoch": None, "after": [{"channel": "room", "event_id": 0}],
            }
            expect("draining", rpc, store, op, codename="agent-a", timeout=0, **fields)
            if rollback:
                assert store.undrain()["state"] == "active"
                post(store)
        thread.join(1)
        assert not thread.is_alive()
        assert errors == ["draining"] and pages == []
        assert store.drain()["state"] == "drained"
        assert store._active_waiters == 0
        store.undrain()
        event = post(store)["event"]
        assert read(store)["cursor"] == 0
        assert read(store)["messages"] == [event]
        assert rpc(store, "wait", channel="room", codename="agent-a", timeout=0)["ready"]
        assert scan(store)["events"][0]["id"] == event["id"]
    finally:
        store.close()
        thread.join(3)


def test_drain_waits_for_existing_write_commit_and_restart_preserves_it(store, tmp_path, monkeypatch):
    join(store)
    prepared, release, draining, drained = (threading.Event() for _ in range(4))
    original_post = store._post

    def held_post(request):
        result = original_post(request)
        prepared.set()
        assert release.wait(3), "test write was not released"
        return result

    def drain():
        draining.set()
        result = store.drain()
        drained.set()
        return result

    monkeypatch.setattr(store, "_post", held_post)
    with ThreadPoolExecutor(max_workers=2) as pool:
        writing = pool.submit(post, store)
        try:
            assert prepared.wait(1)
            freezing = pool.submit(drain)
            assert draining.wait(1)
            assert not drained.wait(.05)
        finally:
            release.set()
        event = writing.result(timeout=2)["event"]
        assert freezing.result(timeout=2)["state"] == "drained"
    store.close()
    reopened = ChatStore(tmp_path / "data", "example-installation")
    try:
        assert reopened.log_epoch == store.log_epoch
        assert post(reopened) == {"event": event, "duplicate": True}
        assert read(reopened)["messages"] == [event]
        assert read(reopened)["cursor"] == 0
    finally:
        reopened.close()


@pytest.mark.parametrize("op", ["wait", "scan"])
def test_waiter_failure_releases_lifecycle_count_and_capacity(store, monkeypatch, op):
    join(store)

    def fail(*args):
        raise RuntimeError("synthetic waiter failure")

    monkeypatch.setattr(store, "_unread" if op == "wait" else "_scan_page", fail)
    fields = {"channel": "room"} if op == "wait" else {
        "epoch": None, "after": [{"channel": "room", "event_id": 0}],
    }
    with pytest.raises(RuntimeError, match="synthetic waiter failure"):
        rpc(store, op, codename="agent-a", timeout=0, **fields)
    assert store.drain()["state"] == "drained"
    held = [store._waiters.acquire(False) for _ in range(store.max_waiters)]
    try:
        assert all(held)
    finally:
        for acquired in held:
            if acquired:
                store._waiters.release()


@pytest.mark.parametrize("body", [
    b'{"op":"read","op":"wait"}', b'{"actor":{"x":1,"x":2}}',
    b'{"x":NaN}', b'{"x":Infinity}', b'{"x":-Infinity}', b'{"x":1e999}',
    b'{"x":"\\ud800"}', b'{"\\udfff":1}', b'\xff', b'{} {}',
    b' ' * 65537, b'[' * 2000,
], ids=[
    "duplicate-operation", "duplicate-nested-key", "nan", "infinity",
    "negative-infinity", "numeric-overflow", "surrogate-value", "surrogate-key",
    "invalid-utf8", "multiple-values", "oversized-body", "deep-nesting",
])
def test_strict_serialized_inputs_are_bounded_and_unambiguous(body):
    expect("invalid", strict_json, body)


@pytest.mark.parametrize("payload", [
    [], {}, {"op": "unknown"}, {"op": []},
    {"op": "join", "channel": "room", "actor": {"codename": "UPPER"}},
    {"op": "join", "channel": "room", "actor": {"codename": "agent-a", "unknown": "value"}},
    {"op": "read", "channel": "room", "codename": "agent-a", "limit": True},
    {"op": "read", "channel": "room", "codename": "agent-a", "limit": 101},
    {"op": "scan", "codename": "agent-a", "epoch": None, "after": []},
    {"op": "scan", "codename": "agent-a", "epoch": None,
     "after": [{"channel": "room", "event_id": 1}]},
    {"op": "wait", "channel": "room", "codename": "agent-a", "timeout": float("nan")},
    {"op": "wait", "channel": "room", "codename": "agent-a", "timeout": 301},
    {"op": "wait", "channel": "room", "codename": "agent-a", "batch_ms": 5001},
])
def test_invalid_operation_fields_and_controls_are_explicit(store, payload):
    join(store)
    expect("invalid", store.dispatch, payload)
    assert read(store)["cursor"] == 0


def test_legacy_epoch_upgrade_preserves_events_and_semantic_cursor(store, tmp_path):
    join(store)
    original = post(store)["event"]
    rpc(store, "cursor", channel="room", codename="agent-a",
        expected_cursor=0, next_cursor=original["id"])
    store._db.execute("DELETE FROM settings WHERE key='log_epoch'")
    store.close()
    upgraded = ChatStore(tmp_path / "data", "example-installation")
    try:
        epoch = upgraded.log_epoch
        assert read(upgraded)["cursor"] == original["id"]
        assert post(upgraded) == {"event": original, "duplicate": True}
        assert upgraded.health()["schema"] == 1
    finally:
        upgraded.close()
    restarted = ChatStore(tmp_path / "data", "example-installation")
    try:
        assert restarted.log_epoch == epoch
        assert read(restarted)["cursor"] == original["id"]
    finally:
        restarted.close()


@pytest.mark.parametrize("field", ["max_channels", "max_actors", "max_memberships",
                                   "max_channel_members"])
def test_membership_capacity_is_finite_and_inactive_rows_are_retained(store, field):
    setattr(store, field, 1)
    join(store)
    if field == "max_channels":
        expect("capacity", join, store, channel="another")
    else:
        expect("capacity", join, store, "agent-b")
        rpc(store, "leave", channel="room", codename="agent-a")
        expect("capacity", join, store, "agent-b")
    assert join(store)["membership"]["active"]


@pytest.mark.parametrize(
    "text", ["", " ", "\0", "x\0y", "\u00e9" * 4097, "\ud800", 1],
    ids=["empty", "whitespace", "nul", "embedded-nul", "oversized-utf8", "surrogate", "nonstring"],
)
def test_message_byte_and_unicode_bounds(store, text):
    join(store)
    expect("invalid", post, store, text=text)
    assert post(store, text="\u00e9" * 4096)["event"]["text"] == "\u00e9" * 4096


def test_actor_aggregate_and_request_id_bounds(store):
    metadata = {key: "x" * 1000 for key in ("machine", "environment", "repo", "worktree_id")}
    join(store, **metadata)
    expect("invalid", join, store, session_id="s" * 200)
    for request_id in ("", "x" * 129, "\n", 1):
        expect("invalid", post, store, request_id)
    assert post(store, "r" * 128)["event"]["request_id"] == "r" * 128


def test_quiet_wait_and_plural_post_batch_do_not_consume_reading(store, monkeypatch):
    import time

    join(store)
    started = time.monotonic()
    assert not rpc(store, "wait", channel="room", codename="agent-a", timeout=0.03)["ready"]
    assert time.monotonic() - started >= 0.02
    observed = threading.Event()
    result = []
    original = store._unread

    def capture(channel, codename):
        value = original(channel, codename)
        observed.set()
        return value

    def wait():
        result.append(rpc(store, "wait", channel="room", codename="agent-a",
                          timeout=1, batch_ms=40))

    monkeypatch.setattr(store, "_unread", capture)
    thread = threading.Thread(target=wait)
    thread.start()
    try:
        assert observed.wait(1)
        post(store)
        post(store, "request-2")
        thread.join(2)
        assert not thread.is_alive()
        assert result[0]["ready"] and result[0]["unread_count"] == 2
        assert read(store)["cursor"] == 0
    finally:
        store.close()
        thread.join(3)
