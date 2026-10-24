import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from agent_chat_service import ChatError, ChatStore, Client, ClientError, json_bytes
from agent_chat_service.store import validate_request
from test_client import pair as pair
from test_store import expect, join, post, read, rpc, store as store


def annotation(kind="question", **fields):
    return {"schema": "agent-chat.collaboration", "version": 1, "kind": kind, **fields}


def collaborate(store, request_id="question", *, codename="agent-a", channel="room",
                text="Please review", **fields):
    return rpc(store, "post", channel=channel, codename=codename, text=text,
               request_id=request_id, collaboration=annotation(**fields))


def inbox(store, codename="agent-b", **controls):
    return rpc(store, "inbox", channel="room", codename=codename, **controls)


@pytest.mark.parametrize("fields", [
    {"version": True}, {"version": 2}, {"schema": "foreign"}, {"kind": "task"},
    {"author": "agent-b"}, {"thread_root": 1}, {"to": ["agent-b", "agent-b"]},
    {"to": ["bad name"]}, {"to": "agent-b"}, {"to": ["agent-b"] * 17},
    {"reply_to": True}, {"reply_to": 0}, {"supersedes": False},
    {"evidence": ["\n"]}, {"evidence": ["x" * 513]}, {"evidence": ["x"] * 9},
    {"evidence": "artifact"}, {"evidence": ["\ud800"]},
    {"evidence": ["x" * 512] * 8}, {"kind": "answer"}, {"kind": "review"},
    {"kind": "resolve"},
])
def test_metadata_refuses_invalid_fields_and_bounds(fields):
    request = {
        "op": "post", "channel": "room", "codename": "agent-a", "text": "hello",
        "request_id": "original", "collaboration": annotation(**fields),
    }
    expect("invalid", validate_request, request)


def test_structured_identity_recovery_and_legacy_shapes(store, tmp_path):
    join(store)
    join(store, "agent-b")
    plain = post(store, text='{"kind":"question","to":["agent-b"]}')["event"]
    original = collaborate(store, to=["agent-b"])
    assert original["collaboration"] == annotation(to=["agent-b"], evidence=[])
    assert set(original["event"]) == set(plain)
    assert original["thread_root"] == original["event"]["id"]
    assert collaborate(store, to=["agent-b"], evidence=[]) == {**original, "duplicate": True}
    expect("conflict", collaborate, store, to=[])
    expect("conflict", post, store, request_id="question", text="Please review")
    assert read(store)["messages"] == [plain, original["event"]]
    rpc(store, "leave", channel="room", codename="agent-a")
    store.close()
    with_store = ChatStore(tmp_path / "data", "example-installation")
    try:
        assert collaborate(with_store, to=["agent-b"]) == {**original, "duplicate": True}
        assert inbox(with_store)["entries"][0]["event"] == original["event"]
    finally:
        with_store.close()


def test_relationships_refuse_wrong_targets_without_mutation(store):
    join(store)
    join(store, "agent-b")
    join(store, "agent-a", channel="other")
    question = collaborate(store, to=["agent-b"])["event"]["id"]
    foreign = collaborate(store, "foreign", channel="other")["event"]["id"]
    plain = post(store, "plain")["event"]["id"]
    before = store._db.execute("SELECT count(*) FROM events").fetchone()[0]
    for fields in (
        {"to": ["absent"]}, {"reply_to": foreign}, {"reply_to": 999},
        {"kind": "answer", "reply_to": plain},
        {"kind": "review", "reply_to": question},
        {"kind": "resolve", "reply_to": question, "codename": "agent-b"},
        {"supersedes": question, "codename": "agent-b"},
        {"kind": "notice", "supersedes": question},
        {"supersedes": question, "reply_to": plain},
    ):
        expect("invalid", collaborate, store, "invalid", **fields)
    assert store._db.execute("SELECT count(*) FROM events").fetchone()[0] == before


