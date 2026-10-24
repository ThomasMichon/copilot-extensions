"""Explicit conversation clients; transport and native ownership remain separate."""

from __future__ import annotations

import hashlib
import os
import re
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol

from .store import (
    ChatError, MAX_BODY_BYTES, MAX_PAGE_BYTES, json_bytes, strict_json, validate_request,
)

MAX_PENDING = 128
UNANNOTATED = object()


class ClientError(Exception):
    def __init__(self, code: str, message: str, *, status: int | None = None):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status

    def as_dict(self) -> dict:
        result = {"error": self.code, "message": self.message}
        if self.status is not None:
            result["status"] = self.status
        return result


class RpcTransport(Protocol):
    identity: str

    def call(self, payload: dict) -> dict: ...


@contextmanager
def reader_lock(path: Path):
    """Nonblocking OS locks are released even if the client process dies."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink():
        raise ClientError("invalid_state", "Redirected lock file.")
    with path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if not stream.tell():
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise ClientError("reader_busy", "Another local client operation owns this viewer.") from None
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise ClientError("reader_busy", "Another local client operation owns this viewer.") from None
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def emit(value: dict, stream) -> None:
    text = json_bytes(value).decode("utf-8") + "\n"
    if len(text.encode("utf-8")) > MAX_PAGE_BYTES:
        raise ClientError("invalid_response", "Output exceeds the response byte bound.")
    if stream.write(text) != len(text):
        raise ClientError("output_incomplete", "Output was incomplete; progress was not committed.")
    stream.flush()


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(json_bytes(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _event(event: object) -> dict:
    if not isinstance(event, dict) or set(event) != {
        "id", "channel", "timestamp", "author", "text", "request_id",
    }:
        raise ClientError("invalid_response", "Invalid event fields.")
    if (type(event["id"]) is not int or event["id"] <= 0
            or not isinstance(event["timestamp"], str) or not event["timestamp"]
            or len(event["timestamp"].encode("utf-8")) > 128):
        raise ClientError("invalid_response", "Invalid event identity.")
    try:
        validate_request({"op": "join", "channel": event["channel"], "actor": event["author"]})
        validate_request({
            "op": "post", "channel": event["channel"], "codename": event["author"]["codename"],
            "text": event["text"], "request_id": event["request_id"],
        })
    except ChatError:
        raise ClientError("invalid_response", "Invalid event content.") from None
    return event


class Client:
    def __init__(self, transport: RpcTransport, actor: dict, state_dir: str | Path):
        validate_request({"op": "join", "channel": "validation", "actor": actor})
        if (not isinstance(transport.identity, str) or not transport.identity
                or len(transport.identity.encode("utf-8")) > 4096):
            raise ClientError("invalid_configuration", "A bounded transport identity is required.")
        directory = Path(state_dir)
        if not directory.is_absolute():
            raise ClientError("invalid_configuration", "The owned client state directory must be absolute.")
        self.transport, self.actor = transport, dict(actor)
        key = hashlib.sha256(json_bytes([transport.identity, actor["codename"]])).hexdigest()
        self.directory = directory.resolve() / key
        if self.directory.is_symlink():
            raise ClientError("invalid_state", "Redirected client namespace.")

    def request(self, op: str, **fields) -> dict:
        payload = {"op": op, **fields}
        validate_request(payload)
        if len(json_bytes(payload)) > MAX_BODY_BYTES:
            raise ClientError("invalid_request", "Request exceeds the byte bound.")
        result = self.transport.call(payload)
        try:
            raw = json_bytes(result)
            if not isinstance(result, dict) or len(raw) + 1 > MAX_PAGE_BYTES:
                raise ValueError("invalid response")
            strict_json(raw, max_bytes=MAX_PAGE_BYTES)
        except (ChatError, TypeError, ValueError, UnicodeError, RecursionError):
            raise ClientError("invalid_response", "Invalid or oversized service response.") from None
        return result

    def join(self, channel: str) -> dict:
        result = self.request("join", channel=channel, actor=self.actor)
        try:
            if set(result) != {"channel", "actor", "membership", "cursor"}:
                raise ValueError("join fields")
            validate_request({"op": "join", "channel": result["channel"], "actor": result["actor"]})
            membership = result["membership"]
            if (result["channel"] != channel
                    or any(result["actor"].get(key) != value for key, value in self.actor.items())
                    or not isinstance(membership, dict) or set(membership) != {"active", "joined_at"}
                    or membership["active"] is not True
                    or not isinstance(membership["joined_at"], str) or not membership["joined_at"]
                    or type(result["cursor"]) is not int or result["cursor"] < 0):
                raise ValueError("join identity")
        except (ChatError, ValueError):
            raise ClientError("join_unconfirmed", "The service did not confirm the intended membership.") from None
        return result

    def leave(self, channel: str) -> dict:
        result = self.request("leave", channel=channel, codename=self.actor["codename"], actor=self.actor)
        if (set(result) != {"channel", "codename", "active"} or result["channel"] != channel
                or result["codename"] != self.actor["codename"] or result["active"] is not False):
            raise ClientError("leave_unconfirmed", "The service did not confirm the intended leave.")
        return result

    def wait(self, channel: str, *, timeout=300, batch_ms=250) -> dict:
        result = self.request(
            "wait", channel=channel, codename=self.actor["codename"], timeout=timeout, batch_ms=batch_ms,
        )
        if (set(result) != {"channel", "codename", "ready", "unread_count", "next_cursor"}
                or result["channel"] != channel or result["codename"] != self.actor["codename"]
                or type(result["ready"]) is not bool or type(result["unread_count"]) is not int
                or result["unread_count"] < 0 or result["ready"] != (result["unread_count"] > 0)
                or type(result["next_cursor"]) is not int or result["next_cursor"] < 0):
            raise ClientError("invalid_response", "Invalid wait metadata.")
        return result

    def scan(self, **controls) -> dict:
        result = self.request("scan", codename=self.actor["codename"], **controls)
        try:
            if (set(result) != {"schema", "version", "installation_id", "epoch", "codename",
                               "channels", "events", "more"}
                    or result["schema"] != "agent-chat.scan" or type(result["version"]) is not int
                    or result["version"] != 1 or result["codename"] != self.actor["codename"]
                    or not isinstance(result["epoch"], str)
                    or not re.fullmatch(r"[a-f0-9]{32}", result["epoch"])
                    or controls.get("epoch") not in (None, result["epoch"])
                    or not isinstance(result["installation_id"], str) or not result["installation_id"]
                    or not isinstance(result["channels"], list) or not isinstance(result["events"], list)
                    or type(result["more"]) is not bool
                    or len(result["events"]) > controls.get("limit", 100)):
                raise ValueError("scan envelope")
            requested = {item["channel"]: item["event_id"] for item in controls["after"]}
            states = {}
            for state in result["channels"]:
                if (not isinstance(state, dict) or set(state) != {
                    "channel", "after", "scan_cursor", "head", "history_start", "read_cursor",
                } or not isinstance(state["channel"], str) or state["channel"] in states
                        or state["channel"] not in requested
                        or any(type(state[key]) is not int or state[key] < 0 for key in (
                            "after", "scan_cursor", "head", "history_start", "read_cursor",
                        ))
                        or state["after"] != requested[state["channel"]]
                        or not state["history_start"] <= state["after"] <= state["scan_cursor"] <= state["head"]
                        or state["read_cursor"] > state["head"]):
                    raise ValueError("scan positions")
                states[state["channel"]] = state
            if set(states) != set(requested):
                raise ValueError("scan channel set")
            positions = dict(requested)
            previous = 0
            for event in result["events"]:
                if (not isinstance(event, dict) or set(event) != {"id", "channel", "timestamp", "author"}
                        or type(event["id"]) is not int or event["id"] <= previous
                        or not isinstance(event["channel"], str) or event["channel"] not in states
                        or not positions[event["channel"]] < event["id"] <= states[event["channel"]]["head"]
                        or not isinstance(event["timestamp"], str) or not event["timestamp"]):
                    raise ValueError("scan event")
                validate_request({"op": "join", "channel": event["channel"], "actor": event["author"]})
                positions[event["channel"]] = previous = event["id"]
            if (any(state["scan_cursor"] != positions[channel] for channel, state in states.items())
                    or result["more"] != any(state["scan_cursor"] < state["head"] for state in states.values())):
                raise ValueError("scan returned-only progress")
        except (ChatError, ValueError):
            raise ClientError("invalid_response", "Invalid body-free scan metadata; progress is not accepted.") from None
        return result

    def members(self, channel: str, *, limit: int | None = None, after: str | None = None) -> dict:
        controls: dict[str, int | str] = {}
        if limit is not None:
            controls["limit"] = limit
        if after is not None:
            controls["after"] = after
        result = self.request("members", channel=channel, **controls)
        try:
            fields = {"channel", "members"}
            if controls:
                fields |= {"more", "next_after"}
            if (set(result) != fields or result["channel"] != channel
                    or not isinstance(result["members"], list)):
                raise ValueError("member fields")
            names = []
            for actor in result["members"]:
                if not isinstance(actor, dict) or not isinstance(actor.get("joined_at"), str):
                    raise ValueError("member metadata")
                validate_request({
                    "op": "join", "channel": channel,
                    "actor": {key: value for key, value in actor.items() if key != "joined_at"},
                })
                names.append(actor["codename"])
            if len(names) != len(set(names)):
                raise ValueError("duplicate members")
            if controls:
                if (type(result["more"]) is not bool
                        or len(names) > (limit if limit is not None else 100)
                        or names != sorted(names)
                        or after is not None and any(name <= after for name in names)):
                    raise ValueError("member page")
                if result["more"]:
                    if not names or result["next_after"] != names[-1]:
                        raise ValueError("member continuation")
                elif result["next_after"] is not None:
                    raise ValueError("terminal member continuation")
        except (ChatError, ValueError):
            raise ClientError("invalid_response", "Invalid recorded membership metadata.") from None
        return result

    def resolve(self, codename: str) -> dict:
        result = self.request("resolve", codename=codename)
        try:
            if set(result) != {"actor", "channels"} or not isinstance(result["channels"], list):
                raise ValueError("resolve fields")
            validate_request({"op": "join", "channel": "validation", "actor": result["actor"]})
            if result["actor"]["codename"] != codename or not result["channels"]:
                raise ValueError("resolve identity")
            for channel in result["channels"]:
                validate_request({"op": "members", "channel": channel})
            if len(result["channels"]) != len(set(result["channels"])):
                raise ValueError("duplicate channels")
        except (ChatError, ValueError):
            raise ClientError("invalid_response", "Invalid recorded actor metadata.") from None
        return result

    def read(self, channel: str, stream, *, limit: int = 10) -> dict:
        with reader_lock(self.directory / "reader.lock"):
            page = self.request("read", channel=channel, codename=self.actor["codename"], limit=limit)
            if (set(page) != {"channel", "codename", "cursor", "next_cursor", "messages", "more"}
                    or page["channel"] != channel or page["codename"] != self.actor["codename"]
                    or type(page["cursor"]) is not int or page["cursor"] < 0
                    or type(page["next_cursor"]) is not int or page["next_cursor"] < page["cursor"]
                    or not isinstance(page["messages"], list) or len(page["messages"]) > limit
                    or type(page["more"]) is not bool):
                raise ClientError("invalid_response", "Invalid read page; progress was not committed.")
            events = [_event(event) for event in page["messages"]]
            ids = [event["id"] for event in events]
            if (any(event["channel"] != channel for event in events)
                    or ids != sorted(set(ids)) or any(value <= page["cursor"] for value in ids)
                    or page["next_cursor"] != (ids[-1] if ids else page["cursor"])):
                raise ClientError("invalid_response", "Invalid read positions; progress was not committed.")
            emit(page, stream)
            if page["next_cursor"] != page["cursor"]:
                try:
                    committed = self.request(
                        "cursor", channel=channel, codename=self.actor["codename"],
                        expected_cursor=page["cursor"], next_cursor=page["next_cursor"],
                    )
                    if (set(committed) != {"channel", "codename", "cursor"}
                            or committed["channel"] != channel
                            or committed["codename"] != self.actor["codename"]
                            or type(committed["cursor"]) is not int
                            or committed["cursor"] != page["next_cursor"]):
                        raise ClientError("invalid_response", "Cursor response does not match.")
                except (ClientError, ChatError) as error:
                    raise ClientError(
                        "cursor_unconfirmed",
                        f"Output was flushed but progress is unconfirmed ({error.code}); the batch may replay.",
                    ) from error
            return page

    def _pending(self) -> list[tuple[Path, dict]]:
        records = []
        directory = self.directory / "pending-posts"
        if directory.is_symlink():
            raise ClientError("invalid_state", "Redirected pending state.")
        for path in directory.glob("*.json"):
            if len(records) >= MAX_PENDING or path.is_symlink():
                raise ClientError("invalid_state", "Pending state is redirected or exceeds capacity.")
            try:
                with path.open("rb") as stream:
                    request = strict_json(stream.read(MAX_BODY_BYTES + 1))
                op = validate_request(request)
            except ChatError:
                raise ClientError("invalid_state", "Malformed pending post.") from None
            if op != "post" or request["codename"] != self.actor["codename"]:
                raise ClientError("invalid_state", "Pending post identity does not match.")
            expected = hashlib.sha256(request["request_id"].encode("utf-8")).hexdigest() + ".json"
            if path.name != expected:
                raise ClientError("invalid_state", "Pending post filename does not match.")
            records.append((path, request))
        return sorted(records, key=lambda record: record[0].name)

    def status(self) -> dict:
        with reader_lock(self.directory / "reader.lock"):
            pending = self._pending()
        return {
            "scope": "local_only", "remote_verified": False, "native_claims_verified": False,
            "pending_posts": [
                {"channel": request["channel"], "request_id": request["request_id"]}
                for _, request in pending
            ],
        }

    def thread(self, channel: str, event_id: int, **controls) -> dict:
        return self._view("thread", channel, event_id=event_id, **controls)

    def inbox(self, channel: str, **controls) -> dict:
        return self._view("inbox", channel, **controls)

    def _view(self, op: str, channel: str, **controls) -> dict:
        from .collaboration import normalize, validate_view

        result = self.request(op, channel=channel, codename=self.actor["codename"], **controls)
        try:
            fields = {
                "schema", "version", "view", "channel", "codename", "epoch", "head",
                "entries", "recent", "count", "more", "next",
            } | ({"thread_root"} if op == "thread" else set())
            if (set(result) != fields or result["schema"] != "agent-chat.collaboration-view"
                    or type(result["version"]) is not int or result["version"] != 1
                    or result["view"] != op or result["channel"] != channel
                    or result["codename"] != self.actor["codename"]
                    or not isinstance(result["epoch"], str)
                    or not re.fullmatch(r"[a-f0-9]{32}", result["epoch"])
                    or type(result["head"]) is not int or not 0 <= result["head"] <= 2**63 - 1
                    or type(result["count"]) is not int or result["count"] < 0
                    or type(result["more"]) is not bool
                    or controls.get("epoch", result["epoch"]) != result["epoch"]
                    or controls.get("head", result["head"]) != result["head"]
                    or len(json_bytes(result)) + 1 > controls.get("max_bytes", MAX_PAGE_BYTES)):
                raise ValueError("view envelope")
            if op == "thread" and (
                type(result["thread_root"]) is not int
                or not 1 <= result["thread_root"] <= controls["event_id"] <= result["head"]
                or result["recent"] != []
            ):
                raise ValueError("thread root")
            positions = {}
            count = 0
            for name, position in (("entries", "after"), ("recent", "recent_after")):
                rows = result[name]
                if not isinstance(rows, list):
                    raise ValueError("view entries")
                previous = controls.get(position, 0)
                for item in rows:
                    if not isinstance(item, dict) or set(item) != {
                        "event", "collaboration", "thread_root", "attention",
                    }:
                        raise ValueError("view entry fields")
                    event = _event(item["event"])
                    if (event["channel"] != channel or not previous < event["id"] <= result["head"]
                            or type(item["thread_root"]) is not int
                            or not 1 <= item["thread_root"] <= event["id"]):
                        raise ValueError("view entry identity")
                    value = item["collaboration"]
                    if value is not None and normalize(value) != value:
                        raise ValueError("unnormalized collaboration")
                    if op == "thread":
                        if item["thread_root"] != result["thread_root"] or item["attention"] is not None:
                            raise ValueError("thread entry")
                    elif name == "entries":
                        if value is None or (
                            item["attention"] == "reply"
                            and (value["kind"] != "question" or self.actor["codename"] not in value["to"])
                        ) or (
                            item["attention"] == "review" and value["kind"] != "answer"
                        ) or item["attention"] not in {"reply", "review"}:
                            raise ValueError("pending attention")
                    elif item["attention"] is not None:
                        raise ValueError("ambient attention")
                    previous = event["id"]
                positions[position] = previous
                count += len(rows)
            if (count != result["count"] or count > controls.get("limit", 50)
                    or len(result["recent"]) > controls.get("recent_limit", 10)):
                raise ValueError("view count")
            if result["more"]:
                if not isinstance(result["next"], dict):
                    raise ValueError("view continuation fields")
                validate_view({
                    "op": op, **result["next"],
                    **({"event_id": controls["event_id"]} if op == "thread" else {}),
                })
                expected = {"epoch": result["epoch"], "head": result["head"], "after": positions["after"]}
                if op == "inbox":
                    position = result["next"]["after"]
                    if (not positions["after"] <= position <= result["head"]
                            or position == controls.get("after", 0)
                            and positions["recent_after"] == controls.get("recent_after", 0)
                            or not count and position == result["head"]):
                        raise ValueError("inbox scan progress")
                    expected.update(
                        after=position,
                        recent_after=positions["recent_after"], recent_limit=controls.get("recent_limit", 10),
                    )
                if (op == "thread" and not count) or result["next"] != expected:
                    raise ValueError("view continuation")
            elif result["next"] is not None:
                raise ValueError("terminal continuation")
        except (ChatError, ValueError, TypeError, KeyError):
            raise ClientError("invalid_response", "Invalid non-consuming collaboration view.") from None
        return result

    def post(self, channel: str, text: str, request_id: str, *, collaboration=UNANNOTATED) -> dict:
        from .collaboration import normalize

        request = {
            "op": "post", "channel": channel, "codename": self.actor["codename"],
            "text": text, "request_id": request_id,
        }
        if collaboration is not UNANNOTATED:
            request["collaboration"] = normalize(collaboration)
        validate_request(request)
        key = hashlib.sha256(request_id.encode("utf-8")).hexdigest()
        path = self.directory / "pending-posts" / (key + ".json")
        with reader_lock(self.directory / "reader.lock"):
            pending = dict(self._pending())
            if path in pending and pending[path] != request:
                raise ClientError("pending_conflict", "The retained request has different content.")
            retained = path in pending
            if not retained and len(pending) >= MAX_PENDING:
                raise ClientError("pending_capacity", "Reconcile retained posts before adding more.")
            _atomic_json(path, request)
            try:
                result = self.request(**request)
                fields = {"event", "duplicate"} | (
                    {"collaboration", "thread_root"} if "collaboration" in request else set()
                )
                if set(result) != fields:
                    raise ClientError("invalid_response", "Invalid post response fields.")
                event = _event(result.get("event"))
                if (type(result.get("duplicate")) is not bool or event["channel"] != channel
                        or event["author"]["codename"] != self.actor["codename"]
                        or event["request_id"] != request_id or event["text"] != text):
                    raise ClientError("invalid_response", "Post response does not match the original request.")
                if "collaboration" in request and (
                    normalize(result["collaboration"]) != request["collaboration"]
                    or result["collaboration"] != request["collaboration"]
                    or type(result["thread_root"]) is not int
                    or not 1 <= result["thread_root"] <= event["id"]
                ):
                    raise ClientError("invalid_response", "Post annotation is not confirmed.")
            except ClientError as error:
                if not retained and error.status is not None and 400 <= error.status < 500:
                    path.unlink()
                    raise
                raise ClientError(
                    "post_unconfirmed",
                    f"The original post is retained ({error.code}); no automatic replay was attempted.",
                ) from error
            except ChatError as error:
                raise ClientError(
                    "post_unconfirmed", f"The original post is retained ({error.code}).",
                ) from error
            path.unlink()
            return result
