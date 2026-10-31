"""Bounded authenticated feed transport and complete-transaction CLI relay."""

from __future__ import annotations

import json
import socket
import sys
import time

from . import worktrees_observation_contract as observations
from .worktrees_client import READ_TIMEOUT, validate_result
from .worktrees_feed import FEED_ACTION, FEED_CAPABILITY
from .worktrees_projection import MAX_COLLECTION_ITEMS, MAX_ROW_BYTES, _identity
from .worktrees_projection_index import ProjectionUnavailable

MAX_FRAME_BYTES = MAX_ROW_BYTES + 16384
IDLE_TIMEOUT = 5.0


class FeedFailure(ProjectionUnavailable):
    """Carry reconnect policy across the CLI boundary without message matching."""

    def __init__(self, message: str, *, code: str, retry: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retry = retry


def transport(client, payload: dict):
    """The initial/each update barrier has a total deadline, not a trickle lease."""
    deadline = time.monotonic() + client.timeout
    body = json.dumps({
        "version": 1, "token": client.token, "action": FEED_ACTION, "payload": payload,
    }, separators=(",", ":")).encode("utf-8") + b"\n"
    with socket.create_connection((client.host, client.port), timeout=client.timeout) as sock:
        sock.sendall(body)
        pending = bytearray()
        in_transaction = True
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("resident current-roster feed deadline exceeded")
            sock.settimeout(remaining)
            chunk = sock.recv(65536)
            if not chunk:
                if pending:
                    raise FeedFailure(
                        "resident feed disconnected inside a frame", code="disconnected", retry=True,
                    )
                raise FeedFailure(
                    "resident feed disconnected without a going-away notice",
                    code="disconnected", retry=True,
                )
            pending.extend(chunk)
            while b"\n" in pending:
                line, _, rest = pending.partition(b"\n")
                pending = bytearray(rest)
                if len(line) > MAX_FRAME_BYTES:
                    raise ProjectionUnavailable("resident feed frame exceeds its byte bound")
                frame = json.loads(line)
                if not isinstance(frame, dict):
                    raise ProjectionUnavailable("resident feed emitted a non-object frame")
                kind = frame.get("type")
                if kind in {"begin", "update"} and not in_transaction:
                    in_transaction = True
                    deadline = time.monotonic() + client.timeout
                yield frame
                if kind in {"going-away", "error"}:
                    return
                if kind in {"done", "commit"}:
                    in_transaction = False
                    deadline = time.monotonic() + IDLE_TIMEOUT
                elif not in_transaction and kind in {"heartbeat", "availability"}:
                    deadline = time.monotonic() + IDLE_TIMEOUT
            if len(pending) > MAX_FRAME_BYTES:
                raise ProjectionUnavailable("resident feed frame exceeds its byte bound")


class FeedReceiver:
    """Validate the relay before stdout exposes a commit to downstream consumers."""

    def __init__(self, request: dict) -> None:
        self.request = request
        self.current: dict | None = None
        self._begin: dict | None = None
        self._rows: dict = {}
        self._touched: set = set()
        self._frames: list = []
        self._order: list[str] = []
        self._ordered: set[str] = set()
        self._observations: dict = {}
        self._observed: set = set()

    def receive(self, frame: dict) -> list[dict] | None:
        if not isinstance(frame, dict):
            raise ProjectionUnavailable("resident feed emitted a non-object frame")
        kind = frame.get("type")
        if kind == "error":
            error = frame.get("error")
            if (
                not isinstance(error, dict)
                or not isinstance(error.get("message"), str) or not error["message"]
                or not isinstance(error.get("code"), str) or not error["code"]
                or ("retry" in error and type(error["retry"]) is not bool)
            ):
                raise ProjectionUnavailable("resident feed emitted an invalid error")
            retry = error.get("retry", error["code"] in {
                "unready", "unavailable", "draining", "superseded", "subscriber-limit",
            })
            raise FeedFailure(
                error["message"], code=error["code"], retry=retry,
            )
        if kind in {"begin", "update"}:
            if self._begin is not None:
                raise ProjectionUnavailable("resident feed restarted before its commit")
            head = frame.get("projection")
            if (
                not isinstance(head, dict)
                or type(frame.get("cursor")) is not int
                or frame["cursor"] != head.get("revision")
                or type(frame.get("selected_count")) is not int
                or frame["selected_count"] != head.get("selected_count")
                or type(head.get("roster_count")) is not int
                or not 0 <= frame["selected_count"] <= head["roster_count"]
            ):
                raise ProjectionUnavailable("resident feed has an invalid cursor")
            if kind == "begin":
                if self.current is not None or frame.get("base_cursor") is not None:
                    raise ProjectionUnavailable("resident feed sent an unexpected reset")
                self._rows = {}
            else:
                if self.current is None:
                    raise ProjectionUnavailable("resident feed update has no initial snapshot")
                old = self.current["projection"]
                if (
                    head.get("generation") != old["generation"]
                    or type(frame.get("cursor")) is not int
                    or frame["cursor"] <= old["revision"]
                    or frame.get("base_cursor") != old["revision"]
                ):
                    raise ProjectionUnavailable("resident feed update is stale or out of order")
                self._rows = {row["id"]: row for row in self.current["worktrees"]}
            self._begin = frame
            self._observations = {} if self.current is None else {
                item["id"]: item for item in self.current.get("observations", [])
            }
            self._observed = set()
            self._frames = [frame]
            self._touched = set()
            self._order = []
            self._ordered = set()
            return None
        if kind in {"heartbeat", "availability", "going-away"}:
            if kind == "going-away" and self._begin is None and self.current is None:
                generation = frame.get("generation")
                if (
                    not isinstance(generation, str) or len(generation) != 32
                    or any(c not in "0123456789abcdef" for c in generation)
                    or frame.get("cursor") is not None
                    or not isinstance(frame.get("reason"), str) or not frame["reason"]
                ):
                    raise ProjectionUnavailable(
                        "resident feed sent an invalid early cutover notice"
                    )
                return [frame]
            if self._begin is not None or self.current is None:
                raise ProjectionUnavailable("resident feed interrupted its publication")
            head = self.current["projection"]
            if kind == "availability":
                if frame.get("projection") != head:
                    raise ProjectionUnavailable("resident feed availability changed publication")
                candidate = {**self.current, "availability": frame.get("availability")}
                self.current = validate_result(candidate, self.request)
            elif (
                frame.get("generation") != head["generation"]
                or frame.get("cursor") != head["revision"]
            ):
                raise ProjectionUnavailable("resident feed notice changed generation or cursor")
            if kind == "going-away" and (
                not isinstance(frame.get("reason"), str) or not frame["reason"]
            ):
                raise ProjectionUnavailable("resident feed going-away notice omitted its reason")
            return [frame]
        if self._begin is None or frame.get("projection") != self._begin["projection"]:
            raise ProjectionUnavailable("resident feed mixed publication barriers")
        if kind == "observation":
            if self.request["contract_version"] != observations.VERSION:
                raise ProjectionUnavailable("unnegotiated observation frame")
            item = frame.get("observation")
            observations.validate_observation(item, self._begin["cursor"])
            wid = item["id"]
            if wid in self._observed or wid not in self._rows:
                raise ProjectionUnavailable("duplicate or unknown observation identity")
            observations.check_observation_advance(self._observations.get(wid), item)
            self._observed.add(wid)
            self._observations[wid] = item
            self._frames.append(frame)
            return None
        if kind == "order":
            identities = frame.get("ids")
            if (
                self._begin["type"] != "update"
                or not isinstance(identities, list)
                or not 0 < len(identities) <= MAX_COLLECTION_ITEMS
                or len(self._order) + len(identities) > self._begin["selected_count"]
            ):
                raise ProjectionUnavailable("resident feed has an invalid identity order")
            for wid in identities:
                _identity(wid, name="ordered worktree id")
                if wid in self._ordered:
                    raise ProjectionUnavailable("resident feed duplicated an ordered identity")
                self._ordered.add(wid)
                self._order.append(wid)
            self._frames.append(frame)
            return None
        if kind in {"worktree", "delete"}:
            row = frame.get("wt")
            wid = row.get("id") if isinstance(row, dict) else frame.get("id")
            if not isinstance(wid, str) or not wid or wid in self._touched:
                raise ProjectionUnavailable("resident feed duplicated or omitted a row identity")
            self._touched.add(wid)
            if kind == "delete":
                if self._begin["type"] != "update" or wid not in self._rows:
                    raise ProjectionUnavailable("resident feed deleted an unknown row")
                del self._rows[wid]
                self._observations.pop(wid, None)
            else:
                from .worktrees_projection import _encode_row

                _encode_row({k: v for k, v in row.items() if k != "semantic_revision"} if (
                    self.request["contract_version"] == observations.VERSION
                ) else row)
                if self.request["contract_version"] == observations.VERSION:
                    observations.check_advance(
                        self._rows.get(wid), row, field="semantic_revision",
                    )
                    observations.validate_wire_row(row)
                self._rows[wid] = row
                if len(self._rows) > self._begin["selected_count"]:
                    # Updates may insert before deleting, so bound against both rosters.
                    old_count = 0 if self.current is None else len(self.current["worktrees"])
                    if len(self._rows) > old_count + self._begin["selected_count"]:
                        raise ProjectionUnavailable(
                            "resident feed exceeded its declared roster bound"
                        )
            self._frames.append(frame)
            return None
        expected = "done" if self._begin["type"] == "begin" else "commit"
        if kind != expected or frame.get("cursor") != self._begin["cursor"]:
            raise ProjectionUnavailable("resident feed omitted its commit barrier")
        if self._begin["type"] == "update":
            if self._ordered != self._rows.keys():
                raise ProjectionUnavailable("resident feed omitted its committed identity order")
            rows = [self._rows[wid] for wid in self._order]
        else:
            rows = list(self._rows.values())
        result = {
            **self._begin, "ok": True, "worktrees": rows,
        }
        if self.request["contract_version"] == observations.VERSION:
            result["observations"] = list(self._observations.values())
        self.current = validate_result(result, self.request)
        if self._begin.get("selected_count") != len(self._rows):
            raise ProjectionUnavailable("resident feed did not complete its declared roster")
        frames = [*self._frames, frame]
        self._begin = None
        self._frames = []
        self._rows = {}
        self._touched = set()
        self._order = []
        self._ordered = set()
        return frames


def _client(request: dict):
    from . import status_monitor_cutover as control
    from . import config as cfg
    from .worktrees_client import read_current, resident_endpoint

    runtime_home = cfg.install_dir()
    try:
        endpoint = resident_endpoint(runtime_home)
    except ConnectionError:
        restored = read_current(request)
        if restored["projection"]["quality"] != "current":
            raise FeedFailure(
                restored["availability"]["reason"], code="unready", retry=True,
            )
        try:
            endpoint = resident_endpoint(runtime_home)
        except ConnectionError as exc:
            raise FeedFailure(
                "resident feed endpoint disappeared after recovery", code="disconnected", retry=True,
            ) from exc
    with control.control_token_path(runtime_home).open(encoding="utf-8") as token_file:
        token = token_file.read(4097).strip()
    if not token or len(token) > 4096:
        raise FeedFailure("resident feed authentication is unavailable", code="authentication")
    client = control.ControlClient(endpoint.base_url, token=token, timeout=READ_TIMEOUT)
    try:
        health = client._request("health", max_response_bytes=16384, total_timeout=client.timeout)
    except ConnectionError as exc:
        # A retained route whose listener actually refused/reset the connection
        # is a crash/recovery case. A slow, malformed or old live peer is not.
        restored = read_current(request)
        if restored["projection"]["quality"] != "current":
            raise FeedFailure(
                restored["availability"]["reason"], code="unready", retry=True,
            ) from exc
        try:
            endpoint = resident_endpoint(runtime_home)
        except ConnectionError:
            raise FeedFailure(
                "resident feed endpoint disappeared after recovery", code="disconnected", retry=True,
            ) from exc
        client = control.ControlClient(endpoint.base_url, token=token, timeout=READ_TIMEOUT)
        health = client._request("health", max_response_bytes=16384, total_timeout=client.timeout)
    if FEED_CAPABILITY not in health.get("capabilities", []):
        raise FeedFailure(
            "resident lacks worktrees-feed-v1; update the engine", code="incompatible",
        )
    if request["contract_version"] == observations.VERSION and observations.CAPABILITY not in (
        health.get("capabilities", [])
    ):
        raise FeedFailure("resident lacks worktrees-observations-v2", code="incompatible")
    return client


def relay(request: dict) -> int:
    client = _client(request)
    receiver = FeedReceiver(request)
    frames = transport(client, request)
    try:
        for frame in frames:
            admitted = receiver.receive(frame)
            for output in admitted or ():
                print(json.dumps(output, separators=(",", ":"), ensure_ascii=False))
            if admitted:
                sys.stdout.flush()
    finally:
        frames.close()
    return 0
