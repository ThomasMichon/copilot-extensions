"""Nested foreground embeddings share their sink and restore caller logging."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
import json
import logging
import os
from pathlib import Path
import socket
import sys
import time
from types import SimpleNamespace
from typing import Any, cast

import pytest

from agent_pull_requests_service.read_logging import LOGGER, reviewer_reads
from agent_pull_requests_service import hosting
from agent_pull_requests_service.local_config import LocalHostConfig
from agent_pull_requests.repository_read_trace import read

NativeSink = tuple[int, Callable[[int], bytes], Callable[[bytes], int]]


@pytest.fixture(params=["pipe", "socket"])
def native_sink(request: pytest.FixtureRequest) -> Iterator[NativeSink]:
    if sys.platform != "linux":
        pytest.skip("Linux foreground stderr transport")
    if request.param == "pipe":
        reader, writer = os.pipe()
        filler = os.open(f"/proc/self/fd/{writer}", os.O_WRONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        os.set_blocking(reader, False)
        selected: NativeSink = writer, lambda size: os.read(reader, size), lambda value: os.write(filler, value)
        def close() -> None:
            for fd in (reader, writer, filler):
                os.close(fd)
    else:
        receiver, sender = socket.socketpair()
        receiver.setblocking(False)
        selected = sender.fileno(), receiver.recv, lambda value: sender.send(value, socket.MSG_DONTWAIT)
        def close() -> None:
            receiver.close()
            sender.close()
    try:
        yield selected
    finally:
        close()


def drain(sink: NativeSink) -> bytes:
    value = b""
    while True:
        try:
            value += sink[1](65536)
        except BlockingIOError:
            return value


def blocking(descriptor: int) -> bool:
    if sys.platform == "linux":
        return os.get_blocking(descriptor)
    raise RuntimeError("Linux stderr fixture required")


def test_foreground_trace_sink_is_shared_and_restored_after_failure(
    native_sink: NativeSink, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "stderr", SimpleNamespace(fileno=lambda: native_sink[0]))
    assert sys.stderr.fileno() == native_sink[0]
    logger = logging.getLogger(LOGGER)
    before = logger.level, logger.propagate, list(logger.handlers)
    with pytest.raises(RuntimeError, match="embedding stopped"):
        with reviewer_reads():
            with reviewer_reads():
                logger.info('{"outcome":"projected"}')
            logger.info('{"outcome":"failed"}')
            raise RuntimeError("embedding stopped")
    assert [json.loads(line)["outcome"] for line in drain(native_sink).splitlines()] == [
        "projected", "failed",
    ]
    assert (logger.level, logger.propagate, logger.handlers) == before


def test_selected_embedding_captures_to_stderr_without_changing_root_logging(
    tmp_path: Path, native_sink: NativeSink, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "stderr", SimpleNamespace(fileno=lambda: native_sink[0]))
    root = logging.getLogger()
    before = root.level, list(root.handlers)
    config = LocalHostConfig(tmp_path, "test", cast(Any, None), reviewer_actions=True)

    async def serve(selected: Any, **_kwargs: Any) -> None:
        assert selected is config
        logging.getLogger(LOGGER).info('{"outcome":"projected","action_count":0}')

    monkeypatch.setattr(hosting, "_run_service", serve)
    asyncio.run(hosting.run(config))
    assert json.loads(drain(native_sink))["action_count"] == 0
    assert (root.level, root.handlers) == before


def test_stalled_sink_drops_without_blocking_reads_or_shutdown(
    native_sink: NativeSink, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "stderr", SimpleNamespace(fileno=lambda: native_sink[0]))
    for _ in range(1024):
        try:
            native_sink[2](b"x" * 4096)
        except BlockingIOError:
            break
    else:
        pytest.fail("fixture did not fill its bounded diagnostic sink")
    assert blocking(native_sink[0])
    result: dict[str, Any] = {
        "now": 1, "inventory": {"outcome": "complete", "observed_at": 1},
        "cursor": "a" * 64, "event_cursor": 1, "next_actions": [], "unavailable": [], "more": False,
    }

    def query() -> dict[str, Any]:
        return read(lambda: result, target="repository", repository_key="b" * 64, payload={"reviewer": "reviewer"})

    started = time.monotonic()
    with reviewer_reads():
        for _ in range(40):
            assert query() is result
        assert time.monotonic() - started < 2
        assert blocking(native_sink[0])
        drain(native_sink)
        assert query() is result
        rows = [json.loads(line) for line in drain(native_sink).splitlines()]
        assert len(rows) == 2 and rows[-1]["dropped_reads"] == 39
    assert time.monotonic() - started < 2


def test_shutdown_discards_a_stalled_pending_line_without_waiting(
    native_sink: NativeSink, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "stderr", SimpleNamespace(fileno=lambda: native_sink[0]))
    for _ in range(1024):
        try:
            native_sink[2](b"x" * 4096)
        except BlockingIOError:
            break
    else:
        pytest.fail("fixture did not fill its bounded diagnostic sink")
    started = time.monotonic()
    with reviewer_reads():
        logging.getLogger(LOGGER).info('{"outcome":"projected"}')
    assert time.monotonic() - started < 2 and blocking(native_sink[0])
