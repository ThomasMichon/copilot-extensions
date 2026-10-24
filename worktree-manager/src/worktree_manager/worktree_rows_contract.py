"""Current-roster envelope admission, independent of daemon transport."""

from __future__ import annotations

import json
import math

from worktree_identity import parse_machine_identity

from . import worktree_observation_contract as observations
_HISTORICAL_FIELDS = frozenset(
    {
        "resources",
        "prs",
        "profile_assignments",
        "sessions",
        "handoffs",
        "journal",
        "history",
        "events",
        "ledger",
        "claims_graph",
    }
)
_RECEIVER_FIELDS = frozenset({
    "_projection", "_availability", "_observations", "_observation_derivations",
})


def publication_wire_rows(rows: list[dict]) -> list[dict]:
    """Remove local metadata only from previously admitted publication rows."""
    return [{key: value for key, value in row.items() if key not in _RECEIVER_FIELDS}
            for row in rows]


def _bounded(value, depth: int = 0) -> None:
    if depth > 6:
        raise ValueError("current-roster row exceeds its nesting bound")
    if isinstance(value, (dict, list)):
        if len(value) > 128:
            raise ValueError("current-roster row exceeds its collection bound")
        if isinstance(value, dict):
            for key, child in value.items():
                if not isinstance(key, str) or key in _HISTORICAL_FIELDS:
                    raise ValueError("current-roster row includes historical or invalid fields")
                _bounded(key, depth + 1)
                _bounded(child, depth + 1)
        else:
            for child in value:
                _bounded(child, depth + 1)
    elif isinstance(value, str):
        if len(value.encode("utf-8")) > 4096:
            raise ValueError("current-roster row exceeds its text bound")
    elif type(value) is int:
        if not -(2**63) <= value < 2**63:
            raise ValueError("current-roster row exceeds its integer bound")
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("current-roster row includes a non-finite number")
    elif value is not None and type(value) is not bool:
        raise TypeError("current-roster row includes a non-JSON value")


def parse_current_rows(obj: dict, *, complete: bool = True) -> list[dict]:
    if not isinstance(obj, dict):
        raise TypeError("engine returned a non-object current-roster envelope")
    if (
        type(obj.get("contract_version")) is not int
        or obj["contract_version"] not in (1, 2)
        or obj.get("ok") is not True
    ):
        raise ValueError("engine lacks a usable worktrees-current-v1 contract; update and retry")
    rows, head = obj.get("worktrees"), obj.get("projection")
    if obj["contract_version"] == 2 and (
        not isinstance(obj.get("project"), str) or not obj["project"]
        or len(obj["project"].encode("utf-8")) > 256
        or obj["project"] in {".", ".."}
        or any(c in obj["project"] for c in ("/", "\\", "\x00"))
    ):
        raise ValueError("engine returned invalid observation project")
    if not isinstance(rows, list) or not isinstance(head, dict):
        raise TypeError("engine omitted the current-roster publication")
    generation, revision = head.get("generation"), head.get("revision")
    observed_at, count = head.get("observed_at"), head.get("roster_count")
    quality = head.get("quality")
    if (
        type(head.get("schema_version")) is not int
        or head["schema_version"] != 1
        or quality not in {"current", "advisory"}
        or not isinstance(generation, str)
        or len(generation) != 32
        or any(c not in "0123456789abcdef" for c in generation)
        or type(revision) is not int
        or revision < 1
        or type(count) is not int
        or count < len(rows)
        or type(head.get("selected_count")) is not int
        or not len(rows) <= head["selected_count"] <= count
        or (complete and head["selected_count"] != len(rows))
        or type(observed_at) not in (int, float)
        or not math.isfinite(observed_at)
        or observed_at < 0
    ):
        raise ValueError("engine returned invalid current-roster provenance")
    availability = obj.get("availability")
    if availability is not None and (
        not isinstance(availability, dict)
        or availability.get("status") != ("advisory" if quality == "advisory" else "stale")
        or not isinstance(availability.get("reason"), str)
        or not availability["reason"]
    ):
        raise ValueError("engine returned invalid current-roster availability")
    if quality == "advisory" and (
        not isinstance(availability, dict)
        or availability.get("status") != "advisory"
        or not isinstance(availability.get("reason"), str)
        or not availability["reason"]
    ):
        raise ValueError("engine returned unlabeled advisory rows")
    result = []
    identities = set()
    facts = observations.validate_rows(
        rows, obj.get("observations"), revision, complete=complete,
    ) if obj["contract_version"] == 2 else {}
    for row in rows:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("id"), str)
            or not row["id"]
            or row["id"] in identities
        ):
            raise ValueError("engine returned invalid or duplicate current-roster rows")
        identities.add(row["id"])
        _bounded(row)
        parse_machine_identity(row)
        if len(json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > 65536:
            raise ValueError("current-roster row exceeds its byte bound")
        result.append(
            {
                **row,
                "_projection": dict(head),
                "_availability": dict(availability) if isinstance(availability, dict) else None,
                **({"_observations": facts[row["id"]]["facts"],
                    "_observation_derivations": facts[row["id"]].get("derivations", {})}
                   if row["id"] in facts else {}),
            }
        )
    return result


class CurrentSnapshot:
    """Admit one complete generation before replacing a visible roster."""

    def __init__(self) -> None:
        self._begin: dict | None = None
        self._rows: list[dict] = []
        self._identities: set[str] = set()
        self._complete = False
        self._observations: dict = {}

    def receive(self, frame: dict) -> list[dict] | None:
        if not isinstance(frame, dict):
            raise TypeError("current-roster stream emitted a non-object frame")
        kind = frame.get("type")
        if kind == "begin":
            if self._begin is not None:
                raise ValueError("current-roster stream restarted before completion")
            count = frame.get("selected_count")
            envelope = {**frame, "ok": True, "worktrees": []}
            parse_current_rows(envelope, complete=False)
            if type(count) is not int or not 0 <= count <= frame["projection"]["roster_count"]:
                raise ValueError("current-roster stream omitted the selected row count")
            if count != frame["projection"]["selected_count"]:
                raise ValueError("current-roster stream changed its selected row count")
            self._begin = frame
            return None
        if self._begin is None or self._complete:
            raise ValueError("current-roster stream has no open publication barrier")
        if frame.get("projection") != self._begin["projection"]:
            raise ValueError("current-roster stream mixed publication generations or revisions")
        if kind == "observation" and self._begin["contract_version"] == 2:
            item = frame.get("observation")
            observations.validate_observation(item, self._begin["projection"]["revision"])
            if item["id"] not in self._identities or item["id"] in self._observations:
                raise ValueError("duplicate or unknown observation identity")
            self._observations[item["id"]] = item
            return None
        if kind == "worktree":
            rows = parse_current_rows(
                {
                    **self._begin,
                    "ok": True,
                    "worktrees": [frame.get("wt")],
                },
                complete=False,
            )
            row = rows[0]
            if row["id"] in self._identities:
                raise ValueError("current-roster stream duplicated a row")
            if len(self._rows) >= self._begin["selected_count"]:
                raise ValueError("current-roster stream exceeded its selected row count")
            self._identities.add(row["id"])
            self._rows.append(row)
            return None
        if kind != "done" or len(self._rows) != self._begin["selected_count"]:
            raise ValueError("current-roster stream did not complete its publication")
        if self._begin["contract_version"] == 2:
            self._rows = parse_current_rows({
                **self._begin, "ok": True, "worktrees": publication_wire_rows(self._rows),
                "observations": list(self._observations.values()),
            })
        self._complete = True
        return self._rows