def test_metadata_indexes_and_quota_rollback_atomically(store):
    join(store)
    join(store, "agent-b")
    before = dict(store._db.execute("SELECT key, value FROM settings"))
    store._db.execute(
        "CREATE TEMP TRIGGER fail_counter BEFORE UPDATE ON settings "
        "WHEN OLD.key='message_bytes' "
        "BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END"
    )
    try:
        with pytest.raises(sqlite3.IntegrityError, match="synthetic failure"):
            collaborate(store, to=["agent-b"])
    finally:
        store._db.execute("DROP TRIGGER fail_counter")
    for table in ("events", "collaborations", "collaboration_recipients"):
        assert store._db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    assert dict(store._db.execute("SELECT key, value FROM settings")) == before
    result = collaborate(store, to=["agent-b"])
    charge = len("Please review".encode()) + len(json_bytes(result["collaboration"]))
    assert int(dict(store._db.execute("SELECT key, value FROM settings"))["message_bytes"]) == charge
    store.max_message_bytes = charge
    assert collaborate(store, to=["agent-b"])["duplicate"]
    expect("capacity", collaborate, store, "overflow")


def test_pending_survives_noise_cursors_membership_and_reopen(store, tmp_path):
    join(store)
    join(store, "agent-b")
    question = collaborate(store, to=["agent-b"])
    for number in range(105):
        post(store, str(number))
    page = read(store, "agent-b", limit=100)
    rpc(store, "cursor", channel="room", codename="agent-b",
        expected_cursor=0, next_cursor=page["next_cursor"])
    rpc(store, "leave", channel="room", codename="agent-b")
    expect("notjoined", inbox, store)
    store.close()
    reopened = ChatStore(tmp_path / "data", "example-installation")
    try:
        join(reopened, "agent-b")
        result = inbox(reopened, limit=2)
        assert [entry["event"] for entry in result["entries"]] == [question["event"]]
        assert result["entries"][0]["attention"] == "reply"
        assert read(reopened, "agent-b")["cursor"] == page["next_cursor"]
    finally:
        reopened.close()


def test_answers_resolution_and_supersession_have_distinct_effects(store):
    for name in ("agent-a", "agent-b", "agent-c"):
        join(store, name)
    question = collaborate(store, to=["agent-b"])["event"]["id"]
    collaborate(store, "other-answer", codename="agent-c", kind="answer", reply_to=question)
    assert inbox(store)["entries"][0]["event"]["id"] == question
    assert inbox(store, "agent-a")["entries"][0]["attention"] == "review"
    collaborate(store, "own-answer", codename="agent-b", kind="answer", reply_to=question)
    assert inbox(store)["entries"] == []
    assert len(inbox(store, "agent-a")["entries"]) == 2
    collaborate(store, "resolution", kind="resolve", reply_to=question)
    assert inbox(store, "agent-a")["entries"] == []
    replacement = collaborate(store, "replacement", supersedes=question, to=["agent-b"])
    assert inbox(store)["entries"][0]["event"] == replacement["event"]
    thread = rpc(store, "thread", channel="room", codename="agent-b",
                 event_id=replacement["event"]["id"])
    assert thread["thread_root"] == question
    assert len(thread["entries"]) == 5


def test_captured_pages_remain_nonconsuming_despite_later_answers(store):
    join(store)
    join(store, "agent-b")
    questions = [collaborate(store, str(n), to=["agent-b"]) for n in range(3)]
    first = inbox(store, limit=1)
    assert first["more"] and first["entries"][0]["event"] == questions[0]["event"]
    collaborate(store, "late-answer", codename="agent-b", kind="answer",
                reply_to=questions[1]["event"]["id"])
    second = inbox(store, limit=1, **first["next"])
    assert second["entries"][0]["event"] == questions[1]["event"]
    assert second["head"] == first["head"]
    assert read(store, "agent-b")["cursor"] == 0
    expect("invalid", inbox, store, epoch="0" * 32, head=first["head"])
    expect("invalid", inbox, store, head=first["head"])
    expect("invalid", inbox, store, after=1)


