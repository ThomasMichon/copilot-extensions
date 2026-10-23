import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from agent_chat_service import ChatError, ChatStore, Client, ClientError, strict_json
from agent_chat_service.client import MAX_PENDING, reader_lock


class LocalTransport:
    identity = "example-conversation"

    def __init__(self, store):
        self.store, self.calls = store, []
        self.before = None
        self.after = None

    def call(self, request):
        self.calls.append(copy.deepcopy(request))
        if self.before:
            self.before(request)
        try:
            result = self.store.dispatch(request)
        except ChatError as error:
            raise ClientError(error.code, "Service refusal.", status=error.status) from None
        return self.after(request, result) if self.after else result


@pytest.fixture
def pair(tmp_path):
    store = ChatStore(tmp_path / "store", "example-installation")
    transport = LocalTransport(store)
    client = Client(transport, {"codename": "agent-a"}, tmp_path / "client")
    client.join("room")
    try:
        yield client, transport, store
    finally:
        store.close()


def test_uncertain_post_creates_private_exclusive_temporary_before_atomic_replace(pair, monkeypatch):
    import agent_chat_service.client as module

    client, transport, _ = pair
    opened, replaced = [], []
    original_open, original_replace = os.open, os.replace

    def create(path, flags, mode=0o777, **kwargs):
        opened.append((Path(path), flags, mode))
        return original_open(path, flags, mode, **kwargs)

    def replace(source, target):
        assert json.loads(source.read_text(encoding="utf-8"))["text"] == "retained private body"
        if os.name != "nt":
            assert source.stat().st_mode & 0o777 == 0o600
        replaced.append(target)
        return original_replace(source, target)

    def unavailable(_request):
        raise ClientError("provider_timeout", "Synthetic uncertain response.")

    transport.before = unavailable
    monkeypatch.setattr(module.os, "open", create)
    monkeypatch.setattr(module.os, "replace", replace)
    with pytest.raises(ClientError) as caught:
        client.post("room", "retained private body", "private-request")
    assert caught.value.code == "post_unconfirmed"
    assert len(opened) == len(replaced) == 1
    temporary, flags, mode = opened[0]
    assert flags & os.O_EXCL and flags & os.O_CREAT and flags & os.O_WRONLY
    assert mode == 0o600 and temporary.parent == replaced[0].parent
    assert not temporary.exists()
    assert json.loads(replaced[0].read_text(encoding="utf-8"))["text"] == "retained private body"


