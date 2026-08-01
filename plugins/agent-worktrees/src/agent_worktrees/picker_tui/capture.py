"""Deterministic capture of the picker's rendered screen -- for audit + tests.

The picker is a *deterministic renderer*: given injected ``source`` data, the
composed segment/region widget tree paints the same character grid every time
(topbar, pivots, machine tabs, borders, body window, footer). Capture reads that
grid straight off Textual's **compositor** -- the live composited display -- and
serializes it (styles stripped, with ANSI colour, or as an SVG). So any state can
be exported as a human-auditable **SVG screenshot** or captured as a **character
grid** a test asserts against, with no live terminal and no human watching.
(Overlays are native Textual ``ModalScreen``s since #88 F4, drawn by the
compositor rather than the base screen; capture one with
:func:`capture_modal_async`, the app-level seam.)

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
from rich.segment import Segment, Segments

# The picker's canonical headless render size (matches the test suite). A wide
# grid so columns don't get dropped by the responsive fitter.
DEFAULT_SIZE = (118, 40)


def _screen_segments(scr: Any) -> list:
    """Segments of the *composited* screen -- what the picker actually displays.

    NF5-4 (#88): capture sources from the live Textual compositor (the composed
    segment/region widget tree that drives the real display), not a parallel
    whole-screen ``render()``. The compositor's per-row strips are flattened into
    one newline-separated ``Segment`` stream and fed to the same recording
    ``Console`` below -- so ``text`` / ``ansi`` / ``svg`` are byte-identical to
    the former ``render()``-based seam (the two derive from the same
    ``_frame_segments`` / ``_build_body_split`` source) while now reflecting the
    exact composited display. This is what lets ``PickerScreen.render()`` retire.
    """
    comp = scr.screen._compositor
    segments: list = []
    for strip in comp.render_strips():
        segments.extend(strip)
        segments.append(Segment("\n"))
    return segments


def _screen_text(scr: Any) -> str:
    """The plain character grid of the composited screen."""
    return "".join(seg.text for seg in _screen_segments(scr))


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
    return _screen_text(scr)


def screen_to_ansi(scr: Any) -> str:
    """The character grid with ANSI colour -- a colour-aware, diffable golden."""
    console = _console(scr)
    console.print(Segments(_screen_segments(scr)), end="")
    return console.export_text(styles=True)


def screen_to_svg(scr: Any, *, title: str = "Worktree Picker") -> str:
    """The rendered state as a standalone SVG screenshot (colours preserved)."""
    console = _console(scr)
    console.print(Segments(_screen_segments(scr)), end="")
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
    view: str = "local",
    settle: float = 0.0,
    keys: Optional[list[str]] = None,
    update_state: Optional[str] = None,
    prepare: Optional[Callable[[Any, Any], Awaitable[None]]] = None,
    title: str = "Worktree Picker",
) -> dict[str, str]:
    """Render ``source`` headlessly and capture the screen (text + ansi + svg).

    Spins the real :class:`PickerApp` under Textual's headless test driver, so no
    terminal is required. ``source`` is any picker data source (a fixture object
    or the ``data_local`` / ``data_ssh`` modules).

    - ``view`` -- ``"local"`` focuses the local machine tab; ``"all"`` focuses the
      aggregate *All machines* tab (index 0).
    - ``settle`` -- seconds to wait before capture, so a ``live=False`` multi-
      machine source's staggered "ready" transitions resolve (else remotes show a
      connect spinner and their rows are excluded from the All view).
    - ``keys`` -- keys to send (see Textual key names) to drive the picker to a
      target state before capture.
    - ``update_state`` -- force the topbar update indicator (e.g. ``"current"``
      for a clean ✓ instead of a transient "update available").
    - ``prepare`` -- an escape-hatch coroutine awaited with ``(screen, pilot)``
      for anything ``keys`` can't express.
    """
    from .engine import PickerApp, PickerScreen

    app = PickerApp(source, live=live)
    async with app.run_test(size=size) as pilot:
        scr = app.query_one(PickerScreen)
        if view == "all":
            scr.machine_idx = 0
        else:
            try:
                scr.machine_idx = scr.local_index()
            except Exception:
                pass
        await pilot.pause()
        if settle:
            import asyncio

            await asyncio.sleep(settle)
            await pilot.pause()
        if keys:
            await pilot.press(*keys)
            await pilot.pause()
        if prepare is not None:
            await prepare(scr, pilot)
            await pilot.pause()
        if update_state is not None:
            # Set last, just before render: a background update-poll can flip it
            # back during settle, so an early assignment would not stick.
            scr.update_state = update_state
        return capture_screen(scr, title=title)


async def capture_frames_async(
    source: Any,
    steps: list[Optional[list[str]]],
    *,
    live: bool = False,
    size: tuple[int, int] = DEFAULT_SIZE,
    view: str = "local",
    settle: float = 0.0,
    update_state: Optional[str] = None,
    title: str = "Worktree Picker",
) -> list[dict[str, str]]:
    """Capture a **sequence** of frames as the picker is keyboard-driven.

    ``steps`` is a list of key-batches: the picker is driven to the initial
    ``view`` (settling first), a frame is captured, then for each entry the keys
    are pressed and another frame captured. The first entry may be ``None`` to
    capture the initial state before any keys. Groundwork for an animated
    walkthrough (assemble the returned SVGs/PNGs into a GIF) -- realizes the
    picker vision's *state-sequence* capture.
    """
    from .engine import PickerApp, PickerScreen

    frames: list[dict[str, str]] = []
    app = PickerApp(source, live=live)
    async with app.run_test(size=size) as pilot:
        scr = app.query_one(PickerScreen)
        if view == "all":
            scr.machine_idx = 0
        else:
            try:
                scr.machine_idx = scr.local_index()
            except Exception:
                pass
        if update_state is not None:
            scr.update_state = update_state
        await pilot.pause()
        if settle:
            import asyncio

            await asyncio.sleep(settle)
            await pilot.pause()
        for batch in steps:
            if batch:
                await pilot.press(*batch)
                await pilot.pause()
            if update_state is not None:
                scr.update_state = update_state
            frames.append(capture_screen(scr, title=title))
    return frames


def capture(source: Any, **kwargs: Any) -> dict[str, str]:
    """Synchronous :func:`capture_async` -- render + capture in one call."""
    import asyncio

    return asyncio.run(capture_async(source, **kwargs))


async def capture_modal_async(
    source: Any,
    opener: Callable[[Any, Any], Awaitable[None]],
    *,
    live: bool = False,
    size: tuple[int, int] = DEFAULT_SIZE,
    view: str = "local",
    title: str = "Worktree Picker",
) -> str:
    """SVG screenshot of the **composited app** -- the picker *plus* any open
    native ``ModalScreen``.

    The three ``screen_to_*`` seams above capture the base picker screen off the
    compositor. A pushed native ``ModalScreen`` (since #88 F4) lives *above* the
    base screen on the app's screen stack -- so this helper instead exports
    Textual's own app-level screenshot (the full compositor, modal included),
    which is the right seam for auditing / A/B-comparing a modal's appearance.

    ``opener`` is a coroutine ``(screen, pilot) -> None`` that drives the picker
    to open the target modal before the screenshot is taken (e.g. set
    ``scr.sel`` and call ``scr._activate()``).
    """
    from .engine import PickerApp, PickerScreen

    app = PickerApp(source, live=live)
    async with app.run_test(size=size) as pilot:
        scr = app.query_one(PickerScreen)
        if view == "all":
            scr.machine_idx = 0
        else:
            try:
                scr.machine_idx = scr.local_index()
            except Exception:
                pass
        await pilot.pause()
        await opener(scr, pilot)
        await pilot.pause()
        return app.export_screenshot(title=title)


def capture_modal(
    source: Any,
    opener: Callable[[Any, Any], Awaitable[None]],
    **kwargs: Any,
) -> str:
    """Synchronous :func:`capture_modal_async` -- composited-app modal SVG."""
    import asyncio

    return asyncio.run(capture_modal_async(source, opener, **kwargs))