def test_byte_bounded_pending_and_recent_pages_never_skip_or_consume(store):
    join(store)
    join(store, "agent-b")
    questions = [
        collaborate(store, str(n), to=["agent-b"], text=("quoted \" \N{SNOWMAN}" * 20))
        for n in range(4)
    ]
    first = inbox(store, limit=1, recent_limit=4)
    budget = max(1024, len(json_bytes(first)) + 32)
    controls = {"limit": 100, "max_bytes": budget, "recent_limit": 4}
    pending, recent = [], []
    for _ in range(10):
        page = inbox(store, **controls)
        assert len(json_bytes(page)) + 1 <= budget
        pending.extend(entry["event"]["id"] for entry in page["entries"])
        recent.extend(entry["event"]["id"] for entry in page["recent"])
        if not page["more"]:
            break
        controls.update(page["next"])
    else:
        pytest.fail("bounded pages did not terminate")
    expected = [question["event"]["id"] for question in questions]
    assert pending == recent == expected
    assert read(store, "agent-b")["cursor"] == 0
    collaborate(store, "large", to=["agent-b"], text="\1" * 2000)
    expect("capacity", inbox, store, after=questions[-1]["event"]["id"],
           epoch=page["epoch"], head=5, recent_limit=0, max_bytes=1024)


def test_plain_thread_root_and_superseding_reply_preserve_relationship(store):
    join(store)
    join(store, "agent-b")
    root = post(store)["event"]
    child = collaborate(store, "child", kind="notice", reply_to=root["id"])
    amendment = collaborate(store, "amendment", kind="notice", supersedes=child["event"]["id"])
    page = rpc(store, "thread", channel="room", codename="agent-b",
               event_id=amendment["event"]["id"], limit=2)
    assert page["thread_root"] == root["id"] and page["more"]
    assert page["entries"][0]["collaboration"] is None
    last = rpc(store, "thread", channel="room", codename="agent-b",
               event_id=amendment["event"]["id"], **page["next"])
    assert last["entries"][0]["event"] == amendment["event"] and not last["more"]
    assert store._db.execute(
        "SELECT reply_to FROM collaborations WHERE event_id=?", (amendment["event"]["id"],),
    ).fetchone()[0] == root["id"]
    expect("invalid", rpc, store, "thread", channel="room", codename="agent-b",
           event_id=amendment["event"]["id"], epoch=page["epoch"], head=root["id"])


def test_concurrent_annotated_retry_commits_one_effect_and_order_is_content(store):
    for name in ("agent-a", "agent-b", "agent-c"):
        join(store, name)
    with ThreadPoolExecutor(max_workers=4) as workers:
        results = list(workers.map(
            lambda _: collaborate(store, to=["agent-b", "agent-c"]), range(4),
        ))
    assert sum(not result["duplicate"] for result in results) == 1
    expect("conflict", collaborate, store, to=["agent-c", "agent-b"])
    assert store._db.execute("SELECT count(*) FROM collaboration_recipients").fetchone()[0] == 2


@pytest.mark.parametrize("damage", [
    "missing", "changed", "boolean-version", "boolean-root", "extra", "lost",
])
def test_annotated_pending_requires_complete_receipt_before_recovery(pair, damage):
    client, transport, store = pair
    value = annotation()

    def uncertain(request, result):
        if damage == "missing":
            result.pop("collaboration")
        elif damage == "changed":
            result["collaboration"]["kind"] = "notice"
        elif damage == "boolean-version":
            result["collaboration"]["version"] = True
        elif damage == "boolean-root":
            result["thread_root"] = True
        elif damage == "extra":
            result["authority"] = "complete"
        else:
            raise ClientError("provider_unavailable", "Synthetic lost response.")
        return result

    transport.after = uncertain
    with pytest.raises(ClientError) as caught:
        client.post("room", "question", "original", collaboration=value)
    assert caught.value.code == "post_unconfirmed"
    assert client.status()["pending_posts"] == [{"channel": "room", "request_id": "original"}]
    assert len(transport.calls) == 2  # Explicit join and exactly one post.
    with pytest.raises(ClientError) as caught:
        client.post("room", "question", "original")
    assert caught.value.code == "pending_conflict" and len(transport.calls) == 2
    transport.after = None
    client.leave("room")
    result = client.post("room", "question", "original", collaboration=value)
    assert result["duplicate"] and client.status()["pending_posts"] == []
    assert store._db.execute("SELECT count(*) FROM events").fetchone()[0] == 1


def test_unsupported_structured_service_never_falls_back_to_plaintext(pair):
    client, transport, _ = pair

    def unsupported(request):
        assert request["op"] == "post" and "collaboration" in request
        raise ClientError("invalid", "Unsupported operation fields.", status=400)

    transport.before = unsupported
    with pytest.raises(ClientError) as caught:
        client.post("room", "question", "original", collaboration=annotation())
    assert caught.value.code == "invalid"
    assert len(transport.calls) == 2
    assert client.status()["pending_posts"] == []
    with pytest.raises(ChatError) as caught:
        client.post("room", "question", "null", collaboration=None)
    assert caught.value.code == "invalid" and len(transport.calls) == 2


