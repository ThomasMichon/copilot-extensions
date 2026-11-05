"""Opt-in bounded-source scale controls; timings are not native-frame evidence."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import asdict, dataclass
import json
from hashlib import sha256
import math
from pathlib import Path
import platform
import sys
import time

import psutil
import pytest

from worktree_manager.companion_timeline_contract import (
    Completeness, HistoryPage, LiveDelta, MAX_STRIP_CACHE_BYTES, MAX_WINDOW_BYTES,
    MAX_WINDOW_EVENTS, SourceToken, TimelineEvent,
)
from worktree_manager.companion_timeline_model import TimelineWindow
from worktree_manager.companion_timeline_widget import SubwayTimeline
from worktree_manager import (
    companion_timeline_contract, companion_timeline_layout,
    companion_timeline_model, companion_timeline_widget,
)
from _companion_timeline_fixtures import event, position, snapshot, token
from test_companion_timeline_widget import TimelineApp

pytestmark = [pytest.mark.portfolio_tier("T3"), pytest.mark.timeout(180)]


@dataclass(frozen=True)
class Sample:
    wall_ms: float
    process_cpu_ms: float
    process_rss_bytes: int


def scale_event(source: SourceToken, number: int) -> TimelineEvent:
    return event(
        source, number, actor=f"actor-{number % 40}",
        summary="Bounded wide history 界e\u0301 with literal [red] text",
        details="First logical detail 界e\u0301\nSecond logical detail **literal**",
    )


def load_selected_window(total: int) -> tuple[TimelineWindow, SourceToken, int]:
    """Generate only selected pages from a large logical source, not its whole history."""
    source = token()
    window = TimelineWindow()
    assert window.bind(source).applied
    lower, generated = total - 64, 64
    assert window.apply_snapshot(snapshot(
        source, tuple(scale_event(source, number) for number in range(lower, total)),
        before=f"older-{lower}",
    )).applied
    while generated < MAX_WINDOW_EVENTS:
        before = window.sources[source.identity.source_key].before
        assert before is not None and window.request_history(source, before).applied
        start = lower - min(64, MAX_WINDOW_EVENTS - generated)
        rows = tuple(scale_event(source, number) for number in range(start, lower))
        assert window.prepend_page(HistoryPage(
            source, before, position(source, f"older-{start}"), rows, Completeness.PARTIAL,
        )).applied
        generated += len(rows)
        lower = start
    assert generated == len(window.events) == MAX_WINDOW_EVENTS < total
    assert window.encoded_size <= MAX_WINDOW_BYTES
    return window, source, generated


def report(samples: list[Sample]) -> dict[str, object]:
    values = sorted(sample.wall_ms for sample in samples)
    return {
        "expected_samples": 40, "completed_samples": len(samples),
        "quantile_method": "nearest-rank",
        "p50_ms": values[math.ceil(0.50 * len(values)) - 1],
        "p95_ms": values[math.ceil(0.95 * len(values)) - 1],
        "p99_ms": values[math.ceil(0.99 * len(values)) - 1],
        "max_ms": values[-1],
        "samples": [asdict(sample) for sample in samples],
    }


def test_full_window_paint_and_live_updates_keep_resource_and_browsing_bounds(
    monkeypatch: pytest.MonkeyPatch, record_property: Callable[[str, object], None],
) -> None:
    total = 1_000_000
    window, source, generated = load_selected_window(total)
    process = psutil.Process()
    paints: list[Sample] = []
    updates: list[Sample] = []

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("visible paint attempted external work")

    async def check() -> None:
        app = TimelineApp(window)
        async with app.run_test(size=(120, 40)) as pilot:
            timeline = app.query_one(SubwayTimeline)
            timeline.focus()
            await pilot.press("pageup")
            anchor = timeline.capture_anchor()
            assert anchor is not None and not window.follow_live
            selected = timeline.selected
            cursor = window.sources[source.identity.source_key].position
            assert cursor is not None
            for number in range(40):
                timeline._clear_cache()
                timeline.layout_index.materialized_rows = 0
                with monkeypatch.context() as patch:
                    patch.setattr("builtins.open", forbidden)
                    patch.setattr("socket.create_connection", forbidden)
                    patch.setattr("subprocess.Popen", forbidden)
                    patch.setattr("os.scandir", forbidden)
                    wall, cpu = time.perf_counter(), time.process_time()
                    for y in range(timeline.size.height):
                        assert timeline.render_line(y).cell_length == timeline.scrollable_content_region.width
                    paint_wall, paint_cpu = time.perf_counter() - wall, time.process_time() - cpu
                paints.append(Sample(paint_wall * 1000, paint_cpu * 1000, process.memory_info().rss))
                assert timeline.layout_index.materialized_rows <= timeline.size.height + 8
                assert timeline.cache_bytes <= MAX_STRIP_CACHE_BYTES
                after = position(source, f"live-scale-{number}")
                wall, cpu = time.perf_counter(), time.process_time()
                assert timeline.apply_delta(LiveDelta(
                    source, cursor, after, (scale_event(source, total + number),),
                )).applied
                await pilot.pause()
                updates.append(Sample(
                    (time.perf_counter() - wall) * 1000,
                    (time.process_time() - cpu) * 1000,
                    process.memory_info().rss,
                ))
                cursor = after
                retained = timeline.capture_anchor()
                assert retained is not None and retained.event == anchor.event
                assert timeline.selected == selected and timeline.has_focus
                assert len(window.events) <= MAX_WINDOW_EVENTS and window.encoded_size <= MAX_WINDOW_BYTES
                assert timeline.cache_bytes <= MAX_STRIP_CACHE_BYTES
            assert generated + len(updates) == len(window._proofs)
            assert len(window.unread) == len(updates) and window.evicted == len(updates)
            assert not window.reconciliation_required
            await pilot.press("enter")
            assert selected in window.expanded
            await pilot.resize_terminal(63, 26)
            resized = timeline.capture_anchor()
            assert resized is not None and resized.event == anchor.event
            assert timeline.selected == selected and timeline.has_focus
            await pilot.press("end")
            assert window.follow_live and not window.unread
            assert timeline.is_vertical_scroll_end

    asyncio.run(check())
    assert len(paints) == len(updates) == 40
    record_property("sidecar-scale-observation", json.dumps({
        "tier": "source-fixture; no native-client or installed acceptance",
        "logical_history_events": total, "generated_initial_events": generated,
        "display_event_limit": MAX_WINDOW_EVENTS, "viewport_cells": [120, 40],
        "python": list(sys.version_info[:3]), "os": platform.system(),
        "source_sha256": {
            module.__name__.rsplit(".", 1)[-1]: sha256(Path(module.__file__).read_bytes()).hexdigest()
            for module in (
                companion_timeline_contract, companion_timeline_layout,
                companion_timeline_model, companion_timeline_widget,
            )
        },
        "timing": "in-process monotonic wall; process_time CPU; psutil current-process RSS",
        "rss_coverage": "sampled whole pytest process, not isolated widget allocation or child memory",
        "cache_conditions": "warm app/source state; strip cache cleared before every visible paint",
        "tail_qualification": "unqualified source samples, not a native-frame latency verdict",
        "uncached_visible_paint": report(paints),
        "live_apply_to_textual_pilot_idle": report(updates),
    }, allow_nan=False))
