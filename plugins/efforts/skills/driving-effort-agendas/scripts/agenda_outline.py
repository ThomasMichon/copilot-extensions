#!/usr/bin/env python3
"""Short paths into a context-qualified agenda: compact outlines, effort remainders and previews.

An outline is a small, agent-authored list of items (action, optional exit and
status, optional nested children). This module expands it into the verbose
request-v2 ``init`` the managed store requires, derives an outline from the
open remainder of an effort README, and renders an offline preview by
replaying the outline into a temporary standalone store and building the same
display graph the Companion shows. Nothing here touches a managed lane: the
agent still initializes and drives its own agenda through the normal commands.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import re
import sys
import tempfile
import unicodedata
from typing import Any

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from agenda import AgendaError  # noqa: E402

SCHEMA = "efforts.agenda-outline"
STATUSES = ("queued", "current", "done", "waiting")
PROVENANCE = ("authored", "derived", "reconstructed")
SOURCE_KINDS = ("effort", "task", "aside")
OUTCOMES = {"done": "advance", "blocked": "wait", "needs-context": "replan"}
SUBJECT = "outline"
DEFAULT_CAPS = {"max_depth": 4, "max_pending_items": 32, "max_deferred_frames": 4,
                "max_no_parent_progress": 3}
LABEL_LIMIT = 120
ITEM_FIELDS = {"id", "action", "label", "exit", "status", "children", "evidence", "source_ref"}


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AgendaError(f"outline {label} must be a nonempty string")
    return " ".join(value.split())


def clip(text: str, limit: int = LABEL_LIMIT) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40].strip("-")
    return slug or "item"


def _validate(outline: Any) -> dict[str, Any]:
    if not isinstance(outline, dict):
        raise AgendaError("outline must be a JSON object")
    unknown = set(outline) - {"schema", "version", "source", "worktree", "provenance", "items", "caps"}
    if unknown:
        raise AgendaError(f"unknown outline fields: {sorted(unknown)}")
    if outline.get("schema", SCHEMA) != SCHEMA or outline.get("version", 1) != 1:
        raise AgendaError("unsupported outline schema/version")
    source = outline.get("source")
    if not isinstance(source, dict) or source.get("kind") not in SOURCE_KINDS:
        raise AgendaError(f"outline source.kind must be one of {SOURCE_KINDS}")
    _text(source.get("ref"), "source.ref")
    if outline.get("provenance", "authored") not in PROVENANCE:
        raise AgendaError(f"outline provenance must be one of {PROVENANCE}")
    if "caps" in outline:
        if not isinstance(outline["caps"], dict) or set(outline["caps"]) - set(DEFAULT_CAPS):
            raise AgendaError("outline caps may only override the four resource caps")

    def walk(items: Any, depth: int) -> None:
        if not isinstance(items, list) or not items:
            raise AgendaError("outline items must be a nonempty list")
        for item in items:
            if not isinstance(item, dict) or set(item) - ITEM_FIELDS:
                raise AgendaError(f"outline item fields are limited to {sorted(ITEM_FIELDS)}")
            _text(item.get("action"), "item action")
            if item.get("status", "queued") not in STATUSES:
                raise AgendaError(f"outline item status must be one of {STATUSES}")
            exits = item.get("exit")
            if exits is not None and not isinstance(exits, str) and not (
                    isinstance(exits, list) and exits and all(isinstance(entry, str) for entry in exits)):
                raise AgendaError("outline item exit must be a string or nonempty string list")
            if "children" in item:
                walk(item["children"], depth + 1)

    walk(outline.get("items"), 1)
    return outline


def _flatten(items: list[dict[str, Any]], parent_done: bool = False) -> list[dict[str, Any]]:
    """Normalize nested outline items in pre-order, assigning stable unique identities."""
    flat: list[dict[str, Any]] = []

    def visit(values: list[dict[str, Any]], inherited_done: bool, depth: int) -> list[dict[str, Any]]:
        nodes = []
        for value in values:
            status = value.get("status", "done" if inherited_done else "queued")
            node = {"value": value, "status": status, "depth": depth, "children": []}
            flat.append(node)
            if value.get("children"):
                node["children"] = visit(value["children"], status == "done", depth + 1)
            nodes.append(node)
        return nodes

    roots = visit(items, parent_done, 1)
    used: set[str] = set()
    for index, node in enumerate(flat, start=1):
        value = node["value"]
        base = _slug(value.get("id") or value.get("label") or value["action"])
        identity, suffix = base, index
        while identity in used:
            identity, suffix = f"{base}-{suffix}", suffix + 1
        used.add(identity)
        node["id"] = identity
    return roots


def _definition(node: dict[str, Any], outline: dict[str, Any]) -> dict[str, Any]:
    value = node["value"]
    action = _text(value["action"], "item action")
    label = clip(value.get("label") or action)
    exits = value.get("exit") or [f"{label} is complete."]
    exits = [exits] if isinstance(exits, str) else list(exits)
    provenance = outline.get("provenance", "authored")
    definition = {
        "source_ref": node["source_ref"],
        "scope_ref": f"Outline ({provenance})",
        "action": action, "exit": [_text(entry, "exit criterion") for entry in exits],
        "on": dict(OUTCOMES), "relevant_subjects": [SUBJECT],
    }
    if label != action:
        definition["label"] = label
    return definition


def _all(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for node in nodes:
        result.append(node)
        result.extend(_all(node["children"]))
    return result


def _max_depth(nodes: list[dict[str, Any]]) -> int:
    return max((1 + _max_depth(node["children"]) for node in nodes), default=0)


def expand(outline: dict[str, Any], *, operation_id: str | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return the request-v2 init for an outline and its normalized item tree."""
    outline = _validate(deepcopy(outline))
    roots = _flatten(outline["items"])
    nodes = _all(roots)
    seen: set[str] = set()
    for node in nodes:
        # The store treats an NFC-normalized source_ref as semantic identity; keep every item distinct.
        ref = _text(node["value"].get("source_ref")
                    or f"{outline['source']['kind']}:{outline['source']['ref']}#{node['id']}", "item source_ref")
        base, suffix = ref, 2
        if unicodedata.normalize("NFC", ref) in seen:
            ref = f"{base}#{node['id']}"
        while unicodedata.normalize("NFC", ref) in seen:
            ref, suffix = f"{base}#{node['id']}-{suffix}", suffix + 1
        seen.add(unicodedata.normalize("NFC", ref))
        node["source_ref"] = ref
    caps = {**DEFAULT_CAPS,
            "max_depth": max(DEFAULT_CAPS["max_depth"], _max_depth(roots)),
            "max_pending_items": max(DEFAULT_CAPS["max_pending_items"], len(nodes)),
            "max_no_parent_progress": max(DEFAULT_CAPS["max_no_parent_progress"], len(nodes)),
            **outline.get("caps", {})}
    worktree = outline.get("worktree") or "outline-preview"
    change = {
        "operation": "init", "worktree": _text(worktree, "worktree"),
        "board": {"version": 2, "source": dict(outline["source"]), "revision": 1,
                  "items": {node["id"]: _definition(node, outline) for node in nodes},
                  "order": [node["id"] for node in roots]},
        "baseline_context": {"facts": [{
            "kind": "input", "subject": SUBJECT,
            "value": {"provenance": outline.get("provenance", "authored"), "items": len(nodes)},
            "evidence_ref": f"outline:{outline['source']['ref']}"}]},
        "resource_caps": caps,
    }
    request = {"schema": "efforts.agenda-request", "version": 2, "mode": "context-qualified",
               "expected_revision": 0,
               "operation_id": operation_id or f"outline-init-{_slug(outline['source']['ref'])}",
               "change": change}
    return request, roots


