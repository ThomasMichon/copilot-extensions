#!/usr/bin/env python3
"""Read-only, standard-library agenda inspection. Never executes task data."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import sys
from typing import Any


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


def _read(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as stream:
            raw = stream.read(131073)
        if len(raw) > 131072:
            raise AgendaError(f"{path.name} exceeds 128 KiB")
        value = json.loads(
            raw.decode("utf-8-sig"), object_pairs_hook=_pairs, parse_constant=_constant
        )
        return _object(value, path.name)
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


def inspect(
    home: Path, expected_revision: int | None = None, worktree: str | None = None
) -> Snapshot:
    """Validate a local snapshot, not its source's real-world completion."""
    try:
        home = home.resolve()
    except (OSError, ValueError, RuntimeError) as exc:
        raise AgendaError("cannot resolve state home") from exc
    cursor = _read(home / "cursor.json")
    version = cursor.get("version", 1)
    if type(version) is not int or version != 1:
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
    if len(frames) > _number(cursor.get("max_depth"), "max_depth", 1):
        raise AgendaError(
            "work stack exceeds its depth budget; replan without discarding it"
        )
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
    for name in queue:
        _text(name, "queued agenda")
    if queue:
        _board(home, queue[0])
    stack: list[str] = []
    root = "No active root"
    action = _text(
        cursor.get("next_action", "Reconcile the recorded source gates."), "next action"
    )
    criteria: list[str] = []
    outcomes: dict[str, str] = {}
    previous: dict[str, Any] | None = None
    terminal = False
    for index, value in enumerate(frames):
        frame = _object(value, "frame")
        board = _board(home, frame.get("agenda"))
        nodes = board["nodes"]
        node_id = frame.get("node")
        if not isinstance(node_id, str) or node_id not in nodes:
            raise AgendaError("cursor node does not exist; reconcile the definition")
        if "revision" in board and board["revision"] != frame.get("agenda_revision"):
            raise AgendaError("agenda revision changed; reconcile the saved frame")
        if "revision" not in board:
            notes.append(
                f"{frame['agenda']}: definition revision is manually verified."
            )
        visits = _object(frame.get("visits"), "visits")
        for visited, count in visits.items():
            if (
                visited not in nodes
                or _number(count, "visit count") > nodes[visited]["max_visits"]
            ):
                raise AgendaError("node visit budget exceeded or unknown visited node")
        if visits.get(node_id, 0) < 1:
            raise AgendaError("active node has no recorded visit")
        if previous is not None:
            target = _object(frame.get("return_to"), "child return point")
            parent = _board(home, previous.get("agenda"))
            if (
                _local(home, target.get("agenda"))
                != _local(home, previous.get("agenda"))
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
    if status == "complete" and (queue or len(frames) > 1 or (frames and not terminal)):
        raise AgendaError("complete cursor still has queued or nonterminal work")
    if not frames and queue and status == "working":
        action = f"Select the next already-assigned root: {queue[0]}"
    elif not frames and status == "working":
        raise AgendaError("working cursor has no active or queued assignment")
    return Snapshot(
        identity,
        revision,
        status,
        root,
        stack,
        action,
        criteria,
        outcomes,
        queue,
        len(parked),
        notes,
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
    args = parser.parse_args(argv)
    try:
        snapshot = inspect(args.state_home, args.expected_revision, args.worktree)
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
        for note in snapshot.notes:
            print(f"Note: {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
