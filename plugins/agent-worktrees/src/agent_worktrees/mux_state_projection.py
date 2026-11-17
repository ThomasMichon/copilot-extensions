"""Format already-computed state without observing Git or refreshing facts."""

from __future__ import annotations

from typing import Any

from .closure_descriptor import interpret_descriptor_payload
from .status_bar_cli import _DESCRIPTOR_STYLE_BG, _SEGMENT_TITLE_MAX


def render_projected_segment(row: dict[str, Any] | None, *, current: bool) -> str:
    def text(value: str) -> str:
        return "".join(char for char in value if char.isprintable()).replace("#", "##")

    row = row if row is not None else {}
    title = row.get("title")
    title = title if isinstance(title, str) and title != "null" else ""
    if len(title) > _SEGMENT_TITLE_MAX:
        title = title[: _SEGMENT_TITLE_MAX - 1].rstrip() + "\u2026"
    descriptor = interpret_descriptor_payload(row.get("closure"))
    label = descriptor["compact"] if descriptor["supported"] else "STATE(?)"
    if descriptor["supported"] and not current:
        label += " ?"
    bg = _DESCRIPTOR_STYLE_BG.get(descriptor["style"], "colour238")
    tags: list[str] = []
    if descriptor["supported"] and current:
        for name, marker in (("ahead", "\u2191"), ("behind", "\u2193")):
            count = row.get(name)
            if (
                isinstance(count, int) and not isinstance(count, bool) and count > 0
                and (name != "ahead" or row.get("state") != "completed")
            ):
                tags.append(marker + str(count))
    tag = " " + " ".join(tags) if tags else ""
    block = f"#[bg={bg},fg=colour015,bold] {text(label)}{tag} #[default]"
    return " ".join(part for part in (text(title), block) if part)