def _proof(exits: list[str], node: dict[str, Any], provenance: str) -> list[dict[str, str]]:
    # An outline asserts outcomes; it never measured them, so replayed proof is judgment.
    ref = node["value"].get("evidence") or f"outline-{provenance}"
    return [{"criterion": criterion, "kind": "judgment", "ref": ref, "inputs": "outline replay"}
            for criterion in exits]


class _Replay:
    """Drive a fresh standalone store through an outline's recorded statuses."""

    def __init__(self, home: Path, request: dict[str, Any], provenance: str, *,
                 session: str = "outline-preview", actor: str | None = None) -> None:
        import agenda_store as store

        self.store, self.home, self.provenance = store, home, provenance
        self.session, self.actor = session, actor
        self.prefix = request["operation_id"]
        self.board = request["change"]["board"]["items"]
        self.warnings: list[str] = []
        store.mutate(home, request, session=session, actor=actor)

    def _cursor(self) -> tuple[str, str, str]:
        cursor = self.store.read(self.home)["documents"]["cursor"]
        frame = cursor["frames"][-1]
        item = cursor["frame_state"][frame]["pending"][0]
        return frame, item, cursor["item_state"][item]["current_attempt_id"]

    def _update(self, change: dict[str, Any]) -> dict[str, Any]:
        revision = self.store.read(self.home)["revision"]
        request = {"schema": "efforts.agenda-request", "version": 2, "mode": "context-qualified",
                   "expected_revision": revision, "operation_id": f"{self.prefix}-replay-{revision}",
                   "change": change}
        result, _ = self.store.mutate(self.home, request, session=self.session, actor=self.actor)
        transition = result.get("transition", {})
        if transition.get("decision") == "not-admitted":
            raise AgendaError(f"outline replay not admitted: {transition.get('reason_code')}")
        return transition

    def _outcome(self, node: dict[str, Any], outcome: str) -> None:
        frame, item, attempt = self._cursor()
        self._update({"operation": "outcome", "frame_id": frame, "item_id": item, "attempt_id": attempt,
                      "outcome": outcome,
                      "evidence": _proof(self.board[node["id"]]["exit"], node, self.provenance)})

    def run(self, nodes: list[dict[str, Any]]) -> bool:
        """Replay one frame; return True when every node in it completed."""
        for index, node in enumerate(nodes):
            status = node["status"]
            started = any(child["status"] != "queued" for child in _all(node["children"]))
            # Reaching a parent means it is the running head; open its subqueue unless it is parked.
            if node["children"] and (status != "waiting" or started):
                frame, item, attempt = self._cursor()
                expansion = self._update({
                    "operation": "expand", "frame_id": frame, "item_id": item, "attempt_id": attempt,
                    "items": [{"definition_ref": child["id"]} for child in node["children"]],
                    "continuation": {"parent_item_id": item,
                                     "next_action": f"Resume: {self.board[node['id']].get('label') or self.board[node['id']]['action']}"},
                    "evidence": _proof(["decomposed into a subqueue"], node, self.provenance)})
                if not self.run(node["children"]):
                    self._late(nodes[index + 1:])
                    return False
                self._update({"operation": "return", "frame_id": expansion["frame_id"],
                              "expansion_id": expansion["expansion_id"],
                              "acceptance_ref": f"outline-{self.provenance}",
                              "evidence": _proof(["subqueue accepted"], node, self.provenance)})
            if status == "done":
                self._outcome(node, "done")
                continue
            if status == "waiting":
                self._outcome(node, "blocked")
            self._late(nodes[index + 1:])
            return False
        return True

    def _late(self, nodes: list[dict[str, Any]]) -> None:
        for node in _all(nodes):
            if node["status"] != "queued":
                self.warnings.append(
                    f"{node['id']}: marked {node['status']} after an unfinished earlier item; shown as queued")