@pytest.mark.skipif(os.name == "nt", reason="POSIX creation modes require native POSIX")
def test_posix_uncertain_post_is_private_under_umask_022_without_changing_test_process(tmp_path):
    probe = """
import os,sys
from pathlib import Path
from agent_chat_service.client import Client,ClientError
os.umask(0o022)
class Unavailable:
    identity="private-state-fixture"
    def call(self,payload):
        raise ClientError("provider_timeout","Synthetic uncertain response.")
client=Client(Unavailable(),{"codename":"agent-a"},Path(sys.argv[1]))
try:
    client.post("room","retained private body","private-request")
except ClientError as error:
    assert error.code=="post_unconfirmed"
else:
    raise AssertionError("fixture did not retain an uncertain post")
pending=client.directory/"pending-posts"
files=list(pending.glob("*.json"))
assert len(files)==1
assert client.directory.stat().st_mode & 0o777==0o700
assert pending.stat().st_mode & 0o777==0o700
assert files[0].stat().st_mode & 0o777==0o600
assert "retained private body" in files[0].read_text()
assert not list(pending.glob("*.tmp"))
print("private retained post",flush=True)
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", probe, str(tmp_path / "traversable-client-state")],
        capture_output=True, text=True, timeout=10, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "private retained post"


def test_members_pages_preserve_legacy_shape_without_read_progress(pair):
    client, transport, store = pair
    for name in ("agent-b", "agent-c"):
        store.dispatch({"op": "join", "channel": "room", "actor": {"codename": name}})
    legacy = client.members("room")
    assert set(legacy) == {"channel", "members"}
    first = client.members("room", limit=2)
    assert [actor["codename"] for actor in first["members"]] == ["agent-a", "agent-b"]
    assert first["more"] is True and first["next_after"] == "agent-b"
    last = client.members("room", after=first["next_after"])
    assert [actor["codename"] for actor in last["members"]] == ["agent-c"]
    assert last["more"] is False and last["next_after"] is None
    assert first["members"] + last["members"] == legacy["members"]
    assert store.dispatch({"op": "read", "channel": "room", "codename": "agent-a"})["cursor"] == 0
    assert not any(call["op"] in {"read", "cursor"} for call in transport.calls)


@pytest.mark.parametrize("controls", [
    {"limit": 0}, {"limit": 101}, {"limit": True},
    {"after": ""}, {"after": "invalid name"},
])
def test_invalid_member_controls_refuse_before_transport(pair, controls):
    client, transport, _ = pair
    calls = len(transport.calls)
    with pytest.raises(ChatError):
        client.members("room", **controls)
    assert len(transport.calls) == calls


@pytest.mark.parametrize("mutation", [
    "more-type", "empty-more", "wrong-next", "over-limit",
    "descending", "duplicate", "terminal-next", "extra-field", "before-after",
])
def test_invalid_member_pages_refuse_progress(pair, mutation):
    client, transport, store = pair
    for name in ("agent-b", "agent-c", "agent-d"):
        store.dispatch({"op": "join", "channel": "room", "actor": {"codename": name}})

    def after(request, result):
        if mutation == "more-type":
            result["more"] = 1
        elif mutation == "empty-more":
            result["members"] = []
        elif mutation == "wrong-next":
            result["next_after"] = "agent-d"
        elif mutation == "over-limit":
            result["members"].append({"codename": "agent-z", "joined_at": "synthetic"})
        elif mutation == "descending":
            result["members"].reverse()
        elif mutation == "duplicate":
            result["members"][1] = result["members"][0]
        elif mutation == "terminal-next":
            result["more"] = False
        elif mutation == "extra-field":
            result["extra"] = True
        else:
            result["members"][0]["codename"] = "agent-a"
        return result

    transport.after = after
    with pytest.raises(ClientError) as caught:
        client.members("room", limit=2, after="agent-a" if mutation == "before-after" else None)
    assert caught.value.code == "invalid_response"


def test_output_flush_precedes_progress_and_viewers_are_independent(pair, tmp_path):
    client, transport, store = pair
    client.post("room", "original body", "request-1")
    other = Client(transport, {"codename": "agent-b"}, tmp_path / "client")
    other.join("room")
    flushed = []

    class Output(io.StringIO):
        def flush(self):
            assert store.dispatch({"op": "read", "channel": "room", "codename": "agent-a"})["cursor"] == 0
            flushed.append(True)
            super().flush()

    def before(request):
        if request["op"] == "cursor":
            assert flushed

    transport.before = before
    stream = Output()
    page = client.read("room", stream)
    assert json.loads(stream.getvalue()) == page
    assert page["messages"][0]["text"] == "original body"
    assert client.read("room", io.StringIO())["messages"] == []
    assert other.read("room", io.StringIO())["messages"][0]["text"] == "original body"


@pytest.mark.parametrize("failure", ["write", "partial", "flush"])
def test_failed_output_never_commits_a_cursor(pair, failure):
    client, transport, _ = pair
    client.post("room", "body", "request-1")

    class Output(io.StringIO):
        def write(self, value):
            if failure == "write":
                raise BrokenPipeError("synthetic")
            return 1 if failure == "partial" else super().write(value)

        def flush(self):
            if failure == "flush":
                raise OSError("synthetic")
            super().flush()

    with pytest.raises((OSError, ClientError)):
        client.read("room", Output())
    assert not any(call["op"] == "cursor" for call in transport.calls)
    assert len(client.read("room", io.StringIO())["messages"]) == 1


@pytest.mark.parametrize("failure", ["refused", "mismatched"])
def test_flushed_output_with_unconfirmed_progress_is_explicit(pair, failure):
    client, transport, _ = pair
    client.post("room", "body", "request-1")

    def before(request):
        if request["op"] == "cursor" and failure == "refused":
            raise ClientError("unavailable", "Synthetic failure.")

    def after(request, result):
        if request["op"] == "cursor" and failure == "mismatched":
            result["cursor"] = True
        return result

    transport.before = before
    transport.after = after
    stream = io.StringIO()
    with pytest.raises(ClientError, match="may replay") as caught:
        client.read("room", stream)
    assert caught.value.code == "cursor_unconfirmed"
    assert json.loads(stream.getvalue())["messages"][0]["text"] == "body"
    transport.before = None
    transport.after = None
    assert len(client.read("room", io.StringIO())["messages"]) == (1 if failure == "refused" else 0)


@pytest.mark.parametrize("mutation", [
    "channel", "codename", "cursor-bool", "position", "duplicate-id", "foreign-event", "event-type",
])
def test_invalid_pages_do_not_print_or_advance(pair, mutation):
    client, transport, _ = pair
    client.post("room", "body", "request-1")

    def after(request, result):
        if request["op"] != "read":
            return result
        if mutation in {"channel", "codename"}:
            result[mutation] = "other"
        elif mutation == "cursor-bool":
            result["cursor"] = False
        elif mutation == "position":
            result["next_cursor"] += 1
        elif mutation == "duplicate-id":
            result["messages"] *= 2
        elif mutation == "foreign-event":
            result["messages"][0]["channel"] = "other"
        else:
            result["messages"][0]["id"] = True
        return result

    transport.after = after
    stream = io.StringIO()
    with pytest.raises(ClientError):
        client.read("room", stream)
    assert stream.getvalue() == ""
    assert not any(call["op"] == "cursor" for call in transport.calls)

@pytest.mark.parametrize("op", ["join", "leave", "wait", "scan", "members", "resolve"])
def test_malformed_success_objects_do_not_confirm_an_operation(pair, op):
    client, transport, _ = pair
    transport.after = lambda request, result: {"unexpected": True}
    with pytest.raises(ClientError):
        if op in {"join", "leave", "members"}:
            getattr(client, op)("room")
        elif op == "wait":
            client.wait("room", timeout=0)
        elif op == "scan":
            client.scan(epoch=None, after=[{"channel": "room", "event_id": 0}], timeout=0)
        else:
            client.resolve("agent-a")


@pytest.mark.parametrize("mutation", ["body", "unreturned-progress", "epoch", "duplicate-channel"])
def test_scan_validates_body_free_returned_only_progress_without_reading(pair, mutation):
    client, transport, store = pair
    client.post("room", "one", "request-1")
    client.post("room", "two", "request-2")

    def after(request, result):
        if request["op"] != "scan":
            return result
        if mutation == "body":
            result["events"][0]["text"] = "must not reach the caller"
        elif mutation == "unreturned-progress":
            result["channels"][0]["scan_cursor"] = result["channels"][0]["head"]
        elif mutation == "epoch":
            result["epoch"] = "0" * 32
        else:
            result["channels"] *= 2
        return result

    transport.after = after
    with pytest.raises(ClientError) as caught:
        client.scan(epoch=store.log_epoch, after=[{"channel": "room", "event_id": 0}],
                    timeout=0, limit=1)
    assert caught.value.code == "invalid_response"
    assert not any(request["op"] in {"read", "cursor"} for request in transport.calls)


def test_pending_original_is_retained_and_only_explicit_same_id_recovery_sends(pair):
    client, transport, store = pair

    def lost(request, result):
        if request["op"] == "post":
            raise ClientError("unavailable", "Synthetic lost response.")
        return result

    transport.after = lost
    with pytest.raises(ClientError) as caught:
        client.post("room", "retained body", "request-1")
    assert caught.value.code == "post_unconfirmed"
    assert len([call for call in transport.calls if call["op"] == "post"]) == 1
    status = client.status()
    assert status["pending_posts"] == [{"channel": "room", "request_id": "request-1"}]
    assert "retained body" not in json.dumps(status)
    with pytest.raises(ClientError) as caught:
        client.post("room", "changed", "request-1")
    assert caught.value.code == "pending_conflict"
    transport.after = None
    result = client.post("room", "retained body", "request-1")
    assert result["duplicate"] and result["event"]["text"] == "retained body"
    assert client.status()["pending_posts"] == []
    assert store._db.execute("SELECT count(*) FROM events").fetchone()[0] == 1


def test_pending_capacity_is_exact_and_a_later_refusal_does_not_erase_uncertainty(pair):
    client, transport, _ = pair

    def unavailable(_):
        raise ClientError("unavailable", "Synthetic failure.")

    transport.before = unavailable
    # Seed the persisted boundary without repeatedly rescanning the growing inventory.
    directory = client.directory / "pending-posts"
    directory.mkdir(parents=True)
    for index in range(MAX_PENDING - 1):
        request_id = str(index)
        key = hashlib.sha256(request_id.encode("utf-8")).hexdigest()
        (directory / (key + ".json")).write_text(json.dumps({
            "op": "post", "channel": "room", "codename": client.actor["codename"],
            "text": "body", "request_id": request_id,
        }), encoding="utf-8")
    final_id = str(MAX_PENDING - 1)
    with pytest.raises(ClientError) as caught:
        client.post("room", "body", final_id)
    assert caught.value.code == "post_unconfirmed"
    assert len(client.status()["pending_posts"]) == MAX_PENDING
    before = len(transport.calls)
    with pytest.raises(ClientError) as caught:
        client.post("room", "body", "overflow")
    assert caught.value.code == "pending_capacity" and len(transport.calls) == before

    def refused(_):
        raise ClientError("notjoined", "Synthetic refusal.", status=409)

    transport.before = refused
    with pytest.raises(ClientError):
        client.post("room", "body", final_id)
    assert len(client.status()["pending_posts"]) == MAX_PENDING


def test_new_definitive_refusal_does_not_leave_an_unknown_post(pair):
    client, _, _ = pair
    client.leave("room")
    with pytest.raises(ClientError) as caught:
        client.post("room", "body", "request-1")
    assert caught.value.status == 409
    assert client.status()["pending_posts"] == []


@pytest.mark.parametrize("publication", ["normal", "sharing-race"])
def test_local_reader_lock_is_shared_and_crash_released(pair, tmp_path, monkeypatch, publication):
    from agent_procutil import direct_python, no_window_kwargs, spawn_sync_in_kill_on_close_job

    client, _, _ = pair
    lock = client.directory / "reader.lock"
    ready = tmp_path / "ready"
    code = (
        "from agent_chat_service.client import reader_lock; from pathlib import Path; "
        "import json,os,sys,time\n"
        "with reader_lock(Path(sys.argv[1])):\n"
        " ready=Path(sys.argv[2]); temporary=ready.with_suffix('.tmp')\n"
        " temporary.write_text(json.dumps({'pid':os.getpid(),'prefix':sys.prefix}))\n"
        " temporary.replace(ready); time.sleep(30)\n"
    )
    executable, extra_environment = direct_python(sys.executable)
    process, job = spawn_sync_in_kill_on_close_job(
        [executable, "-c", code, str(lock), str(ready)],
        require_job=True, allow_breakaway=False,
        env={**os.environ, **extra_environment},
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        **no_window_kwargs(),
    )
    try:
        deadline = time.monotonic() + 5
        last_error = None
        injected = False
        original_read = Path.read_text
        original_exists = Path.exists

        def read_published(path, *args, **kwargs):
            nonlocal injected
            value = original_read(path, *args, **kwargs)
            if path == ready and publication == "sharing-race" and not injected:
                injected = True
                if os.name == "nt":
                    raise PermissionError(13, "Synthetic publication sharing race.")
                raise FileNotFoundError("Synthetic publication visibility race.")
            return value

        with monkeypatch.context() as publication_patch:
            publication_patch.setattr(Path, "read_text", read_published)
            if publication == "sharing-race":
                # A stale absence probe cannot bypass the successful-read fault.
                publication_patch.setattr(
                    Path, "exists",
                    lambda path, *args, **kwargs:
                    False if path == ready else original_exists(path, *args, **kwargs),
                )
            while True:
                if time.monotonic() >= deadline or process.poll() is not None:
                    raise AssertionError("Lock holder did not publish a readable ready record.") from last_error
                try:
                    holder = json.loads(ready.read_text())
                    break
                except FileNotFoundError as error:
                    last_error = error
                except PermissionError as error:
                    # Windows replacement can publish the name before its handle retires.
                    if os.name != "nt" or error.errno != 13:
                        raise
                    last_error = error
                time.sleep(.01)
        assert injected == (publication == "sharing-race")
        assert holder["pid"] == process.pid
        assert Path(holder["prefix"]).resolve() == Path(sys.prefix).resolve()
        with pytest.raises(ClientError) as caught:
            client.read("room", io.StringIO())
        assert caught.value.code == "reader_busy"
        process.kill()
        process.wait(timeout=5)
        # Observe release before the fixture's enclosing Job is closed.
        with reader_lock(lock):
            pass
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if job is not None:
            job.close()
        process.stderr.close()


def test_response_json_can_be_large_without_widening_default_request_bounds():
    raw = json.dumps({"value": "x" * 65536}).encode()
    with pytest.raises(ChatError):
        strict_json(raw)
    assert strict_json(raw, max_bytes=1048576)["value"] == "x" * 65536
    for bound in (True, 0, 1048577):
        with pytest.raises(ChatError):
            strict_json(b"{}", max_bytes=bound)
