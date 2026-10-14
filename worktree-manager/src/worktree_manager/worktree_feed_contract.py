"""Atomic held-feed admission; no transport or engine/domain state reductions."""

from __future__ import annotations

from .worktree_rows_contract import CurrentSnapshot, parse_current_rows


class FeedUnavailable(ValueError):
    def __init__(self, message: str, *, retry: bool = True) -> None:
        super().__init__(message)
        self.retry = retry


class CurrentFeed:
    """Keep committed rows separate from pending snapshot/update transactions."""

    def __init__(self) -> None:
        self.rows: list[dict] | None = None
        self.head: dict | None = None
        self.notice: str | None = None
        self._snapshot = CurrentSnapshot()
        self._begin: dict | None = None
        self._pending: dict | None = None
        self._touched: set = set()
        self._order: list[str] = []
        self._ordered: set[str] = set()

    def _metadata(self, rows, frame) -> list[dict]:
        return [
            {**row, "_projection": dict(frame["projection"]),
             "_availability": frame.get("availability")}
            for row in rows
        ]

    def receive(self, frame: dict) -> list[dict] | None:
        if not isinstance(frame, dict):
            raise TypeError("current-roster feed emitted a non-object frame")
        kind = frame.get("type")
        if kind == "error":
            error = frame.get("error")
            message = error.get("message") if isinstance(error, dict) else None
            if not isinstance(message, str) or not message:
                raise ValueError("current-roster feed omitted its error")
            if "retry" in error and type(error["retry"]) is not bool:
                raise ValueError("current-roster feed returned an invalid retry policy")
            fatal = (
                error.get("code") in {
                    "invalid-request", "timeout", "malformed-peer", "incompatible", "authentication",
                }
                or "update the engine" in message
                or "unrecognized arguments" in message
            )
            raise FeedUnavailable(message, retry=not fatal and error.get("retry", True))
        if kind == "going-away":
            generation, cursor = frame.get("generation"), frame.get("cursor")
            if (
                self._begin is not None
                or not isinstance(generation, str) or len(generation) != 32
                or any(c not in "0123456789abcdef" for c in generation)
                or not isinstance(frame.get("reason"), str) or not frame["reason"]
                or (self.head is None and cursor is not None)
                or (self.head is not None and (
                    generation != self.head["generation"] or cursor != self.head["revision"]
                ))
            ):
                raise ValueError("current-roster feed sent an invalid cutover notice")
            self.notice = frame["reason"]
            return None
        if self.notice is not None:
            raise ValueError("current-roster feed continued after its cutover notice")
        if kind in {"heartbeat", "availability"}:
            if self.head is None or self._begin is not None:
                raise ValueError("current-roster feed interrupted its publication barrier")
            if kind == "heartbeat":
                if (
                    frame.get("generation") != self.head["generation"]
                    or frame.get("cursor") != self.head["revision"]
                ):
                    raise ValueError("current-roster feed heartbeat changed generation or cursor")
                return None
            if frame.get("projection") != self.head:
                raise ValueError("current-roster feed availability changed publication")
            parse_current_rows(
                {**frame, "ok": True, "contract_version": 1, "worktrees": []}, complete=False
            )
            self.rows = self._metadata(self.rows, frame)
            return self.rows
        if kind in {"begin", "update"}:
            if self._begin is not None:
                raise ValueError("current-roster feed opened a second publication barrier")
            head = frame.get("projection")
            parse_current_rows({**frame, "ok": True, "worktrees": []}, complete=False)
            if (
                head["quality"] != "current"
                or type(frame.get("cursor")) is not int
                or frame["cursor"] != head["revision"]
                or frame.get("selected_count") != head["selected_count"]
            ):
                raise ValueError("current-roster feed omitted its cursor or selected count")
            if kind == "begin":
                if self.head is not None or frame.get("base_cursor") is not None:
                    raise ValueError("current-roster feed sent an unexpected reset")
                self._snapshot.receive(frame)
            else:
                if (
                    self.head is None or head["generation"] != self.head["generation"]
                    or head["revision"] <= self.head["revision"]
                    or frame.get("base_cursor") != self.head["revision"]
                ):
                    raise ValueError("current-roster feed update is stale or out of order")
                self._pending = {row["id"]: row for row in self.rows}
            self._begin = frame
            self._touched = set()
            self._order = []
            self._ordered = set()
            return None
        if self._begin is None or frame.get("projection") != self._begin["projection"]:
            raise ValueError("current-roster feed mixed publication barriers")
        if self._begin["type"] == "begin":
            rows = self._snapshot.receive(frame)
            if rows is None:
                return None
        elif kind == "order":
            identities = frame.get("ids")
            if (
                not isinstance(identities, list) or not 0 < len(identities) <= 128
                or len(self._order) + len(identities) > self._begin["selected_count"]
            ):
                raise ValueError("current-roster feed has an invalid identity order")
            for wid in identities:
                if (
                    not isinstance(wid, str) or not wid or len(wid.encode("utf-8")) > 256
                    or wid in self._ordered
                ):
                    raise ValueError("current-roster feed duplicated or invalidated an ordered identity")
                self._ordered.add(wid)
                self._order.append(wid)
            return None
        elif kind in {"worktree", "delete"}:
            row = frame.get("wt")
            wid = row.get("id") if isinstance(row, dict) else frame.get("id")
            if not isinstance(wid, str) or not wid or wid in self._touched:
                raise ValueError("current-roster feed duplicated or omitted a row identity")
            self._touched.add(wid)
            if kind == "delete":
                if wid not in self._pending:
                    raise ValueError("current-roster feed deleted an unknown row")
                del self._pending[wid]
            else:
                admitted = parse_current_rows(
                    {**self._begin, "ok": True, "worktrees": [row]}, complete=False
                )
                self._pending[wid] = admitted[0]
                if len(self._pending) > len(self.rows) + self._begin["selected_count"]:
                    raise ValueError("current-roster feed exceeded its declared roster bound")
            return None
        else:
            if kind != "commit":
                raise ValueError("current-roster feed omitted its commit barrier")
            if self._ordered != self._pending.keys():
                raise ValueError("current-roster feed omitted its committed identity order")
            rows = self._metadata([self._pending[wid] for wid in self._order], self._begin)
        if (
            kind not in {"done", "commit"}
            or frame.get("cursor") != self._begin["cursor"]
            or len(rows) != self._begin["selected_count"]
        ):
            raise ValueError("current-roster feed did not complete its declared roster")
        self.rows = rows
        self.head = dict(self._begin["projection"])
        self._begin = None
        self._pending = None
        self._touched = set()
        self._order = []
        self._ordered = set()
        return rows