def adopt(outline: dict[str, Any], home: Path, *, session: str | None = None,
          actor: str | None = None) -> dict[str, Any]:
    """Initialize a fresh explicit state home from an outline, replaying its progress and hierarchy."""
    import agenda_store as store

    home = Path(home)
    if home.exists() and any(home.iterdir()):
        raise AgendaError("adopt requires a new or empty state home; it never rewrites an existing lane")
    home.mkdir(parents=True, exist_ok=True)
    request, roots = expand(outline)
    replay = _Replay(home, request, outline.get("provenance", "authored"),
                     session=session or "outline-adopt", actor=actor)
    replay.run(roots)
    state = store.read(home)
    return {"revision": state["revision"], "snapshot": state["snapshot"], "warnings": replay.warnings}


def preview(outline: dict[str, Any]) -> dict[str, Any]:
    """Replay an outline into a throwaway store and return its display graph and snapshot."""
    import agenda_store as store
    from agenda_v2_graph import graph

    request, roots = expand(outline)
    with tempfile.TemporaryDirectory(prefix="agenda-outline-") as directory:
        replay = _Replay(Path(directory), request, outline.get("provenance", "authored"))
        replay.run(roots)
        state = store.read(Path(directory))
        rendered = graph(state["documents"], state["records"])
    return {"request": request, "graph": rendered, "snapshot": state["snapshot"],
            "warnings": replay.warnings}


_MARKS = {"done": "✓", "running": "▶", "queued": "○", "waiting": "⏸", "replanning": "↻", "closed": "·"}


