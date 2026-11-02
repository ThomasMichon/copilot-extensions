#!/usr/bin/env python3
"""Read-only, standard-library agenda inspection. Never executes task data."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys
from typing import Any, Callable


class AgendaError(ValueError):
    """The snapshot needs explicit correction or reconciliation."""


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AgendaError(f"{label} must be an object")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise AgendaError(f"{label} must be nonempty text without NUL characters")
    return value


def _number(value: Any, label: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise AgendaError(f"{label} must be an integer >= {minimum}")
    return value


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AgendaError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _constant(value: str) -> Any:
    raise AgendaError(f"nonfinite JSON value: {value}")


def _float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise AgendaError("nonfinite JSON number")
    return result


def decode(raw: bytes, label: str = "document", *, limit: int = 131072) -> dict[str, Any]:
    """Decode one bounded strict document, including overflowing JSON floats."""
    if len(raw) > limit:
        raise AgendaError(f"{label} exceeds {limit // 1024} KiB")
    try:
        return _object(json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_pairs,
                                  parse_constant=_constant, parse_float=_float), label)
    except AgendaError:
        raise
    except (ValueError, RecursionError) as exc:
        raise AgendaError(f"cannot decode {label}: {exc}") from exc


def _read(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as stream:
            raw = stream.read(131073)
        if len(raw) > 131072:
            raise AgendaError(f"{path.name} exceeds 128 KiB")
        return decode(raw, path.name)
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise AgendaError(f"cannot read {path.name}: {exc}") from exc


def _local(home: Path, value: Any) -> Path:
    name = Path(_text(value, "agenda path"))
    if name.is_absolute():
        raise AgendaError("agenda path must be relative to state home")
    try:
        path = (home / name).resolve()
    except (OSError, ValueError, RuntimeError) as exc:
        raise AgendaError("cannot resolve agenda path") from exc
    try:
        path.relative_to(home)
    except ValueError as exc:
        raise AgendaError("agenda path escapes state home") from exc
    return path


def _board(home: Path, name: Any) -> dict[str, Any]:
    board = _read(_local(home, name))
    return validate_board(board)


def validate_board(board: dict[str, Any]) -> dict[str, Any]:
    if type(board.get("version")) is not int or board["version"] != 1:
        raise AgendaError("unsupported agenda version")
    if "revision" in board:
        tag = board["revision"]
        if not (
            (type(tag) is int and tag >= 0) or (isinstance(tag, str) and tag.strip())
        ):
            raise AgendaError(
                "agenda revision must be nonempty text or a nonnegative integer"
            )
    source = _object(board.get("source"), "source")
    if not isinstance(source.get("kind"), str) or source["kind"] not in {
        "effort",
        "task",
        "aside",
    }:
        raise AgendaError("source kind must be effort, task or aside")
    _text(source.get("ref"), "source reference")
    nodes = _object(board.get("nodes"), "nodes")
    if (
        not nodes
        or not isinstance(board.get("entry"), str)
        or board["entry"] not in nodes
    ):
        raise AgendaError("agenda entry must name an existing node")
    for name, value in nodes.items():
        _text(name, "node id")
        node = _object(value, f"node {name}")
        _text(node.get("source_ref"), "node source reference")
        _text(node.get("action"), "node action")
        _number(node.get("max_visits"), "max_visits", 1)
        exits = node.get("exit")
        if not isinstance(exits, list) or not exits:
            raise AgendaError("node exit must be a nonempty list")
        for criterion in exits:
            _text(criterion, "exit criterion")
        outcomes = _object(node.get("on"), "node outcomes")
        if not outcomes and node.get("terminal") is not True:
            raise AgendaError("a node without outcomes must be terminal")
        for outcome, target in outcomes.items():
            _text(outcome, "outcome")
            if not isinstance(target, str) or target not in nodes:
                raise AgendaError("outcome destination does not exist")
    return board


def _time(value: Any, label: str) -> datetime:
    text = _text(value, label)
    try:
        result = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AgendaError(f"{label} must be a timezone-aware ISO timestamp") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise AgendaError(f"{label} must include a timezone")
    return result


@dataclass(frozen=True)
class Chain:
    root: str
    stack: list[str]
    next_action: str | None
    exit_criteria: list[str]
    declared_outcomes: dict[str, str]
    terminal: bool


def _chain(
    home: Path,
    frames: list[Any],
    max_depth: int,
    board_for: Callable[[Any], dict[str, Any]],
    notes: list[str],
) -> Chain:
    if len(frames) > max_depth:
        raise AgendaError("work stack exceeds its depth budget; replan without discarding it")
    stack: list[str] = []
    root, action = "No active root", None
    criteria: list[str] = []
    outcomes: dict[str, str] = {}
    previous: dict[str, Any] | None = None
    terminal = False
    for index, value in enumerate(frames):
        frame = _object(value, "frame")
        board = board_for(frame.get("agenda"))
        nodes = board["nodes"]
        node_id = frame.get("node")
        if not isinstance(node_id, str) or node_id not in nodes:
            raise AgendaError("cursor node does not exist; reconcile the definition")
        if "revision" in board and board["revision"] != frame.get("agenda_revision"):
            raise AgendaError("agenda revision changed; reconcile the saved frame")
        if "revision" not in board:
            notes.append(f"{frame['agenda']}: definition revision is manually verified.")
        visits = _object(frame.get("visits"), "visits")
        for visited, count in visits.items():
            if visited not in nodes or _number(count, "visit count") > nodes[visited]["max_visits"]:
                raise AgendaError("node visit budget exceeded or unknown visited node")
        if visits.get(node_id, 0) < 1:
            raise AgendaError("active node has no recorded visit")
        if previous is not None:
            target = _object(frame.get("return_to"), "child return point")
            parent = board_for(previous.get("agenda"))
            if (
                _local(home, target.get("agenda")) != _local(home, previous.get("agenda"))
                or not isinstance(target.get("node"), str)
                or target["node"] not in parent["nodes"]
            ):
                raise AgendaError("child return point does not name its parent")
            _text(target.get("next_action"), "parent return action")
        elif frame.get("return_to") is not None:
            raise AgendaError("root frame cannot have a parent return point")
        source = board["source"]
        label = f"{source['kind']}:{source['ref']} @ {node_id}"
        if index == 0:
            root = label
        stack.append(label)
        node = nodes[node_id]
        action = _text(frame.get("next_action"), "frame next action")
        criteria, outcomes = node["exit"], node["on"]
        terminal = node.get("terminal") is True
        previous = frame
    return Chain(root, stack, action, criteria, outcomes, terminal)


@dataclass(frozen=True)
class DeferredBranch:
    id: str
    root: str
    stack: list[str]
    resume_action: str
    decision_ref: str
    decision_packet_ref: str
    reason: str
    attempted_at: str
    revisit_at: str
    recheck_action: str
    saved_revision: int
    max_depth: int
    max_detours_without_progress: int
    detours_without_progress: int
    due: bool
    trigger: str | None


def _deferred(
    home: Path,
    values: Any,
    revision: int,
    max_branches: Any,
    board_for: Callable[[Any], dict[str, Any]],
    notes: list[str],
    now: datetime,
) -> list[DeferredBranch]:
    if not isinstance(values, list):
        raise AgendaError("deferred must be a list")
    if len(values) > _number(max_branches, "max_deferred", 1):
        raise AgendaError("deferred branch budget exceeded; replan without discarding work")
    result: list[DeferredBranch] = []
    seen: set[str] = set()
    for value in values:
        item = _object(value, "deferred branch")
        identity = _text(item.get("id"), "deferred branch id")
        if identity in seen:
            raise AgendaError("duplicate deferred branch id")
        seen.add(identity)
        saved = _number(item.get("saved_revision"), "saved cursor revision")
        if saved >= revision:
            raise AgendaError("saved cursor revision must precede the current revision")
        frames = item.get("frames")
        if not isinstance(frames, list) or not frames:
            raise AgendaError("deferred branch must preserve a nonempty frame chain")
        depth = _number(item.get("max_depth"), "saved max_depth", 1)
        budget = _number(item.get("max_detours_without_progress"), "saved detour budget", 1)
        detours = _number(item.get("detours_without_progress"), "saved detour count")
        if detours > budget:
            raise AgendaError("saved detour budget exceeded; replan without discarding work")
        chain = _chain(home, frames, depth, board_for, notes)
        attempted = _time(item.get("attempted_at"), "attempted_at")
        checked = _time(item.get("last_checked_at", item["attempted_at"]), "last_checked_at")
        revisit = _time(item.get("revisit_at"), "revisit_at")
        if checked < attempted or revisit <= checked:
            raise AgendaError("revisit must follow the last check, not precede the attempt")
        response = item.get("response_ref")
        processed = item.get("checked_response_ref")
        if response is not None:
            _text(response, "response reference")
        if processed is not None:
            _text(processed, "checked response reference")
        trigger = "response" if response is not None and response != processed else (
            "time" if now >= revisit else None
        )
        result.append(DeferredBranch(
            identity, chain.root, chain.stack,
            _text(chain.next_action, "deferred resume action"),
            _text(item.get("decision_ref"), "decision reference"),
            _text(item.get("decision_packet_ref"), "decision packet reference"),
            _text(item.get("reason"), "deferred reason"),
            item["attempted_at"], item["revisit_at"],
            _text(item.get("recheck_action"), "deferred recheck action"),
            saved, depth, budget, detours, trigger is not None, trigger,
        ))
    return result


@dataclass(frozen=True)
class Snapshot:
    worktree: str
    revision: int
    status: str
    root: str
    stack: list[str]
    next_action: str
    exit_criteria: list[str]
    declared_outcomes: dict[str, str]
    queued: list[str]
    parked: int
    notes: list[str]
    cursor_version: int = 1
    deferred: list[DeferredBranch] = field(default_factory=list)
    due_rechecks: list[str] = field(default_factory=list)
    next_revisit_at: str | None = None


def inspect(
    home: Path, expected_revision: int | None = None, worktree: str | None = None,
    *, now: datetime | None = None,
) -> Snapshot:
    """Validate a local snapshot, not its source's real-world completion."""
    try:
        home = home.resolve()
    except (OSError, ValueError, RuntimeError) as exc:
        raise AgendaError("cannot resolve state home") from exc
    if (home / "agenda.sqlite3").exists() or (home / "agenda.sqlite3").is_symlink():
        raise AgendaError("managed state exists; use agenda_cli.py read/check, not legacy JSON")
    cursor = _read(home / "cursor.json")
    return inspect_documents(home, cursor, lambda name: _board(home, name),
                             expected_revision, worktree, now=now)


