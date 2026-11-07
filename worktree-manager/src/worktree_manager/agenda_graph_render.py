"""Git-graph style rendering of an admitted agenda display graph.

Each frame is a lane: root frames run down the left edge, a subqueue forks
right from the item that expanded it and merges back when it closes. Items are
commits on their lane, in queue order; the cursor is the highlighted node at the
top of the active stack. Rendering is pure and display-only.
"""

from __future__ import annotations

from rich.text import Text

LABEL_BUDGET = 72
HISTORY_ROWS = 8
_ITEM_GLYPHS = {
    "done": ("●", "green"),
    "queued": ("○", ""),
    "running": ("◐", "bold cyan"),
    "waiting": ("◇", "yellow"),
    "replanning": ("↻", "magenta"),
    "closed": ("⊘", "dim"),
}
_FRAME_STYLES = {"active": "bold", "deferred": "yellow", "closed": "dim", "idle": "dim"}
_HISTORY_GLYPHS = {
    "result": "●", "attempt": "◐", "expansion": "╮", "return": "╯", "wait": "◇",
    "deferred": "⏸", "recovery": "↺", "assignment": "→", "decision": "◆", "refinement": "↻",
}
_CURSOR = ("◉", "bold #d78700")


def _clip(text: str, budget: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= budget else text[: max(budget - 1, 1)] + "…"


def _clock(stamp: str) -> str:
    # "2026-11-06T20:43:07Z" -> "11-06 20:43"; anything unexpected is shown as-is.
    date, _, time = stamp.partition("T")
    return f"{date[5:]} {time[:5]}" if len(date) == 10 and len(time) >= 5 else stamp


def _lanes(open_lanes: list[bool]) -> str:
    return "".join("│ " if lane else "  " for lane in open_lanes)


def _header(text: Text, observation: dict, budget: int) -> None:
    snapshot = observation["snapshot"]
    counts = [snapshot["status"]]
    if snapshot["deferred"]:
        counts.append(f"{len(snapshot['deferred'])} deferred")
    if snapshot["parked"]:
        counts.append(f"{snapshot['parked']} parked")
    text.append(f"r{observation['revision']}", style="bold")
    text.append(f" · {_clock(observation['committed_at'])} · ", style="dim")
    text.append(" · ".join(counts) + "\n", style="bold cyan")
    rows = [("◎ Goal ", snapshot["root"] or "—", ""), ("▸ Next ", snapshot["next_action"] or "—", "bold")]
    if snapshot["exit_criteria"]:
        rows.append(("⚑ Exit ", " · ".join(snapshot["exit_criteria"]), ""))
    # The snapshot's stack names each frame's work; graph frame labels are lane titles.
    stack = [str(entry) for entry in snapshot["stack"]]
    if stack:
        rows.append(("⎇ Stack", " ▸ ".join(stack), ""))
    if snapshot["next_revisit_at"]:
        rows.append(("⏲ Recheck", snapshot["next_revisit_at"], "yellow"))
    if snapshot["due_rechecks"]:
        rows.append(("⏲ Due", " · ".join(snapshot["due_rechecks"]), "yellow"))
    for label, value, style in rows:
        text.append(f"{label} ", style="bold #d78700")
        text.append(_clip(value, budget + 8) + "\n", style=style)


def _graph(text: Text, graph: dict, budget: int) -> None:
    items_by_frame: dict[str, list[dict]] = {}
    for item in graph["items"]:
        items_by_frame.setdefault(item["frame"], []).append(item)
    children: dict[str, list[dict]] = {}
    roots = []
    for frame in graph["frames"]:
        if frame["parent"] is None:
            roots.append(frame)
        else:
            children.setdefault(frame["parent"], []).append(frame)
    cursor_item = graph["cursor"]["item"]

    def item_row(prefix: str, item: dict) -> None:
        glyph, style = _CURSOR if item["id"] == cursor_item else _ITEM_GLYPHS[item["state"]]
        text.append(prefix, style="dim")
        text.append(glyph, style=style)
        label_style = (
            "bold" if item["id"] == cursor_item
            else "dim" if item["state"] in {"done", "closed"} else ""
        )
        text.append(" " + _clip(item["label"], max(budget - len(prefix), 16)), style=label_style)
        if item["attempts"] > 1:
            text.append(f" ×{item['attempts']}", style="dim")
        if item["id"] == cursor_item:
            text.append("  ◀ cursor", style="bold #d78700")
        elif item["state"] in {"waiting", "replanning", "running"}:
            text.append(f"  {item['state']}", style=_ITEM_GLYPHS[item["state"]][1])
        text.append("\n")

    def frame_rows(frame: dict, open_lanes: list[bool]) -> None:
        rows = items_by_frame.get(frame["id"], [])
        forks = children.get(frame["id"], [])
        placed = {item["id"] for item in rows}
        stray = [child for child in forks if child["via"] not in placed]
        sequence: list[tuple[str, dict]] = []
        for item in rows:
            sequence.append(("item", item))
            sequence.extend(("fork", child) for child in forks if child["via"] == item["id"])
        sequence.extend(("fork", child) for child in stray)
        for index, (kind, entry) in enumerate(sequence):
            continues = index + 1 < len(sequence)
            prefix = _lanes(open_lanes)
            if kind == "item":
                item_row(prefix, entry)
                continue
            style = _FRAME_STYLES[entry["state"]]
            text.append(prefix + ("├─╮ " if continues else "╰─╮ "), style="dim")
            text.append(_clip(entry["label"], max(budget - len(prefix) - 4, 16)), style=style)
            text.append(f"  {entry['state']}", style="dim")
            text.append("\n")
            inner = [*open_lanes, continues]
            frame_rows(entry, inner)
            child_prefix = _lanes(inner)
            if entry["state"] == "closed":
                text.append(prefix + ("├─╯ " if continues else "╰─╯ "), style="dim")
                text.append("↩ " + _clip(entry["returns"], max(budget - len(prefix) - 6, 16)), style="dim")
            else:
                text.append(child_prefix + "┆ ", style="dim")
                text.append("↩ then " + _clip(entry["returns"], max(budget - len(child_prefix) - 9, 16)),
                            style="dim italic")
            text.append("\n")

    for index, root in enumerate(roots):
        if index:
            text.append("\n")
        text.append("◆ ", style="bold #d78700")
        text.append(_clip(root["label"], budget), style=_FRAME_STYLES[root["state"]])
        if root["state"] != "active":
            text.append(f"  {root['state']}", style="dim")
        text.append("\n")
        frame_rows(root, [])


def _history(text: Text, graph: dict, budget: int) -> None:
    entries = graph["history"][-HISTORY_ROWS:]
    hidden = len(graph["history"]) - len(entries) + graph["omitted"]["history"]
    if not entries:
        return
    text.append("\nHistory", style="bold #d78700")
    if hidden:
        text.append(f"  (+{hidden} earlier)", style="dim")
    text.append("\n")
    for entry in reversed(entries):
        text.append(f"  {_clock(entry['at'])} ", style="dim")
        text.append(_HISTORY_GLYPHS.get(entry["kind"], "·") + " ", style="cyan")
        text.append(_clip(entry["text"], budget - 14) + "\n")


def render(observation: dict, *, budget: int = LABEL_BUDGET) -> Text:
    """Render an admitted cursor-4 observation that carries a display graph."""
    graph = observation["graph"]
    text = Text(no_wrap=True, overflow="ellipsis")
    _header(text, observation, budget)
    text.append("\n")
    _graph(text, graph, budget)
    omitted = graph["omitted"]
    if omitted["frames"] or omitted["items"]:
        text.append(
            f"… {omitted['items']} items / {omitted['frames']} frames not shown\n", style="dim",
        )
    _history(text, graph, budget)
    return text