@pytest.mark.parametrize("damage", [
    "fields", "version", "identity", "epoch", "head", "ordering",
    "metadata", "attention", "continuation", "boolean-position", "empty-more",
    "count", "boolean-count",
])
def test_client_refuses_invalid_views_without_mutating_semantic_progress(pair, tmp_path, damage):
    _, transport, store = pair
    join(store, "agent-b")
    viewer = Client(transport, {"codename": "agent-b"}, tmp_path / "viewer")
    for number in range(3):
        collaborate(store, str(number), to=["agent-b"])

    def corrupt(request, result):
        if damage == "fields":
            result["cursor"] = 1
        elif damage == "version":
            result["version"] = True
        elif damage == "identity":
            result["codename"] = "agent-c"
        elif damage == "epoch":
            result["epoch"] = "foreign"
        elif damage == "head":
            result["head"] = False
        elif damage == "ordering":
            result["entries"][0]["event"]["id"] = 0
        elif damage == "metadata":
            result["entries"][0]["collaboration"]["version"] = True
        elif damage == "attention":
            result["entries"][0]["attention"] = "complete"
        elif damage == "continuation":
            result["next"]["head"] += 1
        elif damage == "boolean-position":
            result["next"]["after"] = True
        elif damage == "count":
            result["count"] += 1
        elif damage == "boolean-count":
            result["count"] = True
        else:
            result["entries"] = []
        return result

    transport.after = corrupt
    with pytest.raises(ClientError) as caught:
        viewer.inbox("room", limit=1, recent_limit=0)
    assert caught.value.code == "invalid_response"
    assert all(call["op"] != "cursor" for call in transport.calls)
    assert read(store, "agent-b")["cursor"] == 0
    transport.after = None
    first = viewer.inbox("room", limit=1)
    assert first["more"]
    assert viewer.inbox("room", **first["next"])["entries"][0]["event"]["id"] == 2


def seed_unrelated(store, start, count, *, structured, codename="agent-a",
                   channel="room", recipients=None, kind="question"):
    with store._transaction():
        for number in range(start, start + count):
            request = {
                "channel": channel, "codename": codename, "text": "unrelated context",
                "request_id": f"noise-{channel}-{number}",
            }
            if structured:
                request["collaboration"] = annotation(
                    kind, to=["agent-c"] if recipients is None else recipients,
                )
            store._post(request)


def sqlite_work(store, operation):
    steps = 0

    def progress():
        nonlocal steps
        steps += 1
        return 0

    store._db.set_progress_handler(progress, 1)
    try:
        result = operation()
    finally:
        store._db.set_progress_handler(None, 0)
    return steps, result


def test_empty_inbox_work_does_not_grow_with_unrelated_questions(store):
    for name in ("agent-a", "agent-b", "agent-c"):
        join(store, name)
    own = collaborate(store, "own", codename="agent-b", to=["agent-c"])
    seed_unrelated(store, 0, 100, structured=True)
    seed_unrelated(store, 0, 100, structured=False, codename="agent-b")
    seed_unrelated(store, 0, 100, structured=True, codename="agent-c",
                   kind="notice", recipients=["agent-b"])
    small, first = sqlite_work(store, lambda: inbox(store, recent_limit=0, limit=1))
    seed_unrelated(store, 100, 4900, structured=True)
    seed_unrelated(store, 100, 4900, structured=False, codename="agent-b")
    seed_unrelated(store, 100, 4900, structured=True, codename="agent-c",
                   kind="notice", recipients=["agent-b"])
    large, last = sqlite_work(store, lambda: inbox(store, recent_limit=0, limit=1))
    assert first["entries"] == last["entries"] == []
    assert not first["more"] and not last["more"]
    assert large <= small + 1000, (small, large)
    answer = collaborate(store, "answer", codename="agent-c", kind="answer",
                         reply_to=own["event"]["id"])
    assert inbox(store, recent_limit=0)["entries"][0]["event"] == answer["event"]
    assert read(store, "agent-b")["cursor"] == 0


