"""Deterministic capture of the picker's rendered screen -- for audit + tests.

The picker is a *deterministic renderer*: :meth:`PickerScreen.render` composes the
entire screen (topbar, pivots, machine tabs, borders, body window, footer, and
any modal overlay) into a single styled Rich :class:`~rich.text.Text`. Given
injected ``source`` data, the same inputs yield the same grid -- so any state can
be exported as a human-auditable **SVG screenshot** or captured as a **character
grid** a test asserts against, with no live terminal and no human watching.

Realizes the picker vision's Features/``auditable-testable-rendering`` and
Behaviors/``renderable-and-assertable-headless`` (rides on
Features/``programmatic-parity`` -- known inputs, known grid).

Three capture forms, one seam:

- :func:`screen_to_text`  -- the plain character grid (styles stripped). Stable
  across platforms; the natural golden for layout / labels / focus-cursor
  position.
- :func:`screen_to_ansi`  -- the character grid *with* ANSI color. A
  colour-aware, still text-diffable golden for asserting semantic state colour.
- :func:`screen_to_svg`   -- the rendered state as a standalone SVG (colours
  preserved) for a human (or an agent) to eyeball.

:func:`capture` / :func:`capture_async` spin the picker headlessly over a given
``source``, optionally drive it to a target state, and return all three forms.
"""
from __future__ import annotations

import io
from typing import Any, Awaitable, Callable, Optional

from rich.console import Console
from rich.text import Text

# The picker's canonical headless render size (matches the test suite). A wide
# grid so columns don't get dropped by the responsive fitter.
DEFAULT_SIZE = (118, 40)


def _rendered_text(scr: Any) -> Text:
    """The screen the picker would paint, as a Rich ``Text`` (one call, no loop)."""
    rendered = scr.render()
    return rendered if isinstance(rendered, Text) else Text(str(rendered))


def _console(scr: Any) -> Console:
    """A recording Console sized to the screen, truecolor, platform-neutral.

    ``legacy_windows=False`` keeps the capture identical on Windows and POSIX so
    a golden written on one platform matches on the other.
    """
    width = getattr(getattr(scr, "size", None), "width", None) or DEFAULT_SIZE[0]
    height = getattr(getattr(scr, "size", None), "height", None) or DEFAULT_SIZE[1]
    return Console(
        record=True,
        width=width,
        height=height,
        file=io.StringIO(),
        color_system="truecolor",
        legacy_windows=False,
    )


def screen_to_text(scr: Any) -> str:
    """The plain character grid the picker paints (all styling stripped)."""
    return _rendered_text(scr).plain


def screen_to_ansi(scr: Any) -> str:
    """The character grid with ANSI colour -- a colour-aware, diffable golden."""
    console = _console(scr)
    console.print(_rendered_text(scr), end="")
    return console.export_text(styles=True)


def screen_to_svg(scr: Any, *, title: str = "Worktree Picker") -> str:
    """The rendered state as a standalone SVG screenshot (colours preserved)."""
    console = _console(scr)
    console.print(_rendered_text(scr), end="")
    return console.export_svg(title=title)


def capture_screen(scr: Any, *, title: str = "Worktree Picker") -> dict[str, str]:
    """All three capture forms for an already-mounted ``PickerScreen``."""
    return {
        "text": screen_to_text(scr),
        "ansi": screen_to_ansi(scr),
        "svg": screen_to_svg(scr, title=title),
    }


async def capture_async(
    source: Any,
    *,
    live: bool = False,
    size: tuple[int, int] = DEFAULT_SIZE,
    local_tab: bool = True,
    prepare: Optional[Callable[[Any, Any], Awaitable[None]]] = None,
    title: str = "Worktree Picker",
) -> dict[str, str]:
    """Render ``source`` headlessly and capture the screen (text + ansi + svg).

    Spins the real :class:`PickerApp` under Textual's headless test driver, so no
    terminal is required. ``source`` is any picker data source (a fixture object
    or the ``data_local`` / ``data_ssh`` modules). When ``prepare`` is given it is
    awaited with ``(screen, pilot)`` to drive the picker to a target state (switch
    pivot, focus a row, open a dialog) before the capture is taken.
    """
    from .engine import PickerApp, PickerScreen

    app = PickerApp(source, live=live)
    async with app.run_test(size=size) as pilot:
        scr = app.query_one(PickerScreen)
        if local_tab:
            # Default to the local machine tab so a screenshot isn't stuck on the
            # aggregate "All machines" view.
            try:
                scr.machine_idx = scr.local_index()
            except Exception:
                pass
        await pilot.pause()
        if prepare is not None:
            await prepare(scr, pilot)
            await pilot.pause()
        return capture_screen(scr, title=title)


def capture(source: Any, **kwargs: Any) -> dict[str, str]:
    """Synchronous :func:`capture_async` -- render + capture in one call."""
    import asyncio

    return asyncio.run(capture_async(source, **kwargs))
