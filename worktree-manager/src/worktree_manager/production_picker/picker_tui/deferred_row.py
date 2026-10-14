"""Fixed-height native list rows, formatted only when they enter the viewport."""
from __future__ import annotations

from collections.abc import Callable

from rich.text import Text
from textual.content import Content
from textual.visual import Visual


class DeferredRow(Visual):
    __slots__ = ("_render",)

    def __init__(self, render: Callable[[], Text]) -> None:
        self._render = render

    @property
    def plain(self) -> str:
        return self._render().plain

    def __str__(self) -> str:
        return self.plain

    def as_text(self) -> Text:
        return self._render()

    def get_height(self, rules, width: int) -> int:
        return 1

    def get_optimal_width(self, rules, container_width: int) -> int:
        return container_width

    def render_strips(self, width, height, style, options):
        text = self._render().copy()
        text.truncate(max(0, width), overflow="crop")
        content = Content.from_rich_text(text)
        return content.render_strips(width, 1, style, options)[:1]