def test_inbox_work_ignores_other_channel_correspondence(store):
    for channel in ("room", "other"):
        for name in ("agent-a", "agent-b"):
            join(store, name, channel=channel)
    seed_unrelated(store, 0, 100, structured=True, channel="other", recipients=["agent-b"])
    small, first = sqlite_work(store, lambda: inbox(store, recent_limit=0, limit=1))
    seed_unrelated(store, 100, 4900, structured=True, channel="other", recipients=["agent-b"])
    large, last = sqlite_work(store, lambda: inbox(store, recent_limit=0, limit=1))
    assert first["entries"] == last["entries"] == []
    assert not first["more"] and not last["more"]
    assert large <= small + 1000, (small, large)


def test_sparse_thread_work_and_pagination_ignore_unrelated_history(store):
    join(store)
    join(store, "agent-b")
    root = post(store)["event"]
    child = collaborate(store, "child", kind="notice", reply_to=root["id"])["event"]
    seed_unrelated(store, 0, 100, structured=False)

    def whole_thread():
        return rpc(store, "thread", channel="room", codename="agent-b",
                   event_id=child["id"], limit=2)

    small, first = sqlite_work(store, whole_thread)
    seed_unrelated(store, 100, 4900, structured=False)
    large, last = sqlite_work(store, whole_thread)
    assert [entry["event"] for entry in first["entries"]] == [root, child]
    assert last["entries"] == first["entries"]
    assert not first["more"] and not last["more"]
    assert large <= small + 1000, (small, large)
    paged = rpc(store, "thread", channel="room", codename="agent-b",
                event_id=child["id"], limit=1)
    assert paged["entries"][0]["event"] == root and paged["more"]
    next_page = rpc(store, "thread", channel="room", codename="agent-b",
                    event_id=child["id"], **paged["next"])
    assert [entry["event"] for entry in next_page["entries"]] == [child]
    assert not next_page["more"] and read(store, "agent-b")["cursor"] == 0


@pytest.mark.parametrize("attention", ["reply", "review"])
def test_inbox_lookahead_work_is_bounded_before_merging_pending_branches(store, attention):
    join(store)
    join(store, "agent-b")
    if attention == "reply":
        def seed(start, count):
            seed_unrelated(store, start, count, structured=True, recipients=["agent-b"])
    else:
        question = collaborate(store, "own", codename="agent-b", to=["agent-a"])["event"]["id"]

        def seed(start, count):
            with store._transaction():
                for number in range(start, start + count):
                    store._post({
                        "channel": "room", "codename": "agent-a", "text": "related answer",
                        "request_id": f"answer-{number}",
                        "collaboration": annotation("answer", reply_to=question),
                    })
            seed_unrelated(store, start, count, structured=True, recipients=["agent-b"])

    seed(0, 100)
    small, first = sqlite_work(store, lambda: inbox(store, recent_limit=0, limit=1))
    seed(100, 4900)
    large, last = sqlite_work(store, lambda: inbox(store, recent_limit=0, limit=1))
    assert len(first["entries"]) == len(last["entries"]) == 1
    assert first["entries"] == last["entries"] and last["entries"][0]["attention"] == attention
    assert first["more"] and last["more"]
    assert large <= small + 1000, (small, large)
    following = inbox(store, limit=1, **last["next"])
    assert following["entries"][0]["event"]["id"] > last["entries"][0]["event"]["id"]
    assert read(store, "agent-b")["cursor"] == 0


def test_pending_question_lookup_work_ignores_other_participants_answers(store):
    for name in ("agent-a", "agent-b", "agent-c"):
        join(store, name)
    question = collaborate(store, "question", to=["agent-b"])["event"]

    def seed(start, count):
        with store._transaction():
            for number in range(start, start + count):
                store._post({
                    "channel": "room", "codename": "agent-c", "text": "third-party answer",
                    "request_id": f"third-party-{number}",
                    "collaboration": annotation("answer", reply_to=question["id"]),
                })

    seed(0, 100)
    small, first = sqlite_work(store, lambda: inbox(store, recent_limit=0, limit=1))
    seed(100, 4900)
    large, last = sqlite_work(store, lambda: inbox(store, recent_limit=0, limit=1))
    assert len(first["entries"]) == len(last["entries"]) == 1
    assert first["entries"] == last["entries"]
    assert last["entries"][0]["event"] == question
    assert last["entries"][0]["attention"] == "reply"
    assert not first["more"] and not last["more"]
    assert large <= small + 1000, (small, large)
    collaborate(store, "own-answer", codename="agent-b", kind="answer",
                reply_to=question["id"])
    assert inbox(store, recent_limit=0)["entries"] == []
    assert read(store, "agent-b")["cursor"] == 0