def render_text(rendered: dict[str, Any], title: str | None = None) -> str:
    """Plain-text tree of a display graph: frames as nested queues under the item that opened them."""
    items_by_frame: dict[str, list[dict[str, Any]]] = {}
    for item in rendered["items"]:
        items_by_frame.setdefault(item["frame"], []).append(item)
    children: dict[str | None, list[dict[str, Any]]] = {}
    for frame in rendered["frames"]:
        children.setdefault(frame["via"], []).append(frame)
    cursor = rendered["cursor"]
    lines = [title] if title else []

    def frame_lines(frame: dict[str, Any], indent: str) -> None:
        for item in items_by_frame.get(frame["id"], []):
            here = " ◀ cursor" if item["id"] == cursor["item"] and frame["id"] == cursor["frame"] else ""
            lines.append(f"{indent}{_MARKS.get(item['state'], '?')} {item['label']}{here}")
            for child in children.get(item["id"], []):
                frame_lines(child, indent + "    ")

    for root in children.get(None, []):
        lines.append(f"{root['label']} [{root['state']}]")
        frame_lines(root, "  ")
    # The item bound can drop a parent item while keeping the subqueue it opened.
    shown = {item["id"] for item in rendered["items"]}
    for via, frames in children.items():
        if via is None or via in shown:
            continue
        for frame in frames:
            lines.append(f"  … (omitted parent) {frame['label']} [{frame['state']}]")
            frame_lines(frame, "      ")
    omitted = rendered.get("omitted", {})
    if omitted.get("items") or omitted.get("frames"):
        lines.append(f"  … {omitted.get('items', 0)} items / {omitted.get('frames', 0)} frames omitted")
    return "\n".join(lines)


def from_effort(readme: Path, *, worktree: str | None = None) -> dict[str, Any]:
    """Outline the open remainder of an effort: one item per Plan/Validation section with open work."""
    sys.path.insert(0, str(SCRIPTS.parents[1] / "planning-efforts" / "scripts"))
    from effort_digest import open_items, parse_sections

    readme = readme.resolve()
    remaining = open_items(parse_sections(readme.read_text(encoding="utf-8")))
    if not remaining:
        raise AgendaError("effort has no open Plan or Validation Plan items")
    groups: dict[str, list[dict[str, Any]]] = {}
    for entry in remaining:
        groups.setdefault(entry["section"], []).append(entry)
    items = []
    for section, entries in groups.items():
        children = [{"action": entry["text"], "source_ref": f"{section} (line {entry['line']})"}
                    for entry in entries]
        heading = section.split(" > ")[-1]
        if len(children) == 1:
            items.append(children[0])
        else:
            items.append({"action": f"Complete the open items in {heading}", "label": clip(heading),
                          "source_ref": section, "children": children})
    items[0]["status"] = "current"
    if items[0].get("children"):
        items[0]["children"][0]["status"] = "current"
    ref = readme.as_posix()
    marker = ref.rfind("/efforts/")
    return {"schema": SCHEMA, "version": 1, "provenance": "derived",
            "source": {"kind": "effort", "ref": ref[marker + 1:] if marker >= 0 else readme.name},
            "worktree": worktree or "effort-remainder", "items": items}


def _load(path: str) -> dict[str, Any]:
    text = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    return json.loads(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("expand", help="outline JSON -> request-v2 init JSON (definitions only, no progress)")
    init.add_argument("outline", help="outline JSON path or - for stdin")
    init.add_argument("--operation-id")
    show = sub.add_parser("preview", help="render an outline offline as the Companion display graph")
    show.add_argument("outline", help="outline JSON path or - for stdin")
    show.add_argument("--format", choices=("text", "json"), default="text")
    take = sub.add_parser("adopt", help="initialize a new explicit state home, replaying outline progress")
    take.add_argument("outline", help="outline JSON path or - for stdin")
    take.add_argument("--state-home", type=Path, required=True)
    take.add_argument("--session")
    take.add_argument("--actor")
    effort = sub.add_parser("from-effort", help="outline the open remainder of an effort README")
    effort.add_argument("readme", type=Path)
    effort.add_argument("--worktree")
    args = parser.parse_args(argv)
    try:
        if args.command == "expand":
            output: Any = expand(_load(args.outline), operation_id=args.operation_id)[0]
        elif args.command == "adopt":
            output = adopt(_load(args.outline), args.state_home, session=args.session, actor=args.actor)
        elif args.command == "from-effort":
            output = from_effort(args.readme, worktree=args.worktree)
        else:
            outline = _load(args.outline)
            result = preview(outline)
            if args.format == "text":
                print(render_text(result["graph"]))
                for warning in result["warnings"]:
                    print(f"warning: {warning}", file=sys.stderr)
                return 0
            output = result
    except (AgendaError, OSError, json.JSONDecodeError) as exc:
        print(f"agenda-outline: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