def inspect_documents(
    home: Path, cursor: dict[str, Any],
    load_board: Callable[[Any], dict[str, Any]],
    expected_revision: int | None = None, worktree: str | None = None,
    *, now: datetime | None = None,
) -> Snapshot:
    """Inspect in-memory documents from one transaction without cached files."""
    version = cursor.get("version", 1)
    if type(version) is not int or version not in (1, 2):
        raise AgendaError("unsupported cursor version")
    revision = _number(cursor.get("revision"), "cursor revision")
    if expected_revision is not None and revision != expected_revision:
        raise AgendaError("cursor revision changed; reread before continuing")
    identity = _text(cursor.get("worktree"), "worktree identity")
    if worktree is not None and identity != worktree:
        raise AgendaError("cursor belongs to a different worktree")
    status = cursor.get("status")
    if not isinstance(status, str) or status not in {
        "working",
        "waiting",
        "steering",
        "complete",
    }:
        raise AgendaError("unsupported cursor status")
    frames, queue, parked = (cursor.get(key) for key in ("frames", "queue", "parked"))
    if not all(isinstance(value, list) for value in (frames, queue, parked)):
        raise AgendaError("frames, queue and parked must be lists")
    depth = _number(cursor.get("max_depth"), "max_depth", 1)
    detours = _number(
        cursor.get("detours_without_progress"), "detours_without_progress"
    )
    if detours > _number(
        cursor.get("max_detours_without_progress"), "detour budget", 1
    ):
        raise AgendaError("detour budget exceeded; replan without discarding the stack")
    notes = [
        "Canonical source acceptance must be verified separately; no state was changed."
    ]
    if "version" not in cursor:
        notes.append("Legacy manual cursor without version is read as version 1.")
    boards: dict[Path, dict[str, Any]] = {}

    def board_for(name: Any) -> dict[str, Any]:
        path = _local(home, name)
        if path not in boards:
            boards[path] = validate_board(load_board(name))
        return boards[path]

    for name in queue:
        _text(name, "queued agenda")
    if queue:
        board_for(queue[0])
    chain = _chain(home, frames, depth, board_for, notes)
    root, stack = chain.root, chain.stack
    action = _text(
        cursor.get("next_action", "Reconcile the recorded source gates."), "next action"
    )
    if chain.next_action is not None:
        action = chain.next_action
    at = now if now is not None else datetime.now(timezone.utc)
    if not isinstance(at, datetime) or at.tzinfo is None or at.utcoffset() is None:
        raise AgendaError("inspection time must be a timezone-aware datetime")
    values = cursor.get("deferred", [])
    if version == 1 and values:
        raise AgendaError("deferred branches require cursor version 2")
    branches = _deferred(home, values, revision, cursor.get("max_deferred", 16),
                         board_for, notes, at) if version == 2 else []
    due = sorted((branch for branch in branches if branch.due),
                 key=lambda branch: (branch.trigger != "response",
                                     _time(branch.revisit_at, "revisit_at"), branch.id))
    next_revisit = min((branch.revisit_at for branch in branches if not branch.due),
                      key=lambda value: _time(value, "revisit_at"), default=None)
    if status == "complete" and (branches or queue or len(frames) > 1 or (frames and not chain.terminal)):
        raise AgendaError("complete cursor still has queued or nonterminal work")
    if not frames and due and status == "working":
        root = due[0].root
        action = f"Recheck deferred branch {due[0].id}: {due[0].recheck_action}"
    elif not frames and queue and status == "working":
        action = f"Select the next already-assigned root: {queue[0]}"
    elif not frames and status == "working":
        raise AgendaError("working cursor has no active or queued assignment")
    if branches:
        notes.append("Due/response references permit reinspection only, not approval, effects or automatic stack restoration.")
    return Snapshot(
        identity,
        revision,
        status,
        root,
        stack,
        action,
        chain.exit_criteria,
        chain.declared_outcomes,
        queue,
        len(parked),
        notes,
        version,
        branches,
        [branch.id for branch in due],
        next_revisit,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("next", "check"))
    parser.add_argument(
        "--state-home",
        type=Path,
        required=True,
        help="explicit worktree journal/agenda directory",
    )
    parser.add_argument("--expected-revision", type=int)
    parser.add_argument("--worktree", help="optional expected worktree identity")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--now", help="optional timezone-aware inspection time; not a scheduling request")
    args = parser.parse_args(argv)
    try:
        at = _time(args.now, "inspection time") if args.now is not None else None
        snapshot = inspect(args.state_home, args.expected_revision, args.worktree, now=at)
    except AgendaError as exc:
        print(f"agenda: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(asdict(snapshot), indent=2))
    else:
        print(
            f"Worktree: {snapshot.worktree}; revision: {snapshot.revision}; status: {snapshot.status}"
        )
        print(f"Root goal: {snapshot.root}")
        print("Work stack: " + (" -> ".join(snapshot.stack) or "(none active)"))
        print(f"Now: {snapshot.next_action}")
        print(
            "Exit: "
            + ("; ".join(snapshot.exit_criteria) or "verify the canonical source gate")
        )
        print("Then (declared, not yet proven): " + str(snapshot.declared_outcomes))
        print(f"Queued: {len(snapshot.queued)}; parked: {snapshot.parked}")
        if snapshot.cursor_version == 2:
            print(f"Deferred: {len(snapshot.deferred)}; due rechecks: {snapshot.due_rechecks}")
            print(f"Next revisit: {snapshot.next_revisit_at or '(none scheduled)'}")
        for note in snapshot.notes:
            print(f"Note: {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