def seed_settled(store, start, count, settlement):
    asker, answerer = (
        ("agent-a", "agent-b") if settlement == "answered" else ("agent-b", "agent-a")
    )
    def append(number, codename, suffix, value):
        return store._post({
            "channel": "room", "codename": codename, "text": "settled correspondence",
            "request_id": f"{settlement}-{number}-{suffix}", "collaboration": value,
        })["event"]["id"]

    with store._transaction():
        for number in range(start, start + count):
            question = append(number, asker, "question", annotation(to=[answerer]))
            append(number, answerer, "answer", annotation("answer", reply_to=question))
            if settlement == "resolved":
                append(number, asker, "close", annotation("resolve", reply_to=question))
            elif settlement == "superseded":
                append(number, asker, "replace", annotation(to=[answerer], supersedes=question))


@pytest.mark.parametrize("settlement", ["answered", "resolved", "superseded"])
def test_inbox_examination_is_bounded_over_settled_history(store, settlement):
    join(store)
    join(store, "agent-b")
    seed_settled(store, 0, 100, settlement)
    small, first = sqlite_work(store, lambda: inbox(store, limit=1, recent_limit=0))
    seed_settled(store, 100, 4900, settlement)
    large, page = sqlite_work(store, lambda: inbox(store, limit=1, recent_limit=0))
    assert first["entries"] == page["entries"] == []
    assert large <= small + 1000, (small, large)
    captured = page["epoch"], page["head"]
    requests = 1
    while page["more"]:
        assert page["count"] == 0
        after = page["next"]["after"]
        page = inbox(store, limit=1, **page["next"])
        assert (page["epoch"], page["head"]) == captured
        assert page["entries"] == [] and page["recent"] == []
        if page["more"]:
            assert page["next"]["after"] > after
        requests += 1
        assert requests <= 60
    assert requests > 1
    assert read(store, "agent-b")["cursor"] == 0


@pytest.mark.parametrize("damage", ["normal", "stalled", "past-head"])
def test_inbox_scan_continuations_preserve_captured_pending_without_skips(
    pair, tmp_path, damage,
):
    _, transport, store = pair
    join(store, "agent-b")
    seed_settled(store, 0, 101, "answered")
    seed_settled(store, 0, 100, "resolved")
    pending = [
        collaborate(store, f"pending-{number}", to=["agent-b"])["event"]["id"]
        for number in range(2)
    ]
    viewer = Client(transport, {"codename": "agent-b"}, tmp_path / "viewer")

    def corrupt(request, result):
        if damage == "stalled":
            result["next"]["after"] = request.get("after", 0)
        else:
            result["next"]["after"] = result["head"] + 1
        return result

    if damage != "normal":
        transport.after = corrupt
        with pytest.raises(ClientError) as caught:
            viewer.inbox("room", limit=1, recent_limit=0)
        assert caught.value.code == "invalid_response"
        assert read(store, "agent-b")["cursor"] == 0
        return

    page = viewer.inbox("room", limit=1, recent_limit=0)
    assert page["entries"] == [] and page["more"] and page["count"] == 0
    captured = page["epoch"], page["head"]
    collaborate(store, "later-answer", codename="agent-b", kind="answer", reply_to=pending[0])
    collaborate(store, "outside-capture", to=["agent-b"])
    seen = []
    requests = 0
    while True:
        assert (page["epoch"], page["head"]) == captured
        assert page["count"] == len(page["entries"])
        seen.extend(entry["event"]["id"] for entry in page["entries"])
        if not page["more"]:
            break
        page = viewer.inbox("room", limit=1, **page["next"])
        requests += 1
        assert requests <= 6
    assert seen == pending
    assert read(store, "agent-b")["cursor"] == 0
